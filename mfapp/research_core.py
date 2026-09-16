from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any


DEFAULT_RESEARCH: dict[str, Any] = {
    "business_summary": "",
    "numbers_summary": "",
    "expectations_summary": "",
    "valuation_summary": "",
    "bear_case": "",
    "catalysts": "",
    "flows": "",
    "risk_notes": "",
    "monitoring_summary": "",
    "sources": "",
    "thesis": "",
    "invalidation": "",
    "invalidation_locked": False,
    "position_size_plan": "",
    "variant_market": "",
    "variant_we": "",
    "variant_evidence": "",
    "variant_resolution": "",
    "horizon": "",
    "why_now": "",
    "why_not_yet": "",
    "changes_decision": "",
    "kills_thesis": "",
    "business_label": "MIXED",
    "expectations_label": "BALANCED",
    "path_label": "UNCLEAR",
    "model_confidence": "UNVALIDATED",
    "risk_override": "",
    "decision_override": "",
    "bear_value": None,
    "base_value": None,
    "bull_value": None,
    "bear_prob": 0.25,
    "base_prob": 0.50,
    "bull_prob": 0.25,
    "valuation_method": "",
    "freshness_note": "",
}


EDITABLE_TEXT_FIELDS = [
    "business_summary",
    "numbers_summary",
    "expectations_summary",
    "valuation_summary",
    "bear_case",
    "catalysts",
    "flows",
    "risk_notes",
    "monitoring_summary",
    "sources",
    "thesis",
    "invalidation",
    "position_size_plan",
    "variant_market",
    "variant_we",
    "variant_evidence",
    "variant_resolution",
    "horizon",
    "why_now",
    "why_not_yet",
    "changes_decision",
    "kills_thesis",
    "valuation_method",
    "freshness_note",
]


LABEL_FIELDS = {
    "business_label": {"GOOD", "MIXED", "BAD"},
    "expectations_label": {"FAVORABLE", "BALANCED", "DEMANDING"},
    "path_label": {"SUPPORTIVE", "UNCLEAR", "HOSTILE"},
    "model_confidence": {"STRONG", "MODERATE", "LIMITED", "UNVALIDATED"},
    "risk_override": {"", "CONTROLLED", "UNRESOLVED", "HIGH", "DATA REVIEW"},
    "decision_override": {"", "LONG READY", "LONG WATCH", "SHORT READY", "SHORT WATCH", "VALUATION REVIEW", "DATA REVIEW", "NO EDGE / WAIT"},
}


@dataclass(frozen=True)
class DecisionView:
    current_price: float | None
    bear_value: float | None
    base_value: float | None
    bull_value: float | None
    expected_value: float | None
    downside_pct: float | None
    upside_pct: float | None
    base_gap_pct: float | None
    business: str
    value: str
    expectations: str
    variant: str
    path: str
    model_confidence: str
    risk: str
    decision: str
    position_action: str
    readiness_count: int
    readiness_total: int
    gates: list[dict[str, Any]]


def merged_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_RESEARCH)
    if payload:
        merged.update(payload)
    return merged


def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def clamp_probability(value: Any, default: float) -> float:
    number = safe_float(value)
    if number is None:
        return default
    if number > 1:
        number = number / 100.0
    return min(1.0, max(0.0, number))


def update_payload_from_form(payload: dict[str, Any], form: Any) -> dict[str, Any]:
    out = merged_payload(payload)
    for field in EDITABLE_TEXT_FIELDS:
        if field in form:
            out[field] = str(form.get(field, "")).strip()
    for field, allowed in LABEL_FIELDS.items():
        if field in form:
            value = str(form.get(field, "")).strip().upper()
            if value in allowed:
                out[field] = value
    for field in ("bear_value", "base_value", "bull_value"):
        if field in form:
            out[field] = safe_float(form.get(field))
    out["bear_prob"] = clamp_probability(form.get("bear_prob", out.get("bear_prob")), 0.25)
    out["base_prob"] = clamp_probability(form.get("base_prob", out.get("base_prob")), 0.50)
    out["bull_prob"] = clamp_probability(form.get("bull_prob", out.get("bull_prob")), 0.25)
    if "invalidation_locked" in form or form.get("_section") == "risk":
        out["invalidation_locked"] = form.get("invalidation_locked") in {"1", "true", "on", "yes"}
    return out


def _normalized_probabilities(payload: dict[str, Any]) -> tuple[float, float, float]:
    bear = clamp_probability(payload.get("bear_prob"), 0.25)
    base = clamp_probability(payload.get("base_prob"), 0.50)
    bull = clamp_probability(payload.get("bull_prob"), 0.25)
    total = bear + base + bull
    if total <= 0:
        return 0.25, 0.50, 0.25
    return bear / total, base / total, bull / total


