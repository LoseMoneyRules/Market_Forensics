from __future__ import annotations

from datetime import date, timedelta

from flask import abort, g, jsonify, request

from .access import audit, require_control_view
from .core_models import FinancialPeriod, HistoricalPrice, MonitoringRule
from .extensions import db
from .finra import stored_summary as finra_stored_summary
from .research_synthesis import audit_2_summary, build_synthesis, expectations_chart, valuation_price_history
from .monitoring_engine import NUMERIC_OPERATORS
from .alert_engine import (
    SYSTEM_ALERTS,
    alert_catalog,
    alert_email,
    alert_subscription,
    evaluate_coverage,
    save_alert_catalog,
    save_alert_email,
    save_alert_subscription,
    subscription_catalog_for_user,
)
from .routes import _ctx, bp
from .support_routes import _calculated_flows
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
            out.append({
                "date": day.isoformat(),
                "short_interest": float(value),
                "days_to_cover": float(row["days_to_cover"]) if row.get("days_to_cover") is not None else None,
            })
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
    return out[-100:]


def _rule_payload(row: MonitoringRule, catalog: dict, subscription: dict) -> dict:
    threshold = float(row.threshold_value) if row.threshold_value is not None else None
    return {
        "id": row.id,
        "name": row.name,
        "metric": row.metric,
        "operator": row.operator,
        "threshold": threshold,
        "threshold_text": row.threshold_text,
        "unit": row.unit,
        "severity": row.severity,
        "locked": bool(row.locked_pre_investment),
        "member_available": row.id in catalog["rule_ids"],
        "subscribed": row.id in subscription["rule_ids"],
    }


def _monitoring_surface(coverage) -> dict:
    rules = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(MonitoringRule.id.asc()).all()
    catalog = alert_catalog(coverage.id)
    subscription = alert_subscription(g.user.id, coverage.id)
    return {
        "coverage_id": coverage.id,
        "email": alert_email(g.user.id),
        "smtp_ready": bool(__import__("os").environ.get("MF_SMTP_HOST", "").strip() and __import__("os").environ.get("MF_SMTP_FROM", "").strip()),
        "catalog": catalog,
        "subscription": subscription,
        "rules": [_rule_payload(row, catalog, subscription) for row in rules],
        "system_alerts": [
            {
                "key": key,
                "label": label,
                "member_available": key in catalog["system_alerts"],
                "subscribed": key in subscription["system_alerts"],
            }
            for key, label in SYSTEM_ALERTS.items()
        ],
    }


def _bool(value, default=False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@bp.get("/company/<ticker>/api/surface-detail/<section>")
@role_required("CONTROL")
def research_surface_detail(ticker: str, section: str):
    require_control_view()
    ctx = _ctx(ticker)
    company = ctx["company"]
    security = ctx["security"]
    coverage = ctx["coverage"]
    base = {"section": section, "ticker": security.ticker}

    if section in {"overview", "business"}:
        synthesis = build_synthesis(
            coverage=coverage,
            security=security,
            company=company,
            research=ctx["research"],
            risk=ctx["risk"],
            model=ctx["model"],
            market=ctx["market"],
            valuation=ctx["valuation"],
            intelligence=ctx["intelligence"],
            readiness=ctx["readiness"],
        )
        if section == "overview":
            base["synthesis"] = synthesis
            base["publish_ready"] = bool(
                ctx["readiness"].get("total")
                and ctx["readiness"].get("done") == ctx["readiness"].get("total")
            )
        else:
            base["macro"] = synthesis.get("macro", [])
            base["strategic_divergences"] = synthesis.get("micro_against", [])
    elif section == "expectations":
        base["expectations"] = expectations_chart(coverage.id)
    elif section == "valuation":
        base["price_history"] = valuation_price_history(security.id, 730)
        base["levels"] = {
            key: ctx["valuation"].get(key)
            for key in ("bear", "base", "bull", "expected_value", "current_price")
        }
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


@bp.route("/alerts/email", methods=["GET", "POST"])
@login_required
def alert_email():
    if request.method == "GET":
        return jsonify({"email": alert_email(g.user.id)})
    payload = request.get_json(silent=True) or request.form.to_dict()
    try:
        value = save_alert_email(g.user.id, str(payload.get("email") or ""), g.user.id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "email": value})


@bp.route("/alerts/subscription/<int:coverage_id>", methods=["GET", "POST"])
@login_required
def alert_subscription(coverage_id: int):
    try:
        current = subscription_catalog_for_user(g.user.id, coverage_id)
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    if request.method == "GET":
        return jsonify({"ok": True, **current})

    payload = request.get_json(silent=True) or {}
    if "email" in payload:
        try:
            save_alert_email(g.user.id, str(payload.get("email") or ""), g.user.id)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        value = save_alert_subscription(
            g.user.id,
            coverage_id,
            rule_ids=[int(x) for x in (payload.get("rule_ids") or [])],
            system_alerts=[str(x) for x in (payload.get("system_alerts") or [])],
            email_enabled=_bool(payload.get("email_enabled"), True),
            in_app_enabled=_bool(payload.get("in_app_enabled"), True),
            actor_user_id=g.user.id,
        )
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid alert selection."}), 400
    return jsonify({"ok": True, "subscription": value, "email": alert_email(g.user.id)})


