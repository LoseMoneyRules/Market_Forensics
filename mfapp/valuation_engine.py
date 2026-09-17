from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any

ENGINE_VERSION = "0.1.3"

TYPE_PRIORS = {
    "Generic": {"pe": (12.0, 18.0, 24.0), "ev_sales": (0.8, 1.5, 2.4), "fcf_yield": (0.080, 0.055, 0.040)},
    "Consumer / Brand": {"pe": (15.0, 21.0, 27.0), "ev_sales": (0.9, 1.8, 3.0), "fcf_yield": (0.070, 0.050, 0.035)},
    "Industrial": {"pe": (13.0, 18.0, 24.0), "ev_sales": (0.7, 1.4, 2.2), "fcf_yield": (0.080, 0.060, 0.042)},
    "Software": {"pe": (20.0, 30.0, 42.0), "ev_sales": (3.0, 6.0, 9.0), "fcf_yield": (0.055, 0.038, 0.025)},
    "Semiconductor / AI": {"pe": (16.0, 24.0, 34.0), "ev_sales": (2.0, 4.5, 7.5), "fcf_yield": (0.065, 0.045, 0.030)},
    "Auto / EV": {"pe": (8.0, 14.0, 22.0), "ev_sales": (0.35, 0.80, 1.50), "fcf_yield": (0.110, 0.075, 0.045)},
    "Financial / REIT": {"pe": (8.0, 12.0, 16.0), "ev_sales": (0.7, 1.0, 1.4), "fcf_yield": (0.090, 0.070, 0.050)},
}


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def quantile(values: list[float], q: float) -> float | None:
    rows = sorted(x for x in (n(v) for v in values) if x is not None)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    pos = (len(rows) - 1) * q
    lo, hi = int(pos), min(len(rows) - 1, int(pos) + (0 if pos.is_integer() else 1))
    if lo == hi:
        return rows[lo]
    w = pos - lo
    return rows[lo] * (1 - w) + rows[hi] * w


def _median_growth(values: list[float | None]) -> float | None:
    changes: list[float] = []
    for previous, current in zip(values, values[1:]):
        if current is None or previous in (None, 0):
            continue
        change = current / previous - 1.0
        if -0.75 < change < 2.0:
            changes.append(change)
    return median(changes[-3:]) if changes else None


def _cagr(values: list[float | None], years: int = 3) -> float | None:
    usable = [(i, x) for i, x in enumerate(values) if x is not None and x > 0]
    if len(usable) < 2:
        return None
    end_i, end = usable[-1]
    start = next(((i, x) for i, x in reversed(usable[:-1]) if end_i - i >= min(years, end_i)), usable[0])
    start_i, start_value = start
    span = end_i - start_i
    if span <= 0 or start_value <= 0:
        return None
    return (end / start_value) ** (1.0 / span) - 1.0


def infer_company_type(sector: str = "", industry: str = "") -> str:
    text = f"{sector} {industry}".lower()
    if any(x in text for x in ("bank", "insurance", "financial", "reit", "real estate")):
        return "Financial / REIT"
    if any(x in text for x in ("semiconductor", "chip", "ai hardware")):
        return "Semiconductor / AI"
    if any(x in text for x in ("software", "cloud", "saas")):
        return "Software"
    if any(x in text for x in ("auto", "vehicle", "automotive", "ev ")):
        return "Auto / EV"
    if any(x in text for x in ("industrial", "machinery", "aerospace", "equipment")):
        return "Industrial"
    if any(x in text for x in ("apparel", "footwear", "consumer", "beverage", "restaurant", "retail", "brand")):
        return "Consumer / Brand"
    return "Generic"


def _empty_metrics(issue: str) -> dict[str, Any]:
    return {
        "fiscal_year": None,
        "filed_at": None,
        "revenue": None,
        "net_income": None,
        "fcf": None,
        "net_debt": None,
        "revenue_growth": None,
        "net_margin": None,
        "fcf_margin": None,
        "operating_margin": None,
        "shares": None,
        "share_source": "UNRESOLVED",
        "basis_usable": False,
        "basis_issue": issue,
        "data_warnings": [issue],
    }


