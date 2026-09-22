from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import flash, g, redirect, render_template, request, url_for

from .access import audit, require_control_view
from .autofill import _financial_history, _point_in_time_calibration, prefill_coverage
from .core_models import FinancialFlow, FinancialPeriod, HistoricalPrice, HistoricalTestRun, HistoricalTestSample, Job, ValuationScenario
from .extensions import db
from .jobs import enqueue_job
from .historical_data import preferred_provider
from .routes import SECTIONS, _ctx, bp
from .security import role_required
from .research_synthesis import valuation_price_history
from .valuation_engine import ENGINE_VERSION as VALUATION_ENGINE_VERSION, default_cases, evaluate, infer_company_type, metrics_from_history, n
from .validation_policy import state_for_run


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



def _attach_valuation_market_context(metrics: dict, calibration: dict, current_price: float | None) -> None:
    metrics["current_price"] = current_price
    anchor_price = n(calibration.get("latest_filing_anchor_price"))
    if current_price not in (None, 0) and anchor_price not in (None, 0):
        metrics["market_move_since_filing_pct"] = (current_price / anchor_price - 1.0) * 100.0
        metrics["latest_filing_anchor_price"] = anchor_price
        metrics["latest_filing_anchor_date"] = calibration.get("latest_filing_anchor_date")
    equity = n(metrics.get("equity"))
    shares = n(metrics.get("shares"))
    pb_history = calibration.get("p_b") or (None, None, None)
    historical_pb_median = n(pb_history[1]) if len(pb_history) >= 2 else None
    if current_price not in (None, 0) and equity not in (None, 0) and equity > 0 and shares not in (None, 0):
        current_pb = current_price * shares / equity
        metrics["current_p_b"] = current_pb
        if historical_pb_median not in (None, 0):
            metrics["pb_deviation_from_history_pct"] = (current_pb / historical_pb_median - 1.0) * 100.0


def _price_history_context(ctx: dict) -> tuple[list[dict], dict]:
    # Valuation plots the recent 2Y window, but the core cache must retain a 10Y
    # history for calibration, validation, portfolio correlation and audit.
    history = valuation_price_history(ctx["security"].id, 730)
    today = date.today()
    chart_cutoff = today - timedelta(days=730)
    chart_first = date.fromisoformat(history[0]["date"]) if history else None
    chart_last = date.fromisoformat(history[-1]["date"]) if history else None
    chart_complete = bool(
        len(history) >= 100
        and chart_first is not None
        and chart_first <= chart_cutoff + timedelta(days=75)
        and chart_last is not None
        and chart_last >= today - timedelta(days=10)
    )

    provider = preferred_provider(ctx["security"].id)
    full_query = HistoricalPrice.query.filter_by(security_id=ctx["security"].id)
    if provider:
        full_query = full_query.filter(HistoricalPrice.provider == provider)
    full_rows = full_query.count()
    full_first = db.session.query(db.func.min(HistoricalPrice.trade_date)).filter(
        HistoricalPrice.security_id == ctx["security"].id,
        *([HistoricalPrice.provider == provider] if provider else []),
    ).scalar()
    full_last = db.session.query(db.func.max(HistoricalPrice.trade_date)).filter(
        HistoricalPrice.security_id == ctx["security"].id,
        *([HistoricalPrice.provider == provider] if provider else []),
    ).scalar()
    target_start = today - timedelta(days=366 * 10)
    core_history_complete = bool(
        full_rows >= 1000
        and full_first is not None
        and full_first <= target_start + timedelta(days=120)
        and full_last is not None
        and full_last >= today - timedelta(days=10)
    )
    needs_refresh = not (chart_complete and core_history_complete)

    active = Job.query.filter(
        Job.user_id == g.user.id,
        Job.security_id == ctx["security"].id,
        Job.job_type == "PRICE_HISTORY_REFRESH",
        Job.status.in_(["QUEUED", "RUNNING"]),
    ).order_by(Job.id.desc()).first()
    latest_terminal = Job.query.filter(
        Job.user_id == g.user.id,
        Job.security_id == ctx["security"].id,
        Job.job_type == "PRICE_HISTORY_REFRESH",
        Job.status.in_(["DONE", "FAILED", "CANCELLED"]),
        Job.finished_at.is_not(None),
    ).order_by(Job.finished_at.desc(), Job.id.desc()).first()

    job = active
    cooldown = False
    if needs_refresh and job is None and latest_terminal and latest_terminal.finished_at:
        elapsed = max(
            0.0,
            (datetime.now(timezone.utc).replace(tzinfo=None) - latest_terminal.finished_at).total_seconds(),
        )
        # A successful wide backfill may legitimately be shorter than 10Y for a
        # newer issuer/provider. Do not thrash the queue: retry weekly, not every
        # page load. Failed jobs can retry after a short diagnostic cooldown.
        window = 15 * 60 if latest_terminal.status in {"FAILED", "CANCELLED"} else 7 * 24 * 60 * 60
        cooldown = elapsed < window
        if cooldown:
            job = latest_terminal

    if needs_refresh and active is None and not cooldown:
        job = enqueue_job(
            "PRICE_HISTORY_REFRESH",
            user_id=g.user.id,
            company_id=ctx["company"].id,
            security_id=ctx["security"].id,
            payload={"coverage_id": ctx["coverage"].id, "lookback_years": 10},
            priority=35,
        )

    status = {
        "rows": len(history),
        "first_date": chart_first.isoformat() if chart_first else None,
        "last_date": chart_last.isoformat() if chart_last else None,
        "provider": history[-1].get("provider") if history else provider,
        "needs_refresh": needs_refresh,
        "chart_complete": chart_complete,
        "history_target_years": 10,
        "history_complete": core_history_complete,
        "stored_rows": full_rows,
        "cache_first_date": full_first.isoformat() if full_first else None,
        "cache_last_date": full_last.isoformat() if full_last else None,
        "cache_provider": provider,
        "job_id": job.id if job else None,
        "job_status": job.status if job else None,
        "job_error": (job.error_message or "")[:500] if job else "",
        "cooldown": cooldown,
    }
    return history, status



