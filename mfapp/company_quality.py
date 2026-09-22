from __future__ import annotations

"""Deterministic company-quality and red-flag interpretation.

This is not a stock score and it is not a valuation model.  It answers a
different question: what does the filed operating/economic evidence say about
the quality and fragility of the company itself?

The engine is deliberately dimension-based.  A final state is derived from
explicit dimension states and red flags; there is no hidden weighted score.
"""

from math import isfinite
from statistics import median
from typing import Any

from .economic_reality import economic_from_row, has_suppression, metric as economic_metric

ENGINE_VERSION = "0.3.0"
STATES = {"STRONG", "SOUND", "WATCH", "RED_FLAG", "UNKNOWN"}


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _ratio(a: Any, b: Any) -> float | None:
    x, y = n(a), n(b)
    if x is None or y in (None, 0):
        return None
    return x / y


def _growth(current: Any, prior: Any) -> float | None:
    ratio = _ratio(current, prior)
    return (ratio - 1.0) * 100.0 if ratio is not None else None


def _margin(row: dict[str, Any], field: str) -> float | None:
    value = _ratio(row.get(field), row.get("revenue"))
    return value * 100.0 if value is not None else None


def _chronological(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in history if n(row.get("revenue")) is not None]
    rows.sort(key=lambda row: (
        str(row.get("period_end") or ""),
        int(row.get("fiscal_year") or 0),
    ))
    return rows


def _dim(key: str, label: str, state: str, detail: str, evidence: list[str] | None = None) -> dict[str, Any]:
    state = state if state in STATES else "UNKNOWN"
    return {
        "key": key,
        "label": label,
        "state": state,
        "detail": detail,
        "evidence": [str(x) for x in (evidence or []) if x],
    }


def _alarm(code: str, severity: str, detail: str, dimension: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail, "dimension": dimension}


def _strength(code: str, detail: str, dimension: str) -> dict[str, str]:
    return {"code": code, "detail": detail, "dimension": dimension}


def _recent_growth(rows: list[dict[str, Any]], count: int = 4) -> list[float]:
    values: list[float] = []
    for prior, current in zip(rows[-(count + 1):], rows[-count:]):
        value = _growth(current.get("revenue"), prior.get("revenue"))
        if value is not None:
            values.append(value)
    return values


def _share_change(rows: list[dict[str, Any]], years: int = 3) -> float | None:
    usable = []
    for row in rows:
        shares = n(row.get("diluted_shares")) or n(row.get("shares_outstanding"))
        if shares not in (None, 0):
            usable.append(shares)
    if len(usable) < 2:
        return None
    start = usable[max(0, len(usable) - 1 - years)]
    end = usable[-1]
    return (end / start - 1.0) * 100.0 if start else None