def _variant_label(payload: dict[str, Any]) -> str:
    market = bool(str(payload.get("variant_market", "")).strip())
    ours = bool(str(payload.get("variant_we", "")).strip())
    evidence = bool(str(payload.get("variant_evidence", "")).strip())
    resolution = bool(str(payload.get("variant_resolution", "")).strip())
    if not market or not ours:
        return "UNPROVEN"
    if not evidence:
        return "DEFINED UNPROVEN"
    if not resolution:
        return "POSSIBLE"
    base = safe_float(payload.get("base_value"))
    bear = safe_float(payload.get("bear_value"))
    bull = safe_float(payload.get("bull_value"))
    if base is not None and bear is not None and bull is not None:
        center = (bear + bull) / 2.0
        return "POSITIVE EDGE" if base >= center else "NEGATIVE EDGE"
    return "POSSIBLE"


def _risk_label(payload: dict[str, Any]) -> str:
    override = str(payload.get("risk_override", "")).strip().upper()
    if override:
        return override
    invalidation = str(payload.get("invalidation", "")).strip()
    sizing = str(payload.get("position_size_plan", "")).strip()
    if not invalidation:
        return "DATA REVIEW"
    if not payload.get("invalidation_locked"):
        return "UNRESOLVED"
    if not sizing:
        return "UNRESOLVED"
    return "CONTROLLED"


def _value_label(current_price: float | None, base_value: float | None, model_confidence: str) -> tuple[str, float | None]:
    if current_price is None or current_price <= 0 or base_value is None or base_value <= 0:
        return "DATA REVIEW", None
    gap = (base_value / current_price - 1.0) * 100.0
    if model_confidence in {"LIMITED", "UNVALIDATED"} and abs(gap) >= 25:
        return "REVIEW", gap
    if gap >= 15:
        return "ATTRACTIVE", gap
    if gap <= -15:
        return "EXPENSIVE", gap
    return "FAIR", gap


def _decision_label(payload: dict[str, Any], business: str, value: str, risk: str, variant: str, path: str) -> str:
    override = str(payload.get("decision_override", "")).strip().upper()
    if override:
        return override
    if value == "DATA REVIEW" or risk == "DATA REVIEW":
        return "DATA REVIEW"
    if value == "REVIEW":
        return "VALUATION REVIEW"
    if value == "ATTRACTIVE":
        if business == "GOOD" and risk == "CONTROLLED" and variant in {"POSITIVE EDGE", "POSSIBLE"} and path != "HOSTILE":
            return "LONG READY"
        return "LONG WATCH"
    if value == "EXPENSIVE":
        if business == "BAD" and risk == "CONTROLLED" and variant in {"NEGATIVE EDGE", "POSSIBLE"} and path != "SUPPORTIVE":
            return "SHORT READY"
        return "SHORT WATCH"
    return "NO EDGE / WAIT"


def _position_action(decision: str, shares: float | None) -> str:
    if shares is None or abs(shares) < 1e-12:
        return decision
    if shares > 0:
        return {
            "LONG READY": "HOLD / ADD ON EVIDENCE",
            "LONG WATCH": "HOLD / MONITOR",
            "SHORT READY": "REDUCE / SELL REVIEW",
            "SHORT WATCH": "RISK REVIEW",
            "VALUATION REVIEW": "HOLD / VALUATION REVIEW",
            "DATA REVIEW": "HOLD / DATA REVIEW",
            "NO EDGE / WAIT": "HOLD / REASSESS",
        }.get(decision, decision)
    return {
        "SHORT READY": "HOLD SHORT / ADD ON EVIDENCE",
        "SHORT WATCH": "HOLD SHORT / MONITOR",
        "LONG READY": "COVER / REDUCE REVIEW",
        "LONG WATCH": "RISK REVIEW",
        "VALUATION REVIEW": "HOLD SHORT / VALUATION REVIEW",
        "DATA REVIEW": "HOLD SHORT / DATA REVIEW",
        "NO EDGE / WAIT": "HOLD SHORT / REASSESS",
    }.get(decision, decision)


