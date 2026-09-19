from __future__ import annotations

from typing import Any

from .core_models import BearCaseItem, Catalyst, Expectation, MonitoringRule, ResearchState, RiskPlan
from .expectations_engine import price_implied_expectations
from .valuation_engine import valuation_base_quality, valuation_is_decision_grade


def _text(value: Any) -> str:
    return str(value or "").strip()


def build_decision_lenses(
    *,
    coverage,
    company,
    research: ResearchState,
    risk: RiskPlan,
    model,
    market,
    valuation: dict[str, Any],
    intelligence: dict[str, Any],
    readiness: dict[str, Any],
    management: dict[str, Any] | None = None,
    tape: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate evidence into the mature Local-style lenses without mixing portfolio sizing.

    These lenses are descriptive research states. They do not independently order,
    rank, or size a portfolio position.
    """
    management = management or {}
    tape = tape or {}
    base_gap = intelligence.get("base_gap_pct")
    implied = price_implied_expectations(
        company.id,
        model,
        market.price if market else valuation.get("current_price"),
    )

    gates = {str(g.get("key")): g for g in (readiness.get("gates") or [])}
    positives = int(intelligence.get("positives") or 0)
    negatives = int(intelligence.get("negatives") or 0)

    # BUSINESS
    mgmt_score = management.get("score")
    business_gate = gates.get("business") or {}
    if not business_gate.get("evidence_ready"):
        business = "UNPROVEN"
    elif mgmt_score is not None and float(mgmt_score) >= 65 and positives >= negatives:
        business = "GOOD"
    elif mgmt_score is not None and float(mgmt_score) < 40:
        business = "BAD"
    else:
        business = "MIXED"

    # VALUE — displayed targets are not automatically decision-grade.
    explicit_quality = any(key in valuation for key in ("base_quality", "quality", "scenarios"))
    if explicit_quality:
        base_quality = valuation_base_quality(valuation)
        decision_grade_valuation = valuation_is_decision_grade(valuation)
    else:
        # Compatibility for direct/internal callers predating quality metadata.
        # Production cache/route paths attach stored Base quality before calling us.
        base_quality = "LEGACY_UNSPECIFIED"
        decision_grade_valuation = True
    if base_gap is None or not decision_grade_valuation:
        value = "UNVERIFIED"
    elif base_gap >= 20:
        value = "ATTRACTIVE"
    elif base_gap <= -15:
        value = "EXPENSIVE"
    else:
        value = "FAIR"

    # EXPECTATIONS
    expectations = implied.get("classification") or "UNAVAILABLE"

    # VARIANT
    market_view = _text(research.variant_market)
    our_view = _text(research.variant_us)
    variant_evidence = _text(research.variant_evidence)
    expectation_count = Expectation.query.filter_by(coverage_id=coverage.id).count()
    if not market_view or not our_view:
        variant = "UNPROVEN"
    elif not decision_grade_valuation:
        variant = "DEFINED · UNPROVEN"
    elif not variant_evidence and expectation_count == 0:
        variant = "DEFINED · UNPROVEN"
    elif value == "ATTRACTIVE" and expectations in {"FAVORABLE", "BALANCED"} and variant_evidence:
        variant = "POSITIVE EDGE"
    elif value == "EXPENSIVE" and expectations in {"DEMANDING", "BALANCED"} and variant_evidence:
        variant = "NEGATIVE EDGE"
    else:
        variant = "POSSIBLE"

    # PATH
    catalysts = Catalyst.query.filter_by(coverage_id=coverage.id, status="OPEN").all()
    positive_catalysts = sum(1 for row in catalysts if str(row.direction).upper() == "POSITIVE")
    negative_catalysts = sum(1 for row in catalysts if str(row.direction).upper() == "NEGATIVE")
    tape_regime = str(((tape.get("metrics") or {}).get("regime") or "MIXED")).upper()
    path_score = positive_catalysts - negative_catalysts
    path_score += 1 if tape_regime == "SUPPORTIVE" else -1 if tape_regime == "HOSTILE" else 0
    if path_score >= 2:
        path = "SUPPORTIVE"
    elif path_score <= -2:
        path = "HOSTILE"
    else:
        path = "UNCLEAR"

    # MODEL CONFIDENCE
    validation = readiness.get("validation") or {}
    val_state = str(validation.get("state") or "NOT RUN").upper()
    engine_conf = str(intelligence.get("confidence") or "LOW").upper()
    warnings = list(intelligence.get("warnings") or [])
    if val_state == "VALIDATED" and engine_conf == "HIGH" and not warnings:
        model_confidence = "STRONG"
    elif val_state in {"VALIDATED", "LIMITED"} and engine_conf in {"HIGH", "MEDIUM"}:
        model_confidence = "MODERATE"
    elif val_state == "NOT RUN":
        model_confidence = "UNVALIDATED"
    else:
        model_confidence = "LIMITED"

    # THESIS CONTROL replaces Local's monetary risk lens. Money risk lives in Portfolio.
    invalidation = _text(risk.thesis_invalidation if risk else "")
    monitor_locked = MonitoringRule.query.filter_by(coverage_id=coverage.id, locked_pre_investment=True, is_active=True).count()
    invalidating_bears = BearCaseItem.query.filter_by(coverage_id=coverage.id, invalidates=True, status="OPEN").count()
    if risk and risk.invalidation_locked_at and invalidation and monitor_locked:
        thesis_control = "CONTROLLED" if invalidating_bears == 0 else "CHALLENGED"
    elif invalidation:
        thesis_control = "DEFINED · UNLOCKED"
    else:
        thesis_control = "UNRESOLVED"

    research_ready = bool(readiness.get("ready_to_validate"))
    if not research_ready:
        conclusion = "RESEARCH INCOMPLETE"
    elif not decision_grade_valuation:
        conclusion = "DATA REVIEW"
    elif model_confidence == "UNVALIDATED":
        conclusion = "READY TO VALIDATE"
    elif value == "ATTRACTIVE" and variant == "POSITIVE EDGE" and path == "SUPPORTIVE" and model_confidence in {"STRONG", "MODERATE"}:
        conclusion = "LONG READY"
    elif value == "ATTRACTIVE" and variant in {"POSITIVE EDGE", "POSSIBLE"}:
        conclusion = "LONG WATCH"
    elif value == "EXPENSIVE" and variant == "NEGATIVE EDGE" and path == "HOSTILE" and model_confidence in {"STRONG", "MODERATE"}:
        conclusion = "SHORT READY"
    elif value == "EXPENSIVE" and variant in {"NEGATIVE EDGE", "POSSIBLE"}:
        conclusion = "SHORT WATCH"
    elif model_confidence == "LIMITED":
        conclusion = "DATA REVIEW"
    else:
        conclusion = "NO EDGE · WAIT"

    rows = [
        {"key": "business", "label": "BUSINESS", "state": business, "section": "business"},
        {"key": "value", "label": "VALUE", "state": value, "section": "valuation"},
        {"key": "expectations", "label": "EXPECTATIONS", "state": expectations, "section": "expectations"},
        {"key": "variant", "label": "VARIANT", "state": variant, "section": "expectations"},
        {"key": "path", "label": "PATH", "state": path, "section": "catalysts"},
        {"key": "model_confidence", "label": "MODEL CONFIDENCE", "state": model_confidence, "section": "validate"},
        {"key": "thesis_control", "label": "THESIS CONTROL", "state": thesis_control, "section": "monitoring"},
    ]

    return {
        "rows": rows,
        "business": business,
        "value": value,
        "expectations": expectations,
        "variant": variant,
        "path": path,
        "model_confidence": model_confidence,
        "thesis_control": thesis_control,
        "research_conclusion": conclusion,
        "valuation_base_quality": base_quality,
        "valuation_decision_grade": decision_grade_valuation,
        "implied_expectations": implied,
    }


__all__ = ["build_decision_lenses"]
