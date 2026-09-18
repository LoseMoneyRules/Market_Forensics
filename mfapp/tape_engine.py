from __future__ import annotations

from math import isfinite
from typing import Any

MIN_CONFIDENCE = 55.0


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _pct_points(value: Any) -> float | None:
    out = n(value)
    if out is None:
        return None
    return out * 100.0 if abs(out) <= 1.5 else out


def _weighted(parts: list[tuple[float | None, float]]) -> float | None:
    usable = [(value, weight) for value, weight in parts if value is not None and weight > 0]
    total = sum(weight for _, weight in usable)
    if total <= 0:
        return None
    return sum(float(value) * weight for value, weight in usable) / total


def rank_for_score(score: Any) -> str:
    value = n(score)
    if value is None:
        return "—"
    if value >= 70:
        return "A"
    if value >= 58:
        return "B"
    if value > 42:
        return "C"
    if value > 30:
        return "D"
    return "F"


def regime_for_scores(*, net_tape: Any, institutional_flow: Any, short_pressure: Any,
                      absorption: Any, price_resilience: Any, battle_intensity: Any,
                      confidence: Any) -> str:
    tape = n(net_tape)
    inst = n(institutional_flow)
    short = n(short_pressure)
    absorb = n(absorption)
    resilience = n(price_resilience)
    battle = n(battle_intensity)
    conf = n(confidence) or 0.0

    if tape is None or conf < MIN_CONFIDENCE:
        return "LOW DATA"

    if tape >= 70 and (inst or 0) >= 65 and (absorb or 0) >= 60:
        return "ACCUMULATION"
    if tape >= 58 and (short or 0) >= 65 and (absorb or 0) >= 60:
        return "ACCUMULATION UNDER PRESSURE"
    if (absorb or 0) >= 65 and (battle or 0) >= 60 and tape >= 50:
        return "BATTLE - BUYERS ABSORB"
    if tape >= 58:
        return "CONSTRUCTIVE"
    if tape <= 30:
        if (short or 0) >= 70 and (resilience if resilience is not None else 50) < 40:
            return "DISTRIBUTION / BEARS CONTROL"
        return "DISTRIBUTION"
    if tape <= 42:
        if (short or 0) >= 65 and (resilience if resilience is not None else 50) < 45:
            return "DISTRIBUTION / BEARS CONTROL"
        return "DISTRIBUTION"
    return "NEUTRAL / BATTLE"


def path_regime(forensic_regime: str) -> str:
    value = str(forensic_regime or "").upper()
    if value in {"ACCUMULATION", "ACCUMULATION UNDER PRESSURE", "CONSTRUCTIVE", "BATTLE - BUYERS ABSORB"}:
        return "SUPPORTIVE"
    if value in {"DISTRIBUTION", "DISTRIBUTION / BEARS CONTROL"}:
        return "HOSTILE"
    return "MIXED"


def machine_read(*, regime: str, institutional_flow: Any, short_pressure: Any,
                 absorption: Any, price_resilience: Any) -> str:
    inst = n(institutional_flow)
    short = n(short_pressure)
    absorb = n(absorption)
    resilience = n(price_resilience)
    value = str(regime or "").upper()

    if value == "LOW DATA":
        return "Evidence is incomplete; do not force a tape regime."
    if (short or 0) >= 70 and (inst or 0) >= 65 and (absorb or 0) >= 60:
        return "Heavy bearish supply is being absorbed by large demand."
    if (short or 0) >= 70 and (inst or 0) >= 65 and (absorb or 100) < 45:
        return "Large buyers are present, but sellers still control price."
    if value == "ACCUMULATION":
        return "Large-flow demand and price response are aligned with accumulation."
    if value == "ACCUMULATION UNDER PRESSURE":
        return "Large demand is holding while bearish pressure remains elevated."
    if value == "BATTLE - BUYERS ABSORB":
        return "Two-sided pressure is high, but buyers are absorbing supply."
    if value == "CONSTRUCTIVE":
        return "Demand and price response are constructive, but not yet dominant."
    if value == "DISTRIBUTION / BEARS CONTROL":
        return "Bear pressure is translating into price damage; sellers control the tape."
    if value == "DISTRIBUTION":
        if inst is not None and inst < 45:
            return "Large/whale flow and price response are consistent with distribution."
        if resilience is not None and resilience < 40:
            return "Price response is weak; supply is not being absorbed."
        return "Distribution evidence dominates despite mixed supporting flow."
    return "Mixed evidence; wait for convergence."