def process_gates(payload: dict[str, Any], current_price: float | None, monitoring_count: int, journal_count: int, validation_count: int) -> list[dict[str, Any]]:
    p = merged_payload(payload)
    values = [safe_float(p.get(k)) for k in ("bear_value", "base_value", "bull_value")]
    gates = [
        ("Business", bool(str(p["business_summary"]).strip()), "research"),
        ("Numbers", bool(str(p["numbers_summary"]).strip()), "research"),
        ("Expectations", bool(str(p["expectations_summary"]).strip()), "research"),
        ("Variant", bool(str(p["variant_market"]).strip()) and bool(str(p["variant_we"]).strip()), "research"),
        ("Variant evidence", bool(str(p["variant_evidence"]).strip()), "research"),
        ("Valuation cases", all(v is not None and v > 0 for v in values), "research"),
        ("Current price", current_price is not None and current_price > 0, "decide"),
        ("Bear case", bool(str(p["bear_case"]).strip()), "research"),
        ("Catalyst / path", bool(str(p["catalysts"]).strip()) and p["path_label"] != "UNCLEAR", "research"),
        ("Flows / positioning", bool(str(p["flows"]).strip()), "research"),
        ("Risk", bool(str(p["risk_notes"]).strip()), "research"),
        ("Invalidation LOCKED", bool(str(p["invalidation"]).strip()) and bool(p["invalidation_locked"]), "research"),
        ("Position sizing", bool(str(p["position_size_plan"]).strip()), "research"),
        ("Monitoring", monitoring_count > 0 or bool(str(p["monitoring_summary"]).strip()), "monitor"),
        ("Sources / provenance", bool(str(p["sources"]).strip()), "research"),
        ("Decision journal / validation", journal_count > 0 or validation_count > 0, "validate"),
    ]
    return [{"label": label, "ok": ok, "target": target} for label, ok, target in gates]


def build_decision(
    payload: dict[str, Any] | None,
    current_price: float | None,
    shares: float | None = None,
    monitoring_count: int = 0,
    journal_count: int = 0,
    validation_count: int = 0,
) -> DecisionView:
    p = merged_payload(payload)
    bear = safe_float(p.get("bear_value"))
    base = safe_float(p.get("base_value"))
    bull = safe_float(p.get("bull_value"))
    bp, xp, up = _normalized_probabilities(p)
    expected = None
    if bear is not None and base is not None and bull is not None:
        expected = bear * bp + base * xp + bull * up
    downside = ((bear / current_price - 1) * 100.0) if bear is not None and current_price and current_price > 0 else None
    upside = ((bull / current_price - 1) * 100.0) if bull is not None and current_price and current_price > 0 else None
    business = str(p.get("business_label", "MIXED")).upper()
    confidence = str(p.get("model_confidence", "UNVALIDATED")).upper()
    value, gap = _value_label(current_price, base, confidence)
    expectations = str(p.get("expectations_label", "BALANCED")).upper()
    variant = _variant_label(p)
    path = str(p.get("path_label", "UNCLEAR")).upper()
    risk = _risk_label(p)
    decision = _decision_label(p, business, value, risk, variant, path)
    gates = process_gates(p, current_price, monitoring_count, journal_count, validation_count)
    readiness = sum(1 for gate in gates if gate["ok"])
    return DecisionView(
        current_price=current_price,
        bear_value=bear,
        base_value=base,
        bull_value=bull,
        expected_value=expected,
        downside_pct=downside,
        upside_pct=upside,
        base_gap_pct=gap,
        business=business,
        value=value,
        expectations=expectations,
        variant=variant,
        path=path,
        model_confidence=confidence,
        risk=risk,
        decision=decision,
        position_action=_position_action(decision, shares),
        readiness_count=readiness,
        readiness_total=len(gates),
        gates=gates,
    )


def publication_payload(payload: dict[str, Any], decision: DecisionView) -> dict[str, Any]:
    """Create a publication-safe snapshot. Personal holdings/sizing stay out of published research."""
    p = merged_payload(payload)
    return {
        "business": p["business_summary"],
        "numbers": p["numbers_summary"],
        "expectations": p["expectations_summary"],
        "valuation": p["valuation_summary"],
        "bear_case": p["bear_case"],
        "catalysts": p["catalysts"],
        "flows": p["flows"],
        "risk": p["risk_notes"],
        "monitoring": p["monitoring_summary"],
        "sources": p["sources"],
        "thesis": p["thesis"],
        "variant_market": p["variant_market"],
        "variant_we": p["variant_we"],
        "variant_evidence": p["variant_evidence"],
        "variant_resolution": p["variant_resolution"],
        "horizon": p["horizon"],
        "why_now": p["why_now"],
        "why_not_yet": p["why_not_yet"],
        "changes_decision": p["changes_decision"],
        "kills_thesis": p["kills_thesis"],
        "business_label": decision.business,
        "value_label": decision.value,
        "expectations_label": decision.expectations,
        "variant_label": decision.variant,
        "path_label": decision.path,
        "model_confidence": decision.model_confidence,
        "risk_label": decision.risk,
        "decision": decision.decision,
        "bear_value": decision.bear_value,
        "base_value": decision.base_value,
        "bull_value": decision.bull_value,
        "expected_value": decision.expected_value,
        "disclosure": "Independent research snapshot. General information only; not personalized investment advice.",
    }
