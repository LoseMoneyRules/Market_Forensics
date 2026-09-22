from __future__ import annotations

from datetime import date
from math import isfinite
from random import Random
from statistics import median, pstdev
from typing import Any

from .economic_reality import economic_from_row, has_suppression, metric as economic_metric
from .company_quality import build_company_quality

ENGINE_VERSION = "0.3.2-integrity-v1"
MONTE_CARLO_DRAWS = 10000

DECISION_GRADE_QUALITIES = {"INTRINSIC", "MANUAL_OVERRIDE"}
QUALITY_ALIASES = {
    "STALE_INTRINSIC_FALLBACK": "PROVISIONAL_STORED_FALLBACK",
    "REFERENCE_PRICE_FALLBACK": "PROVISIONAL_REFERENCE_FALLBACK",
}


def canonical_valuation_quality(value: Any) -> str:
    quality = str(value or "").strip().upper()
    return QUALITY_ALIASES.get(quality, quality or "DATA_WARNING")


def valuation_base_quality(valuation: dict[str, Any] | None) -> str:
    valuation = valuation or {}
    return canonical_valuation_quality(
        valuation.get("base_quality")
        or ((valuation.get("scenarios") or {}).get("BASE") or {}).get("quality")
        or valuation.get("quality")
    )


def valuation_is_decision_grade(valuation: dict[str, Any] | None) -> bool:
    return valuation_base_quality(valuation) in DECISION_GRADE_QUALITIES


def stored_model_base_quality(model: Any | None) -> str:
    """Recover Base quality from already-stored model state without running valuation."""
    if model is None:
        return "DATA_WARNING"
    try:
        scenarios = {str(row.name or "").upper(): row for row in (model.scenarios or [])}
    except Exception:
        scenarios = {}
    base = scenarios.get("BASE")
    outputs = dict(getattr(base, "outputs", None) or {}) if base is not None else {}
    assumptions = dict(getattr(model, "assumptions", None) or {})
    latest = dict(assumptions.get("latest_engine_result") or {})
    latest_scenarios = dict(latest.get("scenarios") or {})
    return canonical_valuation_quality(
        outputs.get("quality")
        or (latest_scenarios.get("BASE") or {}).get("quality")
        or latest.get("quality")
    )

# Compatibility metadata only. Automatic valuation never uses fixed sector/type multiples.
# Compatibility export only. 0.3.2 integrity forbids fixed sector/type
# valuation-multiple proxies in automatic valuation.
TYPE_PRIORS: dict[str, Any] = {}



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




def _distribution(values: list[Any]) -> dict[str, Any]:
    vals = [n(value) for value in values]
    vals = [value for value in vals if value is not None]
    return {
        "sample_size": len(vals),
        "p10": quantile(vals, .10) if vals else None,
        "median": median(vals) if vals else None,
        "p90": quantile(vals, .90) if vals else None,
        "std": pstdev(vals) if len(vals) >= 2 else (0.0 if vals else None),
    }


def _ccc_days(row: dict[str, Any]) -> float | None:
    revenue = n(row.get("revenue"))
    cogs = n(row.get("cogs"))
    receivables = n(row.get("receivables"))
    inventory = n(row.get("inventory"))
    payables = n(row.get("payables"))
    if revenue in (None, 0) or cogs in (None, 0) or receivables is None or inventory is None or payables is None:
        return None
    cogs = abs(cogs)
    if cogs == 0:
        return None
    dso = receivables / revenue * 365.0
    dio = inventory / cogs * 365.0
    dpo = payables / cogs * 365.0
    return dso + dio - dpo


