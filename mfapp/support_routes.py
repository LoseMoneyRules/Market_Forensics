from __future__ import annotations

import os
from datetime import date, timedelta

from flask import flash, g, jsonify, redirect, request, url_for

from .access import audit, require_control_view
from .core_models import (
    FinancialFlow,
    FinancialPeriod,
    HistoricalPrice,
    MonitoringRule,
    NormalizedFinancial,
    Provenance,
)
from .extensions import db
from .finra import stored_summary as finra_stored_summary
from .financial_flow_engine import FLOW_VERSION, build_cash_flow, build_income_statement_flow
from .research_synthesis import (
    audit_2_summary,
    build_synthesis,
    business_update_status,
    expectations_chart,
    valuation_price_history,
)
from .monitoring_engine import (
    NUMERIC_OPERATORS,
    evaluate_coverage,
    monitoring_interpretation,
    notification_preferences,
    save_notification_preferences,
)
from .routes import _ctx, bp, dec
from .security import role_required


def _flow_input(period: FinancialPeriod, row: NormalizedFinancial) -> dict:
    return {
        "period_label": f"FY{period.fiscal_year}",
        "fiscal_year": period.fiscal_year,
        "revenue": row.revenue,
        "cogs": row.cogs,
        "gross_profit": row.gross_profit,
        "operating_expenses": row.operating_expenses,
        "operating_income": row.operating_income,
        "pretax_income": row.pretax_income,
        "income_tax": row.income_tax,
        "net_income": row.net_income,
        "cfo": row.cfo,
        "capex": row.capex,
        "fcf": row.fcf,
        "buybacks": row.buybacks,
        "dividends": row.dividends,
    }


def _calculated_flows(period: FinancialPeriod) -> dict:
    normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
    if normalized is None:
        return {}
    payload = _flow_input(period, normalized)
    return {
        "INCOME_STATEMENT": build_income_statement_flow(payload),
        "CASH_FLOW": build_cash_flow(payload),
    }


def _store_flow(period: FinancialPeriod, flow_type: str, payload: dict) -> FinancialFlow:
    row = FinancialFlow.query.filter_by(
        financial_period_id=period.id,
        flow_type=flow_type,
        calculation_version=FLOW_VERSION,
    ).first()
    if row is None:
        row = FinancialFlow(
            financial_period_id=period.id,
            flow_type=flow_type,
            calculation_version=FLOW_VERSION,
        )
        db.session.add(row)
        db.session.flush()
    row.payload = payload
    existing = Provenance.query.filter_by(
        object_type="financial_flow",
        object_id=str(row.id),
        field_name="statement_bridge",
        calculation_version=FLOW_VERSION,
    ).first()
    if existing is None:
        db.session.add(Provenance(
            source_id=period.source_id,
            object_type="financial_flow",
            object_id=str(row.id),
            field_name="statement_bridge",
            raw_or_normalized="CALCULATED",
            financial_period_id=period.id,
            provider="SEC",
            manual_override=False,
            restated=bool(period.is_restated),
            calculation_version=FLOW_VERSION,
            notes="0.2.0 explicit accounting bridge; node values are statement subtotals, edges are reconciled uses/contributions.",
        ))
    return row