def _current_model_context(ctx: dict) -> dict:
    model = ctx["model"]
    saved = dict(model.assumptions or {})
    history = _financial_history(ctx["company"].id)
    company_type = str(saved.get("company_type") or infer_company_type(ctx["company"].sector, ctx["company"].industry))
    metrics = metrics_from_history(history, saved.get("current_shares"), str(saved.get("share_source") or ""), company_type)
    calibration = _point_in_time_calibration(ctx["security"].id, history, company_type)
    current_price, reference_price_source, reference_price_stale = _reference_price(ctx)
    _attach_valuation_market_context(metrics, calibration, current_price)
    defaults = default_cases(metrics, company_type, calibration)

    same_engine = str(saved.get("engine_version") or "") == VALUATION_ENGINE_VERSION
    weights = dict(saved.get("weights") or {}) if same_engine else {}
    weights = weights or dict(defaults["weights"])
    for method, default_weight in defaults["weights"].items():
        weights.setdefault(method, default_weight)
    years = int(saved.get("horizon_years") or defaults["horizon_years"])
    scenario_rows = {row.name.upper(): row for row in model.scenarios}
    cases = {}
    fallback_values = {}

    for name in ("BEAR", "BASE", "BULL"):
        row = scenario_rows.get(name)
        inputs = dict((row.inputs or {}) if row else {})
        if bool(inputs.get("auto_prefill")) and not same_engine:
            # Old auto-generated assumptions are invalid under the integrity
            # engine. Manual analyst assumptions survive the engine upgrade.
            inputs = {}
        fallback = defaults[name]
        fallback_values[name] = n(row.equity_value_per_share) if row else None
        cases[name] = {
            "growth": n(inputs.get("growth")) if n(inputs.get("growth")) is not None else fallback["growth"],
            "net_margin": n(inputs.get("net_margin")) if n(inputs.get("net_margin")) is not None else fallback["net_margin"],
            "fcf_margin": n(inputs.get("fcf_margin")) if n(inputs.get("fcf_margin")) is not None else fallback["fcf_margin"],
            "ebitda_margin": n(inputs.get("ebitda_margin")) if n(inputs.get("ebitda_margin")) is not None else fallback["ebitda_margin"],
            "share_growth": n(inputs.get("share_growth")) if n(inputs.get("share_growth")) is not None else fallback["share_growth"],
            "pe": n(inputs.get("pe")) if n(inputs.get("pe")) is not None else fallback["pe"],
            "p_sales": n(inputs.get("p_sales")) if n(inputs.get("p_sales")) is not None else fallback["p_sales"],
            "ev_sales": n(inputs.get("ev_sales")) if n(inputs.get("ev_sales")) is not None else fallback["ev_sales"],
            "ev_ebitda": n(inputs.get("ev_ebitda")) if n(inputs.get("ev_ebitda")) is not None else fallback["ev_ebitda"],
            "target_fcf_yield": n(inputs.get("target_fcf_yield")) if n(inputs.get("target_fcf_yield")) is not None else fallback["target_fcf_yield"],
            "equity_discount_rate": n(inputs.get("equity_discount_rate")) if n(inputs.get("equity_discount_rate")) is not None else fallback["equity_discount_rate"],
            "terminal_growth": n(inputs.get("terminal_growth")) if n(inputs.get("terminal_growth")) is not None else fallback["terminal_growth"],
            "probability": n(row.probability) if row and row.probability is not None and not (bool((row.inputs or {}).get("auto_prefill")) and not same_engine) else fallback["probability"],
            "manual_override": n(inputs.get("manual_override")),
            "method_exclusions": fallback.get("method_exclusions") or [],
            "life_cycle": fallback.get("life_cycle"),
            "solvency_state": fallback.get("solvency_state"),
            "integrity_notes": fallback.get("integrity_notes") or [],
            "scenario_multiplier": fallback.get("scenario_multiplier"),
            "liquidation_floor": fallback.get("liquidation_floor"),
        }

    price_history, price_history_status = _price_history_context(ctx)
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
        "price_history": price_history,
        "price_history_status": price_history_status,
    }


