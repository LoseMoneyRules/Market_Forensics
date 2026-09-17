from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from .core_models import Company, Coverage, FinancialPeriod, NormalizedFinancial, ResearchState, Security, ValuationModel, ValuationScenario
from .data_providers import latest_snapshot
from .extensions import db
from .formatting import format_number

AUTO_MARKER = "[AUTO 0.1.2]"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _median_growth(values: list[float | None]) -> float | None:
    changes = []
    for previous, current in zip(values, values[1:]):
        change = _growth(current, previous)
        if change is not None and -200 < change < 300:
            changes.append(change)
    return median(changes[-3:]) if changes else None


def _replaceable(text: str | None) -> bool:
    value = str(text or "").strip()
    return not value or value.startswith(AUTO_MARKER)


def _financial_history(company_id: int) -> list[tuple[FinancialPeriod, NormalizedFinancial]]:
    rows = []
    periods = FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").order_by(FinancialPeriod.fiscal_year.asc()).all()
    for period in periods:
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if normalized is not None:
            rows.append((period, normalized))
    return rows


def _per_share(normalized: NormalizedFinancial, field: str) -> float | None:
    shares = _n(normalized.diluted_shares) or _n(normalized.shares_outstanding)
    value = _n(getattr(normalized, field, None))
    if shares in (None, 0) or value is None:
        return None
    return value / shares


def _margin(value: Any, revenue: Any) -> float | None:
    numerator, denominator = _n(value), _n(revenue)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100.0