def metrics_from_history(history: list[dict[str, Any]], shares_override: Any = None, share_source: str = "", company_type: str = "Generic") -> dict[str, Any]:
    rows = [dict(row) for row in history if n(row.get("revenue")) is not None]
    rows.sort(key=lambda row: (str(row.get("period_end") or ""), int(row.get("fiscal_year") or 0)))
    if not rows:
        return _empty_metrics("No annual filing-derived fundamentals are available.")

    latest = rows[-1]
    revenue = n(latest.get("revenue"))
    net_income = n(latest.get("net_income"))
    fcf = n(latest.get("fcf"))
    cash = n(latest.get("cash")) or 0.0
    debt = n(latest.get("debt")) or 0.0
    economic = economic_from_row(latest)
    economic_net_debt = economic_metric(economic, "economic_net_debt")
    economic_unresolved = (not bool(economic)) or bool(economic.get("material_unresolved"))
    if not economic:
        net_debt = None
        net_debt_basis = "ECONOMIC_REALITY_NOT_MATERIALIZED"
    elif economic_unresolved:
        net_debt = None
        net_debt_basis = "ECONOMIC_CLASSIFICATION_UNRESOLVED"
    elif economic_net_debt is not None:
        net_debt = economic_net_debt
        net_debt_basis = str(economic.get("debt_basis") or "ECONOMIC_REALITY")
    else:
        net_debt = None
        net_debt_basis = "ECONOMIC_NET_DEBT_UNRESOLVED"

    revenues = [n(row.get("revenue")) for row in rows]
    revenue_growths: list[float] = []
    for previous, current in zip(revenues, revenues[1:]):
        if current is None or previous in (None, 0):
            continue
        change = current / previous - 1.0
        if -0.75 < change < 2.0:
            revenue_growths.append(change)

    gross_margins: list[float | None] = []
    net_margins: list[float | None] = []
    fcf_margins: list[float | None] = []
    operating_margins: list[float | None] = []
    ebitda_margins: list[float | None] = []
    ccc_values: list[float] = []
    share_values: list[float] = []
    earnings_normalization_review = False
    sbc_material = False

    for row in rows:
        row_revenue = n(row.get("revenue"))
        row_net_income = n(row.get("net_income"))
        row_fcf = n(row.get("fcf"))
        row_gross = n(row.get("gross_profit"))
        row_operating = n(row.get("operating_income"))
        row_economic = economic_from_row(row)
        row_da = economic_metric(row_economic, "depreciation_amortization")
        row_ebitda = (
            row_operating + row_da
            if row_operating is not None and row_da is not None
            else None
        )
        gross_margins.append(
            row_gross / row_revenue if row_gross is not None and row_revenue not in (None, 0) else None
        )
        if has_suppression(row_economic, "PE_EARNINGS_NORMALIZATION_REVIEW"):
            net_margins.append(None)
            earnings_normalization_review = True
        else:
            net_margins.append(
                row_net_income / row_revenue
                if row_net_income is not None and row_revenue not in (None, 0) else None
            )
        fcf_margins.append(
            row_fcf / row_revenue
            if row_fcf is not None and row_revenue not in (None, 0) else None
        )
        operating_margins.append(
            row_operating / row_revenue
            if row_operating is not None and row_revenue not in (None, 0) else None
        )
        ebitda_margins.append(
            row_ebitda / row_revenue
            if row_ebitda is not None and row_revenue not in (None, 0) else None
        )
        ccc = _ccc_days(row)
        if ccc is not None:
            ccc_values.append(ccc)
        shares_row = n(row.get("diluted_shares")) or n(row.get("shares_outstanding"))
        if shares_row not in (None, 0):
            share_values.append(shares_row)
        if (economic_metric(row_economic, "sbc_to_revenue_pct") or 0.0) >= 5.0:
            sbc_material = True

    share_growths: list[float] = []
    for previous, current in zip(share_values, share_values[1:]):
        if previous in (None, 0) or current is None:
            continue
        change = current / previous - 1.0
        if -0.50 < change < 1.0:
            share_growths.append(change)

    shares = n(shares_override)
    resolved_source = str(share_source or "").strip().upper()
    if shares in (None, 0):
        shares = n(latest.get("diluted_shares"))
        resolved_source = "DILUTED_WA" if shares not in (None, 0) else resolved_source
    if shares in (None, 0):
        shares = n(latest.get("shares_outstanding"))
        resolved_source = "SEC_FY_OUTSTANDING_FALLBACK" if shares not in (None, 0) else resolved_source

    latest_operating = n(latest.get("operating_income"))
    latest_da = economic_metric(economic, "depreciation_amortization")
    latest_ebitda = (
        latest_operating + latest_da
        if latest_operating is not None and latest_da is not None
        else None
    )
    gross_margin = (
        n(latest.get("gross_profit")) / revenue
        if n(latest.get("gross_profit")) is not None and revenue not in (None, 0) else None
    )
    capex_to_revenue = (
        abs(n(latest.get("capex"))) / revenue
        if n(latest.get("capex")) is not None and revenue not in (None, 0) else None
    )
    current_ccc = _ccc_days(latest)
    ccc_median = median(ccc_values[-5:]) if ccc_values else None
    ccc_delta = current_ccc - ccc_median if current_ccc is not None and ccc_median is not None else None
    net_debt_to_ebitda = (
        net_debt / latest_ebitda
        if net_debt is not None and latest_ebitda not in (None, 0) and latest_ebitda > 0 else None
    )
    net_cash = max(0.0, -net_debt) if net_debt is not None else None
    cash_burn = max(0.0, -(fcf or 0.0)) if fcf is not None else 0.0
    liquidation_floor = (
        max(0.0, (net_cash or 0.0) - cash_burn) * 0.85 / shares
        if net_cash is not None and shares not in (None, 0) else None
    )

    warnings: list[str] = []
    if shares in (None, 0):
        warnings.append("No usable shares outstanding / diluted-share denominator is available.")
    if net_income is None:
        warnings.append("Net income is unavailable; P/E is excluded from intrinsic evidence.")
    if fcf is None:
        warnings.append("Free cash flow is unavailable; FCF-yield and DCF evidence are weaker.")
    if earnings_normalization_review:
        warnings.append("Tax/non-operating earnings anomalies are excluded from P/E calibration rather than normalized by guess.")
    if sbc_material:
        warnings.append("Material SBC is handled through observed diluted-share growth; reported FCF is not double-penalized by subtracting SBC again.")
    if not economic:
        warnings.append("Economic Reality is not materialized on this filing basis yet; enterprise-value methods are excluded and a fresh SEC ingest is required.")
    elif economic_unresolved:
        warnings.append("Economic debt classification is materially unresolved; enterprise-value methods are excluded until the financing bridge is classified.")

    company_quality = build_company_quality(rows, company_type)
    valuation_policy = dict(company_quality.get("valuation_policy") or {})
    return {
        "fiscal_year": latest.get("fiscal_year"),
        "filed_at": latest.get("filed_at"),
        "period_end": latest.get("period_end"),
        "revenue": revenue,
        "gross_profit": n(latest.get("gross_profit")),
        "operating_income": latest_operating,
        "net_income": net_income,
        "fcf": fcf,
        "ebitda": latest_ebitda,
        "net_debt": net_debt,
        "net_debt_basis": net_debt_basis,
        "economic_reality": economic,
        "economic_reality_quality": str(economic.get("quality") or "UNAVAILABLE"),
        "economic_reality_unresolved": economic_unresolved,
        "company_quality": company_quality,
        "company_quality_state": company_quality.get("state"),
        "valuation_policy": valuation_policy,
        "revenue_growth": _cagr(revenues, 3) or _median_growth(revenues),
        "gross_margin": gross_margin,
        "net_margin": median([x for x in net_margins[-3:] if x is not None]) if any(x is not None for x in net_margins[-3:]) else None,
        "fcf_margin": median([x for x in fcf_margins[-3:] if x is not None]) if any(x is not None for x in fcf_margins[-3:]) else None,
        "reported_fcf_margin": median([x for x in fcf_margins[-3:] if x is not None]) if any(x is not None for x in fcf_margins[-3:]) else None,
        "fcf_margin_basis": "REPORTED_FCF_WITH_DILUTION_DENOMINATOR" if sbc_material else "REPORTED_FCF",
        "operating_margin": median([x for x in operating_margins[-3:] if x is not None]) if any(x is not None for x in operating_margins[-3:]) else None,
        "ebitda_margin": median([x for x in ebitda_margins[-3:] if x is not None]) if any(x is not None for x in ebitda_margins[-3:]) else None,
        "capex_to_revenue": capex_to_revenue,
        "economic_roic_pct": economic_metric(economic, "economic_roic_pct"),
        "rd_to_revenue_pct": economic_metric(economic, "rd_to_revenue_pct"),
        "sbc_to_revenue_pct": economic_metric(economic, "sbc_to_revenue_pct"),
        "interest_coverage_x": economic_metric(economic, "interest_coverage_x"),
        "fixed_charge_coverage_x": economic_metric(economic, "fixed_charge_coverage_proxy_x"),
        "net_debt_to_ebitda": net_debt_to_ebitda,
        "ccc_days": current_ccc,
        "ccc_median_5y": ccc_median,
        "ccc_delta_days": ccc_delta,
        "liquidation_floor_per_share": liquidation_floor,
        "shares": shares,
        "share_source": resolved_source or "UNRESOLVED",
        "share_growth_rate": median(share_growths[-5:]) if share_growths else 0.0,
        "history_stats": {
            "revenue_growth": _distribution(revenue_growths[-10:]),
            "gross_margin": _distribution(gross_margins[-10:]),
            "net_margin": _distribution(net_margins[-10:]),
            "fcf_margin": _distribution(fcf_margins[-10:]),
            "operating_margin": _distribution(operating_margins[-10:]),
            "ebitda_margin": _distribution(ebitda_margins[-10:]),
            "share_growth": _distribution(share_growths[-10:]),
            "ccc_days": _distribution(ccc_values[-10:]),
        },
        "basis_usable": shares not in (None, 0),
        "basis_issue": "" if shares not in (None, 0) else warnings[0],
        "data_warnings": warnings,
    }


