from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from flask import flash, g, redirect, render_template, request, url_for

from .access import audit, require_control_view
from .autofill import _financial_history, _point_in_time_calibration, prefill_coverage
from .core_models import FinancialFlow, FinancialPeriod, HistoricalPrice, HistoricalTestRun, HistoricalTestSample, ValuationScenario
from .extensions import db
from .jobs import enqueue_job
from .routes import SECTIONS, _ctx, bp
from .security import role_required
from .valuation_engine import default_cases, evaluate, infer_company_type, metrics_from_history, n


def _fraction(value, default=None):
    out = n(value)
    if out is None:
        return default
    return out / 100.0 if abs(out) > 1.0 else out


def _reference_price(ctx: dict) -> tuple[float | None, str, bool]:
    market = ctx.get("market")
    if market and n(market.price) not in (None, 0):
        age_hours = None
        if market.as_of:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            age_hours = max(0.0, (now - market.as_of).total_seconds() / 3600.0)
        return n(market.price), str(market.provider or "Stored market quote"), bool(age_hours is not None and age_hours > 72)
    row = HistoricalPrice.query.filter_by(security_id=ctx["security"].id).order_by(HistoricalPrice.trade_date.desc(), HistoricalPrice.id.desc()).first()
    if row:
        value = n(row.close_split_adjusted) or n(row.close_raw)
        if value not in (None, 0):
            return value, f"Historical price cache · {row.trade_date}", True
    return None, "No stored price reference", True


def _current_model_context(ctx: dict) -> dict:
    model = ctx["model"]
    saved = dict(model.assumptions or {})
    history = _financial_history(ctx["company"].id)
    company_type = str(saved.get("company_type") or infer_company_type(ctx["company"].sector, ctx["company"].industry))
    metrics = metrics_from_history(history, saved.get("current_shares"), str(saved.get("share_source") or ""))
    calibration = dict(saved.get("calibration") or _point_in_time_calibration(ctx["security"].id, history, company_type))
    defaults = default_cases(metrics, company_type, calibration)
    weights = dict(saved.get("weights") or defaults["weights"])
    years = int(saved.get("horizon_years") or defaults["horizon_years"])
    scenario_rows = {row.name.upper(): row for row in model.scenarios}
    cases = {}
    fallback_values = {}
    for name in ("BEAR", "BASE", "BULL"):
        row = scenario_rows.get(name)
        inputs = dict((row.inputs or {}) if row else {})
        fallback = defaults[name]
        fallback_values[name] = n(row.equity_value_per_share) if row else None
        cases[name] = {
            "growth": n(inputs.get("growth")) if n(inputs.get("growth")) is not None else fallback["growth"],
            "net_margin": n(inputs.get("net_margin")) if n(inputs.get("net_margin")) is not None else fallback["net_margin"],
            "fcf_margin": n(inputs.get("fcf_margin")) if n(inputs.get("fcf_margin")) is not None else fallback["fcf_margin"],
            "pe": n(inputs.get("pe")) if n(inputs.get("pe")) is not None else fallback["pe"],
            "ev_sales": n(inputs.get("ev_sales")) if n(inputs.get("ev_sales")) is not None else fallback["ev_sales"],
            "target_fcf_yield": n(inputs.get("target_fcf_yield")) if n(inputs.get("target_fcf_yield")) is not None else fallback["target_fcf_yield"],
            "equity_discount_rate": n(inputs.get("equity_discount_rate")) if n(inputs.get("equity_discount_rate")) is not None else fallback["equity_discount_rate"],
            "terminal_growth": n(inputs.get("terminal_growth")) if n(inputs.get("terminal_growth")) is not None else fallback["terminal_growth"],
            "probability": n(row.probability) if row and row.probability is not None else fallback["probability"],
            "manual_override": n(inputs.get("manual_override")),
        }
    current_price, reference_price_source, reference_price_stale = _reference_price(ctx)
    result = evaluate(metrics, cases, weights, years, current_price=current_price, fallback_values=fallback_values)
    return {
        "metrics": metrics,
        "company_type": company_type,
        "calibration": calibration,
        "weights": weights,
        "years": years,
        "cases": cases,
        "engine_result": result,
        "model_settings": saved,
        "reference_price_source": reference_price_source,
        "reference_price_stale": reference_price_stale,
        "quote_candidates": ((ctx["market"].payload or {}).get("candidates") or []) if ctx.get("market") else [],
    }


