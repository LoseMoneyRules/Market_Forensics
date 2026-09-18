from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import abort, g, jsonify, redirect, render_template, request, url_for

from .access import audit, require_control_view
from .core_models import Company, Coverage, Job, ResearchGateApproval, Security
from .data_providers import latest_snapshot
from .extensions import db
from .jobs import enqueue_job
from .readiness import research_readiness
from .research_cache import patch_research_cache_readiness
from .routes import _ctx, bp
from .security import role_required


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _gate_context(ticker: str) -> dict:
    security = Security.query.filter(
        db.func.upper(Security.ticker) == str(ticker).upper(),
        Security.active.is_(True),
    ).order_by(Security.is_primary.desc(), Security.id.asc()).first()
    if security is None:
        abort(404)
    coverage = Coverage.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    if coverage is None:
        abort(404)
    company = db.session.get(Company, security.company_id)
    if company is None:
        abort(404)
    return {"coverage": coverage, "security": security, "company": company}


@bp.post("/company/<ticker>/readiness/<gate_key>")
@role_required("CONTROL")
def approve_research_gate(ticker: str, gate_key: str):
    require_control_view(); ctx = _gate_context(ticker)
    gate = next((row for row in research_readiness(ctx["coverage"])["gates"] if row["key"] == gate_key), None)
    if gate is None:
        abort(404)
    action = str(request.form.get("action") or "approve").lower()
    if action not in {"approve", "revoke"}:
        return jsonify({"ok": False, "message": "Unsupported readiness action."}), 400
    existing = ResearchGateApproval.query.filter_by(coverage_id=ctx["coverage"].id, gate_key=gate_key).first()
    if action == "revoke":
        if existing:
            db.session.delete(existing)
        audit("research_gate.revoke", "coverage", ctx["coverage"].id, {"gate": gate_key})
    else:
        if not gate["evidence_ready"]:
            if "application/json" in str(request.headers.get("Accept") or ""):
                return jsonify({"ok": False, "message": "Evidence is not ready for approval."}), 409
            return redirect(url_for("web.company_section", ticker=ticker.upper(), section="overview"))
        if existing is None:
            existing = ResearchGateApproval(coverage_id=ctx["coverage"].id, gate_key=gate_key, approved_by=g.user.id)
            db.session.add(existing)
        existing.approved_by = g.user.id
        existing.approved_at = utcnow()
        existing.evidence_hash = gate["evidence_hash"]
        existing.note = str(request.form.get("note") or "")[:240]
        audit("research_gate.approve", "coverage", ctx["coverage"].id, {"gate": gate_key, "evidence_hash": gate["evidence_hash"]})
    db.session.flush()
    fresh_readiness = research_readiness(ctx["coverage"])
    patch_research_cache_readiness(ctx["coverage"].id, fresh_readiness)
    db.session.commit()

    if "application/json" in str(request.headers.get("Accept") or ""):
        html = render_template("_process_readiness.html", readiness=fresh_readiness, security=ctx["security"])
        return jsonify({
            "ok": True,
            "gate_key": gate_key,
            "readiness": {
                "done": fresh_readiness["done"],
                "total": fresh_readiness["total"],
                "ready_to_validate": fresh_readiness["ready_to_validate"],
            },
            "html": html,
        })
    return redirect(request.referrer or url_for("web.company_section", ticker=ticker.upper(), section="overview"))


def _quote_is_fresh(snap) -> bool:
    return bool(snap and snap.as_of and snap.as_of >= utcnow() - timedelta(minutes=5))


def _recent_market_refresh(user_id: int, security_id: int):
    return (
        Job.query.filter(
            Job.user_id == user_id,
            Job.security_id == security_id,
            Job.job_type == "MARKET_REFRESH",
            Job.status.in_(["DONE", "FAILED", "CANCELLED"]),
            Job.finished_at.is_not(None),
        )
        .order_by(Job.finished_at.desc(), Job.id.desc())
        .first()
    )


@bp.get("/company/<ticker>/price/live")
@role_required("CONTROL")
def live_price(ticker: str):
    require_control_view(); ctx = _ctx(ticker); snap = latest_snapshot(ctx["security"].id)
    return jsonify({
        "ticker": ctx["security"].ticker,
        "price": float(snap.price) if snap else None,
        "provider": snap.provider if snap else None,
        "as_of": snap.as_of.isoformat() if snap and snap.as_of else None,
        "quality": snap.quality if snap else None,
        "fresh": _quote_is_fresh(snap),
    })


@bp.post("/company/<ticker>/price/refresh")
@role_required("CONTROL")
def refresh_price(ticker: str):
    require_control_view(); ctx = _ctx(ticker); snap = latest_snapshot(ctx["security"].id)
    fresh = _quote_is_fresh(snap)
    job = None
    recent = None
    cooldown = False
    retry_after_seconds = 0
    active = None
    if not fresh:
        active = (
            Job.query.filter(
                Job.user_id == g.user.id,
                Job.security_id == ctx["security"].id,
                Job.job_type == "MARKET_REFRESH",
                Job.status.in_(["QUEUED", "RUNNING"]),
            )
            .order_by(Job.id.desc())
            .first()
        )
        if active:
            job = active
        else:
            recent = _recent_market_refresh(g.user.id, ctx["security"].id)
            if recent and recent.finished_at:
                elapsed = max(0.0, (utcnow() - recent.finished_at).total_seconds())
                window = 60 if recent.status == "FAILED" else 5 * 60
                cooldown = elapsed < window
                retry_after_seconds = max(0, int(window - elapsed)) if cooldown else 0
            if not cooldown:
                job = enqueue_job(
                    "MARKET_REFRESH", user_id=g.user.id, company_id=ctx["company"].id,
                    security_id=ctx["security"].id, payload={"coverage_id": ctx["coverage"].id}, priority=10,
                )
    reused = bool(job and (active is not None or getattr(job, "_mf_reused", False)))
    queued = bool(job and not reused)
    status = "FRESH" if fresh else ("COOLDOWN" if cooldown else ("REUSED" if reused else "QUEUED"))
    return jsonify({
        "status": status,
        "fresh": fresh,
        "queued": queued,
        "reused": reused,
        "cooldown": cooldown,
        "retry_after_seconds": retry_after_seconds,
        "job_id": job.id if job else (recent.id if cooldown and recent else None),
        "ticker": ctx["security"].ticker,
        "price": float(snap.price) if snap else None,
        "provider": snap.provider if snap else None,
        "as_of": snap.as_of.isoformat() if snap and snap.as_of else None,
    })


__all__ = []