def _calibration_series(observations: list[dict[str, Any]], key: str) -> tuple[list[float], str]:
    usable: list[tuple[int | None, float]] = []
    for row in observations[-12:]:
        value = n(row.get(key))
        if value is None:
            continue
        try:
            fy = int(row.get("fiscal_year")) if row.get("fiscal_year") is not None else None
        except Exception:
            fy = None
        usable.append((fy, value))
    if not usable:
        return [], "UNAVAILABLE"
    fiscal_years = [fy for fy, _ in usable if fy is not None]
    five: list[float] = []
    if fiscal_years:
        latest_fy = max(fiscal_years)
        five = [value for fy, value in usable if fy is not None and fy >= latest_fy - 4]
    else:
        five = [value for _, value in usable[-5:]]
    if len(five) >= 4:
        return five, "5Y"
    ten = [value for _, value in usable[-10:]]
    if len(ten) >= 4:
        return ten, "10Y"
    return [], "INSUFFICIENT"


def calibrate_multiples(observations: list[dict[str, Any]], company_type: str = "Generic") -> dict[str, Any]:
    """Company-specific point-in-time multiple calibration.

    No sector/type multiple proxy is used. Each method must earn its own 5Y
    history (minimum four filing anchors) or fall back to the company's 10Y
    history. If neither is available, that method stays unavailable.
    """
    computed: list[dict[str, Any]] = []
    for row in observations:
        price = n(row.get("price"))
        shares = n(row.get("shares"))
        revenue = n(row.get("revenue"))
        net_income = n(row.get("net_income"))
        fcf = n(row.get("fcf"))
        net_debt = n(row.get("net_debt"))
        ebitda = n(row.get("ebitda"))
        if price is None or shares in (None, 0) or price <= 0:
            continue
        market_cap = price * shares
        out = {
            "fiscal_year": row.get("fiscal_year"),
            "pe": market_cap / net_income if net_income is not None and net_income > 0 else None,
            "p_sales": market_cap / revenue if revenue not in (None, 0) and revenue > 0 else None,
            "ev_sales": ((market_cap + net_debt) / revenue) if net_debt is not None and revenue not in (None, 0) and revenue > 0 else None,
            "ev_ebitda": ((market_cap + net_debt) / ebitda) if net_debt is not None and ebitda not in (None, 0) and ebitda > 0 else None,
            "fcf_yield": fcf / market_cap if fcf is not None and fcf > 0 and market_cap > 0 else None,
        }
        if out["pe"] is not None and not (2.0 <= out["pe"] <= 150.0):
            out["pe"] = None
        if out["p_sales"] is not None and not (0.02 <= out["p_sales"] <= 50.0):
            out["p_sales"] = None
        if out["ev_sales"] is not None and not (0.02 <= out["ev_sales"] <= 50.0):
            out["ev_sales"] = None
        if out["ev_ebitda"] is not None and not (1.0 <= out["ev_ebitda"] <= 80.0):
            out["ev_ebitda"] = None
        if out["fcf_yield"] is not None and not (0.001 <= out["fcf_yield"] <= 0.50):
            out["fcf_yield"] = None
        computed.append(out)

    stats: dict[str, Any] = {}
    result: dict[str, Any] = {}
    for key in ("pe", "p_sales", "ev_sales", "ev_ebitda", "fcf_yield"):
        values, horizon = _calibration_series(computed, key)
        if values:
            p10 = quantile(values, .10)
            p50 = median(values)
            p90 = quantile(values, .90)
            triple = (p90, p50, p10) if key == "fcf_yield" else (p10, p50, p90)
        else:
            p10 = p50 = p90 = None
            triple = (None, None, None)
        result[key] = triple
        stats[key] = {
            "sample_size": len(values),
            "horizon": horizon,
            "p10": p10,
            "median": p50,
            "p90": p90,
        }

    max_sample = max((item["sample_size"] for item in stats.values()), default=0)
    result.update({
        "source": "COMPANY_POINT_IN_TIME_5Y_10Y" if max_sample >= 4 else "COMPANY_HISTORY_INSUFFICIENT",
        "sample_size": max_sample,
        "method_stats": stats,
        "uses_fixed_type_proxy": False,
        "company_type_context": company_type,
    })
    return result


def _hist_stat(metrics: dict[str, Any], key: str, stat: str) -> float | None:
    return n((((metrics.get("history_stats") or {}).get(key) or {}).get(stat)))


def _life_cycle(metrics: dict[str, Any], company_type: str) -> str:
    if company_type == "Financial / REIT":
        return "SECTOR_SPECIFIC"
    growth = (metrics.get("history_stats") or {}).get("revenue_growth") or {}
    op = (metrics.get("history_stats") or {}).get("operating_margin") or {}
    growth_med = n(growth.get("median"))
    growth_std = n(growth.get("std"))
    op_std = n(op.get("std"))
    capex_ratio = n(metrics.get("capex_to_revenue"))
    rd_pct = n(metrics.get("rd_to_revenue_pct"))
    reinvestment_high = (
        (capex_ratio is not None and capex_ratio >= .07)
        or (rd_pct is not None and rd_pct >= 8.0)
    )
    volatility_evidence = (
        int(growth.get("sample_size") or 0) >= 5
        and (
            (growth_std is not None and growth_std >= .12)
            or (op_std is not None and op_std >= .055)
        )
    )
    if volatility_evidence:
        return "CYCLICAL"
    if growth_med is not None and growth_med >= .10 and reinvestment_high:
        return "GROWTH"
    if growth_med is not None and growth_med <= .06 and not reinvestment_high:
        return "MATURE"
    return "STABLE"


