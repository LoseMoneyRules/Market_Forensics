from __future__ import annotations

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from .access import audit, effective_role, require_control_view
from .core_models import Company, Coverage, InvestmentState, Job, Position, Publication, RefreshRun, Security, Snapshot
from .data_providers import latest_snapshot, provider_overview, provider_status, set_secret
from .extensions import db
from .formatting import NUMBER_FORMATS, get_number_format, set_number_format
from .jobs import enqueue_job, run_jobs
from .models import AuditEvent, Invite, User
from .routes import _ctx, _published_for_role, bp, slugify, utcnow
from .security import login_required, role_required
from .services import can_view_publication, create_snapshot, publication_payload, snapshot_changes


def _queue_status(user_id: int) -> dict:
    due = Job.query.filter(Job.user_id == user_id, Job.status == "QUEUED", Job.run_after <= utcnow()).count()
    queued = Job.query.filter_by(user_id=user_id, status="QUEUED").count()
    running = Job.query.filter_by(user_id=user_id, status="RUNNING").count()
    failed = Job.query.filter_by(user_id=user_id, status="FAILED").count()
    return {"due": due, "queued": queued, "running": running, "failed": failed}


def _job_flash(job: Job) -> str:
    if getattr(job, "_mf_reused", False):
        return f"{job.job_type} is already {job.status.lower()} as job #{job.id}; no duplicate was added."
    return f"{job.job_type} queued as job #{job.id}. Browser worker will process it while CONTROL is open."


@bp.post("/company/<ticker>/snapshot")
@role_required("CONTROL")
def snapshot_company(ticker):
    require_control_view(); ctx = _ctx(ticker); snapshot = create_snapshot(ctx["coverage"], g.user.id, snapshot_type="DECISION")
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
    visibility = str(request.form.get("visibility") or "FRIEND").upper()
    if visibility not in {"FRIEND", "INSIDER"}: abort(400)
    version = (db.session.query(db.func.max(Publication.version)).filter(Publication.coverage_id == ctx["coverage"].id).scalar() or 0) + 1
    title = str(request.form.get("title") or f"{ctx['security'].ticker} Research").strip()
    publication = Publication(coverage_id=ctx["coverage"].id, snapshot_id=snapshot.id, version=version, visibility=visibility, title=title,
                              slug=slugify(f"{ctx['security'].ticker}-{title}"), payload=publication_payload(snapshot, visibility), published_by=g.user.id)
    db.session.add(publication); audit("publication.publish", "publication", version, {"coverage_id": ctx["coverage"].id, "visibility": visibility}); db.session.commit()
    flash(f"Published immutable version {version} for {visibility}+.", "success"); return redirect(url_for("web.publications"))


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
    return render_template("published.html", publication=publication, role=role)


@bp.get("/publications")
@login_required
def publications():
    role = effective_role()
    if role != "CONTROL": return render_template("published_index.html", publications=_published_for_role(role), role=role)
    require_control_view(); return render_template("publications.html", publications=Publication.query.order_by(Publication.published_at.desc()).all())


@bp.get("/portfolio")
@login_required
def portfolio():
    require_control_view(); rows = []
    for position in Position.query.filter_by(user_id=g.user.id).all():
        security = db.session.get(Security, position.security_id); company = db.session.get(Company, security.company_id); coverage = Coverage.query.filter_by(user_id=g.user.id, security_id=security.id).first(); market = latest_snapshot(security.id)
        market_value = float(market.price * position.shares) if market else None; cost = float(position.avg_cost * position.shares)
        rows.append({"position": position, "security": security, "company": company, "coverage": coverage, "market": market, "market_value": market_value,
                     "pnl": market_value - cost if market_value is not None else None, "investment": InvestmentState.query.filter_by(coverage_id=coverage.id).first() if coverage else None})
    return render_template("portfolio.html", rows=rows)


@bp.post("/company/<ticker>/refresh/<kind>")
@role_required("CONTROL")
def queue_refresh(ticker, kind):
    require_control_view(); ctx = _ctx(ticker)
    mapping = {
        "market": "MARKET_REFRESH", "sec": "SEC_INGEST", "recalculate": "RECALCULATE",
        "prefill": "RESEARCH_PREFILL", "finra": "FINRA_IMPORT", "validate": "DEEP_VALIDATION",
        "management": "MANAGEMENT_SCAN",
    }
    job_type = mapping.get(kind)
    if not job_type: abort(404)
    priorities = {"market": 10, "sec": 30, "recalculate": 45, "prefill": 50, "finra": 60, "validate": 70, "management": 80}
    job = enqueue_job(job_type, user_id=g.user.id, company_id=ctx["company"].id, security_id=ctx["security"].id,
                      payload={"coverage_id": ctx["coverage"].id}, priority=priorities.get(kind, 50))
    audit("job.reuse" if getattr(job, "_mf_reused", False) else "job.enqueue", "job", job.id, {"type": job_type, "ticker": ctx["security"].ticker}); db.session.commit(); flash(_job_flash(job), "success")
    return redirect(request.referrer or url_for("web.company_section", ticker=ticker.upper(), section="overview"))


@bp.post("/jobs/<kind>")
@role_required("CONTROL")
def queue_global_job(kind):
    require_control_view(); job_type = {"discovery": "DISCOVERY_SCAN", "bulk": "BULK_REFRESH"}.get(str(kind).lower())
    if not job_type: abort(404)
    job = enqueue_job(job_type, user_id=g.user.id, payload={}, priority=70); audit("job.reuse" if getattr(job, "_mf_reused", False) else "job.enqueue", "job", job.id, {"type": job_type}); db.session.commit(); flash(_job_flash(job), "success")
    return redirect(request.referrer or url_for("web.settings"))


@bp.get("/jobs/status")
@role_required("CONTROL")
def job_status():
    require_control_view()
    return jsonify(_queue_status(g.user.id))


@bp.post("/jobs/pump")
@role_required("CONTROL")
def pump_jobs():
    require_control_view()
    before = _queue_status(g.user.id)
    processed = run_jobs(limit=1, user_id=g.user.id) if before["due"] or before["running"] else []
    after = _queue_status(g.user.id)
    return jsonify({"processed": processed, **after})


@bp.get("/settings")
@login_required
def settings():
    require_control_view()
    jobs = Job.query.filter_by(user_id=g.user.id).order_by(Job.created_at.desc()).limit(50).all()
    refreshes = RefreshRun.query.order_by(RefreshRun.started_at.desc()).limit(30).all()
    return render_template(
        "settings.html",
        providers=provider_status(g.user.id),
        provider_catalog=provider_overview(g.user.id),
        jobs=jobs,
        refreshes=refreshes,
        queue_status=_queue_status(g.user.id),
        number_formats=NUMBER_FORMATS,
        number_format=get_number_format(g.user.id),
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


@bp.get("/control")
@role_required("CONTROL")
def control():
    require_control_view(); users = User.query.order_by(User.created_at.desc()).all(); invites = Invite.query.filter(Invite.used_at.is_(None)).order_by(Invite.created_at.desc()).limit(30).all(); events = AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(80).all()
    return render_template("control.html", users=users, invites=invites, events=events)