def build_company_quality(history: list[dict[str, Any]], company_type: str = "Generic") -> dict[str, Any]:
    rows = _chronological(history)
    if not rows:
        return {
            "engine_version": ENGINE_VERSION,
            "state": "INSUFFICIENT EVIDENCE",
            "headline": "Company quality cannot be established from the stored filing history.",
            "dimensions": [],
            "alarm_bells": [],
            "strengths": [],
            "coverage": 0,
            "valuation_policy": _valuation_policy([], "INSUFFICIENT EVIDENCE", {}),
        }

    latest = rows[-1]
    prior = rows[-2] if len(rows) > 1 else {}
    economic = economic_from_row(latest)
    economic_available = bool(economic)
    sector = str(company_type or "").lower()
    financial_sector = any(token in sector for token in ("financial", "bank", "insurance", "reit"))

    revenue_growth = _growth(latest.get("revenue"), prior.get("revenue"))
    growths = _recent_growth(rows)
    recent_declines = sum(1 for value in growths[-3:] if value < 0)
    three_year_growth = median(growths[-3:]) if growths[-3:] else None

    op_margin = _margin(latest, "operating_income")
    prior_op_margin = _margin(prior, "operating_income") if prior else None
    op_delta_bps = (
        (op_margin - prior_op_margin) * 100.0
        if op_margin is not None and prior_op_margin is not None else None
    )

    fcf_values = [n(row.get("fcf")) for row in rows[-3:]]
    fcf_known = [value for value in fcf_values if value is not None]
    fcf_positive_count = sum(1 for value in fcf_known if value > 0)
    cfo_to_ni = _ratio(latest.get("cfo"), latest.get("net_income"))
    latest_fcf = n(latest.get("fcf"))
    after_sbc = economic_metric(economic, "fcf_after_sbc")
    owner_cash = economic_metric(economic, "owner_cash_proxy")
    growth_capex = economic_metric(economic, "growth_capex_proxy")
    capex = n(latest.get("capex"))
    growth_capex_share = (
        growth_capex / capex * 100.0
        if growth_capex is not None and capex not in (None, 0) else None
    )

    economic_roic = economic_metric(economic, "economic_roic_pct")
    lease_roic = economic_metric(economic, "lease_adjusted_roic_pct")
    economic_net_debt = economic_metric(economic, "economic_net_debt")
    leverage = (
        economic_net_debt / latest_fcf
        if economic_net_debt is not None and latest_fcf not in (None, 0) and latest_fcf > 0 else None
    )
    fixed_coverage = economic_metric(economic, "fixed_charge_coverage_proxy_x")
    lease_current_to_cfo = economic_metric(economic, "operating_lease_current_to_cfo_x")
    share_change = _share_change(rows)

    payout = None
    distributions = sum(abs(n(latest.get(key)) or 0.0) for key in ("buybacks", "dividends"))
    if latest_fcf not in (None, 0):
        payout = distributions / abs(latest_fcf)

    latest_metrics = dict(latest.get("metrics") or {})
    inventory_divergence = None
    receivables_divergence = None
    if not financial_sector:
        inv_growth = _growth(latest.get("inventory"), prior.get("inventory"))
        rec_growth = _growth(latest.get("receivables"), prior.get("receivables"))
        if inv_growth is not None and revenue_growth is not None:
            inventory_divergence = inv_growth - revenue_growth
        if rec_growth is not None and revenue_growth is not None:
            receivables_divergence = rec_growth - revenue_growth

    dimensions: list[dict[str, Any]] = []
    alarms: list[dict[str, str]] = []
    strengths: list[dict[str, str]] = []

    # 1. Operating durability.
    if revenue_growth is None and three_year_growth is None and op_margin is None:
        dimensions.append(_dim("durability", "Operating durability", "UNKNOWN", "Revenue/margin history is insufficient."))
    elif recent_declines >= 2 and (revenue_growth is None or revenue_growth < 0):
        detail = f"Revenue declined in {recent_declines} of the last {min(3, len(growths))} comparable periods."
        dimensions.append(_dim("durability", "Operating durability", "RED_FLAG", detail))
        alarms.append(_alarm("REPEATED_REVENUE_CONTRACTION", "RED", detail, "durability"))
    elif (revenue_growth is not None and revenue_growth <= -8.0) or (op_delta_bps is not None and op_delta_bps <= -300):
        detail = f"Current growth {revenue_growth:+.1f}% and margin change {op_delta_bps:+.0f} bps." if revenue_growth is not None and op_delta_bps is not None else "Material operating deterioration is visible."
        dimensions.append(_dim("durability", "Operating durability", "WATCH", detail))
        alarms.append(_alarm("OPERATING_DURABILITY_PRESSURE", "AMBER", detail, "durability"))
    elif three_year_growth is not None and three_year_growth >= 5.0 and recent_declines == 0 and (op_delta_bps is None or op_delta_bps >= -100):
        detail = f"Median recent revenue growth {three_year_growth:+.1f}% with no recent contraction and stable operating margin."
        dimensions.append(_dim("durability", "Operating durability", "STRONG", detail))
        strengths.append(_strength("CONSISTENT_GROWTH", detail, "durability"))
    else:
        detail = f"Current revenue growth {revenue_growth:+.1f}%." if revenue_growth is not None else "Operating history is broadly stable."
        dimensions.append(_dim("durability", "Operating durability", "SOUND", detail))

    # 2. Profitability / return on capital.
    if financial_sector:
        dimensions.append(_dim("returns", "Profitability & returns", "UNKNOWN", "Generic industrial ROIC is not used for Financial / REIT companies."))
    elif economic_roic is not None and economic_roic < 0:
        detail = f"Economic ROIC is {economic_roic:.1f}%."
        dimensions.append(_dim("returns", "Profitability & returns", "RED_FLAG", detail))
        alarms.append(_alarm("NEGATIVE_ECONOMIC_ROIC", "RED", detail, "returns"))
    elif op_margin is not None and op_margin < 0 and (revenue_growth is None or revenue_growth <= 0):
        detail = f"Operating margin is {op_margin:.1f}% without offsetting top-line growth."
        dimensions.append(_dim("returns", "Profitability & returns", "RED_FLAG", detail))
        alarms.append(_alarm("LOSS_MAKING_CORE", "RED", detail, "returns"))
    elif (
        economic_roic is not None and economic_roic >= 15.0
        and (lease_roic is None or lease_roic >= 8.0)
        and (op_margin is None or op_margin > 0)
    ):
        detail = f"Economic ROIC {economic_roic:.1f}%" + (f"; ROIC incl. lease capital {lease_roic:.1f}%." if lease_roic is not None else ".")
        dimensions.append(_dim("returns", "Profitability & returns", "STRONG", detail))
        strengths.append(_strength("HIGH_CAPITAL_RETURNS", detail, "returns"))
    elif (
        (economic_roic is not None and economic_roic < 6.0)
        or (lease_roic is not None and lease_roic < 5.0)
    ):
        detail = f"Economic ROIC {economic_roic:.1f}%." if economic_roic is not None else f"ROIC incl. lease capital {lease_roic:.1f}%."
        dimensions.append(_dim("returns", "Profitability & returns", "WATCH", detail))
        alarms.append(_alarm("LOW_CAPITAL_RETURNS", "AMBER", detail, "returns"))
    elif economic_roic is not None or op_margin is not None:
        detail = f"Economic ROIC {economic_roic:.1f}%." if economic_roic is not None else f"Operating margin {op_margin:.1f}%."
        dimensions.append(_dim("returns", "Profitability & returns", "SOUND", detail))
    else:
        dimensions.append(_dim("returns", "Profitability & returns", "UNKNOWN", "Return-on-capital evidence is unresolved."))

    # 3. Cash quality.
    growth_reinvestment_context = has_suppression(economic, "NEGATIVE_FCF_AUTOMATIC")
    if not fcf_known and cfo_to_ni is None:
        dimensions.append(_dim("cash_quality", "Cash quality", "UNKNOWN", "Cash conversion evidence is insufficient."))
    elif len(fcf_known) >= 2 and fcf_positive_count == 0 and not growth_reinvestment_context:
        detail = "Free cash flow has remained negative across the recent filed history without a material growth-capex context."
        dimensions.append(_dim("cash_quality", "Cash quality", "RED_FLAG", detail))
        alarms.append(_alarm("PERSISTENT_NEGATIVE_FCF", "RED", detail, "cash_quality"))
    elif cfo_to_ni is not None and cfo_to_ni < 0.5 and not growth_reinvestment_context:
        detail = f"CFO / net income is only {cfo_to_ni:.2f}x."
        dimensions.append(_dim("cash_quality", "Cash quality", "RED_FLAG", detail))
        alarms.append(_alarm("WEAK_CASH_CONVERSION", "RED", detail, "cash_quality"))
    elif after_sbc is not None and after_sbc < 0 < (latest_fcf or 0):
        detail = "Reported FCF is positive but turns negative after material share-based compensation."
        dimensions.append(_dim("cash_quality", "Cash quality", "WATCH", detail))
        alarms.append(_alarm("FCF_DEPENDS_ON_SBC", "AMBER", detail, "cash_quality"))
    elif growth_reinvestment_context and owner_cash is not None and owner_cash > 0:
        detail = f"Reported FCF includes material growth reinvestment; owner-cash proxy remains positive at {owner_cash:,.0f}."
        dimensions.append(_dim("cash_quality", "Cash quality", "SOUND", detail))
        strengths.append(_strength("GROWTH_REINVESTMENT_FUNDED", detail, "cash_quality"))
    elif len(fcf_known) >= 3 and fcf_positive_count == len(fcf_known) and (cfo_to_ni is None or cfo_to_ni >= 0.9):
        detail = f"FCF is positive in {fcf_positive_count}/{len(fcf_known)} recent periods" + (f" and CFO / NI is {cfo_to_ni:.2f}x." if cfo_to_ni is not None else ".")
        dimensions.append(_dim("cash_quality", "Cash quality", "STRONG", detail))
        strengths.append(_strength("CONSISTENT_CASH_CONVERSION", detail, "cash_quality"))
    elif cfo_to_ni is not None and cfo_to_ni < 0.75:
        detail = f"CFO / net income is {cfo_to_ni:.2f}x."
        dimensions.append(_dim("cash_quality", "Cash quality", "WATCH", detail))
        alarms.append(_alarm("CASH_CONVERSION_WATCH", "AMBER", detail, "cash_quality"))
    else:
        dimensions.append(_dim("cash_quality", "Cash quality", "SOUND", "Cash conversion is not showing a material filed red flag."))

    # 4. Balance-sheet / fixed-charge resilience.
    if not economic_available:
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "UNKNOWN", "Economic Reality has not been materialized on this filing basis."))
    elif financial_sector:
        detail = "Generic industrial leverage/fixed-charge rules are disabled; sector-specific capital/liquidity evidence is required."
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "UNKNOWN", detail))
    elif bool(economic.get("material_unresolved")):
        detail = "Material financing/accounting classification remains unresolved."
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "RED_FLAG", detail))
        alarms.append(_alarm("ECONOMIC_DEBT_UNRESOLVED", "RED", detail, "balance_sheet"))
    elif leverage is not None and leverage >= 4.0:
        detail = f"Economic net debt is {leverage:.1f}x current FCF."
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "RED_FLAG", detail))
        alarms.append(_alarm("HIGH_ECONOMIC_LEVERAGE", "RED", detail, "balance_sheet"))
    elif fixed_coverage is not None and fixed_coverage < 1.5:
        detail = f"Fixed-charge coverage proxy is only {fixed_coverage:.2f}x."
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "RED_FLAG", detail))
        alarms.append(_alarm("WEAK_FIXED_CHARGE_COVERAGE", "RED", detail, "balance_sheet"))
    elif (
        (leverage is not None and leverage >= 2.5)
        or (fixed_coverage is not None and fixed_coverage < 2.5)
        or (lease_current_to_cfo is not None and lease_current_to_cfo > 0.70)
    ):
        parts = []
        if leverage is not None: parts.append(f"net debt/FCF {leverage:.1f}x")
        if fixed_coverage is not None: parts.append(f"fixed-charge coverage {fixed_coverage:.2f}x")
        if lease_current_to_cfo is not None: parts.append(f"current lease/CFO {lease_current_to_cfo:.2f}x")
        detail = "; ".join(parts) + "."
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "WATCH", detail))
        alarms.append(_alarm("FIXED_CHARGE_WATCH", "AMBER", detail, "balance_sheet"))
    elif economic_net_debt is not None and economic_net_debt <= 0 and (fixed_coverage is None or fixed_coverage >= 3.0):
        detail = "Economic net cash / no net financing debt with no weak fixed-charge signal."
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "STRONG", detail))
        strengths.append(_strength("BALANCE_SHEET_RESILIENCE", detail, "balance_sheet"))
    elif economic_net_debt is not None or fixed_coverage is not None:
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "SOUND", "Financing and fixed-charge burden are within the automatic watch thresholds."))
    else:
        dimensions.append(_dim("balance_sheet", "Balance-sheet resilience", "UNKNOWN", "Economic debt/fixed-charge evidence is insufficient."))

    # 5. Reinvestment efficiency.
    if not economic_available:
        dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "UNKNOWN", "Economic capex/return classification is not materialized yet."))
    elif financial_sector:
        dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "UNKNOWN", "Industrial capex/ROIC reinvestment rules are not applied to Financial / REIT companies."))
    elif growth_capex_share is not None and growth_capex_share >= 25.0:
        if economic_roic is not None and economic_roic >= 12.0 and (revenue_growth is None or revenue_growth >= 3.0):
            detail = f"Growth-capex proxy is {growth_capex_share:.0f}% of capex while economic ROIC is {economic_roic:.1f}%."
            dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "STRONG", detail))
            strengths.append(_strength("PRODUCTIVE_REINVESTMENT", detail, "reinvestment"))
        elif economic_roic is not None and economic_roic <= 5.0 and (revenue_growth is None or revenue_growth <= 2.0):
            detail = f"Growth-capex proxy is {growth_capex_share:.0f}% of capex but economic ROIC is only {economic_roic:.1f}%."
            dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "RED_FLAG", detail))
            alarms.append(_alarm("LOW_RETURN_REINVESTMENT", "RED", detail, "reinvestment"))
        elif economic_roic is None:
            detail = f"Growth-capex proxy is {growth_capex_share:.0f}% of capex, but returns on that capital are unresolved."
            dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "WATCH", detail))
            alarms.append(_alarm("GROWTH_CAPEX_RETURN_UNPROVEN", "AMBER", detail, "reinvestment"))
        else:
            detail = f"Growth-capex proxy is {growth_capex_share:.0f}% of capex; observed return evidence is neither clearly strong nor destructive."
            dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "SOUND", detail))
    elif economic_roic is not None:
        dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "SOUND", f"No large growth-capex distortion; economic ROIC is {economic_roic:.1f}%."))
    else:
        dimensions.append(_dim("reinvestment", "Reinvestment efficiency", "UNKNOWN", "Reinvestment return evidence is insufficient."))

    # 6. Capital allocation / dilution.
    if share_change is not None and share_change >= 15.0:
        detail = f"Diluted/share count increased {share_change:+.1f}% over the available multi-year window."
        dimensions.append(_dim("capital_allocation", "Capital allocation", "RED_FLAG", detail))
        alarms.append(_alarm("MATERIAL_DILUTION", "RED", detail, "capital_allocation"))
    elif not financial_sector and payout is not None and payout > 1.50 and economic_net_debt is not None and economic_net_debt > 0:
        detail = f"Buybacks + dividends are {payout:.2f}x current FCF while economic net debt is positive."
        dimensions.append(_dim("capital_allocation", "Capital allocation", "RED_FLAG", detail))
        alarms.append(_alarm("DEBT_FUNDED_DISTRIBUTION_RISK", "RED", detail, "capital_allocation"))
    elif (share_change is not None and share_change >= 5.0) or (not financial_sector and payout is not None and payout > 1.10 and economic_net_debt is not None and economic_net_debt > 0):
        detail = f"Share count change {share_change:+.1f}%." if share_change is not None else f"Distributions are {payout:.2f}x FCF with positive economic net debt."
        dimensions.append(_dim("capital_allocation", "Capital allocation", "WATCH", detail))
        alarms.append(_alarm("CAPITAL_ALLOCATION_WATCH", "AMBER", detail, "capital_allocation"))
    elif share_change is not None and share_change <= -3.0 and economic_net_debt is not None and economic_net_debt <= 0:
        detail = f"Share count declined {share_change:.1f}% while the balance sheet remains net-cash."
        dimensions.append(_dim("capital_allocation", "Capital allocation", "STRONG", detail))
        strengths.append(_strength("DISCIPLINED_CAPITAL_RETURN", detail, "capital_allocation"))
    elif share_change is not None or payout is not None:
        dimensions.append(_dim("capital_allocation", "Capital allocation", "SOUND", "No material dilution or debt-funded distribution flag is visible."))
    else:
        dimensions.append(_dim("capital_allocation", "Capital allocation", "UNKNOWN", "Capital-allocation evidence is insufficient."))

    # 7. Accounting quality / distortions.
    review_flags = [flag for flag in economic.get("flags") or [] if str(flag.get("tone") or "").upper() == "REVIEW"]
    caution_flags = [flag for flag in economic.get("flags") or [] if str(flag.get("tone") or "").upper() == "CAUTION"]
    restated = bool((latest.get("quality") or {}).get("restated"))
    wc_red = (
        (inventory_divergence is not None and inventory_divergence >= 25.0)
        or (receivables_divergence is not None and receivables_divergence >= 25.0)
    )
    wc_watch = (
        (inventory_divergence is not None and inventory_divergence >= 12.0)
        or (receivables_divergence is not None and receivables_divergence >= 12.0)
    )
    if not economic_available:
        dimensions.append(_dim("accounting_quality", "Accounting quality", "UNKNOWN", "Economic Reality has not been materialized, so accounting-quality classification is not decision-ready."))
    elif bool(economic.get("material_unresolved")):
        detail = "Economic Reality contains a material unresolved classification."
        dimensions.append(_dim("accounting_quality", "Accounting quality", "RED_FLAG", detail))
        alarms.append(_alarm("MATERIAL_ACCOUNTING_UNRESOLVED", "RED", detail, "accounting_quality"))
    elif restated and review_flags:
        detail = "Latest basis is restated and material accounting-review flags are active."
        dimensions.append(_dim("accounting_quality", "Accounting quality", "RED_FLAG", detail))
        alarms.append(_alarm("RESTATEMENT_PLUS_REVIEW", "RED", detail, "accounting_quality"))
    elif wc_red:
        detail = "Working-capital balances are diverging materially from revenue."
        dimensions.append(_dim("accounting_quality", "Accounting quality", "RED_FLAG", detail))
        alarms.append(_alarm("WORKING_CAPITAL_DIVERGENCE", "RED", detail, "accounting_quality"))
    elif review_flags or wc_watch:
        details = [str(flag.get("detail") or "") for flag in review_flags[:2]]
        if wc_watch:
            details.append("Inventory/receivables growth materially exceeds revenue.")
        detail = " ".join(x for x in details if x) or "Accounting-review evidence is active."
        dimensions.append(_dim("accounting_quality", "Accounting quality", "WATCH", detail))
        alarms.append(_alarm("ACCOUNTING_REVIEW", "AMBER", detail, "accounting_quality"))
    elif caution_flags:
        detail = "Accounting distortions are identified and classified, with no current material unresolved item."
        dimensions.append(_dim("accounting_quality", "Accounting quality", "SOUND", detail))
    else:
        detail = "No material automatic accounting-distortion flag is unresolved."
        dimensions.append(_dim("accounting_quality", "Accounting quality", "STRONG", detail))
        strengths.append(_strength("CLEAN_ACCOUNTING_SIGNAL", detail, "accounting_quality"))

    resolved = [row for row in dimensions if row["state"] != "UNKNOWN"]
    red = [row for row in dimensions if row["state"] == "RED_FLAG"]
    watch = [row for row in dimensions if row["state"] == "WATCH"]
    strong = [row for row in dimensions if row["state"] == "STRONG"]
    coverage = round(len(resolved) / len(dimensions) * 100.0) if dimensions else 0

    if not economic_available:
        state = "INSUFFICIENT EVIDENCE"
    elif len(resolved) < 4:
        state = "INSUFFICIENT EVIDENCE"
    elif bool(economic.get("material_unresolved")):
        state = "UNRESOLVED"
    elif len(red) >= 2:
        state = "FRAGILE"
    elif len(red) == 1 or len(watch) >= 3:
        state = "MIXED"
    elif len(strong) >= 4 and len(watch) <= 1:
        state = "STRONG"
    else:
        state = "SOUND"

    headline_map = {
        "STRONG": "Filed evidence describes a high-quality economic engine with no major automatic red flag.",
        "SOUND": "Filed evidence describes a fundamentally sound company, with specific items still worth monitoring.",
        "MIXED": "The company has credible strengths, but material weaknesses prevent a clean quality conclusion.",
        "FRAGILE": "Multiple fundamental red flags are visible; the business economics require a defensive read.",
        "UNRESOLVED": "A material accounting/financing classification is unresolved, so company quality is not decision-ready.",
        "INSUFFICIENT EVIDENCE": "The stored evidence is insufficient to judge company quality with confidence.",
    }

    profile = {
        "engine_version": ENGINE_VERSION,
        "state": state,
        "headline": headline_map[state],
        "dimensions": dimensions,
        "alarm_bells": alarms,
        "strengths": strengths,
        "coverage": coverage,
        "red_flag_count": len(red),
        "watch_count": len(watch),
        "strong_count": len(strong),
        "current_metrics": {
            "revenue_growth_pct": revenue_growth,
            "operating_margin_pct": op_margin,
            "operating_margin_delta_bps": op_delta_bps,
            "economic_roic_pct": economic_roic,
            "lease_adjusted_roic_pct": lease_roic,
            "cfo_to_net_income": cfo_to_ni,
            "economic_net_debt": economic_net_debt,
            "net_debt_to_fcf": leverage,
            "fixed_charge_coverage_proxy_x": fixed_coverage,
            "growth_capex_share_pct": growth_capex_share,
            "share_change_pct": share_change,
        },
    }
    profile["valuation_policy"] = _valuation_policy(dimensions, state, economic)
    return profile