@bp.post("/company/<ticker>/alerts/catalog")
@role_required("CONTROL")
def alert_catalog(ticker: str):
    require_control_view()
    ctx = _ctx(ticker)
    payload = request.get_json(silent=True) or {}
    try:
        value = save_alert_catalog(
            ctx["coverage"].id,
            rule_ids=[int(x) for x in (payload.get("rule_ids") or [])],
            system_alerts=[str(x) for x in (payload.get("system_alerts") or [])],
            actor_user_id=g.user.id,
        )
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid alert catalog."}), 400
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    audit("alerts.catalog.update", "coverage", ctx["coverage"].id, value)
    db.session.commit()
    return jsonify({"ok": True, "catalog": value})


@bp.post("/company/<ticker>/alerts/policy")
@role_required("CONTROL")
def alert_policy(ticker: str):
    """Backward-compatible CONTROL subscription endpoint used by early 0.2.0 UI work."""
    require_control_view()
    ctx = _ctx(ticker)
    payload = request.get_json(silent=True) or {}
    value = save_alert_subscription(
        g.user.id,
        ctx["coverage"].id,
        rule_ids=[int(x) for x in (payload.get("rule_ids") or [])],
        system_alerts=[str(x) for x in (payload.get("system_alerts") or [])],
        email_enabled=_bool(payload.get("email_enabled"), True),
        in_app_enabled=_bool(payload.get("in_app_enabled"), True),
        actor_user_id=g.user.id,
    )
    return jsonify({"ok": True, "subscription": value})


@bp.route("/company/<ticker>/alerts/rule/<int:rule_id>", methods=["PATCH", "DELETE"])
@role_required("CONTROL")
def alert_rule(ticker: str, rule_id: int):
    require_control_view()
    ctx = _ctx(ticker)
    rule = db.session.get(MonitoringRule, rule_id)
    if not rule or rule.coverage_id != ctx["coverage"].id:
        abort(404)
    if rule.locked_pre_investment:
        return jsonify({"ok": False, "error": "Locked pre-investment invalidation rules are immutable."}), 409

    if request.method == "DELETE":
        rule.is_active = False
        audit("monitoring.archive", "monitoring_rule", rule.id, {"ticker": ticker.upper()})
        db.session.commit()
        return jsonify({"ok": True, "archived": rule.id})

    payload = request.get_json(silent=True) or {}
    operator = str(payload.get("operator", rule.operator) or "NOTE").strip()
    if operator not in NUMERIC_OPERATORS and operator.upper() != "NOTE":
        return jsonify({"ok": False, "error": "Operator must be <, <=, >, >=, ==, != or NOTE."}), 400
    threshold_raw = payload.get("threshold")
    threshold = rule.threshold_value
    if operator in NUMERIC_OPERATORS:
        try:
            threshold = float(threshold_raw if threshold_raw not in (None, "") else rule.threshold_value)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Automatic rules require a numeric threshold."}), 400
    else:
        threshold = None

    rule.name = str(payload.get("name", rule.name) or "").strip()[:180]
    rule.metric = str(payload.get("metric", rule.metric) or "").strip()[:100]
    rule.operator = operator.upper() if operator.upper() == "NOTE" else operator
    rule.threshold_value = threshold
    rule.threshold_text = str(payload.get("threshold_text", rule.threshold_text) or "").strip()[:220]
    rule.unit = str(payload.get("unit", rule.unit) or "").strip()[:32]
    severity = str(payload.get("severity", rule.severity) or "WATCH").upper()
    rule.severity = severity if severity in {"INFO", "WATCH", "FAIL", "CRITICAL"} else "WATCH"
    if not rule.name:
        return jsonify({"ok": False, "error": "Rule name is required."}), 400
    audit("monitoring.rule.edit", "monitoring_rule", rule.id, {"ticker": ticker.upper()})
    db.session.commit()
    return jsonify({"ok": True, "rule": _rule_payload(rule, alert_catalog(ctx["coverage"].id), alert_subscription(g.user.id, ctx["coverage"].id))})


@bp.post("/company/<ticker>/monitoring/evaluate-now")
@role_required("CONTROL")
def evaluate_monitoring(ticker: str):
    require_control_view()
    ctx = _ctx(ticker)
    result = evaluate_coverage(ctx["coverage"].id, g.user.id)
    audit("monitoring.evaluate", "coverage", ctx["coverage"].id, {"created_alerts": result.get("created_alerts", 0)})
    db.session.commit()
    return jsonify(result)


__all__ = []