def _scenario_values(history: list[tuple[FinancialPeriod, NormalizedFinancial]], current_price: float | None) -> tuple[dict[str, float], dict[str, Any]]:
    latest = history[-1][1] if history else None
    if latest is None:
        if current_price and current_price > 0:
            return {
                "BEAR": round(current_price * 0.75, 2),
                "BASE": round(current_price, 2),
                "BULL": round(current_price * 1.25, 2),
            }, {"source": "MARKET_ANCHORED_FALLBACK", "confidence": "LOW", "reason": "No normalized FY fundamentals available."}
        return {}, {"source": "UNAVAILABLE", "confidence": "UNRATED", "reason": "No fundamentals or market price available."}

    eps_series = [_per_share(n, "net_income") for _, n in history]
    fcfps_series = [_per_share(n, "fcf") for _, n in history]
    eps = eps_series[-1] if eps_series else None
    fcfps = fcfps_series[-1] if fcfps_series else None
    eps_growth = _median_growth(eps_series)
    fcf_growth = _median_growth(fcfps_series)
    revenue_growth = _median_growth([_n(n.revenue) for _, n in history])

    operating_margin = _margin(latest.operating_income, latest.revenue)
    fcf_margin = _margin(latest.fcf, latest.revenue)
    net_debt = (_n(latest.debt) or 0.0) - (_n(latest.cash) or 0.0)

    growth_signal_values = [x for x in (eps_growth, fcf_growth, revenue_growth) if x is not None]
    growth_signal = median(growth_signal_values) if growth_signal_values else 0.0
    growth_signal = _clamp(growth_signal, -15.0, 25.0)

    quality_bonus = 0.0
    if operating_margin is not None:
        quality_bonus += 2.0 if operating_margin >= 15 else (1.0 if operating_margin >= 8 else 0.0)
    if fcf_margin is not None:
        quality_bonus += 2.0 if fcf_margin >= 12 else (1.0 if fcf_margin >= 5 else 0.0)
    if net_debt < 0:
        quality_bonus += 1.0

    base_pe = _clamp(14.0 + 0.35 * growth_signal + quality_bonus, 8.0, 30.0)
    base_fcf_multiple = _clamp(12.0 + 0.28 * growth_signal + quality_bonus, 7.0, 26.0)
    projection_growth = _clamp(growth_signal / 100.0, -0.15, 0.20)

    candidates: dict[str, list[float]] = {"BEAR": [], "BASE": [], "BULL": []}
    if eps is not None and eps > 0:
        projected = eps * (1.0 + projection_growth)
        candidates["BEAR"].append(max(0.0, eps * 0.90) * max(6.0, base_pe * 0.70))
        candidates["BASE"].append(max(0.0, projected) * base_pe)
        candidates["BULL"].append(max(0.0, eps * (1.0 + max(projection_growth, 0.08))) * min(40.0, base_pe * 1.30))
    if fcfps is not None and fcfps > 0:
        projected = fcfps * (1.0 + projection_growth)
        candidates["BEAR"].append(max(0.0, fcfps * 0.85) * max(5.0, base_fcf_multiple * 0.70))
        candidates["BASE"].append(max(0.0, projected) * base_fcf_multiple)
        candidates["BULL"].append(max(0.0, fcfps * (1.0 + max(projection_growth, 0.08))) * min(34.0, base_fcf_multiple * 1.30))

    shares = _n(latest.diluted_shares) or _n(latest.shares_outstanding)
    book_per_share = (_n(latest.equity) / shares) if shares not in (None, 0) and _n(latest.equity) is not None else None
    if not candidates["BASE"] and book_per_share is not None and book_per_share > 0:
        candidates["BEAR"].append(book_per_share * 0.8)
        candidates["BASE"].append(book_per_share * 1.2)
        candidates["BULL"].append(book_per_share * 1.8)

    if not candidates["BASE"] and current_price and current_price > 0:
        candidates["BEAR"].append(current_price * 0.75)
        candidates["BASE"].append(current_price)
        candidates["BULL"].append(current_price * 1.25)
        source = "MARKET_ANCHORED_FALLBACK"
        confidence = "LOW"
    else:
        source = "FUNDAMENTAL_DRAFT"
        confidence = "MEDIUM" if len(candidates["BASE"]) >= 2 else "LOW"

    values = {name: round(float(median(rows)), 2) for name, rows in candidates.items() if rows}
    if all(name in values for name in ("BEAR", "BASE", "BULL")):
        ordered = sorted([values["BEAR"], values["BASE"], values["BULL"]])
        values = {"BEAR": ordered[0], "BASE": ordered[1], "BULL": ordered[2]}

    meta = {
        "source": source,
        "confidence": confidence,
        "latest_fiscal_year": history[-1][0].fiscal_year if history else None,
        "eps": eps,
        "fcf_per_share": fcfps,
        "median_eps_growth_pct": eps_growth,
        "median_fcf_growth_pct": fcf_growth,
        "median_revenue_growth_pct": revenue_growth,
        "growth_signal_pct": growth_signal,
        "operating_margin_pct": operating_margin,
        "fcf_margin_pct": fcf_margin,
        "net_debt": net_debt,
        "base_pe": round(base_pe, 2),
        "base_fcf_multiple": round(base_fcf_multiple, 2),
        "current_price": current_price,
        "generated_at": utcnow().isoformat(),
    }
    return values, meta