def score_tape_day(
    *,
    return_pct: Any,
    volume_ratio: Any,
    close_location: Any,
    short_pct: Any = None,
    net_large_ratio: Any = None,
    net_whale_ratio: Any = None,
    flow_confidence: Any = None,
    si_change_pct: Any = None,
    put_call_oi: Any = None,
    hard_to_borrow: bool = False,
    has_market: bool = True,
    has_short_volume: bool = False,
    has_flow: bool = False,
    has_positioning: bool = False,
) -> dict[str, Any]:
    ret = _pct_points(return_pct)
    volume = n(volume_ratio)
    close = _pct_points(close_location)
    short = _pct_points(short_pct)
    large = _pct_points(net_large_ratio)
    whale = _pct_points(net_whale_ratio)
    flow_conf = _pct_points(flow_confidence)
    si_change = _pct_points(si_change_pct)
    put_call = n(put_call_oi)

    close_score = clamp(close if close is not None else 50.0)
    positive_return_score = clamp(50.0 + (ret or 0.0) * 8.0)
    negative_return_score = 100.0 - positive_return_score
    volume_intensity = clamp(50.0 + ((volume or 1.0) - 1.0) * 30.0)

    price_resilience = _weighted([
        (close_score, .45),
        (positive_return_score, .40),
        (volume_intensity if (ret or 0.0) >= 0 else 100.0 - volume_intensity, .15),
    ])
    price_resilience = clamp(price_resilience if price_resilience is not None else 50.0)

    short_volume_score = clamp(50.0 + ((short if short is not None else 40.0) - 40.0) * 2.5) if short is not None else None
    si_score = clamp(50.0 + (si_change or 0.0) * 2.5) if si_change is not None else None
    options_bear = None
    if put_call is not None:
        options_bear = clamp(50.0 + (put_call - 1.0) * 35.0)
    borrow_bear = 65.0 if hard_to_borrow else (50.0 if has_positioning else None)

    short_pressure = _weighted([
        (short_volume_score, .42),
        (negative_return_score, .23),
        (si_score, .18),
        (options_bear, .10),
        (borrow_bear, .07),
    ])
    short_pressure = clamp(short_pressure if short_pressure is not None else negative_return_score)

    institutional_flow = None
    if has_flow and (large is not None or whale is not None):
        large_signal = clamp(50.0 + (large or 0.0) * 10.0)
        whale_signal = clamp(50.0 + (whale or 0.0) * 16.0)
        raw_flow = _weighted([(large_signal, .70), (whale_signal, .30)]) or 50.0
        quality = clamp(flow_conf if flow_conf is not None else 50.0) / 100.0
        institutional_flow = clamp(50.0 + (raw_flow - 50.0) * quality)

    long_demand = _weighted([
        (institutional_flow, .45),
        (price_resilience, .30),
        (positive_return_score, .15),
        (volume_intensity if (ret or 0.0) >= 0 else 50.0, .10),
    ])
    long_demand = clamp(long_demand if long_demand is not None else price_resilience)

    absorption_base = _weighted([
        (price_resilience, .50),
        (institutional_flow, .30),
        (close_score, .20),
    ])
    absorption = clamp(absorption_base if absorption_base is not None else price_resilience)
    pressure_delta = short_pressure - 50.0
    if pressure_delta > 0:
        absorption = clamp(absorption + pressure_delta * ((price_resilience - 50.0) / 100.0))

    battle_intensity = clamp(
        30.0
        + abs(long_demand - 50.0) * .45
        + abs(short_pressure - 50.0) * .45
        + max(0.0, (volume or 1.0) - 1.0) * 25.0
    )

    positives = _weighted([
        (absorption, .30),
        (long_demand, .25),
        (price_resilience, .20),
        (institutional_flow, .25),
    ])
    positives = positives if positives is not None else _weighted([(absorption, .45), (long_demand, .30), (price_resilience, .25)])
    net_tape = clamp((positives or 50.0) - max(0.0, short_pressure - 50.0) * .30)

    confidence = 0.0
    if has_market:
        confidence += 30.0
    if has_short_volume:
        confidence += 20.0
    if has_flow:
        confidence += 40.0 * (clamp(flow_conf if flow_conf is not None else 50.0) / 100.0)
    if has_positioning:
        confidence += 10.0
    confidence = clamp(confidence)

    rank = rank_for_score(net_tape)
    forensic_regime = regime_for_scores(
        net_tape=net_tape,
        institutional_flow=institutional_flow,
        short_pressure=short_pressure,
        absorption=absorption,
        price_resilience=price_resilience,
        battle_intensity=battle_intensity,
        confidence=confidence,
    )

    return {
        "institutional_flow": round(institutional_flow, 2) if institutional_flow is not None else None,
        "short_pressure": round(short_pressure, 2),
        "absorption": round(absorption, 2),
        "long_demand": round(long_demand, 2),
        "battle_intensity": round(battle_intensity, 2),
        "price_resilience": round(price_resilience, 2),
        "net_tape": round(net_tape, 2),
        "data_confidence": round(confidence, 2),
        "rank": rank,
        "forensic_regime": forensic_regime,
        "path_regime": path_regime(forensic_regime),
        "machine_read": machine_read(
            regime=forensic_regime,
            institutional_flow=institutional_flow,
            short_pressure=short_pressure,
            absorption=absorption,
            price_resilience=price_resilience,
        ),
        "close_score": round(close_score, 2),
        "positive_return_score": round(positive_return_score, 2),
        "short_volume_score": round(short_volume_score, 2) if short_volume_score is not None else None,
        "si_change_score": round(si_score, 2) if si_score is not None else None,
        "volume_intensity": round(volume_intensity, 2),
    }