def metrics_from_history(history: list[dict[str, Any]], shares_override: Any = None, share_source: str = "") -> dict[str, Any]:
    rows = [row for row in history if n(row.get("revenue")) is not None]
    if not rows:
        return _empty_metrics("No annual filing-derived fundamentals are available.")
    latest = rows[-1]
    revenue = n(latest.get("revenue"))
    net_income = n(latest.get("net_income"))
    fcf = n(latest.get("fcf"))
    cash = n(latest.get("cash")) or 0.0
    debt = n(latest.get("debt")) or 0.0
    revenues = [n(x.get("revenue")) for x in rows]
    net_margins = [
        (n(x.get("net_income")) / n(x.get("revenue")))
        if n(x.get("net_income")) is not None and n(x.get("revenue")) not in (None, 0)
        else None
        for x in rows
    ]
    fcf_margins = [
        (n(x.get("fcf")) / n(x.get("revenue")))
        if n(x.get("fcf")) is not None and n(x.get("revenue")) not in (None, 0)
        else None
        for x in rows
    ]
    operating_margins = [
        (n(x.get("operating_income")) / n(x.get("revenue")))
        if n(x.get("operating_income")) is not None and n(x.get("revenue")) not in (None, 0)
        else None
        for x in rows
    ]
    shares = n(shares_override)
    resolved_source = str(share_source or "").strip().upper()
    if shares in (None, 0):
        shares = n(latest.get("shares_outstanding"))
        resolved_source = "SEC_FY_OUTSTANDING" if shares not in (None, 0) else resolved_source
    if shares in (None, 0):
        shares = n(latest.get("diluted_shares"))
        resolved_source = "DILUTED_WA_FALLBACK" if shares not in (None, 0) else resolved_source

    warnings: list[str] = []
    if shares in (None, 0):
        warnings.append("No usable shares outstanding / diluted-share denominator is available.")
    if net_income is None:
        warnings.append("Net income is unavailable; P/E is excluded from the intrinsic blend.")
    if fcf is None:
        warnings.append("Free cash flow is unavailable; FCF-yield and DCF evidence are weaker.")
    return {
        "fiscal_year": latest.get("fiscal_year"),
        "filed_at": latest.get("filed_at"),
        "revenue": revenue,
        "net_income": net_income,
        "fcf": fcf,
        "net_debt": debt - cash,
        "revenue_growth": _cagr(revenues, 3) or _median_growth(revenues),
        "net_margin": median([x for x in net_margins[-3:] if x is not None]) if any(x is not None for x in net_margins[-3:]) else None,
        "fcf_margin": median([x for x in fcf_margins[-3:] if x is not None]) if any(x is not None for x in fcf_margins[-3:]) else None,
        "operating_margin": median([x for x in operating_margins[-3:] if x is not None]) if any(x is not None for x in operating_margins[-3:]) else None,
        "shares": shares,
        "share_source": resolved_source or "UNRESOLVED",
        "basis_usable": shares not in (None, 0),
        "basis_issue": "" if shares not in (None, 0) else warnings[0],
        "data_warnings": warnings,
    }


def calibrate_multiples(observations: list[dict[str, Any]], company_type: str) -> dict[str, Any]:
    """Calibrate only from point-in-time observations supplied by the caller."""
    pe_values: list[float] = []
    evs_values: list[float] = []
    fcf_yields: list[float] = []
    for row in observations:
        price, shares = n(row.get("price")), n(row.get("shares"))
        revenue = n(row.get("revenue"))
        ni = n(row.get("net_income"))
        fcf = n(row.get("fcf"))
        net_debt = n(row.get("net_debt")) or 0.0
        if price is None or shares in (None, 0) or price <= 0:
            continue
        market_cap = price * shares
        if ni is not None and ni > 0:
            pe = market_cap / ni
            if 3 <= pe <= 100:
                pe_values.append(pe)
        if revenue is not None and revenue > 0:
            evs = (market_cap + net_debt) / revenue
            if 0.05 <= evs <= 30:
                evs_values.append(evs)
        if fcf is not None and fcf > 0 and market_cap > 0:
            y = fcf / market_cap
            if 0.005 <= y <= 0.30:
                fcf_yields.append(y)
    prior = TYPE_PRIORS.get(company_type, TYPE_PRIORS["Generic"])
    enough = max(len(pe_values), len(evs_values), len(fcf_yields)) >= 3
    if not enough:
        return {"source": "TYPE_PRIOR", "sample_size": max(len(pe_values), len(evs_values), len(fcf_yields)), **prior}
    pe = (
        quantile(pe_values, .25) or prior["pe"][0],
        median(pe_values) if pe_values else prior["pe"][1],
        quantile(pe_values, .75) or prior["pe"][2],
    )
    evs = (
        quantile(evs_values, .25) or prior["ev_sales"][0],
        median(evs_values) if evs_values else prior["ev_sales"][1],
        quantile(evs_values, .75) or prior["ev_sales"][2],
    )
    fy = (
        quantile(fcf_yields, .75) or prior["fcf_yield"][0],
        median(fcf_yields) if fcf_yields else prior["fcf_yield"][1],
        quantile(fcf_yields, .25) or prior["fcf_yield"][2],
    )
    return {
        "source": "POINT_IN_TIME_CALIBRATION",
        "sample_size": max(len(pe_values), len(evs_values), len(fcf_yields)),
        "pe": pe,
        "ev_sales": evs,
        "fcf_yield": fy,
    }


