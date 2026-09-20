from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from flask import abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access import audit, effective_role, require_control_view
from .core_models import Company, Coverage, InvestmentState, Job, PortfolioRiskPlan, Position, PositionProfile, Publication, RefreshRun, RiskPlan, Security, Snapshot
from .data_providers import latest_snapshot, provider_overview, provider_status, set_secret
from .extensions import db
from .formatting import NUMBER_FORMATS, get_number_format, set_number_format
from .jobs import cancel_job, enqueue_job, recover_stale_running_jobs, terminate_job_executor
from .models import AuditEvent, Invite, User
from .portfolio_engine import portfolio_rows, position_capacity, position_sizing
from .position_action import build_position_action, monitoring_condition_state, monitoring_invalidation_state
from .reporting import emergency_discovery_report_stream, emergency_research_report_stream, get_report_branding, render_discovery_pdf_safe, render_docx_safe, render_pdf_safe, safe_research_report_data, set_report_branding
from .routes import _ctx, _published_for_role, bp, slugify, utcnow
from .security import login_required, role_required
from .services import can_view_publication, create_snapshot, publication_payload, snapshot_changes


def _memory_download(stream, *, mimetype: str, download_name: str):
    """Serve an in-memory artifact without delegating to the WSGI file wrapper.

    Passenger/mod_wsgi deployments can wrap Flask's send_file() object after the
    route has returned, which bypasses our renderer/fallback exception boundary.
    Reports are already bounded in-memory artifacts, so materialize the bytes
    here and return a normal response instead.
    """
    stream.seek(0)
    payload = stream.getvalue() if hasattr(stream, "getvalue") else stream.read()
    response = current_app.response_class(payload, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _job_target_map(jobs: list[Job]) -> dict[int, dict]:
    security_ids = {int(job.security_id) for job in jobs if job.security_id}
    company_ids = {int(job.company_id) for job in jobs if job.company_id}
    securities = {row.id: row for row in Security.query.filter(Security.id.in_(security_ids)).all()} if security_ids else {}
    companies = {row.id: row for row in Company.query.filter(Company.id.in_(company_ids)).all()} if company_ids else {}
    out = {}
    for job in jobs:
        security = securities.get(job.security_id)
        company = companies.get(job.company_id)
        if security is not None:
            out[job.id] = {"label": str(security.ticker).upper(), "ticker": str(security.ticker).upper(), "scope": "SECURITY"}
        elif company is not None:
            out[job.id] = {"label": company.display_name or company.legal_name or f"Company #{company.id}", "ticker": None, "scope": "COMPANY"}
        else:
            out[job.id] = {"label": "GLOBAL", "ticker": None, "scope": "GLOBAL"}
    return out


def _queue_status(user_id: int) -> dict:
    # Status checks also recover dead leases, so a dead worker cannot block the
    # browser fallback forever while the UI still says RUNNING.
    recovered = recover_stale_running_jobs(user_id=user_id)
    due = Job.query.filter(Job.user_id == user_id, Job.status == "QUEUED", Job.run_after <= utcnow()).count()
    queued = Job.query.filter_by(user_id=user_id, status="QUEUED").count()
    running = Job.query.filter_by(user_id=user_id, status="RUNNING").count()
    failed = Job.query.filter_by(user_id=user_id, status="FAILED").count()
    cancelled = Job.query.filter_by(user_id=user_id, status="CANCELLED").count()
    finished = Job.query.filter(
        Job.user_id == user_id,
        Job.status.in_(["DONE", "FAILED", "CANCELLED"]),
        Job.finished_at.is_not(None),
    ).order_by(Job.finished_at.desc(), Job.id.desc()).first()
    active = Job.query.filter(
        Job.user_id == user_id, Job.status.in_(["QUEUED", "RUNNING"])
    ).order_by(Job.status.desc(), Job.priority.asc(), Job.id.asc()).limit(12).all()
    targets = _job_target_map(active)
    active_jobs = [{
        "id": job.id,
        "type": job.job_type,
        "status": job.status,
        "target": targets[job.id]["label"],
        "scope": targets[job.id]["scope"],
        "ticker": targets[job.id]["ticker"],
    } for job in active]
    return {
        "due": due, "queued": queued, "running": running, "failed": failed, "cancelled": cancelled,
        "recovered_stale": recovered,
        "last_finished_id": finished.id if finished else None,
        "last_finished_at": finished.finished_at.isoformat() if finished and finished.finished_at else None,
        "active_jobs": active_jobs,
        "executor": "cron+browser-fallback",
    }


def _spawn_job_runner(user_id: int) -> int:
    """Start one queue consumer outside the Passenger request.

    The CLI owns a cross-process lock, so cron, multiple tabs and repeated kicks
    cannot execute the same queue concurrently.
    """
    root = Path(current_app.root_path).resolve().parent
    manage = root / "manage.py"
    if not manage.exists():
        raise RuntimeError("manage.py not found for background executor")
    venv = str(os.environ.get("VIRTUAL_ENV") or "").strip()
    venv_python = Path(venv) / "bin" / "python" if venv else None
    python_executable = str(venv_python) if venv_python and venv_python.exists() else sys.executable
    command = [
        python_executable, str(manage), "run-jobs",
        "--limit", "1", "--user-id", str(int(user_id)),
    ]
    proc = subprocess.Popen(
        command,
        cwd=str(root),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return int(proc.pid)


def _job_flash(job: Job) -> str:
    if getattr(job, "_mf_reused", False):
        return f"{job.job_type} is already {job.status.lower()} as job #{job.id}; no duplicate was added."
    return f"{job.job_type} queued as job #{job.id}. Background worker/cron will process it."


def _publication_view(publication: Publication, role: str) -> dict:
    payload = dict(publication.payload or {})
    views = payload.get("views") if isinstance(payload.get("views"), dict) else None
    if views:
        key = "INSIDER" if str(role).upper() in {"INSIDER", "CONTROL"} else "FRIEND"
        return dict(views.get(key) or views.get("FRIEND") or {})
    return payload


@bp.post("/company/<ticker>/snapshot")
@role_required("CONTROL")
def snapshot_company(ticker):
    require_control_view(); ctx = _ctx(ticker); snapshot = create_snapshot(
        ctx["coverage"], g.user.id, snapshot_type="DECISION",
        decision_context={
            "research_conclusion": ctx["decision_lenses"].get("research_conclusion"),
            "lenses": ctx["decision_lenses"].get("rows") or [],
            "model_confidence": ctx["decision_lenses"].get("model_confidence"),
            "expectations": ctx["decision_lenses"].get("expectations"),
            "path": ctx["decision_lenses"].get("path"),
        },
    )
    audit("snapshot.create", "snapshot", snapshot.id, {"coverage_id": ctx["coverage"].id}); db.session.commit(); flash(f"Snapshot v{snapshot.version} created. Review it before publishing.", "success")
    return redirect(url_for("web.preview_snapshot", ticker=ticker.upper(), snapshot_id=snapshot.id))


@bp.get("/company/<ticker>/snapshot/<int:snapshot_id>/preview")
@login_required
def preview_snapshot(ticker, snapshot_id):
    require_control_view(); ctx = _ctx(ticker); snapshot = db.session.get(Snapshot, snapshot_id)
    if not snapshot or snapshot.coverage_id != ctx["coverage"].id: abort(404)
    return render_template("publication_preview.html", snapshot=snapshot, changes=snapshot_changes(snapshot), **ctx)


@bp.post("/company/<ticker>/snapshot/<int:snapshot_id>/publish")
@role_required("CONTROL")
def publish_snapshot(ticker, snapshot_id):
    require_control_view(); ctx = _ctx(ticker); snapshot = db.session.get(Snapshot, snapshot_id)
    if not snapshot or snapshot.coverage_id != ctx["coverage"].id: abort(404)
    version = (db.session.query(db.func.max(Publication.version)).filter(Publication.coverage_id == ctx["coverage"].id).scalar() or 0) + 1
    title = str(request.form.get("title") or f"{ctx['security'].ticker} Research").strip()
    payload = {
        "audience": "ALL_MEMBERS",
        "views": {
            "FRIEND": publication_payload(snapshot, "FRIEND"),
            "INSIDER": publication_payload(snapshot, "INSIDER"),
        },
    }
    publication = Publication(
        coverage_id=ctx["coverage"].id, snapshot_id=snapshot.id, version=version,
        visibility="FRIEND", title=title, slug=slugify(f"{ctx['security'].ticker}-{title}"),
        payload=payload, published_by=g.user.id,
    )
    db.session.add(publication)
    audit("publication.publish", "publication", version, {"coverage_id": ctx["coverage"].id, "audience": "ALL_MEMBERS", "role_views": ["FRIEND", "INSIDER"]})
    db.session.commit()
    flash(f"Published immutable version {version} for all members. Role visibility is applied automatically.", "success")
    return redirect(url_for("web.publications"))


@bp.post("/publication/<int:publication_id>/revoke")
@role_required("CONTROL")
def revoke_publication(publication_id):
    require_control_view(); publication = db.session.get(Publication, publication_id)
    if not publication: abort(404)
    if publication.revoked_at is None:
        publication.revoked_at = utcnow(); audit("publication.revoke", "publication", publication.id); db.session.commit()
    flash("Publication revoked. Historical version remains immutable.", "success"); return redirect(url_for("web.publications"))


@bp.get("/published/<slug>")
@login_required
def published(slug):
    role = effective_role(); rows = Publication.query.filter_by(slug=slug).filter(Publication.revoked_at.is_(None)).order_by(Publication.version.desc()).all()
    publication = next((row for row in rows if can_view_publication(row, role)), None)
    if publication is None: abort(404)
    return render_template("published.html", publication=publication, role=role, view_payload=_publication_view(publication, role))


@bp.get("/publications")
@login_required
def publications():
    role = effective_role()
    if role != "CONTROL": return render_template("published_index.html", publications=_published_for_role(role), role=role)
    require_control_view(); return render_template("publications.html", publications=Publication.query.order_by(Publication.published_at.desc()).all())


@bp.get("/discovery/report/pdf")
@role_required("CONTROL")
def discovery_report():
    require_control_view()
    latest = Job.query.filter_by(user_id=g.user.id, job_type="DISCOVERY_SCAN", status="DONE").order_by(Job.finished_at.desc(), Job.id.desc()).first()
    scan = dict(((latest.result or {}).get("market_scan") or {}) if latest else {})
    if not scan.get("candidates"):
        flash("Run a market-wide Discovery scan before exporting the landscape report.", "error")
        return redirect(url_for("web.discovery"))
    try:
        branding = get_report_branding(g.user.id, current_app.config.get("LOGO_URL", ""))
        stream = render_discovery_pdf_safe(scan, branding)
    except Exception:
        current_app.logger.exception("Discovery report render failed; serving emergency fallback")
        db.session.rollback()
        stream = emergency_discovery_report_stream()
    try:
        audit("discovery.report.export", "job", latest.id, {"format": "pdf", "candidates": len(scan.get("candidates") or [])})
        db.session.commit()
    except Exception:
        # Export availability must never depend on the audit write succeeding.
        current_app.logger.exception("Discovery report audit failed; export will still be served")
        db.session.rollback()
    stream.seek(0)
    return _memory_download(stream, mimetype="application/pdf", download_name="Market_Forensics_Discovery.pdf")


@bp.get("/company/<ticker>/report/<fmt>")
@role_required("CONTROL")
def research_report(ticker, fmt):
    require_control_view()
    ctx = _ctx(ticker, queue_recalc=False)
    mode = "executive" if str(request.args.get("mode") or "").lower() == "executive" else "full"
    fmt = str(fmt or "").lower()
    if fmt not in {"pdf", "docx"}:
        abort(404)
    stem = f"{ctx['security'].ticker}_Market_Forensics_{mode}"
    mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if fmt == "docx" else "application/pdf"
    suffix = fmt
    try:
        branding = get_report_branding(g.user.id, current_app.config.get("LOGO_URL", ""))
        data = safe_research_report_data(ctx, mode=mode, branding=branding)
        stream = render_docx_safe(data) if fmt == "docx" else render_pdf_safe(data)
    except Exception:
        current_app.logger.exception("Research report render failed for %s; serving emergency fallback", ctx["security"].ticker)
        db.session.rollback()
        stream = emergency_research_report_stream(
            fmt,
            ticker=ctx["security"].ticker,
            company=ctx["company"].display_name,
            mode=mode,
        )
    try:
        audit("research.report.export", "coverage", ctx["coverage"].id, {"ticker": ctx["security"].ticker, "format": fmt, "mode": mode})
        db.session.commit()
    except Exception:
        current_app.logger.exception("Research report audit failed for %s; export will still be served", ctx["security"].ticker)
        db.session.rollback()
    stream.seek(0)
    return _memory_download(stream, mimetype=mimetype, download_name=f"{stem}.{suffix}")


@bp.get("/portfolio")
@login_required
def portfolio():
    require_control_view()
    rows, totals = portfolio_rows(g.user.id)
    if rows and not totals.get("analytics_ready"):
        enqueue_job("PORTFOLIO_RECALCULATE", user_id=g.user.id, payload={}, priority=99)
    return render_template("portfolio.html", rows=rows, totals=totals)


@bp.get("/portfolio/<ticker>")
@login_required
def portfolio_security(ticker):
    require_control_view()
    ticker = str(ticker or "").strip().upper()
    security = (
        Security.query.filter(db.func.upper(Security.ticker) == ticker)
        .order_by(Security.active.desc(), Security.is_primary.desc(), Security.id.asc())
        .first()
    )
    if security is None:
        abort(404)
    company = db.session.get(Company, security.company_id)
    coverage = Coverage.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    if coverage is not None:
        ctx = _ctx(ticker)
        valuation = ctx["valuation"]
        readiness = ctx["readiness"]
        market = ctx["market"]
        investment = ctx["investment"]
        research_risk = ctx["risk"]
        decision_lenses = dict(ctx.get("decision_lenses") or {})
    else:
        ctx = {}
        valuation = {"bear": None, "base": None, "bull": None, "expected_value": None}
        readiness = {"done": 0, "total": 13, "validation": {"state": "NOT RUN"}}
        market = latest_snapshot(security.id)
        investment = None
        research_risk = None
        decision_lenses = {"research_conclusion": "RESEARCH INCOMPLETE"}

    position = Position.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    profile = PositionProfile.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    money_risk = PortfolioRiskPlan.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    rows, totals = portfolio_rows(g.user.id)
    if rows and not totals.get("analytics_ready"):
        enqueue_job("PORTFOLIO_RECALCULATE", user_id=g.user.id, payload={}, priority=99)
    row = next((item for item in rows if item["security"].id == security.id), None)
    portfolio_value = totals.get("market_value") or 0
    position_value = (row or {}).get("market_value") if row else None
    weight_pct = ((position_value / portfolio_value) * 100.0) if position_value is not None and portfolio_value else 0.0
    sizing = position_sizing(market.price if market else None, money_risk)
    side = str(
        profile.side if profile else
        ("SHORT" if investment and "SHORT" in str(investment.state or "").upper() else "LONG")
    ).upper()
    invalidation_state = monitoring_invalidation_state(coverage.id if coverage else None, research_risk)
    condition_state = monitoring_condition_state(
        user_id=g.user.id,
        security_id=security.id,
        coverage_id=coverage.id if coverage else None,
    )
    position_action = build_position_action(
        position=position,
        side=side,
        research_attached=coverage is not None,
        decision_lenses=decision_lenses,
        readiness=readiness,
        research_risk=research_risk,
        money_risk=money_risk,
        portfolio_weight_pct=weight_pct,
        sizing=sizing,
        monitoring_state=invalidation_state,
        condition_state=condition_state,
    )
    capacity = position_capacity(
        current_price=market.price if market else None,
        shares=position.shares if position else 0,
        portfolio_value=portfolio_value,
        current_weight_pct=weight_pct,
        sizing=sizing,
    )
    history = [
        item for item in list(totals.get("action_history") or [])
        if int(item.get("security_id") or 0) == int(security.id)
    ][:10]

    return render_template(
        "portfolio_security.html",
        security=security,
        company=company,
        coverage=coverage,
        valuation=valuation,
        readiness=readiness,
        market=market,
        investment=investment,
        position=position,
        profile=profile,
        money_risk=money_risk,
        research_risk=research_risk,
        sizing=sizing,
        portfolio_row=row,
        portfolio_totals=totals,
        portfolio_weight_pct=weight_pct,
        research_attached=coverage is not None,
        position_action=position_action,
        invalidation_state=invalidation_state,
        condition_state=condition_state,
        capacity=capacity,
        action_history=history,
    )


@bp.post("/company/<ticker>/refresh/<kind>")
@role_required("CONTROL")
def queue_refresh(ticker, kind):
    require_control_view(); ctx = _ctx(ticker)
    mapping = {
        "market": "MARKET_REFRESH", "sec": "SEC_INGEST", "recalculate": "RECALCULATE",
        "prefill": "RESEARCH_PREFILL", "finra": "FINRA_IMPORT", "prices": "PRICE_HISTORY_REFRESH", "validate": "DEEP_VALIDATION",
        "management": "MANAGEMENT_SCAN", "positioning": "POSITIONING_REFRESH", "macro": "MACRO_REFRESH",
    }
    job_type = mapping.get(kind)
    if not job_type: abort(404)
    priorities = {"market": 10, "sec": 30, "prices": 35, "recalculate": 45, "prefill": 50, "macro": 55, "finra": 60, "positioning": 65, "validate": 70, "management": 80}
    payload = {"coverage_id": ctx["coverage"].id}
    if kind == "prices":
        payload["lookback_years"] = 3
    if kind == "management":
        # A user-requested scan is an explicit re-read, even when this parser
        # version previously completed with zero extracted promises.
        payload.update({"force": True, "limit": 60})
    job = enqueue_job(job_type, user_id=g.user.id, company_id=ctx["company"].id, security_id=ctx["security"].id,
                      payload=payload, priority=priorities.get(kind, 50))
    audit("job.reuse" if getattr(job, "_mf_reused", False) else "job.enqueue", "job", job.id, {"type": job_type, "ticker": ctx["security"].ticker}); db.session.commit(); flash(_job_flash(job), "success")
    return redirect(request.referrer or url_for("web.company_section", ticker=ticker.upper(), section="overview"))


@bp.post("/jobs/<kind>")
@role_required("CONTROL")
def queue_global_job(kind):
    require_control_view(); job_type = {"discovery": "DISCOVERY_SCAN", "bulk": "BULK_REFRESH", "stale": "STALE_REFRESH"}.get(str(kind).lower())
    if not job_type: abort(404)
    job = enqueue_job(job_type, user_id=g.user.id, payload={}, priority=70); audit("job.reuse" if getattr(job, "_mf_reused", False) else "job.enqueue", "job", job.id, {"type": job_type}); db.session.commit(); flash(_job_flash(job), "success")
    return redirect(request.referrer or url_for("web.settings"))


@bp.get("/jobs/status")
@role_required("CONTROL")
def job_status():
    require_control_view()
    return jsonify(_queue_status(g.user.id))


@bp.post("/jobs/<int:job_id>/cancel")
@role_required("CONTROL")
def cancel_active_job(job_id):
    require_control_view()
    job = db.session.get(Job, job_id)
    if not job or job.user_id != g.user.id:
        abort(404)
    previous_status = str(job.status or "").upper()
    if previous_status not in {"QUEUED", "RUNNING"}:
        flash(f"Job #{job.id} is already {previous_status.lower()} and cannot be cancelled.", "error")
        return redirect(request.referrer or url_for("web.settings"))
    pid = cancel_job(job, reason=f"Cancelled by CONTROL user #{g.user.id}")
    audit("job.cancel", "job", job.id, {"type": job.job_type, "previous_status": previous_status})
    db.session.commit()
    terminated = terminate_job_executor(pid) if previous_status == "RUNNING" else False
    flash(
        f"Job #{job.id} cancelled." + (" Executor terminated and lock released." if terminated else ""),
        "success",
    )
    return redirect(request.referrer or url_for("web.settings"))


@bp.post("/jobs/pump")
@role_required("CONTROL")
def pump_jobs():
    require_control_view()
    before = _queue_status(g.user.id)
    if not before["due"]:
        return jsonify({"spawned": False, "reason": "NO_DUE_JOBS", **before})
    if before["running"]:
        return jsonify({"spawned": False, "reason": "EXECUTOR_ACTIVE", **before})
    try:
        pid = _spawn_job_runner(g.user.id)
    except Exception as exc:
        current_app.logger.exception("Unable to start Market Forensics background job executor")
        return jsonify({"spawned": False, "reason": "SPAWN_FAILED", "error": type(exc).__name__, **before}), 503
    return jsonify({"spawned": True, "pid": pid, "executor": "detached-cli", **before}), 202


@bp.get("/settings")
@login_required
def settings():
    require_control_view()
    queue_status = _queue_status(g.user.id)
    jobs = Job.query.filter_by(user_id=g.user.id).order_by(Job.created_at.desc()).limit(50).all()
    job_targets = _job_target_map(jobs)
    job_items = [{"job": job, "target": job_targets[job.id]} for job in jobs]
    refreshes = RefreshRun.query.order_by(RefreshRun.started_at.desc()).limit(30).all()
    return render_template(
        "settings.html",
        providers=provider_status(g.user.id),
        provider_catalog=provider_overview(g.user.id),
        jobs=jobs,
        job_items=job_items,
        refreshes=refreshes,
        queue_status=queue_status,
        number_formats=NUMBER_FORMATS,
        number_format=get_number_format(g.user.id),
        report_branding=get_report_branding(g.user.id, current_app.config.get("LOGO_URL", "")),
    )


@bp.post("/settings/providers")
@role_required("CONTROL")
def save_provider_settings():
    require_control_view()
    for name in (
        "alpaca_key", "alpaca_secret", "tiingo_token", "alpha_vantage_key", "massive_key",
        "sec_user_agent", "finra_client_id", "finra_client_secret",
    ):
        if request.form.get(f"remove_{name}") == "1":
            set_secret(g.user.id, name, "")
        else:
            value = str(request.form.get(name) or "").strip()
            if value: set_secret(g.user.id, name, value)
    audit("settings.providers", "user", g.user.id, {"configured": provider_status(g.user.id)}); db.session.commit(); flash("Provider settings saved. Credentials remain encrypted server-side.", "success")
    return redirect(url_for("web.settings"))


@bp.post("/settings/display")
@role_required("CONTROL")
def save_display_settings():
    require_control_view()
    mode = set_number_format(g.user.id, str(request.form.get("number_format") or "AUTO"))
    audit("settings.display", "user", g.user.id, {"number_format": mode}); db.session.commit()
    flash(f"Number display set to {mode}.", "success")
    return redirect(request.referrer or url_for("web.settings"))


@bp.post("/settings/report-branding")
@role_required("CONTROL")
def save_report_branding():
    require_control_view()
    try:
        value = set_report_branding(
            g.user.id,
            title=str(request.form.get("title") or "Market Forensics").strip(),
            prepared_by=str(request.form.get("prepared_by") or "").strip(),
            footer=str(request.form.get("footer") or "Lose Money Rules").strip(),
            logo_url=str(request.form.get("logo_url") or "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.settings"))
    audit("settings.report_branding", "user", g.user.id, {"title": value.get("title"), "logo": bool(value.get("logo_url"))})
    flash("Report branding saved.", "success")
    return redirect(url_for("web.settings"))


@bp.get("/control")
@role_required("CONTROL")
def control():
    require_control_view(); users = User.query.order_by(User.created_at.desc()).all(); invites = Invite.query.filter(Invite.used_at.is_(None)).order_by(Invite.created_at.desc()).limit(30).all(); events = AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(80).all()
    return render_template("control.html", users=users, invites=invites, events=events)
