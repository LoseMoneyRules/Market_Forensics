from __future__ import annotations

"""Fundamentals forensic read for the Research Fundamentals page.

This module does not fetch data and does not create a new scoring system.
It interprets already-normalized filing data, checks deterministic accounting
relationships, and organizes the current evidence into strengths, watches,
red flags, inconsistencies and data gaps.
"""

from math import isfinite
from statistics import mean
from typing import Any

from .company_quality import build_company_quality
from .economic_reality import economic_from_row, metric as economic_metric


ENGINE_VERSION = "0.3.0"


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _pct_change(current: Any, prior: Any) -> float | None:
    a, b = n(current), n(prior)
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def _delta(current: Any, prior: Any) -> float | None:
    a, b = n(current), n(prior)
    return a - b if a is not None and b is not None else None


def _years_ago(rows: list[dict[str, Any]], years: int) -> dict[str, Any] | None:
    if len(rows) <= years:
        return None
    return rows[-1 - years]


def _cagr(current: Any, prior: Any, years: int) -> float | None:
    a, b = n(current), n(prior)
    if years <= 0 or a is None or b is None or a <= 0 or b <= 0:
        return None
    return ((a / b) ** (1.0 / years) - 1.0) * 100.0


def _item(code: str, label: str, detail: str, category: str, severity: str, *, value: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "detail": detail,
        "category": category,
        "severity": severity,
        "value": value,
    }


def _identity_check(
    out: list[dict[str, Any]],
    *,
    code: str,
    label: str,
    expected: float | None,
    actual: float | None,
    scale: float | None,
    detail: str,
    warn_pct: float = 1.5,
) -> None:
    if expected is None or actual is None:
        return
    denom = max(abs(scale or 0.0), abs(expected), abs(actual), 1.0)
    diff = actual - expected
    diff_pct = abs(diff) / denom * 100.0
    if diff_pct >= warn_pct:
        out.append(_item(
            code, label,
            f"{detail} Difference {diff:,.0f} ({diff_pct:.2f}% of comparison scale).",
            "accounting", "REVIEW", value=diff_pct,
        ))


