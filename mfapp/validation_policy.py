from __future__ import annotations

from typing import Any


MIN_VALID_SAMPLES = 5
VALIDATED_RELIABILITY = 65.0
REVIEW_RELIABILITY = 40.0
VALIDATION_STATES = {"NOT RUN", "REVIEW", "LIMITED", "VALIDATED"}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


def validation_state(
    *,
    exists: bool,
    execution_status: Any = None,
    sample_size: Any = 0,
    reliability: Any = None,
) -> str:
    """Return the one canonical user-facing state for a historical validation run.

    A run is VALIDATED only when it has at least five scored samples and reliability
    of at least 65. Failed/error execution is REVIEW. Insufficient evidence is
    LIMITED rather than upgraded by a strong score on too few samples.
    """
    if not exists:
        return "NOT RUN"

    status = str(execution_status or "").upper()
    if status in {"FAILED", "ERROR", "CANCELLED"}:
        return "REVIEW"

    try:
        samples = max(0, int(sample_size or 0))
    except (TypeError, ValueError, ArithmeticError):
        samples = 0
    score = _number(reliability)

    if score is None:
        return "LIMITED"
    if samples < MIN_VALID_SAMPLES:
        return "LIMITED"
    if score >= VALIDATED_RELIABILITY:
        return "VALIDATED"
    if score < REVIEW_RELIABILITY:
        return "REVIEW"
    return "LIMITED"


def state_for_run(run: Any | None) -> str:
    if run is None:
        return "NOT RUN"
    return validation_state(
        exists=True,
        execution_status=getattr(run, "status", None),
        sample_size=getattr(run, "sample_size", 0),
        reliability=getattr(run, "reliability_score", None),
    )


def validation_payload(run: Any | None) -> dict[str, Any]:
    if run is None:
        return {
            "state": "NOT RUN",
            "run_id": None,
            "status": None,
            "samples": 0,
            "reliability": None,
            "policy": {
                "min_valid_samples": MIN_VALID_SAMPLES,
                "validated_reliability": VALIDATED_RELIABILITY,
                "review_reliability": REVIEW_RELIABILITY,
            },
        }
    score = _number(getattr(run, "reliability_score", None))
    state = state_for_run(run)
    return {
        "state": state,
        "run_id": getattr(run, "id", None),
        "status": state,
        "samples": int(getattr(run, "sample_size", 0) or 0),
        "reliability": score,
        "policy": {
            "min_valid_samples": MIN_VALID_SAMPLES,
            "validated_reliability": VALIDATED_RELIABILITY,
            "review_reliability": REVIEW_RELIABILITY,
        },
    }


__all__ = [
    "MIN_VALID_SAMPLES",
    "VALIDATED_RELIABILITY",
    "REVIEW_RELIABILITY",
    "VALIDATION_STATES",
    "validation_state",
    "state_for_run",
    "validation_payload",
]