def what_changed(latest: dict[str, Any] | None, previous: dict[str, Any] | None) -> list[str]:
    if not latest or not previous:
        return ["Baseline established; compare the next completed tape observation."]
    out: list[str] = []
    for key, label in (
        ("short_pressure", "Short pressure"),
        ("institutional_flow", "Institutional flow"),
        ("price_resilience", "Price resilience"),
        ("absorption", "Absorption"),
        ("long_demand", "Long demand"),
        ("battle_intensity", "Battle intensity"),
    ):
        a, b = n(latest.get(key)), n(previous.get(key))
        if a is None or b is None:
            continue
        delta = a - b
        if abs(delta) >= 5:
            out.append(f"{label} {'increased' if delta > 0 else 'decreased'} {abs(delta):.0f} pts.")
    a, b = n(latest.get("net_tape")), n(previous.get("net_tape"))
    if a is not None and b is not None and abs(a - b) >= 3:
        out.append(f"Net Tape Score moved from {b:.0f} to {a:.0f}.")
    if latest.get("rank") != previous.get("rank"):
        out.append(f"Rank changed {previous.get('rank') or '—'} → {latest.get('rank') or '—'}.")
    if latest.get("forensic_regime") != previous.get("forensic_regime"):
        out.append(f"Regime changed {previous.get('forensic_regime') or '—'} → {latest.get('forensic_regime') or '—'}.")
    return out[:6] or ["No material tape change versus the prior observation."]


def what_would_change_regime(latest: dict[str, Any] | None) -> list[str]:
    latest = latest or {}
    regime = str(latest.get("forensic_regime") or "LOW DATA").upper()
    if regime == "LOW DATA":
        return [
            "Refresh institutional trade flow and FINRA evidence.",
            "Raise Data Confidence above 55 before trusting a regime label.",
        ]
    if regime in {"DISTRIBUTION", "DISTRIBUTION / BEARS CONTROL"}:
        return [
            "Price resilience rises above 55 and stops making weak closes.",
            "Net Large Flow stays positive across multiple sessions.",
            "Absorption rises above 60 while bearish pressure remains elevated.",
            "Short pressure stops rising or begins to fade.",
        ]
    if regime in {"ACCUMULATION", "ACCUMULATION UNDER PRESSURE", "CONSTRUCTIVE", "BATTLE - BUYERS ABSORB"}:
        return [
            "Persistent negative Large/Whale Flow would weaken the constructive read.",
            "Price resilience below 45 would show that demand is no longer absorbing supply.",
            "Bear pressure above 70 with falling absorption would move the regime toward distribution.",
        ]
    return [
        "A persistent Large/Whale flow imbalance must emerge.",
        "Price response must confirm the same direction.",
        "Absorption and pressure must stop offsetting each other.",
    ]


__all__ = [
    "MIN_CONFIDENCE", "clamp", "n", "rank_for_score", "regime_for_scores", "path_regime",
    "machine_read", "score_tape_day", "what_changed", "what_would_change_regime",
]
