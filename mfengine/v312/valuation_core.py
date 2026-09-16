from __future__ import annotations

"""Current-case valuation core preserved from Market Forensics V3.1.12 FULL.

This hosted adapter intentionally exposes the original P/E, EV/Sales, FCF-yield, FCFE-style DCF,
robust cross-method blend and share-basis gates. Historical PIT calibration and automatic current
share-basis recovery remain separate engines and must not be silently approximated here.
"""

import math
from statistics import median


def _finite(x):
    try:
        return x is not None and math.isfinite(float(x))
    except Exception:
        return False


def _quantile(values, q):
    vals = sorted(float(x) for x in values if _finite(x))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * float(q)
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1 - w) + vals[hi] * w


def pe_value(revenue, growth, net_margin, pe, shares):
    """P/E valuation on normalized forward net income, consistent with historical P/E calibration."""
    if not all(_finite(x) for x in (revenue, growth, net_margin, pe, shares)) or float(shares) == 0:
        return None
    forward_revenue = float(revenue) * (1 + float(growth))
    earnings = forward_revenue * float(net_margin)
    v = earnings / float(shares) * float(pe)
    return v if v > 0 else None


def ev_sales_value(revenue, growth, ev_sales, net_debt, shares):
    if not all(_finite(x) for x in (revenue, growth, ev_sales, net_debt, shares)) or float(shares) == 0:
        return None
    enterprise_value = float(revenue) * (1 + float(growth)) * float(ev_sales)
    equity_value = enterprise_value - float(net_debt)
    v = equity_value / float(shares)
    return v if v > 0 else None


def fcf_yield_value(revenue, growth, fcf_margin, target_yield, shares):
    if not all(_finite(x) for x in (revenue, growth, fcf_margin, target_yield, shares)):
        return None
    if float(shares) == 0 or float(target_yield) <= 0:
        return None
    normalized_fcf = float(revenue) * (1 + float(growth)) * float(fcf_margin)
    v = (normalized_fcf / float(target_yield)) / float(shares)
    return v if v > 0 else None


def dcf_value(revenue, growth, fcf_margin, wacc, terminal_growth, years, net_debt, shares):
    """FCFE-style equity cash-flow cross-check from V3.1.12.

    The persisted field is historically named ``wacc`` but is interpreted as the equity discount
    rate because FCF in Market Forensics is CFO - capex (a levered/equity cash-flow proxy).
    Net debt remains in the signature for saved-model compatibility and is intentionally not
    subtracted again.
    """
    if not all(_finite(x) for x in (revenue, growth, fcf_margin, wacc, terminal_growth, years, shares)):
        return None
    discount = float(wacc); tg = float(terminal_growth)
    if float(shares) <= 0 or discount <= tg or discount <= 0:
        return None
    rev = float(revenue); pv = 0.0; years = int(years)
    for y in range(1, years + 1):
        rev *= 1 + float(growth)
        fcfe = rev * float(fcf_margin)
        pv += fcfe / ((1 + discount) ** y)
    terminal_fcfe = rev * (1 + tg) * float(fcf_margin)
    tv = terminal_fcfe / (discount - tg)
    pv += tv / ((1 + discount) ** years)
    v = pv / float(shares)
    return v if v > 0 else None


def robust_blend(components: dict, weights: dict):
    """V3.1.12 robust blend: >45% cross-method outliers receive 70% weight reduction."""
    valid = [(k, float(v)) for k, v in components.items() if _finite(v) and float(v) > 0]
    if not valid:
        return None, {}, []
    vals = [v for _, v in valid]
    med = median(vals)
    adj = {}
    flags = []
    for k, v in valid:
        w = max(0.0, float(weights.get(k, 0)))
        if med > 0 and abs(v / med - 1) > .45 and len(valid) >= 3:
            w *= .30
            flags.append(f"{k} is >45% away from the cross-method median; weight reduced")
        adj[k] = w
    total = sum(adj.values())
    if total <= 0:
        adj = {k: 1.0 for k, _ in valid}; total = len(valid)
    blend = sum(v * adj[k] for k, v in valid) / total
    return blend, {k: adj[k] / total for k in adj}, flags


def scenario_value(metrics: dict, assumptions: dict, weights: dict, years: int = 5) -> dict:
    """One V3.1.12 current valuation case with explicit share-basis gating."""
    revenue = metrics.get("revenue")
    shares = metrics.get("valuation_shares")
    net_debt = metrics.get("net_debt")
    basis_usable = bool(metrics.get("valuation_share_basis_usable"))
    issue = str(metrics.get("valuation_basis_issue") or "Selected share denominator cannot be reconciled to the current corporate-action basis.")
    if not basis_usable:
        return {
            "pe": None, "ev_sales": None, "fcf_yield": None, "dcf": None,
            "fair_value": None, "range_low": None, "range_high": None,
            "effective_weights": {}, "flags": ["DATA REVIEW: " + issue],
        }
    growth = assumptions.get("growth")
    net_margin = assumptions.get("net_margin")
    fcf_margin = assumptions.get("fcf_margin")
    pe = pe_value(revenue, growth, net_margin, assumptions.get("pe"), shares)
    evs = ev_sales_value(revenue, growth, assumptions.get("ev_sales"), net_debt, shares)
    fy = fcf_yield_value(revenue, growth, fcf_margin, assumptions.get("target_fcf_yield"), shares)
    dcf = dcf_value(revenue, growth, fcf_margin, assumptions.get("equity_discount_rate"), assumptions.get("terminal_growth"), years, net_debt, shares)
    components = {"pe": pe, "ev_sales": evs, "fcf_yield": fy}
    blend, effective_weights, flags = robust_blend(components, weights)
    override = assumptions.get("manual_override")
    if _finite(override) and float(override) > 0:
        blend = float(override)
        flags.append("manual fair-value override active")
    valid_vals = [float(v) for v in components.values() if _finite(v) and float(v) > 0]
    return {
        "pe": pe,
        "ev_sales": evs,
        "fcf_yield": fy,
        "dcf": dcf,
        "fair_value": blend,
        "range_low": _quantile(valid_vals, .25) if valid_vals else None,
        "range_high": _quantile(valid_vals, .75) if valid_vals else None,
        "effective_weights": effective_weights,
        "flags": flags,
    }


def scenario_values(metrics: dict, cases: dict, weights: dict | None = None, years: int = 5):
    weights = weights or {"pe": .40, "ev_sales": .30, "fcf_yield": .30}
    out = {}
    for name in ("bear", "base", "bull"):
        out[name] = scenario_value(metrics, cases.get(name, {}), weights, years)
        out[name]["prob"] = float(cases.get(name, {}).get("prob") or 0)
    total_prob = sum(max(0.0, row["prob"]) for row in out.values())
    expected = None
    if total_prob > 0 and all(_finite(row["fair_value"]) for row in out.values()):
        expected = sum(float(row["fair_value"]) * max(0.0, row["prob"]) for row in out.values()) / total_prob
    return out, expected