def _valuation_policy(dimensions: list[dict[str, Any]], state: str, economic: dict[str, Any]) -> dict[str, Any]:
    """Translate quality weaknesses into bounded valuation risk adjustments.

    Positive quality never creates an automatic valuation premium.  It is
    already expressed through actual growth, margins, returns and cash flows.
    Only evidenced downside risk adds conservative haircuts.
    """
    by_key = {row.get("key"): row.get("state") for row in dimensions}
    risk_premium_bps = 0
    growth_haircut_bps = 0
    terminal_haircut_bps = 0
    ledger: list[dict[str, Any]] = []

    def risk(key: str, red_bps: int, watch_bps: int, *, growth_red: int = 0, growth_watch: int = 0, terminal_red: int = 0, terminal_watch: int = 0) -> None:
        nonlocal risk_premium_bps, growth_haircut_bps, terminal_haircut_bps
        status = by_key.get(key)
        if status == "RED_FLAG":
            risk_premium_bps += red_bps
            growth_haircut_bps += growth_red
            terminal_haircut_bps += terminal_red
        elif status == "WATCH":
            risk_premium_bps += watch_bps
            growth_haircut_bps += growth_watch
            terminal_haircut_bps += terminal_watch

    risk("balance_sheet", 125, 50, terminal_red=25)
    risk("cash_quality", 75, 25, terminal_red=25)
    risk("durability", 75, 25, growth_red=200, growth_watch=75, terminal_red=25)
    risk("reinvestment", 50, 25, growth_red=100, growth_watch=50, terminal_red=25)
    risk("capital_allocation", 50, 25)
    risk("accounting_quality", 75, 25)

    risk_premium_bps = min(300, risk_premium_bps)
    growth_haircut_bps = min(300, growth_haircut_bps)
    terminal_haircut_bps = min(100, terminal_haircut_bps)

    if risk_premium_bps:
        ledger.append({
            "item": "Company quality risk premium",
            "effect": "DISCOUNT_RATE",
            "impact": f"+{risk_premium_bps} bps to auto Bear/Base/Bull discount rates",
            "reason": "Bounded downside-only adjustment from explicit WATCH/RED_FLAG company-quality dimensions.",
        })
    if growth_haircut_bps:
        ledger.append({
            "item": "Durability / reinvestment haircut",
            "effect": "GROWTH",
            "impact": f"-{growth_haircut_bps} bps from auto forward revenue-growth assumptions",
            "reason": "Observed durability/reinvestment weakness is reflected in forward assumptions instead of a hidden score.",
        })
    if terminal_haircut_bps:
        ledger.append({
            "item": "Long-run quality haircut",
            "effect": "TERMINAL_GROWTH",
            "impact": f"-{terminal_haircut_bps} bps from auto terminal growth",
            "reason": "Weak durability, cash conversion, reinvestment or fixed-charge resilience reduces long-run economics.",
        })

    suppressions = set(economic.get("suppressions") or [])
    method_exclusions: list[str] = []
    if bool(economic.get("material_unresolved")):
        method_exclusions.append("ev_sales")
        ledger.append({
            "item": "Unresolved economic debt bridge",
            "effect": "METHOD_EXCLUSION",
            "impact": "EV/Sales excluded",
            "reason": "Enterprise-to-equity bridge is not trusted until financing classification is resolved.",
        })
    if "PE_EARNINGS_NORMALIZATION_REVIEW" in suppressions:
        method_exclusions.append("pe")
        ledger.append({
            "item": "Earnings normalization review",
            "effect": "METHOD_EXCLUSION",
            "impact": "P/E excluded when clean earnings calibration is unavailable",
            "reason": "Tax/non-operating distortions must not create a fake earnings multiple signal.",
        })
    if "FCF_POSITIVE_UNADJUSTED" in suppressions:
        ledger.append({
            "item": "Share-based compensation",
            "effect": "DILUTED_SHARE_DENOMINATOR",
            "impact": "Observed diluted-share growth is projected in per-share valuation; reported FCF is not reduced a second time",
            "reason": "Material SBC is an economic shareholder cost, but subtracting SBC from cash flow and also projecting dilution would double count it.",
        })

    operating_leases = economic_metric(economic, "operating_lease_liability")
    if operating_leases is not None:
        ledger.append({
            "item": "Operating leases",
            "effect": "CLASSIFICATION_AND_RISK",
            "impact": "Excluded from financial net debt; fixed-charge burden remains in quality/risk analysis",
            "reason": "Avoids double counting lease economics while retaining the contractual obligation.",
        })
    if economic_metric(economic, "growth_capex_proxy") is not None:
        ledger.append({
            "item": "Growth-capex proxy",
            "effect": "CROSS_CHECK_ONLY",
            "impact": "Does not replace reported FCF automatically",
            "reason": "Maintenance/growth split is a diagnostic estimate; reinvestment quality can affect risk/growth assumptions instead.",
        })
    if economic_metric(economic, "rd_to_revenue_pct") is not None:
        ledger.append({
            "item": "R&D intensity",
            "effect": "CONTEXT_ONLY",
            "impact": "No automatic capitalization uplift",
            "reason": "GAAP expensing can distort margins/book capital, but useful-life assumptions are company-specific and must not be invented.",
        })

    bear_shift = 10 if state == "FRAGILE" else 5 if state in {"MIXED", "UNRESOLVED"} else 0
    if bear_shift:
        ledger.append({
            "item": "Scenario probability discipline",
            "effect": "PROBABILITY",
            "impact": f"+{bear_shift} pts Bear probability in auto-generated cases",
            "reason": "Material company-quality weakness shifts probability toward downside; strong quality never receives an automatic Bull uplift.",
        })

    return {
        "risk_premium_bps": risk_premium_bps,
        "growth_haircut_bps": growth_haircut_bps,
        "terminal_growth_haircut_bps": terminal_haircut_bps,
        "bear_probability_shift_pts": bear_shift,
        "method_exclusions": sorted(set(method_exclusions)),
        "ledger": ledger,
        "positive_quality_uplift": False,
    }


__all__ = ["ENGINE_VERSION", "build_company_quality"]
