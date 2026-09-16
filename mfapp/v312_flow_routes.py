from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, url_for

from mfengine.v312.finra import get_daily_short_volume

from .extensions import db
from .models import AuditEvent, Company
from .security import login_required, role_required
from .v312_models import DailyShortVolume, RefreshRun, ShortInterest

bp = Blueprint("v312_flow", __name__)


def _control_view():
    if not getattr(g, "user", None) or g.user.role != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


def _company(ticker):
    return Company.query.filter_by(ticker=ticker.upper()).first_or_404()


@bp.get("/flows/<ticker>")
@login_required
def flows(ticker):
    _control_view()
    company = _company(ticker)
    daily = DailyShortVolume.query.filter_by(company_id=company.id).order_by(DailyShortVolume.trade_date.desc()).limit(60).all()
    interest = ShortInterest.query.filter_by(company_id=company.id).order_by(ShortInterest.settlement_date.desc()).limit(24).all()
    latest = daily[0] if daily else None
    avg20 = None
    if daily:
        vals = [r.short_pct for r in daily[:20] if r.short_pct is not None]
        avg20 = sum(vals) / len(vals) if vals else None
    return render_template("flows.html", company=company, daily=daily, interest=interest, latest=latest, avg20=avg20)


@bp.post("/flows/<ticker>/refresh-finra")
@role_required("CONTROL")
def refresh_finra(ticker):
    _control_view()
    company = _company(ticker)
    run = RefreshRun(company_id=company.id, actor_user_id=g.user.id, scope="FINRA_DAILY_SHORT_VOLUME", status="RUNNING", provider="FINRA Reg SHO")
    db.session.add(run)
    db.session.commit()
    try:
        rows = get_daily_short_volume(company.ticker, 45)
        for item in rows:
            row = DailyShortVolume.query.filter_by(company_id=company.id, trade_date=item["trade_date"]).first()
            if row is None:
                row = DailyShortVolume(company_id=company.id, trade_date=item["trade_date"])
                db.session.add(row)
            row.short_volume = item.get("short_volume")
            row.short_exempt_volume = item.get("short_exempt_volume")
            row.total_reported_volume = item.get("total_reported_volume")
            row.short_pct = item.get("short_pct")
            row.market = item.get("market") or ""
        run.status = "PASS" if rows else "LOW DATA"
        run.rows_written = len(rows)
        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.add(AuditEvent(actor_user_id=g.user.id, action="flow.finra_refresh", object_type="company", object_id=str(company.id), meta={"ticker": company.ticker, "rows": len(rows), "engine": "3.1.12"}))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        run = db.session.get(RefreshRun, run.id)
        if run:
            run.status = "FAIL"
            run.warnings = [type(exc).__name__]
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()
        flash("FINRA refresh failed safely; existing last-good flow data was preserved.", "error")
    else:
        flash(f"{company.ticker}: refreshed {len(rows)} FINRA short-volume sessions.", "success")
    return redirect(url_for("v312_flow.flows", ticker=company.ticker))