def build_fundamentals_forensics(
    history: list[dict[str, Any]],
    *,
    current: dict[str, Any] | None = None,
    completeness: dict[str, Any] | None = None,
    company_type: str = "Generic",
) -> dict[str, Any]:
    rows = [dict(row) for row in history if row]
    rows.sort(key=lambda row: str(row.get("period_end") or ""))
    current = dict(current or (rows[-1] if rows else {}))
    if current and (not rows or str(rows[-1].get("period_end") or "") != str(current.get("period_end") or "")):
        rows.append(current)
    completeness = dict(completeness or {})

    quality = build_company_quality(rows, company_type)
    strengths: list[dict[str, Any]] = []
    red_flags: list[dict[str, Any]] = []
    watches: list[dict[str, Any]] = []
    inconsistencies: list[dict[str, Any]] = []
    data_gaps: list[dict[str, Any]] = []
    trend_cards: list[dict[str, Any]] = []

    for row in quality.get("strengths") or []:
        strengths.append(_item(
            str(row.get("code") or "STRENGTH"),
            str(row.get("code") or "Strength").replace("_", " ").title(),
            str(row.get("detail") or ""),
            str(row.get("dimension") or "quality"),
            "STRENGTH",
        ))
    for row in quality.get("alarm_bells") or []:
        target = red_flags if str(row.get("severity") or "").upper() == "RED" else watches
        target.append(_item(
            str(row.get("code") or "WATCH"),
            str(row.get("code") or "Watch").replace("_", " ").title(),
            str(row.get("detail") or ""),
            str(row.get("dimension") or "quality"),
            "RED FLAG" if target is red_flags else "WATCH",
        ))

    if not current:
        data_gaps.append(_item(
            "NO_CURRENT_BASIS", "No current financial basis",
            "No FY/TTM normalized filing basis is stored. Refresh SEC before interpreting Fundamentals.",
            "data", "UNRESOLVED",
        ))
        return {
            "engine_version": ENGINE_VERSION,
            "state": "INSUFFICIENT EVIDENCE",
            "headline": "No current filing basis is available.",
            "company_quality": quality,
            "strengths": strengths,
            "red_flags": red_flags,
            "watches": watches,
            "inconsistencies": inconsistencies,
            "data_gaps": data_gaps,
            "trend_cards": trend_cards,
        }

    metrics = dict(current.get("metrics") or {})
    prior = rows[-2] if len(rows) >= 2 else {}
    prior_metrics = dict(prior.get("metrics") or {})
    three_year = _years_ago(rows, 3)
    three_metrics = dict((three_year or {}).get("metrics") or {})
    economic = economic_from_row(current)

    revenue = n(current.get("revenue"))
    revenue_growth = n(metrics.get("revenue_growth_pct"))
    revenue_cagr_3y = _cagr(current.get("revenue"), (three_year or {}).get("revenue"), 3) if three_year else None
    op_margin = n(metrics.get("operating_margin_pct"))
    op_margin_delta = _delta(op_margin, prior_metrics.get("operating_margin_pct"))
    op_margin_3y = _delta(op_margin, three_metrics.get("operating_margin_pct")) if three_year else None
    gross_margin = n(metrics.get("gross_margin_pct"))
    gross_delta = _delta(gross_margin, prior_metrics.get("gross_margin_pct"))
    fcf_margin = n(metrics.get("fcf_margin_pct"))
    fcf_delta = _delta(fcf_margin, prior_metrics.get("fcf_margin_pct"))
    cfo_ni = n(metrics.get("cfo_to_net_income"))
    roic = n(metrics.get("roic_pct"))
    shares_growth = n(metrics.get("share_count_growth_pct"))
    dso = n(metrics.get("dso"))
    dso_delta = _delta(dso, prior_metrics.get("dso"))
    ccc = n(metrics.get("cash_conversion_days"))
    ccc_delta = _delta(ccc, prior_metrics.get("cash_conversion_days"))
    inv_growth = n(metrics.get("inventory_growth_pct"))
    rec_growth = n(metrics.get("receivables_growth_pct"))
    inv_spread = inv_growth - revenue_growth if inv_growth is not None and revenue_growth is not None else None
    rec_spread = rec_growth - revenue_growth if rec_growth is not None and revenue_growth is not None else None
    net_debt_fcf = n(metrics.get("net_debt_to_fcf"))
    economic_net_debt = n(metrics.get("economic_net_debt"))

    def card(label: str, value: Any, read: str, detail: str) -> None:
        trend_cards.append({"label": label, "value": value, "read": read, "detail": detail})

    if revenue_cagr_3y is not None:
        read = "STRENGTH" if revenue_cagr_3y >= 8 else "WATCH" if revenue_cagr_3y < 0 else "NEUTRAL"
        card("3Y revenue CAGR", revenue_cagr_3y, read, "Annualized revenue change over the stored three-year comparison window.")
    elif revenue_growth is not None:
        read = "STRENGTH" if revenue_growth >= 8 else "WATCH" if revenue_growth < 0 else "NEUTRAL"
        card("Revenue growth", revenue_growth, read, "Current comparable-period revenue growth.")

    if op_margin is not None:
        read = "STRENGTH" if op_margin_delta is not None and op_margin_delta >= 1.5 else "WATCH" if op_margin_delta is not None and op_margin_delta <= -1.5 else "NEUTRAL"
        card("Operating margin", op_margin, read, f"Current margin; YoY change {op_margin_delta:+.1f} pts." if op_margin_delta is not None else "Current operating margin.")
    if fcf_margin is not None:
        read = "STRENGTH" if fcf_delta is not None and fcf_delta >= 2.0 else "WATCH" if fcf_delta is not None and fcf_delta <= -2.0 else "NEUTRAL"
        card("FCF margin", fcf_margin, read, f"Current margin; YoY change {fcf_delta:+.1f} pts." if fcf_delta is not None else "Current FCF margin.")
    if cfo_ni is not None:
        read = "STRENGTH" if cfo_ni >= 1.0 else "RED FLAG" if cfo_ni < 0.5 else "WATCH" if cfo_ni < 0.75 else "NEUTRAL"
        card("CFO / net income", cfo_ni, read, "Cash conversion of reported earnings.")
    if roic is not None:
        read = "STRENGTH" if roic >= 15 else "RED FLAG" if roic < 0 else "WATCH" if roic < 6 else "NEUTRAL"
        card("ROIC", roic, read, str(metrics.get("roic_basis") or "Current capital basis").replace("_", " ").title())
    if net_debt_fcf is not None:
        read = "STRENGTH" if net_debt_fcf <= 0 else "RED FLAG" if net_debt_fcf >= 4 else "WATCH" if net_debt_fcf >= 2.5 else "NEUTRAL"
        card("Economic net debt / FCF", net_debt_fcf, read, str(metrics.get("net_debt_basis") or "Economic Reality").replace("_", " "))
    if shares_growth is not None:
        read = "STRENGTH" if shares_growth <= -3 else "RED FLAG" if shares_growth >= 10 else "WATCH" if shares_growth >= 5 else "NEUTRAL"
        card("Share count YoY", shares_growth, read, "Dilution / buyback signal from filed share basis.")
    if ccc_delta is not None:
        read = "STRENGTH" if ccc_delta <= -10 else "WATCH" if ccc_delta >= 15 else "NEUTRAL"
        card("CCC change", ccc_delta, read, "Change in cash-conversion cycle days versus prior comparable basis.")

    # Additional operating signals not already guaranteed by Company Quality.
    if gross_delta is not None and gross_delta >= 2.0:
        strengths.append(_item("GROSS_MARGIN_EXPANSION", "Gross margin expansion", f"Gross margin expanded {gross_delta:+.1f} pts versus the prior comparable basis.", "profitability", "STRENGTH", value=gross_delta))
    elif gross_delta is not None and gross_delta <= -3.0:
        watches.append(_item("GROSS_MARGIN_COMPRESSION", "Gross margin compression", f"Gross margin fell {gross_delta:.1f} pts versus the prior comparable basis.", "profitability", "WATCH", value=gross_delta))

    if op_margin_3y is not None and op_margin_3y >= 3.0:
        strengths.append(_item("MULTIYEAR_OPERATING_LEVERAGE", "Multi-year operating leverage", f"Operating margin is {op_margin_3y:+.1f} pts above the three-year comparison basis.", "profitability", "STRENGTH", value=op_margin_3y))
    elif op_margin_3y is not None and op_margin_3y <= -4.0:
        red_flags.append(_item("MULTIYEAR_MARGIN_EROSION", "Multi-year margin erosion", f"Operating margin is {op_margin_3y:.1f} pts below the three-year comparison basis.", "profitability", "RED FLAG", value=op_margin_3y))

    if dso_delta is not None and dso_delta >= 12:
        watches.append(_item("DSO_DETERIORATION", "Collection cycle deterioration", f"DSO increased {dso_delta:+.1f} days versus the prior comparable basis.", "working_capital", "WATCH", value=dso_delta))
    elif dso_delta is not None and dso_delta <= -10:
        strengths.append(_item("DSO_IMPROVEMENT", "Collection cycle improvement", f"DSO improved {abs(dso_delta):.1f} days versus the prior comparable basis.", "working_capital", "STRENGTH", value=dso_delta))

    if ccc_delta is not None and ccc_delta >= 20:
        watches.append(_item("CCC_DETERIORATION", "Cash-conversion cycle deterioration", f"CCC lengthened {ccc_delta:+.1f} days versus the prior comparable basis.", "working_capital", "WATCH", value=ccc_delta))
    elif ccc_delta is not None and ccc_delta <= -15:
        strengths.append(_item("CCC_IMPROVEMENT", "Cash-conversion cycle improvement", f"CCC shortened {abs(ccc_delta):.1f} days versus the prior comparable basis.", "working_capital", "STRENGTH", value=ccc_delta))

    if inv_spread is not None and inv_spread >= 20:
        red_flags.append(_item("INVENTORY_REVENUE_DIVERGENCE", "Inventory / revenue divergence", f"Inventory grew {inv_spread:+.1f} pts faster than revenue.", "working_capital", "RED FLAG", value=inv_spread))
    elif inv_spread is not None and inv_spread >= 10:
        watches.append(_item("INVENTORY_REVENUE_WATCH", "Inventory build", f"Inventory grew {inv_spread:+.1f} pts faster than revenue.", "working_capital", "WATCH", value=inv_spread))
    if rec_spread is not None and rec_spread >= 20:
        red_flags.append(_item("RECEIVABLES_REVENUE_DIVERGENCE", "Receivables / revenue divergence", f"Receivables grew {rec_spread:+.1f} pts faster than revenue.", "working_capital", "RED FLAG", value=rec_spread))
    elif rec_spread is not None and rec_spread >= 10:
        watches.append(_item("RECEIVABLES_REVENUE_WATCH", "Receivables build", f"Receivables grew {rec_spread:+.1f} pts faster than revenue.", "working_capital", "WATCH", value=rec_spread))

    if economic_net_debt is not None and economic_net_debt <= 0:
        strengths.append(_item("ECONOMIC_NET_CASH", "Economic net cash", f"Economic net debt is {economic_net_debt:,.0f}; financing liquidity exceeds classified debt-like claims.", "balance_sheet", "STRENGTH", value=economic_net_debt))

    # Deterministic accounting consistency checks. These are ingestion/reconciliation
    # alerts, not accusations of accounting misconduct.
    cogs = n(current.get("cogs"))
    gp = n(current.get("gross_profit"))
    if revenue is not None and cogs is not None and gp is not None:
        _identity_check(
            inconsistencies, code="GROSS_PROFIT_RECONCILIATION", label="Gross profit reconciliation",
            expected=revenue - cogs, actual=gp, scale=revenue,
            detail="Revenue − COGS does not reconcile to stored gross profit.",
        )

    cfo, capex, fcf = n(current.get("cfo")), n(current.get("capex")), n(current.get("fcf"))
    if cfo is not None and capex is not None and fcf is not None:
        _identity_check(
            inconsistencies, code="FCF_RECONCILIATION", label="FCF reconciliation",
            expected=cfo - capex, actual=fcf, scale=revenue or cfo,
            detail="CFO − CapEx does not reconcile to stored free cash flow.",
        )

    assets, liabilities, equity = n(current.get("assets")), n(current.get("liabilities")), n(current.get("equity"))
    if assets is not None and liabilities is not None and equity is not None:
        _identity_check(
            inconsistencies, code="BALANCE_SHEET_RECONCILIATION", label="Balance-sheet reconciliation",
            expected=liabilities + equity, actual=assets, scale=assets,
            detail="Assets do not reconcile to liabilities + equity. This may reflect mezzanine/NCI presentation or an ingestion gap and requires review.",
            warn_pct=1.0,
        )

    pretax, tax, net_income = n(current.get("pretax_income")), n(current.get("income_tax")), n(current.get("net_income"))
    if pretax is not None and tax is not None and net_income is not None:
        _identity_check(
            inconsistencies, code="EARNINGS_BRIDGE_REVIEW", label="Pretax-to-net-income bridge",
            expected=pretax - tax, actual=net_income, scale=revenue or pretax,
            detail="Pretax income − tax does not fully reconcile to stored net income; review NCI, discontinued operations or source mapping.",
            warn_pct=2.0,
        )

    outstanding, diluted = n(current.get("shares_outstanding")), n(current.get("diluted_shares"))
    if outstanding not in (None, 0) and diluted is not None:
        dilution_gap = (diluted / outstanding - 1.0) * 100.0
        if dilution_gap >= 10:
            watches.append(_item("DILUTED_SHARE_OVERHANG", "Diluted share overhang", f"Diluted shares are {dilution_gap:.1f}% above current outstanding shares.", "capital_allocation", "WATCH", value=dilution_gap))

    # Economic Reality flags remain visible even when they are context-only.
    for flag in economic.get("flags") or []:
        tone = str(flag.get("tone") or "CONTEXT").upper()
        item = _item(
            str(flag.get("code") or "ECONOMIC_CONTEXT"),
            str(flag.get("code") or "Economic context").replace("_", " ").title(),
            str(flag.get("detail") or ""),
            "economic_reality",
            tone,
        )
        if tone == "REVIEW":
            if not any(x["code"] == item["code"] for x in red_flags + watches):
                watches.append(item)
        elif tone == "CAUTION":
            if not any(x["code"] == item["code"] for x in red_flags + watches):
                watches.append(item)

    # Data completeness / source continuity.
    gap_groups = (
        ("STATEMENT_GAPS", "Statement gaps", completeness.get("missing_current_fields") or []),
        ("CONTINUITY_GAPS", "Balance-sheet continuity gaps", completeness.get("missing_continuity_fields") or []),
        ("DERIVED_GAPS", "Derived-analysis gaps", completeness.get("missing_derived_metrics") or []),
    )
    for code, label, values in gap_groups:
        if values:
            data_gaps.append(_item(code, label, ", ".join(str(v) for v in values), "data", "UNRESOLVED"))
    for message in completeness.get("quarter_gaps") or []:
        data_gaps.append(_item("TTM_SEQUENCE_GAP", "TTM sequence gap", str(message), "data", "UNRESOLVED"))
    if not economic:
        data_gaps.append(_item(
            "ECONOMIC_REALITY_MISSING", "Economic Reality not materialized",
            "This stored filing basis predates the 0.3.0 accounting classification. Refresh SEC before treating leverage, EV/Sales or accounting-quality conclusions as decision-grade.",
            "data", "UNRESOLVED",
        ))
    elif economic.get("material_unresolved"):
        for message in economic.get("unresolved") or []:
            data_gaps.append(_item("ECONOMIC_CLASSIFICATION_UNRESOLVED", "Economic classification unresolved", str(message), "data", "UNRESOLVED"))

    # Deduplicate while preserving the more severe bucket.
    def unique(items: list[dict[str, Any]], blocked: set[str] | None = None) -> list[dict[str, Any]]:
        blocked = set(blocked or set())
        seen = set(blocked)
        out = []
        for item in items:
            code = str(item.get("code") or "")
            if code in seen:
                continue
            seen.add(code)
            out.append(item)
        return out

    red_flags = unique(red_flags)
    watches = unique(watches, {x["code"] for x in red_flags})
    inconsistencies = unique(inconsistencies)
    data_gaps = unique(data_gaps)
    strengths = unique(strengths, {x["code"] for x in red_flags + watches})

    if data_gaps and any(x["code"].startswith("ECONOMIC_") for x in data_gaps):
        state = "UNRESOLVED"
    elif len(red_flags) >= 2:
        state = "RED FLAGS"
    elif red_flags or len(watches) >= 3 or inconsistencies:
        state = "WATCH"
    elif strengths:
        state = "CLEAN / STRENGTH"
    else:
        state = "NEUTRAL"

    headline = {
        "UNRESOLVED": "Fundamentals contain unresolved data/classification gaps that prevent a clean economic read.",
        "RED FLAGS": "Multiple material fundamental red flags are visible in the current filed evidence.",
        "WATCH": "The core numbers are usable, but specific operating/accounting items require attention.",
        "CLEAN / STRENGTH": "Current filed fundamentals show identifiable economic strengths without a material automatic red flag.",
        "NEUTRAL": "Current filed fundamentals do not produce a strong automatic positive or negative signal.",
    }[state]

    return {
        "engine_version": ENGINE_VERSION,
        "state": state,
        "headline": headline,
        "company_quality": quality,
        "strengths": strengths,
        "red_flags": red_flags,
        "watches": watches,
        "inconsistencies": inconsistencies,
        "data_gaps": data_gaps,
        "trend_cards": trend_cards,
        "counts": {
            "strengths": len(strengths),
            "red_flags": len(red_flags),
            "watches": len(watches),
            "inconsistencies": len(inconsistencies),
            "data_gaps": len(data_gaps),
        },
        "current_period": current.get("period_label"),
        "current_period_end": current.get("period_end"),
    }


__all__ = ["ENGINE_VERSION", "build_fundamentals_forensics"]