@bp.get("/company/<ticker>/valuation")
@role_required("CONTROL")
def valuation_company(ticker):
    require_control_view(); ctx = _ctx(ticker); extra = _current_model_context(ctx)
    return render_template("valuation_company.html", **ctx, **extra)


@bp.post("/company/<ticker>/valuation/model")
@role_required("CONTROL")
def save_valuation_company(ticker):
    require_control_view(); ctx = _ctx(ticker); model = ctx["model"]
    saved = dict(model.assumptions or {})
    company_type = str(request.form.get("company_type") or saved.get("company_type") or "Generic")
    share_source = str(request.form.get("share_source") or saved.get("share_source") or "SEC_FY_OUTSTANDING")[:48]
    current_shares = n(request.form.get("current_shares"))
    share_verified = request.form.get("share_basis_verified") == "1"
    share_note = str(request.form.get("share_basis_note") or "").strip()
    weights = {
        "pe": max(0.0, n(request.form.get("weight_pe")) or 0.0),
        "ev_sales": max(0.0, n(request.form.get("weight_ev_sales")) or 0.0),
        "fcf_yield": max(0.0, n(request.form.get("weight_fcf_yield")) or 0.0),
    }
    years = max(1, min(int(n(request.form.get("horizon_years")) or 5), 20))
    history = _financial_history(ctx["company"].id)
    metrics = metrics_from_history(history, current_shares, share_source)
    if current_shares in (None, 0) and metrics.get("shares") is not None:
        current_shares = metrics["shares"]
        share_source = metrics.get("share_source") or share_source
    calibration = dict(saved.get("calibration") or _point_in_time_calibration(ctx["security"].id, history, company_type))
    defaults = default_cases(metrics, company_type, calibration)
    cases = {}
    for name in ("BEAR", "BASE", "BULL"):
        key = name.lower(); fallback = defaults[name]
        cases[name] = {
            "growth": _fraction(request.form.get(f"{key}_growth"), fallback["growth"]),
            "net_margin": _fraction(request.form.get(f"{key}_net_margin"), fallback["net_margin"]),
            "fcf_margin": _fraction(request.form.get(f"{key}_fcf_margin"), fallback["fcf_margin"]),
            "pe": n(request.form.get(f"{key}_pe")) or fallback["pe"],
            "ev_sales": n(request.form.get(f"{key}_ev_sales")) or fallback["ev_sales"],
            "target_fcf_yield": _fraction(request.form.get(f"{key}_target_fcf_yield"), fallback["target_fcf_yield"]),
            "equity_discount_rate": _fraction(request.form.get(f"{key}_equity_discount_rate"), fallback["equity_discount_rate"]),
            "terminal_growth": _fraction(request.form.get(f"{key}_terminal_growth"), fallback["terminal_growth"]),
            "probability": _fraction(request.form.get(f"{key}_probability"), fallback["probability"]),
            "manual_override": n(request.form.get(f"{key}_manual_override")),
        }
    rows = {row.name.upper(): row for row in model.scenarios}
    fallback_values = {name: n(rows[name].equity_value_per_share) if name in rows else None for name in ("BEAR", "BASE", "BULL")}
    current_price, _, _ = _reference_price(ctx)
    result = evaluate(metrics, cases, weights, years, current_price=current_price, fallback_values=fallback_values)
    for name in ("BEAR", "BASE", "BULL"):
        row = rows.get(name)
        if row is None:
            row = ValuationScenario(model_id=model.id, name=name); db.session.add(row)
        row.probability = Decimal(str(cases[name]["probability"] or 0))
        fair = result["scenarios"][name].get("fair_value")
        if fair is not None:
            row.equity_value_per_share = Decimal(str(fair))
        row.confidence = "USER_REVIEWED" if share_verified and result.get("quality") == "INTRINSIC" else "PROVISIONAL"
        row.inputs = {"auto_prefill": False, **cases[name]}
        row.outputs = result["scenarios"][name]
    model.method = "MULTI_METHOD_INTRINSIC"
    model.calculation_version = "0.2.0"
    model.assumptions = saved | {
        "engine_version": "0.2.0", "company_type": company_type, "current_shares": current_shares,
        "share_source": share_source, "share_basis_verified": share_verified, "share_basis_note": share_note,
        "weights": weights, "horizon_years": years, "calibration": calibration, "latest_engine_result": result,
        "notes": str(request.form.get("assumptions_notes") or saved.get("notes") or "").strip(),
    }
    model.updated_by = g.user.id
    audit("valuation.model.save", "coverage", ctx["coverage"].id, {"method": model.method, "share_basis_verified": share_verified, "quality": result.get("quality")})
    db.session.commit()
    flash("Valuation assumptions saved and recalculated.", "success")
    return redirect(url_for("web.valuation_company", ticker=ticker.upper()))