def prefill_coverage(coverage_id: int, user_id: int) -> dict[str, Any]:
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
    market = latest_snapshot(security.id)
    current_price = _n(market.price) if market else None
    values, meta = _scenario_values(history, current_price)
    scenarios = {row.name.upper(): row for row in model.scenarios}
    changed_scenarios = []
    for name, probability in (("BEAR", 0.25), ("BASE", 0.50), ("BULL", 0.25)):
        row = scenarios.get(name)
        if row is None:
            row = ValuationScenario(model_id=model.id, name=name, probability=probability)
            db.session.add(row)
            scenarios[name] = row
        auto_owned = bool((row.inputs or {}).get("auto_prefill"))
        if name in values and (row.equity_value_per_share is None or auto_owned):
            row.equity_value_per_share = values[name]
            row.probability = probability
            row.confidence = meta.get("confidence") or "LOW"
            row.inputs = {
                "auto_prefill": True,
                "source": meta.get("source"),
                "generated_at": meta.get("generated_at"),
                "latest_fiscal_year": meta.get("latest_fiscal_year"),
                "current_price": current_price,
                "base_pe": meta.get("base_pe"),
                "base_fcf_multiple": meta.get("base_fcf_multiple"),
                "growth_signal_pct": meta.get("growth_signal_pct"),
            }
            row.outputs = {"value_per_share": values[name]}
            row.calculated_at = utcnow()
            changed_scenarios.append(name)

    if changed_scenarios and model.method in {"MANUAL_PER_SHARE", "AUTO_FUNDAMENTAL_DRAFT", "AUTO_MARKET_ANCHORED_DRAFT"}:
        model.method = "AUTO_FUNDAMENTAL_DRAFT" if meta.get("source") == "FUNDAMENTAL_DRAFT" else "AUTO_MARKET_ANCHORED_DRAFT"
    model.assumptions = dict(model.assumptions or {}) | {"auto_draft": meta}
    model.calculation_version = "0.1.2"
    model.updated_by = user_id

    text_updates = []
    if history:
        period, latest = history[-1]
        previous = history[-2][1] if len(history) > 1 else None
        revenue = _n(latest.revenue)
        revenue_growth = _growth(revenue, _n(previous.revenue) if previous else None)
        gross_margin = _margin(latest.gross_profit, latest.revenue)
        operating_margin = _margin(latest.operating_income, latest.revenue)
        net_margin = _margin(latest.net_income, latest.revenue)
        fcf_margin = _margin(latest.fcf, latest.revenue)
        inventory_growth = _growth(_n(latest.inventory), _n(previous.inventory) if previous else None)
        lines = [f"{AUTO_MARKER} FY{period.fiscal_year} filing-derived starting point."]
        if revenue is not None:
            lines.append(f"Revenue {format_number(revenue, 'AUTO')}{f' ({revenue_growth:+.1f}% YoY)' if revenue_growth is not None else ''}.")
        margin_bits = []
        for label, value in (("gross", gross_margin), ("operating", operating_margin), ("net", net_margin), ("FCF", fcf_margin)):
            if value is not None:
                margin_bits.append(f"{label} {value:.1f}%")
        if margin_bits:
            lines.append("Margins: " + ", ".join(margin_bits) + ".")
        if latest.inventory is not None:
            lines.append(f"Inventory {format_number(latest.inventory, 'AUTO')}{f' ({inventory_growth:+.1f}% YoY)' if inventory_growth is not None else ''}.")
        if latest.receivables is not None or latest.payables is not None:
            lines.append(f"Receivables {format_number(latest.receivables, 'AUTO')} · Payables {format_number(latest.payables, 'AUTO')}.")
        numbers_text = "\n".join(lines)
        if _replaceable(research.numbers):
            research.numbers = numbers_text
            text_updates.append("numbers")

        flow_lines = [f"{AUTO_MARKER} FY{period.fiscal_year} cash-flow starting point."]
        for label, value in (("CFO", latest.cfo), ("Capex", latest.capex), ("FCF", latest.fcf), ("Buybacks", latest.buybacks), ("Dividends", latest.dividends)):
            if value is not None:
                flow_lines.append(f"{label}: {format_number(value, 'AUTO')}.")
        if _replaceable(research.flows_summary):
            research.flows_summary = "\n".join(flow_lines)
            text_updates.append("financial-flows")

    if _replaceable(research.valuation_notes) and values:
        research.valuation_notes = (
            f"{AUTO_MARKER} Editable valuation draft generated only from stored market/fundamental evidence. "
            f"Method={meta.get('source')}; base P/E={meta.get('base_pe')}; base P/FCF={meta.get('base_fcf_multiple')}; "
            f"growth signal={meta.get('growth_signal_pct'):.1f}%" if meta.get("growth_signal_pct") is not None else
            f"{AUTO_MARKER} Editable valuation draft generated from available stored evidence. Method={meta.get('source')}."
        )
        text_updates.append("valuation")

    if _replaceable(research.business) and (company.sector or company.industry):
        research.business = f"{AUTO_MARKER} Company classification: sector {company.sector or '—'}; industry {company.industry or '—'}. Expand with business-model evidence before marking this section ready."
        text_updates.append("business")

    research.updated_by = user_id
    db.session.commit()
    return {
        "coverage_id": coverage.id,
        "ticker": security.ticker,
        "scenario_values": values,
        "scenario_updates": changed_scenarios,
        "text_updates": text_updates,
        "meta": meta,
    }


__all__ = ["AUTO_MARKER", "prefill_coverage"]