def default_cases(metrics: dict[str, Any], company_type: str = "Generic", calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    prior = TYPE_PRIORS.get(company_type, TYPE_PRIORS["Generic"])
    calibration = calibration or {"source": "TYPE_PRIOR", **prior}
    growth = clamp(n(metrics.get("revenue_growth")) if n(metrics.get("revenue_growth")) is not None else .04, -.15, .25)
    nm = n(metrics.get("net_margin"))
    fm = n(metrics.get("fcf_margin"))
    if nm is None:
        nm = .08
    if fm is None:
        fm = .07
    pe = calibration.get("pe") or prior["pe"]
    evs = calibration.get("ev_sales") or prior["ev_sales"]
    fy = calibration.get("fcf_yield") or prior["fcf_yield"]
    weights = {"pe": .40, "ev_sales": .25, "fcf_yield": .35}
    if n(metrics.get("net_income")) is None or n(metrics.get("net_income")) <= 0:
        weights["pe"] = 0.0
    if n(metrics.get("fcf")) is None or n(metrics.get("fcf")) <= 0:
        weights["fcf_yield"] = 0.0
    if company_type == "Financial / REIT":
        weights = {"pe": 1.0, "ev_sales": 0.0, "fcf_yield": 0.0}
    return {
        "BEAR": {"growth": clamp(growth - .05, -.30, .25), "net_margin": clamp(nm - .025, -.20, .50), "fcf_margin": clamp(fm - .03, -.20, .50), "pe": pe[0], "ev_sales": evs[0], "target_fcf_yield": fy[0], "equity_discount_rate": .11, "terminal_growth": .015, "probability": .25, "manual_override": None},
        "BASE": {"growth": clamp(growth, -.20, .35), "net_margin": clamp(nm, -.15, .60), "fcf_margin": clamp(fm, -.15, .60), "pe": pe[1], "ev_sales": evs[1], "target_fcf_yield": fy[1], "equity_discount_rate": .10, "terminal_growth": .025, "probability": .50, "manual_override": None},
        "BULL": {"growth": clamp(growth + .05, -.10, .50), "net_margin": clamp(nm + .025, -.10, .70), "fcf_margin": clamp(fm + .03, -.10, .70), "pe": pe[2], "ev_sales": evs[2], "target_fcf_yield": fy[2], "equity_discount_rate": .09, "terminal_growth": .030, "probability": .25, "manual_override": None},
        "weights": weights,
        "horizon_years": 5,
        "company_type": company_type,
        "calibration": calibration,
    }


def pe_value(revenue: Any, growth: Any, net_margin: Any, pe: Any, shares: Any) -> float | None:
    values = [n(x) for x in (revenue, growth, net_margin, pe, shares)]
    if any(x is None for x in values) or values[-1] == 0:
        return None
    value = values[0] * (1 + values[1]) * values[2] / values[4] * values[3]
    return value if value > 0 else None


def ev_sales_value(revenue: Any, growth: Any, multiple: Any, net_debt: Any, shares: Any) -> float | None:
    values = [n(x) for x in (revenue, growth, multiple, net_debt, shares)]
    if any(x is None for x in values) or values[-1] == 0:
        return None
    value = (values[0] * (1 + values[1]) * values[2] - values[3]) / values[4]
    return value if value > 0 else None


def fcf_yield_value(revenue: Any, growth: Any, fcf_margin: Any, target_yield: Any, shares: Any) -> float | None:
    values = [n(x) for x in (revenue, growth, fcf_margin, target_yield, shares)]
    if any(x is None for x in values) or values[-1] == 0 or values[3] <= 0:
        return None
    value = values[0] * (1 + values[1]) * values[2] / values[3] / values[4]
    return value if value > 0 else None


def dcf_value(revenue: Any, growth: Any, fcf_margin: Any, discount_rate: Any, terminal_growth: Any, years: Any, shares: Any) -> float | None:
    values = [n(x) for x in (revenue, growth, fcf_margin, discount_rate, terminal_growth, shares)]
    if any(x is None for x in values) or values[-1] <= 0 or values[3] <= values[4] or values[3] <= 0:
        return None
    rev, pv = values[0], 0.0
    horizon = max(1, min(int(n(years) or 5), 20))
    for year in range(1, horizon + 1):
        rev *= 1 + values[1]
        pv += rev * values[2] / ((1 + values[3]) ** year)
    terminal_fcf = rev * (1 + values[4]) * values[2]
    pv += terminal_fcf / (values[3] - values[4]) / ((1 + values[3]) ** horizon)
    value = pv / values[5]
    return value if value > 0 else None


def robust_blend(components: dict[str, Any], weights: dict[str, Any]) -> tuple[float | None, dict[str, float], list[str]]:
    valid = [(key, n(value)) for key, value in components.items() if n(value) is not None and n(value) > 0]
    if not valid:
        return None, {}, []
    med = median([value for _, value in valid])
    adjusted: dict[str, float] = {}
    flags: list[str] = []
    for key, value in valid:
        weight = max(0.0, n(weights.get(key)) or 0.0)
        if len(valid) >= 3 and med > 0 and abs(value / med - 1.0) > .45:
            weight *= .30
            flags.append(f"{key} is >45% from the cross-method median; weight reduced 70%")
        adjusted[key] = weight
    total = sum(adjusted.values())
    if total <= 0:
        adjusted = {key: 1.0 for key, _ in valid}
        total = float(len(valid))
    effective = {key: value / total for key, value in adjusted.items()}
    blend = sum(value * effective[key] for key, value in valid)
    return blend, effective, flags


def _reference_factor(name: str, cases: dict[str, dict[str, Any]]) -> float:
    if name == "BASE":
        return 1.0
    case = cases.get(name) or {}
    base = cases.get("BASE") or {}
    ratios: list[float] = []
    for key in ("pe", "ev_sales"):
        value, base_value = n(case.get(key)), n(base.get(key))
        if value is not None and base_value not in (None, 0) and value > 0 and base_value > 0:
            ratios.append(value / base_value)
    y, base_y = n(case.get("target_fcf_yield")), n(base.get("target_fcf_yield"))
    if y not in (None, 0) and base_y not in (None, 0) and y > 0 and base_y > 0:
        ratios.append(base_y / y)
    factor = median(ratios) if ratios else (0.70 if name == "BEAR" else 1.35)
    if name == "BEAR":
        return clamp(factor, .45, .95)
    return clamp(factor, 1.05, 1.80)


def scenario_value(
    metrics: dict[str, Any],
    assumptions: dict[str, Any],
    weights: dict[str, Any],
    years: int = 5,
    *,
    fallback_value: Any = None,
    reference_price: Any = None,
    reference_factor: float = 1.0,
    allow_reference_fallback: bool = True,
) -> dict[str, Any]:
    flags: list[str] = []
    override = n(assumptions.get("manual_override"))
    if override is not None and override > 0:
        return {
            "fair_value": override,
            "pe": None,
            "ev_sales": None,
            "fcf_yield": None,
            "dcf": None,
            "range_low": override,
            "range_high": override,
            "effective_weights": {},
            "flags": ["Manual fair-value override active"],
            "quality": "MANUAL_OVERRIDE",
            "fallback_source": None,
        }

    revenue, shares, net_debt = metrics.get("revenue"), metrics.get("shares"), metrics.get("net_debt")
    basis_usable = bool(metrics.get("basis_usable")) and n(revenue) not in (None, 0) and n(shares) not in (None, 0)

    pe = evs = fcf = dcf = fair = None
    effective: dict[str, float] = {}
    if basis_usable:
        pe = pe_value(revenue, assumptions.get("growth"), assumptions.get("net_margin"), assumptions.get("pe"), shares)
        evs = ev_sales_value(revenue, assumptions.get("growth"), assumptions.get("ev_sales"), net_debt, shares)
        fcf = fcf_yield_value(revenue, assumptions.get("growth"), assumptions.get("fcf_margin"), assumptions.get("target_fcf_yield"), shares)
        dcf = dcf_value(revenue, assumptions.get("growth"), assumptions.get("fcf_margin"), assumptions.get("equity_discount_rate"), assumptions.get("terminal_growth"), years, shares)
        fair, effective, blend_flags = robust_blend({"pe": pe, "ev_sales": evs, "fcf_yield": fcf}, weights)
        flags.extend(blend_flags)

    fallback_source = None
    quality = "INTRINSIC" if fair is not None else "DATA_WARNING"
    if fair is None:
        stored = n(fallback_value)
        if stored is not None and stored > 0:
            fair = stored
            fallback_source = "LAST_STORED_CASE"
            quality = "STALE_INTRINSIC_FALLBACK"
            flags.append("DATA WARNING: current intrinsic inputs are incomplete; showing the last stored case value.")
        elif allow_reference_fallback:
            ref = n(reference_price)
            if ref is not None and ref > 0:
                fair = max(.01, ref * max(.01, reference_factor))
                fallback_source = "VERIFIED_MARKET_REFERENCE"
                quality = "REFERENCE_PRICE_FALLBACK"
                issue = metrics.get("basis_issue") or "not enough usable fundamental/share-basis data"
                flags.append(
                    f"DATA WARNING: {issue} Showing a provisional reference-price case so Bear/Base/Bull remain visible; this is not an intrinsic valuation."
                )

    valid = [x for x in (pe, evs, fcf) if x is not None and x > 0]
    return {
        "fair_value": fair,
        "pe": pe,
        "ev_sales": evs,
        "fcf_yield": fcf,
        "dcf": dcf,
        "range_low": quantile(valid, .25) if valid else fair,
        "range_high": quantile(valid, .75) if valid else fair,
        "effective_weights": effective,
        "flags": flags,
        "quality": quality,
        "fallback_source": fallback_source,
    }


def evaluate(
    metrics: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    weights: dict[str, Any],
    years: int = 5,
    current_price: Any = None,
    *,
    fallback_values: dict[str, Any] | None = None,
    allow_reference_fallback: bool = True,
) -> dict[str, Any]:
    fallback_values = fallback_values or {}
    scenarios: dict[str, dict[str, Any]] = {}
    price = n(current_price)
    for name in ("BEAR", "BASE", "BULL"):
        row = scenario_value(
            metrics,
            cases.get(name) or {},
            weights,
            years,
            fallback_value=fallback_values.get(name),
            reference_price=price,
            reference_factor=_reference_factor(name, cases),
            allow_reference_fallback=allow_reference_fallback,
        )
        row["probability"] = n((cases.get(name) or {}).get("probability")) or 0.0
        row["gap_pct"] = ((row["fair_value"] / price - 1.0) * 100.0) if row.get("fair_value") is not None and price not in (None, 0) else None
        scenarios[name] = row
    total_prob = sum(max(0.0, row["probability"]) for row in scenarios.values())
    expected = None
    if total_prob > 0 and all(row.get("fair_value") is not None for row in scenarios.values()):
        expected = sum(row["fair_value"] * max(0.0, row["probability"]) for row in scenarios.values()) / total_prob

    qualities = {str(row.get("quality") or "DATA_WARNING") for row in scenarios.values()}
    if qualities == {"INTRINSIC"}:
        overall_quality = "INTRINSIC"
    elif "REFERENCE_PRICE_FALLBACK" in qualities:
        overall_quality = "PROVISIONAL_REFERENCE_FALLBACK"
    elif "STALE_INTRINSIC_FALLBACK" in qualities:
        overall_quality = "PROVISIONAL_STORED_FALLBACK"
    elif qualities == {"MANUAL_OVERRIDE"}:
        overall_quality = "MANUAL_OVERRIDE"
    else:
        overall_quality = "MIXED"

    warnings = list(metrics.get("data_warnings") or [])
    for row in scenarios.values():
        warnings.extend(flag for flag in row.get("flags") or [] if str(flag).startswith("DATA WARNING"))
    warnings = list(dict.fromkeys(str(item) for item in warnings if item))
    return {
        "scenarios": scenarios,
        "expected_value": expected,
        "current_price": price,
        "engine_version": ENGINE_VERSION,
        "quality": overall_quality,
        "warnings": warnings,
    }


__all__ = [
    "ENGINE_VERSION", "TYPE_PRIORS", "infer_company_type", "metrics_from_history", "calibrate_multiples",
    "default_cases", "pe_value", "ev_sales_value", "fcf_yield_value", "dcf_value", "robust_blend",
    "scenario_value", "evaluate", "n", "clamp", "quantile",
]
