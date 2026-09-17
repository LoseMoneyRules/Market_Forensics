from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import abort, g, jsonify, redirect, request, url_for

from .access import audit, require_control_view
from .core_models import ResearchGateApproval
from .data_providers import latest_snapshot
from .extensions import db
from .jobs import enqueue_job
from .readiness_015 import research_readiness
from .routes import _ctx, bp
from .security import role_required


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@bp.post("/company/<ticker>/readiness/<gate_key>")
@role_required("CONTROL")
def approve_research_gate_015(ticker: str, gate_key: str):
    require_control_view(); ctx = _ctx(ticker)
    gate = next((row for row in research_readiness(ctx["coverage"])["gates"] if row["key"] == gate_key), None)
    if gate is None:
        abort(404)
    action = str(request.form.get("action") or "approve").lower()
    existing = ResearchGateApproval.query.filter_by(coverage_id=ctx["coverage"].id, gate_key=gate_key).first()
    if action == "revoke":
        if existing:
            db.session.delete(existing)
        audit("research_gate.revoke", "coverage", ctx["coverage"].id, {"gate": gate_key})
    else:
        if not gate["evidence_ready"]:
            return redirect(url_for("web.company_section", ticker=ticker.upper(), section="overview"))
        if existing is None:
            existing = ResearchGateApproval(coverage_id=ctx["coverage"].id, gate_key=gate_key, approved_by=g.user.id)
            db.session.add(existing)
        existing.approved_by = g.user.id
        existing.approved_at = utcnow()
        existing.evidence_hash = gate["evidence_hash"]
        existing.note = str(request.form.get("note") or "")[:240]
        audit("research_gate.approve", "coverage", ctx["coverage"].id, {"gate": gate_key, "evidence_hash": gate["evidence_hash"]})
    db.session.commit()
    return redirect(request.referrer or url_for("web.company_section", ticker=ticker.upper(), section="overview"))


@bp.get("/company/<ticker>/price/live")
@role_required("CONTROL")
def live_price_015(ticker: str):
    require_control_view(); ctx = _ctx(ticker); snap = latest_snapshot(ctx["security"].id)
    return jsonify({
        "ticker": ctx["security"].ticker,
        "price": float(snap.price) if snap else None,
        "provider": snap.provider if snap else None,
        "as_of": snap.as_of.isoformat() if snap and snap.as_of else None,
        "quality": snap.quality if snap else None,
    })


@bp.post("/company/<ticker>/price/refresh")
@role_required("CONTROL")
def refresh_price_015(ticker: str):
    require_control_view(); ctx = _ctx(ticker); snap = latest_snapshot(ctx["security"].id)
    fresh = bool(snap and snap.as_of and snap.as_of >= utcnow() - timedelta(minutes=5))
    job = None
    if not fresh:
        job = enqueue_job(
            "MARKET_REFRESH", user_id=g.user.id, company_id=ctx["company"].id,
            security_id=ctx["security"].id, payload={"coverage_id": ctx["coverage"].id}, priority=10,
        )
    return jsonify({
        "fresh": fresh,
        "queued": bool(job and not getattr(job, "_mf_reused", False)),
        "reused": bool(job and getattr(job, "_mf_reused", False)),
        "job_id": job.id if job else None,
        "price": float(snap.price) if snap else None,
        "as_of": snap.as_of.isoformat() if snap and snap.as_of else None,
    })


__all__ = []