@bp.get("/company/<ticker>/valuation")
@role_required("CONTROL")
def valuation_company(ticker):
    require_control_view(); ctx = _ctx(ticker); extra = _current_model_context(ctx)
    return render_template("valuation.html", **ctx, **extra)


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
        "p_sales": max(0.0, n(request.form.get("weight_p_sales")) or 0.0),
        "ev_sales": max(0.0, n(request.form.get("weight_ev_sales")) or 0.0),
        "ev_ebitda": max(0.0, n(request.form.get("weight_ev_ebitda")) or 0.0),
        "fcf_yield": max(0.0, n(request.form.get("weight_fcf_yield")) or 0.0),
        "dcf": max(0.0, n(request.form.get("weight_dcf")) or 0.0),
    }
    years = max(1, min(int(n(request.form.get("horizon_years")) or 5), 20))
    history = _financial_history(ctx["company"].id)
    metrics = metrics_from_history(history, current_shares, share_source, company_type)
    if current_shares in (None, 0) and metrics.get("shares") is not None:
        current_shares = metrics["shares"]
        share_source = metrics.get("share_source") or share_source
    calibration = _point_in_time_calibration(ctx["security"].id, history, company_type)
    current_price, _, _ = _reference_price(ctx)
    _attach_valuation_market_context(metrics, calibration, current_price)
    defaults = default_cases(metrics, company_type, calibration)
    cases = {}
    for name in ("BEAR", "BASE", "BULL"):
        key = name.lower(); fallback = defaults[name]
        cases[name] = {
            "growth": _fraction(request.form.get(f"{key}_growth"), fallback["growth"]),
            "net_margin": _fraction(request.form.get(f"{key}_net_margin"), fallback["net_margin"]),
            "fcf_margin": _fraction(request.form.get(f"{key}_fcf_margin"), fallback["fcf_margin"]),
            "ebitda_margin": _fraction(request.form.get(f"{key}_ebitda_margin"), fallback["ebitda_margin"]),
            "share_growth": _fraction(request.form.get(f"{key}_share_growth"), fallback["share_growth"]),
            "pe": n(request.form.get(f"{key}_pe")) if n(request.form.get(f"{key}_pe")) is not None else fallback["pe"],
            "p_sales": n(request.form.get(f"{key}_p_sales")) if n(request.form.get(f"{key}_p_sales")) is not None else fallback["p_sales"],
            "ev_sales": n(request.form.get(f"{key}_ev_sales")) if n(request.form.get(f"{key}_ev_sales")) is not None else fallback["ev_sales"],
            "ev_ebitda": n(request.form.get(f"{key}_ev_ebitda")) if n(request.form.get(f"{key}_ev_ebitda")) is not None else fallback["ev_ebitda"],
            "target_fcf_yield": _fraction(request.form.get(f"{key}_target_fcf_yield"), fallback["target_fcf_yield"]),
            "equity_discount_rate": _fraction(request.form.get(f"{key}_equity_discount_rate"), fallback["equity_discount_rate"]),
            "terminal_growth": _fraction(request.form.get(f"{key}_terminal_growth"), fallback["terminal_growth"]),
            "probability": _fraction(request.form.get(f"{key}_probability"), fallback["probability"]),
            "manual_override": n(request.form.get(f"{key}_manual_override")),
            "method_exclusions": fallback.get("method_exclusions") or [],
            "life_cycle": fallback.get("life_cycle"),
            "solvency_state": fallback.get("solvency_state"),
            "integrity_notes": fallback.get("integrity_notes") or [],
            "scenario_multiplier": fallback.get("scenario_multiplier"),
            "liquidation_floor": fallback.get("liquidation_floor"),
        }
    rows = {row.name.upper(): row for row in model.scenarios}
    fallback_values = {name: n(rows[name].equity_value_per_share) if name in rows else None for name in ("BEAR", "BASE", "BULL")}
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
    model.calculation_version = VALUATION_ENGINE_VERSION
    model.assumptions = saved | {
        "engine_version": VALUATION_ENGINE_VERSION, "company_type": company_type, "current_shares": current_shares,
        "share_source": share_source, "share_basis_verified": share_verified, "share_basis_note": share_note,
        "weights": weights, "horizon_years": years, "calibration": calibration, "latest_engine_result": result,
        "notes": str(request.form.get("assumptions_notes") or saved.get("notes") or "").strip(),
    }
    model.updated_by = g.user.id
    audit("valuation.model.save", "coverage", ctx["coverage"].id, {"method": model.method, "share_basis_verified": share_verified, "quality": result.get("quality")})
    db.session.commit()
    enqueue_job(
        "RECALCULATE",
        user_id=g.user.id,
        company_id=ctx["company"].id,
        security_id=ctx["security"].id,
        payload={"coverage_id": ctx["coverage"].id},
        priority=95,
    )
    flash("Valuation assumptions saved and recalculated. Research cache queued for update.", "success")
    return redirect(url_for("web.valuation_company", ticker=ticker.upper()))


