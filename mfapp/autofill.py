from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .core_models import Company, Coverage, FinancialPeriod, HistoricalPrice, NormalizedFinancial, ResearchState, Security, ValuationModel, ValuationScenario
from .data_providers import latest_snapshot
from .extensions import db
from .formatting import format_number
from .historical_data import preferred_provider, price_on_or_after
from .valuation_engine import ENGINE_VERSION, calibrate_multiples, default_cases, evaluate, infer_company_type, metrics_from_history, n

AUTO_MARKER = "[AUTO 0.1.3]"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _replaceable(text: str | None) -> bool:
    value = str(text or "").strip()
    return not value or value.startswith("[AUTO ")


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _margin(value: Any, revenue: Any) -> float | None:
    numerator, denominator = n(value), n(revenue)
    return numerator / denominator * 100.0 if numerator is not None and denominator not in (None, 0) else None


def _financial_history(company_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    periods = FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").order_by(FinancialPeriod.fiscal_year.asc()).all()
    for period in periods:
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if normalized is None:
            continue
        rows.append({
            "fiscal_year": period.fiscal_year,
            "filed_at": period.filed_at.isoformat() if period.filed_at else None,
            "period_end": period.end_date.isoformat() if period.end_date else None,
            "revenue": normalized.revenue, "gross_profit": normalized.gross_profit,
            "operating_income": normalized.operating_income, "net_income": normalized.net_income,
            "cfo": normalized.cfo, "capex": normalized.capex, "fcf": normalized.fcf,
            "cash": normalized.cash, "debt": normalized.debt,
            "inventory": normalized.inventory, "receivables": normalized.receivables,
            "payables": normalized.payables, "equity": normalized.equity,
            "shares_outstanding": normalized.shares_outstanding, "diluted_shares": normalized.diluted_shares,
        })
    return rows


def _point_in_time_calibration(security_id: int, history: list[dict[str, Any]], company_type: str) -> dict[str, Any]:
    provider = preferred_provider(security_id)
    observations: list[dict[str, Any]] = []
    if provider:
        for row in history[:-1]:
            filed = row.get("filed_at")
            try:
                filing_date = datetime.fromisoformat(str(filed)[:10]).date() if filed else None
            except Exception:
                filing_date = None
            if filing_date is None:
                continue
            market = price_on_or_after(security_id, filing_date, 14, provider=provider)
            shares = n(row.get("shares_outstanding")) or n(row.get("diluted_shares"))
            if not market or shares in (None, 0):
                continue
            observations.append({
                "price": n(market.close_raw), "shares": shares, "revenue": row.get("revenue"),
                "net_income": row.get("net_income"), "fcf": row.get("fcf"),
                "net_debt": (n(row.get("debt")) or 0.0) - (n(row.get("cash")) or 0.0),
            })
    return calibrate_multiples(observations, company_type)


def _case_from_row(row: ValuationScenario | None, fallback: dict[str, Any], force: bool) -> tuple[dict[str, Any], bool]:
    if row is None:
        return dict(fallback), True
    inputs = dict(row.inputs or {})
    auto_owned = bool(inputs.get("auto_prefill"))
    if force or auto_owned or row.equity_value_per_share is None:
        return dict(fallback), True
    return {
        "growth": n(inputs.get("growth")), "net_margin": n(inputs.get("net_margin")),
        "fcf_margin": n(inputs.get("fcf_margin")), "pe": n(inputs.get("pe")),
        "ev_sales": n(inputs.get("ev_sales")), "target_fcf_yield": n(inputs.get("target_fcf_yield")),
        "equity_discount_rate": n(inputs.get("equity_discount_rate")), "terminal_growth": n(inputs.get("terminal_growth")),
        "probability": n(row.probability), "manual_override": n(inputs.get("manual_override")),
    }, False


def _reference_price(security_id: int) -> float | None:
    market = latest_snapshot(security_id)
    if market and n(market.price) not in (None, 0):
        return n(market.price)
    row = HistoricalPrice.query.filter_by(security_id=security_id).order_by(HistoricalPrice.trade_date.desc(), HistoricalPrice.id.desc()).first()
    if row:
        return n(row.close_split_adjusted) or n(row.close_raw)
    return None


def prefill_coverage(coverage_id: int, user_id: int, force: bool = False) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if coverage is None:
        raise RuntimeError("Coverage not found")
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id) if security else None
    if security is None or company is None:
        raise RuntimeError("Security/company not found")
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    if research is None or model is None:
        raise RuntimeError("Coverage workspace incomplete")

    history = _financial_history(company.id)
    saved = dict(model.assumptions or {})
    company_type = str(saved.get("company_type") or infer_company_type(company.sector, company.industry))
    current_shares = saved.get("current_shares")
    share_source = str(saved.get("share_source") or "")
    metrics = metrics_from_history(history, current_shares, share_source)
    if current_shares in (None, "") and metrics.get("shares") is not None:
        current_shares = metrics["shares"]
        share_source = metrics.get("share_source") or share_source
    calibration = _point_in_time_calibration(security.id, history, company_type)
    defaults = default_cases(metrics, company_type, calibration)
    weights = dict(saved.get("weights") or defaults["weights"])
    horizon_years = int(saved.get("horizon_years") or defaults["horizon_years"])
    current_price = _reference_price(security.id)

    scenario_rows = {row.name.upper(): row for row in model.scenarios}
    case_inputs: dict[str, dict[str, Any]] = {}
    auto_flags: dict[str, bool] = {}
    fallback_values = {name: n(scenario_rows[name].equity_value_per_share) if name in scenario_rows else None for name in ("BEAR", "BASE", "BULL")}
    for name in ("BEAR", "BASE", "BULL"):
        case_inputs[name], auto_flags[name] = _case_from_row(scenario_rows.get(name), defaults[name], force)
    result = evaluate(metrics, case_inputs, weights, horizon_years, current_price=current_price, fallback_values=fallback_values)

    changed_scenarios: list[str] = []
    for name in ("BEAR", "BASE", "BULL"):
        row = scenario_rows.get(name)
        if row is None:
            row = ValuationScenario(model_id=model.id, name=name)
            db.session.add(row)
            scenario_rows[name] = row
            auto_flags[name] = True
        output = result["scenarios"][name]
        if auto_flags[name]:
            if output.get("fair_value") is not None:
                row.equity_value_per_share = output.get("fair_value")
            row.probability = case_inputs[name].get("probability") or 0
            row.confidence = "MEDIUM" if output.get("quality") == "INTRINSIC" and calibration.get("source") == "POINT_IN_TIME_CALIBRATION" else "LOW"
            row.inputs = {"auto_prefill": True, **case_inputs[name]}
            row.outputs = output
            row.calculated_at = utcnow()
            changed_scenarios.append(name)

    model.method = "MULTI_METHOD_INTRINSIC"
    model.assumptions = saved | {
        "engine_version": ENGINE_VERSION,
        "company_type": company_type,
        "current_shares": current_shares,
        "share_source": share_source or metrics.get("share_source"),
        "share_basis_verified": bool(saved.get("share_basis_verified", False)),
        "share_basis_note": str(saved.get("share_basis_note") or ""),
        "weights": weights,
        "horizon_years": horizon_years,
        "calibration": calibration,
        "auto_draft": {
            "source": calibration.get("source"), "sample_size": calibration.get("sample_size", 0),
            "latest_fiscal_year": metrics.get("fiscal_year"), "current_price": current_price,
            "current_price_role": "COMPARISON_ONLY_UNLESS_REQUIRED_AS_EXPLICIT_PROVISIONAL_FALLBACK",
            "generated_at": utcnow().isoformat(), "basis_usable": metrics.get("basis_usable"),
            "valuation_quality": result.get("quality"), "warnings": result.get("warnings") or [],
        },
        "latest_engine_result": result,
    }
    model.calculation_version = ENGINE_VERSION
    model.updated_by = user_id

    text_updates: list[str] = []
    if history:
        latest = history[-1]
        previous = history[-2] if len(history) > 1 else None
        revenue, revenue_prev = n(latest.get("revenue")), n((previous or {}).get("revenue"))
        revenue_growth = _growth(revenue, revenue_prev)
        gross_margin = _margin(latest.get("gross_profit"), latest.get("revenue"))
        operating_margin = _margin(latest.get("operating_income"), latest.get("revenue"))
        net_margin = _margin(latest.get("net_income"), latest.get("revenue"))
        fcf_margin = _margin(latest.get("fcf"), latest.get("revenue"))
        inventory_growth = _growth(n(latest.get("inventory")), n((previous or {}).get("inventory")))
        lines = [f"{AUTO_MARKER} FY{latest.get('fiscal_year')} filing-derived starting point."]
        if revenue is not None:
            lines.append(f"Revenue {format_number(revenue, 'AUTO')}{f' ({revenue_growth:+.1f}% YoY)' if revenue_growth is not None else ''}.")
        margin_bits = [f"{label} {value:.1f}%" for label, value in (("gross", gross_margin), ("operating", operating_margin), ("net", net_margin), ("FCF", fcf_margin)) if value is not None]
        if margin_bits:
            lines.append("Margins: " + ", ".join(margin_bits) + ".")
        if latest.get("inventory") is not None:
            lines.append(f"Inventory {format_number(latest.get('inventory'), 'AUTO')}{f' ({inventory_growth:+.1f}% YoY)' if inventory_growth is not None else ''}.")
        if latest.get("receivables") is not None or latest.get("payables") is not None:
            lines.append(f"Receivables {format_number(latest.get('receivables'), 'AUTO')} · Payables {format_number(latest.get('payables'), 'AUTO')}.")
        if _replaceable(research.numbers):
            research.numbers = "\n".join(lines); text_updates.append("numbers")

        flow_lines = [f"{AUTO_MARKER} FY{latest.get('fiscal_year')} cash-flow starting point."]
        for label, value in (("CFO", latest.get("cfo")), ("Capex", latest.get("capex")), ("FCF", latest.get("fcf"))):
            if value is not None:
                flow_lines.append(f"{label}: {format_number(value, 'AUTO')}.")
        if _replaceable(research.flows_summary):
            research.flows_summary = "\n".join(flow_lines); text_updates.append("financial-flows")

    if _replaceable(research.valuation_notes):
        if result.get("quality") == "INTRINSIC":
            research.valuation_notes = (
                f"{AUTO_MARKER} Multi-method intrinsic valuation using P/E, EV/Sales and FCF-yield robust blend; DCF is an independent cross-check. "
                f"Multiples source={calibration.get('source')}, historical sample={calibration.get('sample_size', 0)}. "
                "Current market price is excluded from fair-value construction and used only for upside/downside comparison."
            )
        else:
            research.valuation_notes = (
                f"{AUTO_MARKER} DATA WARNING: one or more valuation inputs are incomplete. Bear/Base/Bull stay visible using the last stored case or an explicit market-reference fallback. "
                "Fallback values are provisional and are not treated as intrinsic evidence."
            )
        text_updates.append("valuation")

    if _replaceable(research.business) and (company.sector or company.industry):
        research.business = f"{AUTO_MARKER} Company classification: sector {company.sector or '—'}; industry {company.industry or '—'}; valuation family {company_type}. Expand with business-model evidence before marking this section ready."
        text_updates.append("business")

    research.updated_by = user_id
    db.session.commit()
    return {
        "coverage_id": coverage.id, "ticker": security.ticker,
        "scenario_values": {name: result["scenarios"][name].get("fair_value") for name in ("BEAR", "BASE", "BULL")},
        "scenario_updates": changed_scenarios, "text_updates": text_updates,
        "meta": model.assumptions.get("auto_draft") or {},
    }


__all__ = ["AUTO_MARKER", "prefill_coverage"]
