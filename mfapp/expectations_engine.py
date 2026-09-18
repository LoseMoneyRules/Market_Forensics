from __future__ import annotations

from math import isfinite
from typing import Any

from .core_models import ValuationModel
from .current_financials import current_row


HORIZON_YEARS = 5


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return value if isfinite(value) else None


def _ratio(value: Any) -> float | None:
    value = _n(value)
    if value is None:
        return None
    return value / 100.0 if abs(value) > 1 else value


def price_implied_expectations(
    company_id: int,
    model: ValuationModel | None,
    market_price: Any,
    *,
    horizon_years: int = HORIZON_YEARS,
) -> dict[str, Any]:
    """Reverse-engineer three intuitive assumptions from the observed share price.

    The inversion is deliberately transparent and single-variable:
    - implied revenue CAGR, holding Base Y5 net margin and exit P/E constant;
    - implied Y5 net margin, holding Base growth and exit P/E constant;
    - implied Y5 exit P/E, holding Base growth and net margin constant.

    It is a diagnostic of what the price would require under the Base earnings
    framework, not a claim that the market literally uses this model.
    """
    current = current_row(company_id) or {}
    price = _n(market_price)
    revenue0 = _n(current.get("revenue"))
    shares = _n(current.get("diluted_shares")) or _n(current.get("shares_outstanding"))
    if shares in (None, 0):
        shares = _n((current.get("metrics") or {}).get("shares"))

    scenario = None
    if model:
        scenario = next((row for row in model.scenarios if str(row.name).upper() == "BASE"), None)
    inputs = dict((scenario.inputs or {}) if scenario else {})
    base_growth = _ratio(inputs.get("growth"))
    base_margin = _ratio(inputs.get("net_margin"))
    base_pe = _n(inputs.get("pe"))

    metrics = current.get("metrics") or {}
    if base_growth is None:
        base_growth = _ratio(metrics.get("revenue_growth_pct"))
    if base_margin is None:
        base_margin = _ratio(metrics.get("net_margin_pct"))

    years = max(1, min(int(horizon_years or HORIZON_YEARS), 10))
    errors: list[str] = []
    if price is None or price <= 0:
        errors.append("Current market price is unavailable.")
    if revenue0 is None or revenue0 <= 0:
        errors.append("Current revenue is unavailable.")
    if shares is None or shares <= 0:
        errors.append("Current share denominator is unavailable.")
    if base_growth is None:
        errors.append("Base revenue growth is unavailable.")
    if base_margin is None or base_margin <= 0:
        errors.append("Base net margin must be positive for earnings-based inversion.")
    if base_pe is None or base_pe <= 0:
        errors.append("Base exit P/E must be positive for earnings-based inversion.")

    if errors:
        return {
            "available": False,
            "errors": errors,
            "horizon_years": years,
            "classification": "UNAVAILABLE",
            "drivers": [],
            "demand_score": None,
        }

    target_equity = price * shares
    base_revenue_y5 = revenue0 * (1.0 + base_growth) ** years
    base_eps_value = base_revenue_y5 * base_margin * base_pe

    implied_growth = None
    growth_den = revenue0 * base_margin * base_pe
    if growth_den > 0 and target_equity > 0:
        implied_growth = (target_equity / growth_den) ** (1.0 / years) - 1.0

    implied_margin = target_equity / (base_revenue_y5 * base_pe) if base_revenue_y5 > 0 and base_pe > 0 else None
    implied_pe = target_equity / (base_revenue_y5 * base_margin) if base_revenue_y5 > 0 and base_margin > 0 else None

    drivers = [
        {
            "key": "revenue_cagr",
            "label": f"Revenue CAGR · {years}Y",
            "market_implied": implied_growth,
            "base": base_growth,
            "unit": "%",
            "difference": (implied_growth - base_growth) if implied_growth is not None else None,
        },
        {
            "key": "net_margin_y5",
            "label": f"Net margin · Y{years}",
            "market_implied": implied_margin,
            "base": base_margin,
            "unit": "%",
            "difference": (implied_margin - base_margin) if implied_margin is not None else None,
        },
        {
            "key": "exit_pe_y5",
            "label": f"Exit P/E · Y{years}",
            "market_implied": implied_pe,
            "base": base_pe,
            "unit": "x",
            "difference": (implied_pe - base_pe) if implied_pe is not None else None,
        },
    ]

    # Normalize demand relative to practical driver ranges. Positive means the
    # market requires more than our Base assumptions; negative means less.
    normalized: list[float] = []
    if implied_growth is not None:
        normalized.append((implied_growth - base_growth) / 0.04)
    if implied_margin is not None:
        normalized.append((implied_margin - base_margin) / 0.03)
    if implied_pe is not None and base_pe:
        normalized.append((implied_pe - base_pe) / max(3.0, abs(base_pe) * 0.20))
    demand_score = sum(normalized) / len(normalized) if normalized else 0.0

    if demand_score >= 0.75:
        classification = "DEMANDING"
    elif demand_score <= -0.75:
        classification = "FAVORABLE"
    else:
        classification = "BALANCED"

    for row in drivers:
        value = row["market_implied"]
        base = row["base"]
        if value is None or base is None:
            row["read"] = "UNAVAILABLE"
        else:
            delta = row["difference"] or 0
            threshold = 0.015 if row["unit"] == "%" else max(1.5, abs(base) * .10)
            row["read"] = "ABOVE BASE" if delta > threshold else "BELOW BASE" if delta < -threshold else "NEAR BASE"

    return {
        "available": True,
        "errors": [],
        "horizon_years": years,
        "classification": classification,
        "demand_score": round(demand_score, 3),
        "drivers": drivers,
        "market_equity_value": target_equity,
        "base_equity_value_from_pe": base_eps_value,
        "method": "single-variable earnings inversion",
        "note": "Price-implied diagnostics hold the other Base drivers constant; they are not consensus estimates.",
    }


__all__ = ["price_implied_expectations"]