@bp.post("/company/<ticker>/valuation/reset")
@role_required("CONTROL")
def reset_valuation_company(ticker):
    require_control_view(); ctx = _ctx(ticker); result = prefill_coverage(ctx["coverage"].id, g.user.id, force=True)
    audit("valuation.model.reset", "coverage", ctx["coverage"].id, {"engine": "0.2.0"}); db.session.commit()
    flash(f"Valuation rebuilt from evidence for {result['ticker']}. Review inputs before relying on it.", "success")
    return redirect(url_for("web.valuation_company", ticker=ticker.upper()))


@bp.get("/company/<ticker>/financial-flows")
@role_required("CONTROL")
def financial_flows(ticker):
    require_control_view(); ctx = _ctx(ticker); company = ctx["company"]
    periods = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY").order_by(FinancialPeriod.fiscal_year.desc()).all()
    requested = int(request.args.get("year") or (periods[0].fiscal_year if periods else 0)); period = next((p for p in periods if p.fiscal_year == requested), None)
    flows = {}
    if period:
        for flow_type in ("INCOME_STATEMENT", "CASH_FLOW"):
            row = FinancialFlow.query.filter_by(financial_period_id=period.id, flow_type=flow_type).order_by(FinancialFlow.id.desc()).first()
            if row:
                flows[flow_type] = row.payload
    return render_template("financial_flows.html", periods=periods, selected_year=requested, flows=flows, **ctx)


@bp.get("/company/<ticker>/validate")
@role_required("CONTROL")
def validate_company(ticker):
    require_control_view(); ctx = _ctx(ticker)
    runs = HistoricalTestRun.query.filter_by(coverage_id=ctx["coverage"].id).order_by(HistoricalTestRun.created_at.desc()).limit(20).all()
    latest = runs[0] if runs else None
    samples = HistoricalTestSample.query.filter_by(run_id=latest.id).order_by(HistoricalTestSample.anchor_date.desc()).all() if latest else []
    return render_template("validate_company.html", runs=runs, latest_run=latest, samples=samples, **ctx)


@bp.post("/company/<ticker>/validate/run")
@role_required("CONTROL")
def queue_validate_company(ticker):
    require_control_view(); ctx = _ctx(ticker); lookback = max(3, min(int(n(request.form.get("lookback_years")) or 15), 40))
    job = enqueue_job("HISTORICAL_TEST", user_id=g.user.id, company_id=ctx["company"].id, security_id=ctx["security"].id,
                      payload={"coverage_id": ctx["coverage"].id, "lookback_years": lookback}, priority=55)
    audit("historical_test.reuse" if getattr(job, "_mf_reused", False) else "historical_test.enqueue", "job", job.id, {"ticker": ctx["security"].ticker, "lookback_years": lookback})
    db.session.commit()
    flash(f"Historical walk-forward {'already queued' if getattr(job, '_mf_reused', False) else 'queued'} as job #{job.id}.", "success")
    return redirect(url_for("web.validate_company", ticker=ticker.upper()))