@bp.post("/company/<ticker>/valuation/reset")
@role_required("CONTROL")
def reset_valuation_company(ticker):
    require_control_view(); ctx = _ctx(ticker); result = prefill_coverage(ctx["coverage"].id, g.user.id, force=True)
    audit("valuation.model.reset", "coverage", ctx["coverage"].id, {"engine": VALUATION_ENGINE_VERSION}); db.session.commit()
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
    validation_states = {run.id: state_for_run(run) for run in runs}
    return render_template(
        "validate.html",
        runs=runs,
        latest_run=latest,
        latest_validation_state=state_for_run(latest),
        latest_validation_engine_current=(
            bool(latest) and str(latest.engine_version or "") == VALUATION_ENGINE_VERSION
        ),
        current_validation_engine_version=VALUATION_ENGINE_VERSION,
        validation_states=validation_states,
        samples=samples,
        **ctx,
    )


@bp.post("/company/<ticker>/validate/run")
@role_required("CONTROL")
def queue_validate_company(ticker):
    require_control_view(); ctx = _ctx(ticker)
    if not ctx["readiness"].get("ready_to_validate"):
        pending = [gate["label"] for gate in ctx["readiness"].get("gates", []) if not gate.get("approved")]
        audit("validation.blocked_readiness", "coverage", ctx["coverage"].id, {"ticker": ctx["security"].ticker, "pending": pending})
        db.session.commit()
        flash("Research is not ready to validate. Review/approve: " + ", ".join(pending[:6]) + ("…" if len(pending) > 6 else ""), "error")
        return redirect(url_for("web.company_section", ticker=ticker.upper(), section="overview"))
    lookback = max(3, min(int(n(request.form.get("lookback_years")) or 15), 40))
    job = enqueue_job("HISTORICAL_TEST", user_id=g.user.id, company_id=ctx["company"].id, security_id=ctx["security"].id,
                      payload={"coverage_id": ctx["coverage"].id, "lookback_years": lookback}, priority=55)
    audit("historical_test.reuse" if getattr(job, "_mf_reused", False) else "historical_test.enqueue", "job", job.id, {"ticker": ctx["security"].ticker, "lookback_years": lookback})
    db.session.commit()
    flash(f"Historical walk-forward {'already queued' if getattr(job, '_mf_reused', False) else 'queued'} as job #{job.id}.", "success")
    return redirect(url_for("web.validate_company", ticker=ticker.upper()))
