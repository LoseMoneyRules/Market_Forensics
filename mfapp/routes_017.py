from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta

from flask import g, jsonify, request

from .access import audit, require_control_view
from .core_models import FinancialPeriod, HistoricalPrice, MonitoringRule
from .extensions import db
from .finra import stored_summary as finra_stored_summary
from .insights_016 import audit_2_summary, build_synthesis, business_update_status, expectations_chart, valuation_price_history
from .monitoring_017 import (
    SYSTEM_ALERTS,
    alert_email,
    alert_policy,
    evaluate_coverage,
    save_alert_email,
    save_alert_policy,
)
from .routes import _ctx, bp
from .routes_016 import _calculated_flows
from .security import login_required, role_required


def _date_value(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except (TypeError, ValueError):
        return None


def _price_history(security_id: int, months: int) -> list[dict]:
    cutoff = date.today() - timedelta(days=31 * months)
    rows = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security_id,
        HistoricalPrice.trade_date >= cutoff,
    ).order_by(HistoricalPrice.trade_date.asc(), HistoricalPrice.id.asc()).all()
    out = []
    for row in rows:
        price = row.close_split_adjusted if row.close_split_adjusted is not None else row.close_raw
        if price is not None:
            out.append({"date": row.trade_date.isoformat(), "price": float(price)})
    if len(out) > 320:
        step = max(1, len(out) // 300)
        compact = out[::step]
        if compact[-1]["date"] != out[-1]["date"]:
            compact.append(out[-1])
        out = compact
    return out


def _short_interest(company_id: int, months: int) -> list[dict]:
    cutoff = date.today() - timedelta(days=31 * months)
    rows = finra_stored_summary(company_id).get("short_interest_rows", []) or []
    out = []
    for row in rows:
        day = _date_value(row.get("settlement_date"))
        if day is None or day < cutoff:
            continue
        value = row.get("current_short")
        if value is None:
            continue
        try:
            out.append({"date": day.isoformat(), "short_interest": float(value), "days_to_cover": float(row["days_to_cover"]) if row.get("days_to_cover") is not None else None})
        except (TypeError, ValueError):
            continue
    return out


def _daily_short(company_id: int) -> list[dict]:
    out = []
    for row in finra_stored_summary(company_id).get("daily_rows", []) or []:
        day = str(row.get("trade_date") or "")[:10]
        value = row.get("short_pct")
        if not day or value is None:
            continue
        try:
            out.append({"date": day, "short_pct": float(value) * 100.0})
        except (TypeError, ValueError):
            continue
    return out[-90:]


def _monitoring_surface(coverage) -> dict:
    rules = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(MonitoringRule.id.asc()).all()
    policy = alert_policy(g.user.id, coverage.id)
    return {
        "email": alert_email(g.user.id),
        "smtp_ready": bool(__import__("os").environ.get("MF_SMTP_HOST", "").strip() and __import__("os").environ.get("MF_SMTP_FROM", "").strip()),
        "policy": policy,
        "rules": [
            {
                "id": row.id,
                "name": row.name,
                "metric": row.metric,
                "operator": row.operator,
                "threshold": float(row.threshold_value) if row.threshold_value is not None else row.threshold_text,
                "unit": row.unit,
                "severity": row.severity,
                "locked": bool(row.locked_pre_investment),
                "email_enabled": row.id in policy["rule_ids"],
            }
            for row in rules
        ],
        "system_alerts": [
            {"key": key, "label": label, "email_enabled": key in policy["system_alerts"]}
            for key, label in SYSTEM_ALERTS.items()
        ],
    }


@bp.get("/company/<ticker>/surface/017/<section>")
@role_required("CONTROL")
def surface_017(ticker: str, section: str):
    require_control_view()
    ctx = _ctx(ticker)
    company = ctx["company"]
    security = ctx["security"]
    coverage = ctx["coverage"]
    base = {"section": section, "ticker": security.ticker}

    if section in {"overview", "business"}:
        synthesis = build_synthesis(
            coverage=coverage, security=security, company=company, research=ctx["research"], risk=ctx["risk"],
            model=ctx["model"], market=ctx["market"], valuation=ctx["valuation"], intelligence=ctx["intelligence"], readiness=ctx["readiness"],
        )
        if section == "overview":
            base["synthesis"] = synthesis
            base["publish_ready"] = bool(ctx["readiness"].get("total") and ctx["readiness"].get("done") == ctx["readiness"].get("total"))
        else:
            status = business_update_status(company.id, ctx["readiness"])
            status.pop("outstanding_gates", None)
            status.pop("outstanding_count", None)
            base["business_status"] = status
            base["macro"] = synthesis.get("macro", [])
            base["strategic_divergences"] = synthesis.get("micro_against", [])
    elif section == "expectations":
        base["expectations"] = expectations_chart(coverage.id)
    elif section == "valuation":
        base["price_history"] = valuation_price_history(security.id, 730)
        base["levels"] = {k: ctx["valuation"].get(k) for k in ("bear", "base", "bull", "expected_value", "current_price")}
    elif section == "financial-flows":
        periods = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY").order_by(FinancialPeriod.fiscal_year.desc()).all()
        year = int(request.args.get("year") or (periods[0].fiscal_year if periods else 0))
        period = next((row for row in periods if row.fiscal_year == year), None)
        base["year"] = year
        base["flows"] = _calculated_flows(period) if period else {}
    elif section == "tape":
        months = 6 if str(request.args.get("months") or "12") == "6" else 12
        base["months"] = months
        base["price_history"] = _price_history(security.id, months)
        base["short_interest"] = _short_interest(company.id, months)
        base["daily_short"] = _daily_short(company.id)
    elif section == "monitoring":
        base["monitoring"] = _monitoring_surface(coverage)
    elif section == "audit":
        base["audit"] = audit_2_summary(company.id, security.id, coverage.id)
    else:
        base["message"] = "No additional surface data required."
    return jsonify(base)


@bp.route("/alerts/017/email", methods=["GET", "POST"])
@login_required
def alert_email_017():
    if request.method == "GET":
        return jsonify({"email": alert_email(g.user.id)})
    payload = request.get_json(silent=True) or request.form.to_dict()
    try:
        value = save_alert_email(g.user.id, str(payload.get("email") or ""), g.user.id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "email": value})


@bp.post("/company/<ticker>/alerts/017/policy")
@role_required("CONTROL")
def alert_policy_017(ticker: str):
    require_control_view()
    ctx = _ctx(ticker)
    payload = request.get_json(silent=True) or {}
    raw_rules = payload.get("rule_ids") or []
    raw_system = payload.get("system_alerts") or []
    try:
        rule_ids = [int(x) for x in raw_rules]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid rule list."}), 400
    value = save_alert_policy(
        g.user.id,
        ctx["coverage"].id,
        rule_ids=rule_ids,
        system_alerts=[str(x) for x in raw_system],
        actor_user_id=g.user.id,
    )
    audit("alerts.policy.update", "coverage", ctx["coverage"].id, {"rule_ids": value["rule_ids"], "system_alerts": value["system_alerts"]})
    db.session.commit()
    return jsonify({"ok": True, "policy": value})


@bp.post("/company/<ticker>/monitoring/evaluate-017")
@role_required("CONTROL")
def evaluate_monitoring_017(ticker: str):
    require_control_view()
    ctx = _ctx(ticker)
    result = evaluate_coverage(ctx["coverage"].id, g.user.id)
    audit("monitoring.evaluate", "coverage", ctx["coverage"].id, {"created_alerts": result.get("created_alerts", 0)})
    db.session.commit()
    return jsonify(result)


__all__ = []