def _scenario_triplet(metrics: dict[str, Any], key: str, current: float | None, *, floor: float = -.90, ceiling: float = .90) -> tuple[float | None, float | None, float | None]:
    stats = (metrics.get("history_stats") or {}).get(key) or {}
    if int(stats.get("sample_size") or 0) >= 4:
        return (
            clamp(n(stats.get("p10")) or 0.0, floor, ceiling),
            clamp(n(stats.get("median")) or 0.0, floor, ceiling),
            clamp(n(stats.get("p90")) or 0.0, floor, ceiling),
        )
    current = n(current)
    if current is None:
        return None, None, None
    stress = max(abs(current) * .25, .01)
    return (
        clamp(current - stress, floor, ceiling),
        clamp(current, floor, ceiling),
        clamp(current + stress, floor, ceiling),
    )


def _method_policy(metrics: dict[str, Any], calibration: dict[str, Any], company_type: str, life_cycle: str) -> tuple[dict[str, float], list[str], list[str]]:
    weights = {key: 0.0 for key in ("pe", "p_sales", "ev_sales", "ev_ebitda", "fcf_yield", "dcf")}
    notes: list[str] = []
    exclusions = set((metrics.get("valuation_policy") or {}).get("method_exclusions") or [])

    def available(key: str) -> bool:
        triple = calibration.get(key) or ()
        return len(triple) >= 3 and n(triple[1]) is not None

    if available("pe") and n(metrics.get("net_income")) not in (None, 0) and n(metrics.get("net_income")) > 0:
        weights["pe"] = 1.0
    if available("p_sales"):
        weights["p_sales"] = 1.0
    if available("ev_sales") and n(metrics.get("net_debt")) is not None:
        weights["ev_sales"] = 1.0
    if available("ev_ebitda") and n(metrics.get("ebitda")) not in (None, 0) and n(metrics.get("ebitda")) > 0:
        weights["ev_ebitda"] = 1.0
    if available("fcf_yield") and n(metrics.get("fcf_margin")) not in (None, 0) and n(metrics.get("fcf_margin")) > 0:
        weights["fcf_yield"] = 1.0
    if n(metrics.get("fcf_margin")) not in (None, 0) and n(metrics.get("fcf_margin")) > 0:
        weights["dcf"] = 1.0

    net_margin = n(metrics.get("net_margin"))
    operating_margin = n(metrics.get("operating_margin"))
    thin_margin = (
        (net_margin is not None and abs(net_margin) <= .03)
        or (operating_margin is not None and abs(operating_margin) <= .04)
    )
    if thin_margin:
        weights["p_sales"] = 0.0
        weights["ev_sales"] = 0.0
        exclusions.update(("p_sales", "ev_sales"))
        notes.append("Thin-margin business: sales multiples are disabled; cash/EBITDA economics must carry valuation.")

    leverage = n(metrics.get("net_debt_to_ebitda"))
    if leverage is not None and leverage >= 3.0:
        weights["pe"] = 0.0
        weights["p_sales"] = 0.0
        exclusions.update(("pe", "p_sales"))
        notes.append("High leverage: equity-only multiples are disabled; enterprise-value and cash-flow methods are required.")

    # Never double count the same sales denominator through P/S and EV/Sales.
    if weights["p_sales"] > 0 and weights["ev_sales"] > 0:
        if n(metrics.get("net_debt")) not in (None, 0) and abs(n(metrics.get("net_debt")) or 0.0) > 0:
            weights["p_sales"] = 0.0
            exclusions.add("p_sales")
        else:
            weights["ev_sales"] = 0.0
            exclusions.add("ev_sales")

    asset_light = (
        n(metrics.get("gross_margin")) is not None
        and n(metrics.get("gross_margin")) >= .60
        and n(metrics.get("capex_to_revenue")) is not None
        and n(metrics.get("capex_to_revenue")) <= .05
    )
    if asset_light and weights["dcf"] > 0:
        weights["dcf"] = 1.75
        if weights["fcf_yield"] > 0:
            weights["fcf_yield"] = 1.25
        notes.append("Asset-light/high-margin economics: DCF and shareholder cash flow receive the strongest evidence weight.")

    if life_cycle == "CYCLICAL":
        if weights["pe"] > 0:
            weights["pe"] = .25
        if weights["ev_ebitda"] > 0:
            weights["ev_ebitda"] = 1.75
        if weights["dcf"] > 0:
            weights["dcf"] = 1.25
        notes.append("Cyclical economics: current-period P/E is de-emphasized and normalized EBITDA/cash-flow evidence is preferred.")

    if life_cycle == "SECTOR_SPECIFIC":
        weights = {key: 0.0 for key in weights}
        exclusions.update(weights.keys())
        notes.append("Financial/REIT generic industrial valuation is disabled pending sector-specific P/B-ROE or AFFO/NAV evidence.")

    for method in exclusions:
        if method in weights:
            weights[method] = 0.0
    return weights, sorted(exclusions), notes


