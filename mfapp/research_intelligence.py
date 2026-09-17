from __future__ import annotations

from math import isfinite
from typing import Any


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _pct(current: Any, previous: Any) -> float | None:
    c, p = _n(current), _n(previous)
    if c is None or p in (None, 0):
        return None
    return (c / p - 1.0) * 100.0


def build_research_intelligence(
    financials: list[dict[str, Any]],
    valuation: dict[str, Any],
    *,
    market_price: Any = None,
    valuation_quality: str = "",
    data_quality_issues: int = 0,
    finra_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an auditable first-pass research read from stored evidence only.

    This deliberately avoids narrative guessing. Every signal is tied to a stored
    financial, valuation or positioning datapoint and remains editable downstream.
    """
    signals: list[dict[str, Any]] = []
    warnings: list[str] = []
    score = 0.0

    def add(label: str, detail: str, tone: str, weight: float = 0.0, section: str = "overview") -> None:
        nonlocal score
        score += weight
        signals.append({"label": label, "detail": detail, "tone": tone, "weight": weight, "section": section})

    latest = financials[0] if financials else {}
    prior = financials[1] if len(financials) > 1 else {}
    metrics = latest.get("metrics") or {}
    price = _n(market_price if market_price is not None else valuation.get("current_price"))
    base = _n(valuation.get("base"))
    expected = _n(valuation.get("expected_value"))
    base_gap = ((base / price - 1.0) * 100.0) if base is not None and price not in (None, 0) else None
    expected_gap = ((expected / price - 1.0) * 100.0) if expected is not None and price not in (None, 0) else None

    if base_gap is not None:
        if base_gap >= 30:
            add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "positive", 2.0, "valuation")
        elif base_gap >= 15:
            add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "positive", 1.0, "valuation")
        elif base_gap <= -25:
            add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "negative", -2.0, "valuation")
        elif base_gap <= -10:
            add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "negative", -1.0, "valuation")
        else:
            add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market; no large dislocation.", "neutral", 0.0, "valuation")
    else:
        warnings.append("No verified market/base comparison is available yet.")

    revenue_growth = _n(metrics.get("revenue_growth_pct"))
    if revenue_growth is not None:
        if revenue_growth >= 8:
            add("Revenue trend", f"Latest revenue growth is {revenue_growth:+.1f}% YoY.", "positive", 1.0, "numbers")
        elif revenue_growth <= -5:
            add("Revenue trend", f"Latest revenue growth is {revenue_growth:+.1f}% YoY.", "negative", -1.0, "numbers")
        else:
            add("Revenue trend", f"Latest revenue growth is {revenue_growth:+.1f}% YoY.", "neutral", 0.0, "numbers")

    op_margin = _n(metrics.get("operating_margin_pct"))
    prior_metrics = prior.get("metrics") or {}
    prior_op_margin = _n(prior_metrics.get("operating_margin_pct"))
    if op_margin is not None and prior_op_margin is not None:
        delta = op_margin - prior_op_margin
        if delta >= 2.0:
            add("Margin inflection", f"Operating margin improved {delta:+.1f} pts to {op_margin:.1f}%.", "positive", 1.0, "numbers")
        elif delta <= -2.0:
            add("Margin inflection", f"Operating margin deteriorated {delta:+.1f} pts to {op_margin:.1f}%.", "negative", -1.0, "numbers")
        else:
            add("Operating margin", f"Operating margin is {op_margin:.1f}% ({delta:+.1f} pts YoY).", "neutral", 0.0, "numbers")

    fcf = _n(latest.get("fcf")); net_income = _n(latest.get("net_income")); revenue = _n(latest.get("revenue"))
    if fcf is not None:
        if fcf < 0:
            add("Cash conversion", "Free cash flow is negative in the latest fiscal year.", "negative", -1.5, "financial-flows")
        elif net_income not in (None, 0):
            conversion = fcf / net_income
            if conversion >= 1.0:
                add("Cash conversion", f"FCF / net income is {conversion:.2f}x.", "positive", 1.0, "financial-flows")
            elif conversion < 0.55:
                add("Cash conversion", f"FCF / net income is only {conversion:.2f}x.", "negative", -1.0, "financial-flows")
            else:
                add("Cash conversion", f"FCF / net income is {conversion:.2f}x.", "neutral", 0.0, "financial-flows")

    inv_growth = _n(metrics.get("inventory_growth_pct")); rec_growth = _n(metrics.get("receivables_growth_pct"))
    if revenue_growth is not None and inv_growth is not None:
        spread = inv_growth - revenue_growth
        if spread >= 12:
            add("Inventory divergence", f"Inventory grew {spread:.1f} pts faster than revenue.", "negative", -1.0, "numbers")
        elif spread <= -10:
            add("Inventory discipline", f"Inventory grew {abs(spread):.1f} pts slower than revenue.", "positive", 0.5, "numbers")
    if revenue_growth is not None and rec_growth is not None:
        spread = rec_growth - revenue_growth
        if spread >= 12:
            add("Receivables divergence", f"Receivables grew {spread:.1f} pts faster than revenue.", "negative", -1.0, "numbers")

    net_debt = _n(metrics.get("net_debt"))
    if net_debt is not None and fcf not in (None, 0):
        if net_debt <= 0:
            add("Balance sheet", "Net cash / no net debt on latest filing basis.", "positive", 0.5, "numbers")
        elif fcf > 0:
            leverage = net_debt / fcf
            if leverage >= 4:
                add("Leverage", f"Net debt is about {leverage:.1f}x latest FCF.", "negative", -1.0, "numbers")
            elif leverage <= 2:
                add("Leverage", f"Net debt is about {leverage:.1f}x latest FCF.", "neutral", 0.25, "numbers")

    finra = finra_summary or {}
    si = finra.get("latest_short_interest") or {}
    si_change = _n(si.get("change_percent"))
    if si_change is not None:
        tone = "watch" if abs(si_change) >= 8 else "neutral"
        add("Short interest", f"Reported short interest changed {si_change:+.1f}% vs prior report.", tone, 0.0, "tape")

    quality = str(valuation_quality or "").upper()
    if quality and quality != "INTRINSIC":
        warnings.append(f"Valuation quality is {quality.replace('_', ' ')}; treat the stance as provisional.")
    if data_quality_issues:
        warnings.append(f"{data_quality_issues} open data-quality issue(s) require review.")

    positives = sum(1 for x in signals if x["tone"] == "positive")
    negatives = sum(1 for x in signals if x["tone"] == "negative")
    evidence_years = len(financials)
    confidence = "HIGH" if evidence_years >= 4 and not warnings and quality == "INTRINSIC" else "MEDIUM" if evidence_years >= 2 else "LOW"

    bias = "LONG" if score >= 2 else "SHORT" if score <= -2 else "NEUTRAL"
    if warnings or base_gap is None:
        action = "WAIT"
    elif score >= 2.5 and base_gap >= 10:
        action = "BUY"
    elif score <= -2.5 and base_gap <= -10:
        action = "SELL"
    else:
        action = "WAIT"

    ordered = sorted(signals, key=lambda x: (abs(float(x.get("weight") or 0)), x["tone"] != "watch"), reverse=True)
    return {
        "action": action,
        "bias": bias,
        "confidence": confidence,
        "score": round(score, 2),
        "base_gap_pct": base_gap,
        "expected_gap_pct": expected_gap,
        "positives": positives,
        "negatives": negatives,
        "signals": ordered,
        "top_signals": ordered[:5],
        "warnings": warnings,
        "evidence_years": evidence_years,
        "summary": f"{action} · {bias} bias · {confidence} confidence",
    }


__all__ = ["build_research_intelligence"]