def _tape_price_short(company_id: int, security_id: int, months: int) -> list[dict]:
    cutoff = date.today() - timedelta(days=31 * (6 if months <= 6 else 12))
    price_rows = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security_id,
        HistoricalPrice.trade_date >= cutoff,
    ).order_by(HistoricalPrice.trade_date.asc(), HistoricalPrice.id.asc()).all()
    price_by_day = {}
    for row in price_rows:
        price = row.close_split_adjusted if row.close_split_adjusted is not None else row.close_raw
        if price is not None:
            price_by_day[row.trade_date.isoformat()] = float(price)
    finra = finra_stored_summary(company_id)
    out = []
    for row in finra.get("daily_rows", []) or []:
        day = str(row.get("trade_date") or "")
        if not day or day < cutoff.isoformat() or day not in price_by_day:
            continue
        short_pct = row.get("short_pct")
        try:
            short_pct = float(short_pct) * 100.0 if short_pct is not None else None
        except (TypeError, ValueError):
            short_pct = None
        out.append({"date": day, "price": price_by_day[day], "short_pct": short_pct})
    if len(out) > 280:
        step = max(1, len(out) // 240)
        out = out[::step] + ([out[-1]] if out[::step][-1]["date"] != out[-1]["date"] else [])
    return out


@bp.before_request
def enforce_release_invariants():
    """0.2.0 invariants that must hold server-side, not only in the browser."""
    if request.endpoint == "web.add_monitoring" and request.method == "POST":
        if request.form.get("locked_pre_investment") != "1":
            return None
        threshold = dec(request.form.get("threshold_value"))
        operator = str(request.form.get("operator") or "").strip()
        if threshold is None or operator not in NUMERIC_OPERATORS:
            ticker = str((request.view_args or {}).get("ticker") or "").upper()
            flash("A locked pre-investment invalidation must use a numeric threshold and a numeric comparison operator (<, <=, >, >=, ==, !=).", "error")
            return redirect(url_for("web.company_section", ticker=ticker, section="monitoring"))
        return None

    if request.endpoint in {"web.snapshot_company", "web.publish_snapshot"} and request.method == "POST":
        ticker = str((request.view_args or {}).get("ticker") or "").upper()
        if not ticker:
            return None
        ctx = _ctx(ticker)
        readiness = ctx["readiness"]
        ready = bool(readiness.get("total") and readiness.get("done") == readiness.get("total"))
        if not ready:
            pending = [row["label"] for row in readiness.get("gates", []) if not row.get("approved")]
            audit("publication.blocked_readiness", "coverage", ctx["coverage"].id, {"pending": pending[:20]})
            db.session.commit()
            flash("Publication is locked until every current evidence hash is reviewed and approved in Process readiness.", "error")
            return redirect(url_for("web.company_section", ticker=ticker, section="overview"))
    return None


@bp.get("/company/<ticker>/api/surface/<section>")
@role_required("CONTROL")
def research_surface(ticker: str, section: str):
    require_control_view(); ctx = _ctx(ticker)
    company = ctx["company"]; security = ctx["security"]; coverage = ctx["coverage"]
    base = {"version": "0.2.0", "section": section, "ticker": security.ticker}

    if section == "overview":
        base["synthesis"] = build_synthesis(
            coverage=coverage, security=security, company=company, research=ctx["research"], risk=ctx["risk"],
            model=ctx["model"], market=ctx["market"], valuation=ctx["valuation"], intelligence=ctx["intelligence"], readiness=ctx["readiness"],
        )
        base["publish_ready"] = bool(ctx["readiness"].get("total") and ctx["readiness"].get("done") == ctx["readiness"].get("total"))
    elif section == "business":
        base["business_status"] = business_update_status(company.id, ctx["readiness"])
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
        base["price_short"] = _tape_price_short(company.id, security.id, months)
    elif section == "monitoring":
        base["interpretation"] = monitoring_interpretation(coverage)
        base["notification_preferences"] = notification_preferences(g.user.id)
        base["smtp_ready"] = bool(os.environ.get("MF_SMTP_HOST", "").strip() and os.environ.get("MF_SMTP_FROM", "").strip())
    elif section == "audit":
        base["audit"] = audit_2_summary(company.id, security.id, coverage.id)
    else:
        base["message"] = "No 0.2.0 surface augmentation required for this section."
    return jsonify(base)


@bp.post("/company/<ticker>/financial-flows/recalculate")
@role_required("CONTROL")
def recalculate_flows(ticker: str):
    require_control_view(); ctx = _ctx(ticker); count = 0
    periods = FinancialPeriod.query.filter_by(company_id=ctx["company"].id, period_type="FY").order_by(FinancialPeriod.fiscal_year.asc()).all()
    for period in periods:
        for flow_type, payload in _calculated_flows(period).items():
            _store_flow(period, flow_type, payload); count += 1
    audit("financial_flows.recalculate", "company", ctx["company"].id, {"rows": count, "calculation_version": FLOW_VERSION})
    db.session.commit()
    flash(f"Financial Flows rebuilt with 0.2.0 accounting bridge ({count} flow rows).", "success")
    return redirect(url_for("web.financial_flows", ticker=ticker.upper()))


@bp.post("/company/<ticker>/monitoring/evaluate")
@role_required("CONTROL")
def evaluate_monitoring_rules(ticker: str):
    require_control_view(); ctx = _ctx(ticker)
    result = evaluate_coverage(ctx["coverage"].id, g.user.id)
    audit("monitoring.evaluate", "coverage", ctx["coverage"].id, {"created_alerts": result.get("created_alerts", 0)})
    db.session.commit()
    if request.accept_mimetypes.best == "application/json" or request.headers.get("Accept") == "application/json":
        return jsonify(result)
    flash(f"Monitoring evaluated. {result.get('created_alerts', 0)} new alert(s).", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))


@bp.route("/settings/notifications", methods=["GET", "POST"])
@role_required("CONTROL")
def notification_settings():
    require_control_view()
    if request.method == "GET":
        return jsonify({
            "preferences": notification_preferences(g.user.id),
            "smtp_ready": bool(os.environ.get("MF_SMTP_HOST", "").strip() and os.environ.get("MF_SMTP_FROM", "").strip()),
        })
    payload = request.get_json(silent=True) or request.form.to_dict()
    for key in ("email_enabled", "in_app_enabled"):
        if key in payload and isinstance(payload[key], str):
            payload[key] = payload[key].lower() in {"1", "true", "yes", "on"}
    prefs = save_notification_preferences(g.user.id, payload)
    return jsonify({"ok": True, "preferences": prefs})


__all__ = []