def default_cases(metrics: dict[str, Any], company_type: str = "Generic", calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    calibration = calibration or dict(metrics.get("historical_calibration") or {}) or {
        "source": "COMPANY_HISTORY_INSUFFICIENT",
        "sample_size": 0,
        "pe": (None, None, None),
        "p_sales": (None, None, None),
        "ev_sales": (None, None, None),
        "ev_ebitda": (None, None, None),
        "fcf_yield": (None, None, None),
    }
    life_cycle = _life_cycle(metrics, company_type)
    growth_current = n(metrics.get("revenue_growth"))
    growth_bear, growth_base, growth_bull = _scenario_triplet(metrics, "revenue_growth", growth_current, floor=-.40, ceiling=.60)
    nm_bear, nm_base, nm_bull = _scenario_triplet(metrics, "net_margin", n(metrics.get("net_margin")), floor=-.40, ceiling=.70)
    fm_bear, fm_base, fm_bull = _scenario_triplet(metrics, "fcf_margin", n(metrics.get("fcf_margin")), floor=-.40, ceiling=.70)
    em_bear, em_base, em_bull = _scenario_triplet(metrics, "ebitda_margin", n(metrics.get("ebitda_margin")), floor=-.40, ceiling=.80)
    sg_bear, sg_base, sg_bull = _scenario_triplet(metrics, "share_growth", n(metrics.get("share_growth_rate")), floor=-.15, ceiling=.30)

    if life_cycle == "MATURE":
        if growth_base is not None:
            growth_base = min(growth_base, .10)
        if growth_bull is not None:
            growth_bull = min(growth_bull, .15)
    elif life_cycle == "GROWTH":
        if growth_bull is not None:
            growth_bull = min(growth_bull, .50)

    policy = dict(metrics.get("valuation_policy") or {})
    quality_risk = (n(policy.get("risk_premium_bps")) or 0.0) / 10000.0
    quality_growth_haircut = (n(policy.get("growth_haircut_bps")) or 0.0) / 10000.0
    terminal_haircut = (n(policy.get("terminal_growth_haircut_bps")) or 0.0) / 10000.0
    growth_bear = (growth_bear - quality_growth_haircut) if growth_bear is not None else None
    growth_base = (growth_base - quality_growth_haircut) if growth_base is not None else None
    growth_bull = (growth_bull - quality_growth_haircut) if growth_bull is not None else None

    leverage = n(metrics.get("net_debt_to_ebitda"))
    leverage_premium = 0.0
    if leverage is not None and leverage > 2.5:
        leverage_premium = min(.025, (leverage - 2.5) * .005)

    ccc_delta = n(metrics.get("ccc_delta_days"))
    ccc_adjustment = .005 if ccc_delta is not None and ccc_delta >= 15 else (-.0025 if ccc_delta is not None and ccc_delta <= -15 else 0.0)

    roic_pct = n(metrics.get("economic_roic_pct"))
    capital_efficiency_adjustment = 0.0
    if (
        roic_pct is not None and roic_pct >= 15.0
        and n(metrics.get("capex_to_revenue")) is not None
        and n(metrics.get("capex_to_revenue")) <= .05
        and n(metrics.get("net_debt")) is not None
        and n(metrics.get("net_debt")) <= 0
    ):
        capital_efficiency_adjustment = -.005

    base_discount = clamp(.10 + quality_risk + leverage_premium + ccc_adjustment + capital_efficiency_adjustment, .07, .20)
    terminal_base = .025 - terminal_haircut
    if life_cycle == "MATURE":
        terminal_base = min(terminal_base, .025)
    elif life_cycle == "GROWTH":
        terminal_base = min(terminal_base, .030)
    terminal_base = clamp(terminal_base, 0.0, .035)

    interest_coverage = n(metrics.get("interest_coverage_x"))
    fixed_coverage = n(metrics.get("fixed_charge_coverage_x"))
    if (
        (leverage is not None and leverage >= 5.0)
        or (interest_coverage is not None and interest_coverage < 1.5)
        or (fixed_coverage is not None and fixed_coverage < 1.2)
    ):
        solvency_state = "DISTRESS"
        bear_multiplier, base_multiplier = .65, .90
    elif (
        (leverage is not None and leverage >= 3.5)
        or (interest_coverage is not None and interest_coverage < 2.5)
        or (fixed_coverage is not None and fixed_coverage < 2.0)
    ):
        solvency_state = "WATCH"
        bear_multiplier, base_multiplier = .80, .95
    else:
        solvency_state = "NORMAL"
        bear_multiplier = base_multiplier = 1.0

    weights, dynamic_exclusions, method_notes = _method_policy(metrics, calibration, company_type, life_cycle)
    bear_shift = (n(policy.get("bear_probability_shift_pts")) or 0.0) / 100.0
    bear_probability = clamp(.25 + bear_shift, .10, .60)
    base_probability = clamp(.50 - bear_shift / 2.0, .20, .70)
    bull_probability = max(0.0, 1.0 - bear_probability - base_probability)

    pe = calibration.get("pe") or (None, None, None)
    ps = calibration.get("p_sales") or (None, None, None)
    evs = calibration.get("ev_sales") or (None, None, None)
    eve = calibration.get("ev_ebitda") or (None, None, None)
    fy = calibration.get("fcf_yield") or (None, None, None)

    common = {
        "method_exclusions": dynamic_exclusions,
        "life_cycle": life_cycle,
        "solvency_state": solvency_state,
        "integrity_notes": method_notes,
    }
    return {
        "BEAR": {
            **common,
            "growth": growth_bear,
            "net_margin": nm_bear,
            "fcf_margin": fm_bear,
            "ebitda_margin": em_bear,
            "share_growth": sg_bear,
            "pe": pe[0], "p_sales": ps[0], "ev_sales": evs[0], "ev_ebitda": eve[0], "target_fcf_yield": fy[0],
            "equity_discount_rate": clamp(base_discount + .015, .08, .24),
            "terminal_growth": max(-.01, terminal_base - .01),
            "probability": bear_probability,
            "manual_override": None,
            "scenario_multiplier": bear_multiplier,
            "liquidation_floor": n(metrics.get("liquidation_floor_per_share")),
        },
        "BASE": {
            **common,
            "growth": growth_base,
            "net_margin": nm_base,
            "fcf_margin": fm_base,
            "ebitda_margin": em_base,
            "share_growth": sg_base,
            "pe": pe[1], "p_sales": ps[1], "ev_sales": evs[1], "ev_ebitda": eve[1], "target_fcf_yield": fy[1],
            "equity_discount_rate": base_discount,
            "terminal_growth": terminal_base,
            "probability": base_probability,
            "manual_override": None,
            "scenario_multiplier": base_multiplier,
            "liquidation_floor": None,
        },
        "BULL": {
            **common,
            "growth": growth_bull,
            "net_margin": nm_bull,
            "fcf_margin": fm_bull,
            "ebitda_margin": em_bull,
            "share_growth": sg_bull,
            "pe": pe[2], "p_sales": ps[2], "ev_sales": evs[2], "ev_ebitda": eve[2], "target_fcf_yield": fy[2],
            "equity_discount_rate": clamp(base_discount - .010, .06, .20),
            "terminal_growth": min(.04, terminal_base + .005),
            "probability": bull_probability,
            "manual_override": None,
            "scenario_multiplier": 1.0,
            "liquidation_floor": None,
        },
        "weights": weights,
        "horizon_years": 5,
        "company_type": company_type,
        "life_cycle": life_cycle,
        "calibration": calibration,
        "company_quality": metrics.get("company_quality") or {},
        "valuation_policy": policy,
        "integrity_policy": {
            "fixed_type_multiple_proxy": False,
            "historical_multiple_rule": "5Y company P10/P50/P90; 10Y fallback with >=4 point-in-time filing anchors",
            "sbc_rule": "Observed diluted-share growth; reported FCF is not reduced again for SBC",
            "cyclical_rule": "Historical growth/margin distribution; P/E de-emphasized",
            "thin_margin_sales_multiple_disabled": True,
            "monte_carlo_draws": MONTE_CARLO_DRAWS,
            "life_cycle": life_cycle,
            "solvency_state": solvency_state,
            "leverage_discount_premium": leverage_premium,
            "ccc_discount_adjustment": ccc_adjustment,
            "capital_efficiency_discount_adjustment": capital_efficiency_adjustment,
            "method_notes": method_notes,
        },
    }


def _future_shares(shares: Any, share_growth: Any, years: float = 1.0) -> float | None:
    shares_value = n(shares)
    growth = n(share_growth) or 0.0
    if shares_value in (None, 0) or shares_value <= 0:
        return None
    growth = clamp(growth, -.20, .50)
    return shares_value * ((1.0 + growth) ** max(0.0, float(years)))


def pe_value(revenue: Any, growth: Any, net_margin: Any, pe: Any, shares: Any, share_growth: Any = 0.0) -> float | None:
    values = [n(x) for x in (revenue, growth, net_margin, pe)]
    projected_shares = _future_shares(shares, share_growth, 1.0)
    if any(x is None for x in values) or projected_shares in (None, 0):
        return None
    value = values[0] * (1 + values[1]) * values[2] / projected_shares * values[3]
    return value if value > 0 else None


def p_sales_value(revenue: Any, growth: Any, multiple: Any, shares: Any, share_growth: Any = 0.0) -> float | None:
    values = [n(x) for x in (revenue, growth, multiple)]
    projected_shares = _future_shares(shares, share_growth, 1.0)
    if any(x is None for x in values) or projected_shares in (None, 0):
        return None
    value = values[0] * (1 + values[1]) * values[2] / projected_shares
    return value if value > 0 else None


def ev_sales_value(revenue: Any, growth: Any, multiple: Any, net_debt: Any, shares: Any, share_growth: Any = 0.0) -> float | None:
    values = [n(x) for x in (revenue, growth, multiple, net_debt)]
    projected_shares = _future_shares(shares, share_growth, 1.0)
    if any(x is None for x in values) or projected_shares in (None, 0):
        return None
    value = (values[0] * (1 + values[1]) * values[2] - values[3]) / projected_shares
    return value if value > 0 else None


def ev_ebitda_value(revenue: Any, growth: Any, ebitda_margin: Any, multiple: Any, net_debt: Any, shares: Any, share_growth: Any = 0.0) -> float | None:
    values = [n(x) for x in (revenue, growth, ebitda_margin, multiple, net_debt)]
    projected_shares = _future_shares(shares, share_growth, 1.0)
    if any(x is None for x in values) or projected_shares in (None, 0):
        return None
    ebitda = values[0] * (1 + values[1]) * values[2]
    value = (ebitda * values[3] - values[4]) / projected_shares
    return value if value > 0 else None


def fcf_yield_value(revenue: Any, growth: Any, fcf_margin: Any, target_yield: Any, shares: Any, share_growth: Any = 0.0) -> float | None:
    values = [n(x) for x in (revenue, growth, fcf_margin, target_yield)]
    projected_shares = _future_shares(shares, share_growth, 1.0)
    if any(x is None for x in values) or projected_shares in (None, 0) or values[3] <= 0:
        return None
    value = values[0] * (1 + values[1]) * values[2] / values[3] / projected_shares
    return value if value > 0 else None


def dcf_value(
    revenue: Any,
    growth: Any,
    fcf_margin: Any,
    discount_rate: Any,
    terminal_growth: Any,
    years: Any,
    shares: Any,
    share_growth: Any = 0.0,
) -> float | None:
    values = [n(x) for x in (revenue, growth, fcf_margin, discount_rate, terminal_growth)]
    shares_value = n(shares)
    if any(x is None for x in values) or shares_value in (None, 0) or shares_value <= 0 or values[3] <= values[4] or values[3] <= 0:
        return None
    rev = values[0]
    start_growth = clamp(values[1], -.50, .80)
    margin = values[2]
    discount = values[3]
    terminal = values[4]
    dilution = clamp(n(share_growth) or 0.0, -.20, .50)
    horizon = max(1, min(int(n(years) or 5), 20))
    pv_per_share = 0.0
    current_shares = shares_value
    for year in range(1, horizon + 1):
        progress = (year - 1) / max(1, horizon - 1)
        year_growth = start_growth + (terminal - start_growth) * progress
        rev *= 1.0 + year_growth
        current_shares *= 1.0 + dilution
        if current_shares <= 0:
            return None
        equity_fcf_per_share = rev * margin / current_shares
        pv_per_share += equity_fcf_per_share / ((1.0 + discount) ** year)
    terminal_fcf_per_share = rev * (1.0 + terminal) * margin / current_shares
    pv_per_share += terminal_fcf_per_share / (discount - terminal) / ((1.0 + discount) ** horizon)
    return pv_per_share if pv_per_share > 0 else None


def robust_blend(components: dict[str, Any], weights: dict[str, Any]) -> tuple[float | None, dict[str, float], list[str]]:
    valid = [
        (key, n(value))
        for key, value in components.items()
        if n(value) is not None and n(value) > 0 and (n(weights.get(key)) or 0.0) > 0
    ]
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
        return None, {}, flags
    effective = {key: value / total for key, value in adjusted.items()}
    blend = sum(value * effective[key] for key, value in valid)
    return blend, effective, flags


def _reference_factor(name: str, cases: dict[str, dict[str, Any]]) -> float:
    if name == "BASE":
        return 1.0
    case = cases.get(name) or {}
    base = cases.get("BASE") or {}
    ratios: list[float] = []
    for key in ("pe", "p_sales", "ev_sales", "ev_ebitda"):
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
            "pe": None, "p_sales": None, "ev_sales": None, "ev_ebitda": None,
            "fcf_yield": None, "dcf": None,
            "range_low": override, "range_high": override,
            "effective_weights": {}, "method_count": 0, "applicable_methods": [],
            "flags": ["Manual fair-value override active"],
            "quality": "MANUAL_OVERRIDE", "fallback_source": None,
        }

    revenue, shares, net_debt = metrics.get("revenue"), metrics.get("shares"), metrics.get("net_debt")
    basis_usable = bool(metrics.get("basis_usable")) and n(revenue) not in (None, 0) and n(shares) not in (None, 0)
    share_growth = assumptions.get("share_growth")

    pe = ps = evs = eve = fcf = dcf = fair = None
    effective: dict[str, float] = {}
    if basis_usable:
        pe = pe_value(revenue, assumptions.get("growth"), assumptions.get("net_margin"), assumptions.get("pe"), shares, share_growth)
        ps = p_sales_value(revenue, assumptions.get("growth"), assumptions.get("p_sales"), shares, share_growth)
        evs = ev_sales_value(revenue, assumptions.get("growth"), assumptions.get("ev_sales"), net_debt, shares, share_growth)
        eve = ev_ebitda_value(revenue, assumptions.get("growth"), assumptions.get("ebitda_margin"), assumptions.get("ev_ebitda"), net_debt, shares, share_growth)
        fcf = fcf_yield_value(revenue, assumptions.get("growth"), assumptions.get("fcf_margin"), assumptions.get("target_fcf_yield"), shares, share_growth)
        dcf = dcf_value(
            revenue, assumptions.get("growth"), assumptions.get("fcf_margin"),
            assumptions.get("equity_discount_rate"), assumptions.get("terminal_growth"),
            years, shares, share_growth,
        )
        components = {
            "pe": pe, "p_sales": ps, "ev_sales": evs,
            "ev_ebitda": eve, "fcf_yield": fcf, "dcf": dcf,
        }
        fair, effective, blend_flags = robust_blend(components, weights)
        flags.extend(blend_flags)

    method_count = len(effective)
    fallback_source = None
    if fair is not None:
        quality = "INTRINSIC" if method_count >= 2 else "INTRINSIC_SINGLE_METHOD"
        multiplier = n(assumptions.get("scenario_multiplier")) or 1.0
        fair *= clamp(multiplier, .40, 1.20)
        floor_value = n(assumptions.get("liquidation_floor"))
        if floor_value is not None and floor_value > 0 and fair < floor_value:
            fair = floor_value
            flags.append("Conservative net-cash liquidation floor applied after one year of current cash burn and a 15% liquidation haircut.")
    else:
        quality = "DATA_WARNING"

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

    valid = [x for x in (pe, ps, evs, eve, fcf, dcf) if x is not None and x > 0]
    if quality == "INTRINSIC_SINGLE_METHOD":
        flags.append("DATA WARNING: only one independent valuation method is usable; this is not decision-grade.")
    return {
        "fair_value": fair,
        "pe": pe,
        "p_sales": ps,
        "ev_sales": evs,
        "ev_ebitda": eve,
        "fcf_yield": fcf,
        "dcf": dcf,
        "range_low": quantile(valid, .25) if valid else fair,
        "range_high": quantile(valid, .75) if valid else fair,
        "effective_weights": effective,
        "method_count": method_count,
        "applicable_methods": sorted(effective),
        "flags": flags,
        "quality": quality,
        "fallback_source": fallback_source,
    }


