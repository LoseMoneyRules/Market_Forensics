from __future__ import annotations

from math import isfinite
from typing import Any

from .valuation_engine import canonical_valuation_quality, valuation_is_decision_grade


BUY_THRESHOLD = 2.5
SELL_THRESHOLD = -2.5


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def build_research_intelligence(
    financials: list[dict[str, Any]],
    valuation: dict[str, Any],
    *,
    market_price: Any = None,
    valuation_quality: str = "",
    data_quality_issues: int = 0,
    finra_summary: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an auditable evidence read.

    The action is deliberately conservative: valuation can create opportunity, but
    price alone cannot create a BUY/SELL. Every weighted datapoint remains visible
    and missing/low-quality evidence is exposed as a blocker instead of guessed.
    """
    signals: list[dict[str, Any]] = []
    warnings: list[str] = []
    blockers: list[str] = []
    score = 0.0

    def add(label: str, detail: str, tone: str, weight: float = 0.0, section: str = "overview") -> None:
        nonlocal score
        score += weight
        signals.append({
            "label": label,
            "detail": detail,
            "tone": tone,
            "weight": round(weight, 2),
            "section": section,
        })

    latest = financials[0] if financials else {}
    prior = financials[1] if len(financials) > 1 else {}
    metrics = latest.get("metrics") or {}
    prior_metrics = prior.get("metrics") or {}

    price = _n(market_price if market_price is not None else valuation.get("current_price"))
    base = _n(valuation.get("base"))
    expected = _n(valuation.get("expected_value"))
    base_gap = ((base / price - 1.0) * 100.0) if base is not None and price not in (None, 0) else None
    expected_gap = ((expected / price - 1.0) * 100.0) if expected is not None and price not in (None, 0) else None

    base_quality = canonical_valuation_quality(valuation.get("base_quality") or valuation_quality)
    decision_grade_valuation = valuation_is_decision_grade({"base_quality": base_quality})
    if base_gap is None:
        blockers.append("Verified market/Base comparison is missing.")
    elif not decision_grade_valuation:
        add(
            "Valuation reference",
            f"Displayed Base is {base_gap:+.1f}% vs market but quality is {base_quality.replace('_', ' ')}; no valuation edge is scored.",
            "watch",
            0.0,
            "valuation",
        )
        blockers.append("Base fair value is visible but is not intrinsic/manual verified.")
    elif base_gap >= 30:
        add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "positive", 2.0, "valuation")
    elif base_gap >= 15:
        add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "positive", 1.0, "valuation")
    elif base_gap <= -25:
        add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "negative", -2.0, "valuation")
    elif base_gap <= -10:
        add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market.", "negative", -1.0, "valuation")
    else:
        add("Valuation gap", f"Base fair value is {base_gap:+.1f}% vs market; no large dislocation.", "neutral", 0.0, "valuation")

    revenue_growth = _n(metrics.get("revenue_growth_pct"))
    if revenue_growth is not None:
        if revenue_growth >= 8:
            add("Revenue trend", f"Latest revenue growth is {revenue_growth:+.1f}% YoY.", "positive", 1.0, "numbers")
        elif revenue_growth <= -5:
            add("Revenue trend", f"Latest revenue growth is {revenue_growth:+.1f}% YoY.", "negative", -1.0, "numbers")
        else:
            add("Revenue trend", f"Latest revenue growth is {revenue_growth:+.1f}% YoY.", "neutral", 0.0, "numbers")

    op_margin = _n(metrics.get("operating_margin_pct"))
    prior_op_margin = _n(prior_metrics.get("operating_margin_pct"))
    if op_margin is not None and prior_op_margin is not None:
        delta = op_margin - prior_op_margin
        if delta >= 2:
            add("Margin inflection", f"Operating margin improved {delta:+.1f} pts to {op_margin:.1f}%.", "positive", 1.0, "numbers")
        elif delta <= -2:
            add("Margin inflection", f"Operating margin deteriorated {delta:+.1f} pts to {op_margin:.1f}%.", "negative", -1.0, "numbers")
        else:
            add("Operating margin", f"Operating margin is {op_margin:.1f}% ({delta:+.1f} pts YoY).", "neutral", 0.0, "numbers")

    fcf = _n(latest.get("fcf"))
    net_income = _n(latest.get("net_income"))
    if fcf is not None:
        if fcf < 0:
            add("Cash conversion", "Free cash flow is negative in the latest fiscal year.", "negative", -1.5, "financial-flows")
        elif net_income not in (None, 0):
            conversion = fcf / net_income
            if conversion >= 1:
                add("Cash conversion", f"FCF / net income is {conversion:.2f}x.", "positive", 1.0, "financial-flows")
            elif conversion < .55:
                add("Cash conversion", f"FCF / net income is only {conversion:.2f}x.", "negative", -1.0, "financial-flows")
            else:
                add("Cash conversion", f"FCF / net income is {conversion:.2f}x.", "neutral", 0.0, "financial-flows")

    inv_growth = _n(metrics.get("inventory_growth_pct"))
    rec_growth = _n(metrics.get("receivables_growth_pct"))
    if revenue_growth is not None and inv_growth is not None:
        spread = inv_growth - revenue_growth
        if spread >= 12:
            add("Inventory divergence", f"Inventory grew {spread:.1f} pts faster than revenue.", "negative", -1.0, "numbers")
        elif spread <= -10:
            add("Inventory discipline", f"Inventory grew {abs(spread):.1f} pts slower than revenue.", "positive", .5, "numbers")
    if revenue_growth is not None and rec_growth is not None:
        spread = rec_growth - revenue_growth
        if spread >= 12:
            add("Receivables divergence", f"Receivables grew {spread:.1f} pts faster than revenue.", "negative", -1.0, "numbers")

    net_debt = _n(metrics.get("net_debt"))
    if net_debt is not None and fcf not in (None, 0):
        if net_debt <= 0:
            add("Balance sheet", "Net cash / no net debt on latest filing basis.", "positive", .5, "numbers")
        elif fcf > 0:
            leverage = net_debt / fcf
            if leverage >= 4:
                add("Leverage", f"Net debt is about {leverage:.1f}x latest FCF.", "negative", -1.0, "numbers")
            elif leverage <= 2:
                add("Leverage", f"Net debt is about {leverage:.1f}x latest FCF.", "neutral", .25, "numbers")

    finra = finra_summary or {}
    si = finra.get("latest_short_interest") or {}
    si_change = _n(si.get("change_percent"))
    if si_change is not None:
        add(
            "Short interest",
            f"Reported short interest changed {si_change:+.1f}% vs prior report.",
            "watch" if abs(si_change) >= 8 else "neutral",
            0.0,
            "tape",
        )

    if not decision_grade_valuation:
        warnings.append(f"Valuation quality is {base_quality.replace('_', ' ')}; target remains visible but cannot create an edge.")
    if data_quality_issues:
        warnings.append(f"{data_quality_issues} open data-quality issue(s) require review.")

    readiness = readiness or {}
    pending = [g.get("label") for g in (readiness.get("gates") or []) if not g.get("approved")]
    if pending:
        blockers.append("Research incomplete: " + ", ".join(str(x) for x in pending[:5]) + ("…" if len(pending) > 5 else ""))

    evidence_years = len(financials)
    validation = readiness.get("validation") or {}
    validation_state = str(validation.get("state") or "NOT RUN")
    confidence_points = 0
    confidence_points += 1 if evidence_years >= 2 else 0
    confidence_points += 1 if evidence_years >= 4 else 0
    confidence_points += 1 if decision_grade_valuation else 0
    confidence_points += 1 if not warnings else 0
    confidence_points += 1 if validation_state == "VALIDATED" else 0
    confidence = "HIGH" if confidence_points >= 4 else "MEDIUM" if confidence_points >= 2 else "LOW"

    positives = sum(1 for s in signals if s["tone"] == "positive")
    negatives = sum(1 for s in signals if s["tone"] == "negative")
    bias = "LONG" if score >= 2 else "SHORT" if score <= -2 else "NEUTRAL"

    hard_block = bool(warnings or base_gap is None or not decision_grade_valuation)
    if hard_block:
        action = "WAIT"
        stance = "DATA REVIEW"
    elif score >= BUY_THRESHOLD and base_gap >= 10:
        action = "BUY"
        stance = "ATTRACTIVE"
    elif score <= SELL_THRESHOLD and base_gap <= -10:
        action = "SELL"
        stance = "DETERIORATING"
    elif bias == "LONG":
        action = "WAIT"
        stance = "WATCH"
    elif bias == "SHORT":
        action = "WAIT"
        stance = "DETERIORATING"
    else:
        action = "WAIT"
        stance = "NO EDGE"

    ordered = sorted(signals, key=lambda x: abs(float(x.get("weight") or 0)), reverse=True)
    for_warning = [{"label": "Data / model warning", "detail": w, "tone": "watch", "weight": 0.0, "section": "audit"} for w in warnings]
    against = [s for s in ordered if s["weight"] < 0] + for_warning
    support = [s for s in ordered if s["weight"] > 0]
    neutral = [s for s in ordered if s["weight"] == 0]

    next_threshold = BUY_THRESHOLD if score < BUY_THRESHOLD else SELL_THRESHOLD
    threshold_gap = (BUY_THRESHOLD - score) if bias != "SHORT" else (score - SELL_THRESHOLD)

    return {
        "action": action,
        "stance": stance,
        "bias": bias,
        "confidence": confidence,
        "score": round(score, 2),
        "buy_threshold": BUY_THRESHOLD,
        "sell_threshold": SELL_THRESHOLD,
        "threshold_gap": round(abs(threshold_gap), 2),
        "base_gap_pct": base_gap,
        "expected_gap_pct": expected_gap,
        "positives": positives,
        "negatives": negatives,
        "signals": ordered,
        "top_signals": ordered[:5],
        "supporting_evidence": support,
        "opposing_evidence": against,
        "context_evidence": neutral,
        "warnings": warnings,
        "blockers": blockers,
        "evidence_years": evidence_years,
        "validation_state": validation_state,
        "valuation_base_quality": base_quality,
        "valuation_decision_grade": decision_grade_valuation,
        "ready_to_validate": bool(readiness.get("ready_to_validate")),
        "summary": f"{action} · {stance} · {bias} bias · {confidence} confidence",
    }


__all__ = ["BUY_THRESHOLD", "SELL_THRESHOLD", "build_research_intelligence"]