def _sample_triangle(rng: Random, low: Any, mode: Any, high: Any, *, scale: float = 1.0) -> float | None:
    values = [n(low), n(mode), n(high)]
    if values[1] is None:
        return None
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    lo, hi = min(usable), max(usable)
    center = values[1]
    if scale > 1.0:
        lo = center - (center - lo) * scale
        hi = center + (hi - center) * scale
    if lo == hi:
        return center
    center = clamp(center, lo, hi)
    return rng.triangular(lo, hi, center)


def _monte_carlo_distribution(
    metrics: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    weights: dict[str, Any],
    years: int,
) -> dict[str, Any]:
    base = cases.get("BASE") or {}
    if any(n((cases.get(name) or {}).get("manual_override")) not in (None, 0) for name in ("BEAR", "BASE", "BULL")):
        return {"available": False, "reason": "Manual override active.", "draws": 0}
    base_check = scenario_value(metrics, base, weights, years, allow_reference_fallback=False)
    if int(base_check.get("method_count") or 0) < 2:
        return {"available": False, "reason": "Fewer than two independent methods are usable.", "draws": 0}

    move = n(metrics.get("market_move_since_filing_pct"))
    freshness_scale = (min(1.75, 1.25 + max(0.0, abs(move) - 30.0) / 100.0) if move is not None and abs(move) >= 30.0 else 1.0)
    rng = Random(20260921)
    values: list[float] = []
    assumption_keys = (
        "growth", "net_margin", "fcf_margin", "ebitda_margin", "share_growth",
        "pe", "p_sales", "ev_sales", "ev_ebitda", "target_fcf_yield",
        "equity_discount_rate", "terminal_growth", "scenario_multiplier",
    )
    for _ in range(MONTE_CARLO_DRAWS):
        sample = dict(base)
        for key in assumption_keys:
            sample[key] = _sample_triangle(
                rng,
                (cases.get("BEAR") or {}).get(key),
                base.get(key),
                (cases.get("BULL") or {}).get(key),
                scale=freshness_scale,
            )
        sample["liquidation_floor"] = None
        row = scenario_value(metrics, sample, weights, years, allow_reference_fallback=False)
        fair = n(row.get("fair_value"))
        if fair is not None and fair > 0:
            values.append(fair)
    if len(values) < max(200, MONTE_CARLO_DRAWS // 4):
        return {"available": False, "reason": "Too few valid simulation outcomes.", "draws": len(values)}
    return {
        "available": True,
        "draws": len(values),
        "requested_draws": MONTE_CARLO_DRAWS,
        "p10": quantile(values, .10),
        "p50": quantile(values, .50),
        "p90": quantile(values, .90),
        "freshness_scale": freshness_scale,
        "market_move_since_filing_pct": move,
        "basis": "DETERMINISTIC_SEEDED_TRIANGULAR_INPUTS_FROM_COMPANY_HISTORY",
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
    policy = dict(metrics.get("valuation_policy") or {})
    exclusions = set(policy.get("method_exclusions") or [])
    exclusions.update(str(x) for x in ((cases.get("BASE") or {}).get("method_exclusions") or []))
    canonical_weights = dict(weights or {})
    for method in exclusions:
        if method in canonical_weights:
            canonical_weights[method] = 0.0

    for name in ("BEAR", "BASE", "BULL"):
        row = scenario_value(
            metrics,
            cases.get(name) or {},
            canonical_weights,
            years,
            fallback_value=fallback_values.get(name),
            reference_price=price,
            reference_factor=_reference_factor(name, cases),
            allow_reference_fallback=allow_reference_fallback,
        )
        row["probability"] = n((cases.get(name) or {}).get("probability")) or 0.0
        row["gap_pct"] = ((row["fair_value"] / price - 1.0) * 100.0) if row.get("fair_value") is not None and price not in (None, 0) else None
        row["deterministic_fair_value"] = row.get("fair_value")
        scenarios[name] = row

    distribution = _monte_carlo_distribution(metrics, cases, canonical_weights, years)
    no_manual = not any(str(row.get("quality")) == "MANUAL_OVERRIDE" for row in scenarios.values())
    if distribution.get("available") and no_manual:
        scenarios["BEAR"]["fair_value"] = distribution.get("p10")
        scenarios["BASE"]["fair_value"] = distribution.get("p50")
        scenarios["BULL"]["fair_value"] = distribution.get("p90")
        for name in ("BEAR", "BASE", "BULL"):
            fair = n(scenarios[name].get("fair_value"))
            scenarios[name]["gap_pct"] = ((fair / price - 1.0) * 100.0) if fair is not None and price not in (None, 0) else None
            scenarios[name]["flags"] = list(scenarios[name].get("flags") or []) + ["Fair value is the requested percentile of the deterministic Monte Carlo distribution."]

    floor_value = n((cases.get("BEAR") or {}).get("liquidation_floor"))
    if no_manual and floor_value is not None and floor_value > 0 and n(scenarios["BEAR"].get("fair_value")) is not None:
        if scenarios["BEAR"]["fair_value"] < floor_value:
            scenarios["BEAR"]["fair_value"] = floor_value
            scenarios["BEAR"]["flags"] = list(scenarios["BEAR"].get("flags") or []) + ["Conservative liquidation floor bound the Bear percentile."]

    order_guard_applied = False
    if no_manual and all(n(scenarios[name].get("fair_value")) is not None for name in ("BEAR", "BASE", "BULL")):
        original = [n(scenarios[name].get("fair_value")) for name in ("BEAR", "BASE", "BULL")]
        ordered = sorted(original)
        if original != ordered:
            order_guard_applied = True
            for name, value in zip(("BEAR", "BASE", "BULL"), ordered):
                scenarios[name]["fair_value"] = value
                scenarios[name]["quality"] = "DATA_WARNING"
                scenarios[name]["flags"] = list(scenarios[name].get("flags") or []) + [
                    "DATA WARNING: deterministic scenario inversion detected. Values were ordered for display only and are not decision-grade until the company-history distribution resolves the ordering."
                ]
        # A floor can legitimately collapse Bear and Base to the same liquidation value.
        if scenarios["BASE"]["fair_value"] < scenarios["BEAR"]["fair_value"]:
            scenarios["BASE"]["fair_value"] = scenarios["BEAR"]["fair_value"]
            order_guard_applied = True
        if scenarios["BULL"]["fair_value"] < scenarios["BASE"]["fair_value"]:
            scenarios["BULL"]["fair_value"] = scenarios["BASE"]["fair_value"]
            order_guard_applied = True

    for name in ("BEAR", "BASE", "BULL"):
        fair = n(scenarios[name].get("fair_value"))
        scenarios[name]["gap_pct"] = ((fair / price - 1.0) * 100.0) if fair is not None and price not in (None, 0) else None

    total_prob = sum(max(0.0, row["probability"]) for row in scenarios.values())
    expected = None
    if total_prob > 0 and all(row.get("fair_value") is not None for row in scenarios.values()):
        expected = sum(row["fair_value"] * max(0.0, row["probability"]) for row in scenarios.values()) / total_prob

    qualities = {str(row.get("quality") or "DATA_WARNING") for row in scenarios.values()}
    if qualities == {"INTRINSIC"}:
        overall_quality = "INTRINSIC"
    elif "INTRINSIC_SINGLE_METHOD" in qualities:
        overall_quality = "INTRINSIC_SINGLE_METHOD"
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
    move = n(metrics.get("market_move_since_filing_pct"))
    if move is not None and abs(move) >= 30.0:
        warnings.append(
            f"DATA DESYNCHRONIZATION SHIELD: market price moved {move:+.1f}% since the latest filing anchor; simulation ranges were widened 25% because the market may be pricing information not yet visible in filed fundamentals."
        )
    if order_guard_applied:
        warnings.append("Scenario-order integrity guard prevented an automatic Bear/Base/Bull inversion.")
    warnings = list(dict.fromkeys(str(item) for item in warnings if item))
    return {
        "scenarios": scenarios,
        "expected_value": expected,
        "current_price": price,
        "engine_version": ENGINE_VERSION,
        "quality": overall_quality,
        "warnings": warnings,
        "company_quality": metrics.get("company_quality") or {},
        "valuation_policy": policy,
        "valuation_impact_ledger": list(policy.get("ledger") or []),
        "method_exclusions": sorted(exclusions),
        "effective_input_weights": canonical_weights,
        "monte_carlo": distribution,
        "scenario_order_guard_applied": order_guard_applied,
        "life_cycle": (cases.get("BASE") or {}).get("life_cycle"),
        "solvency_state": (cases.get("BASE") or {}).get("solvency_state"),
        "integrity_notes": list((cases.get("BASE") or {}).get("integrity_notes") or []),
    }

__all__ = [
    "ENGINE_VERSION", "TYPE_PRIORS", "DECISION_GRADE_QUALITIES", "canonical_valuation_quality",
    "valuation_base_quality", "valuation_is_decision_grade", "stored_model_base_quality", "infer_company_type", "metrics_from_history", "calibrate_multiples",
    "default_cases", "pe_value", "p_sales_value", "ev_ebitda_value", "ev_sales_value", "fcf_yield_value", "dcf_value", "robust_blend",
    "scenario_value", "evaluate", "n", "clamp", "quantile",
]
