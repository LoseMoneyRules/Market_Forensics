from __future__ import annotations

from typing import Any

from .core_models import MonitoringHistory, MonitoringRule
from .extensions import db
from .models import UserPreference


FAIL_STATUSES = {"FAIL", "CRITICAL", "BREACH", "TRIGGERED"}
PASS_STATUSES = {"OK", "PASS", "CONFIRMED", "MET"}
CONDITION_KINDS = ("ADD", "TRIM", "EXIT")
CONDITION_KEY_PREFIX = "portfolio_conditions:"


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _condition_key(security_id: int) -> str:
    return f"{CONDITION_KEY_PREFIX}{int(security_id)}"


def _latest_history(rule_id: int) -> MonitoringHistory | None:
    return MonitoringHistory.query.filter_by(rule_id=rule_id).order_by(
        MonitoringHistory.observed_at.desc(), MonitoringHistory.id.desc()
    ).first()


def load_position_condition_links(user_id: int, security_id: int) -> dict[str, dict[str, Any]]:
    row = UserPreference.query.filter_by(user_id=user_id, key=_condition_key(security_id)).first()
    payload = dict((row.value or {}) if row else {})
    stored = dict(payload.get("conditions") or {})
    out: dict[str, dict[str, Any]] = {}
    for kind in CONDITION_KINDS:
        raw = dict(stored.get(kind) or {})
        rule_id = raw.get("rule_id")
        try:
            rule_id = int(rule_id) if rule_id not in (None, "") else None
        except (TypeError, ValueError):
            rule_id = None
        confirm_on = _text(raw.get("confirm_on")).upper()
        if confirm_on not in {"OK", "TRIGGERED"}:
            confirm_on = "OK"
        out[kind] = {"rule_id": rule_id, "confirm_on": confirm_on}
    return out


def save_position_condition_links(
    *,
    user_id: int,
    security_id: int,
    coverage_id: int | None,
    conditions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Persist CONTROL-private Portfolio links to existing Monitoring rules.

    This creates no second monitoring engine. The Portfolio condition text remains
    Portfolio-owned; a linked Research Monitoring rule only provides an auditable
    confirmation state.
    """
    allowed: set[int] = set()
    if coverage_id:
        allowed = {
            int(row.id)
            for row in MonitoringRule.query.filter_by(
                coverage_id=coverage_id,
                is_active=True,
            ).all()
        }

    normalized: dict[str, dict[str, Any]] = {}
    for kind in CONDITION_KINDS:
        raw = dict(conditions.get(kind) or {})
        try:
            rule_id = int(raw.get("rule_id")) if raw.get("rule_id") not in (None, "") else None
        except (TypeError, ValueError):
            rule_id = None
        if rule_id not in allowed:
            rule_id = None
        confirm_on = _text(raw.get("confirm_on")).upper()
        if confirm_on not in {"OK", "TRIGGERED"}:
            confirm_on = "OK"
        normalized[kind] = {"rule_id": rule_id, "confirm_on": confirm_on}

    key = _condition_key(security_id)
    row = UserPreference.query.filter_by(user_id=user_id, key=key).first()
    value = {"conditions": normalized}
    if row is None:
        row = UserPreference(user_id=user_id, key=key, value=value)
        db.session.add(row)
    else:
        row.value = value
    return normalized


def monitoring_condition_state(
    *,
    user_id: int,
    security_id: int,
    coverage_id: int | None,
) -> dict[str, Any]:
    links = load_position_condition_links(user_id, security_id)
    if not coverage_id:
        return {
            "conditions": {
                kind: {
                    "rule_id": None,
                    "rule_name": "",
                    "confirm_on": links[kind]["confirm_on"],
                    "latest_status": "NOT LINKED",
                    "confirmed": False,
                    "observed_at": None,
                }
                for kind in CONDITION_KINDS
            },
            "available_rules": [],
        }

    rules = MonitoringRule.query.filter_by(
        coverage_id=coverage_id,
        is_active=True,
    ).order_by(MonitoringRule.id.asc()).all()
    rule_map = {int(rule.id): rule for rule in rules}
    latest_map: dict[int, MonitoringHistory | None] = {}
    available_rules: list[dict[str, Any]] = []
    for rule in rules:
        latest = _latest_history(rule.id)
        latest_map[int(rule.id)] = latest
        available_rules.append({
            "id": int(rule.id),
            "name": rule.name,
            "metric": rule.metric,
            "operator": rule.operator,
            "threshold_value": _num(rule.threshold_value),
            "threshold_text": rule.threshold_text,
            "unit": rule.unit,
            "latest_status": _text(latest.status).upper() if latest else "NOT OBSERVED",
            "observed_at": latest.observed_at.isoformat() if latest and latest.observed_at else None,
            "locked_pre_investment": bool(rule.locked_pre_investment),
        })

    conditions: dict[str, dict[str, Any]] = {}
    for kind in CONDITION_KINDS:
        link = links[kind]
        rule = rule_map.get(link["rule_id"]) if link["rule_id"] else None
        latest = latest_map.get(int(rule.id)) if rule else None
        status = _text(latest.status).upper() if latest else ("NOT OBSERVED" if rule else "NOT LINKED")
        confirm_on = link["confirm_on"]
        confirmed = (
            status in PASS_STATUSES if confirm_on == "OK"
            else status in FAIL_STATUSES
        )
        conditions[kind] = {
            "rule_id": int(rule.id) if rule else None,
            "rule_name": rule.name if rule else "",
            "confirm_on": confirm_on,
            "latest_status": status,
            "confirmed": bool(confirmed),
            "observed_at": latest.observed_at.isoformat() if latest and latest.observed_at else None,
        }

    return {"conditions": conditions, "available_rules": available_rules}


def monitoring_invalidation_state(coverage_id: int | None, research_risk: Any) -> dict[str, Any]:
    """Read only stored monitoring state; never calls a provider."""
    locked = bool(
        research_risk
        and getattr(research_risk, "invalidation_locked_at", None)
        and _text(getattr(research_risk, "thesis_invalidation", ""))
    )
    if not coverage_id:
        return {"locked": locked, "triggered": False, "failed_rules": [], "latest": []}

    rules = MonitoringRule.query.filter_by(
        coverage_id=coverage_id,
        is_active=True,
        locked_pre_investment=True,
    ).order_by(MonitoringRule.id.asc()).all()

    latest_rows: list[dict[str, Any]] = []
    failed_rules: list[str] = []
    for rule in rules:
        history = _latest_history(rule.id)
        status = _text(history.status).upper() if history else "NOT OBSERVED"
        latest_rows.append({
            "rule_id": rule.id,
            "name": rule.name,
            "status": status,
            "severity": _text(rule.severity).upper() or "WATCH",
            "observed_at": history.observed_at.isoformat() if history and history.observed_at else None,
        })
        if status in FAIL_STATUSES:
            failed_rules.append(rule.name)

    return {
        "locked": locked,
        "triggered": bool(locked and failed_rules),
        "failed_rules": failed_rules,
        "latest": latest_rows,
    }


def decide_position_action(
    *,
    has_position: bool,
    side: str,
    research_attached: bool,
    research_conclusion: str,
    validation_state: str,
    value_state: str,
    variant_state: str,
    path_state: str,
    model_confidence: str,
    thesis_control: str,
    invalidation_triggered: bool,
    portfolio_weight_pct: float | None,
    max_position_pct: float | None,
    suggested_position_pct: float | None,
    entry_conditions: str = "",
    add_conditions: str = "",
    trim_conditions: str = "",
    exit_conditions: str = "",
    add_evidence_required: bool = False,
    add_evidence_confirmed: bool = False,
    add_evidence_status: str = "NOT LINKED",
    trim_condition_confirmed: bool = False,
    trim_condition_status: str = "NOT LINKED",
    exit_condition_confirmed: bool = False,
    exit_condition_status: str = "NOT LINKED",
) -> dict[str, Any]:
    """Deterministic Portfolio action downstream from the canonical Research conclusion.

    Deliberately absent inputs: market price, average cost and P/L. Price can affect
    already-computed Portfolio exposure/sizing, but price movement is never evidence
    and can never satisfy an add condition.
    """
    side = _text(side).upper()
    if side not in {"LONG", "SHORT"}:
        side = "LONG"
    conclusion = _text(research_conclusion).upper() or "RESEARCH INCOMPLETE"
    validation = _text(validation_state).upper() or "NOT RUN"
    value = _text(value_state).upper() or "UNVERIFIED"
    variant = _text(variant_state).upper() or "UNPROVEN"
    path = _text(path_state).upper() or "UNCLEAR"
    confidence = _text(model_confidence).upper() or "UNVALIDATED"
    thesis = _text(thesis_control).upper() or "UNRESOLVED"
    weight = _num(portfolio_weight_pct)
    cap = _num(max_position_pct)
    suggested = _num(suggested_position_pct)

    limits = [x for x in (cap, suggested) if x is not None and x >= 0]
    effective_limit = min(limits) if limits else None
    risk_breach = bool(
        has_position
        and weight is not None
        and effective_limit is not None
        and weight > effective_limit + 0.05
    )
    risk_headroom = bool(
        has_position
        and weight is not None
        and effective_limit is not None
        and weight + 0.05 < effective_limit
    )

    audit_inputs = {
        "has_position": bool(has_position),
        "side": side,
        "research_attached": bool(research_attached),
        "research_conclusion": conclusion,
        "validation_state": validation,
        "value": value,
        "variant": variant,
        "path": path,
        "model_confidence": confidence,
        "thesis_control": thesis,
        "invalidation_triggered": bool(invalidation_triggered),
        "portfolio_weight_pct": weight,
        "max_position_pct": cap,
        "suggested_position_pct": suggested,
        "effective_limit_pct": effective_limit,
        "risk_breach": risk_breach,
        "risk_headroom": risk_headroom,
        "entry_conditions_defined": bool(_text(entry_conditions)),
        "add_conditions_defined": bool(_text(add_conditions)),
        "trim_conditions_defined": bool(_text(trim_conditions)),
        "exit_conditions_defined": bool(_text(exit_conditions)),
        "add_evidence_required": bool(add_evidence_required),
        "add_evidence_confirmed": bool(add_evidence_confirmed),
        "add_evidence_status": _text(add_evidence_status).upper() or "NOT LINKED",
        "trim_condition_confirmed": bool(trim_condition_confirmed),
        "trim_condition_status": _text(trim_condition_status).upper() or "NOT LINKED",
        "exit_condition_confirmed": bool(exit_condition_confirmed),
        "exit_condition_status": _text(exit_condition_status).upper() or "NOT LINKED",
    }

    def result(rule: str, action: str, why: str, blocker: str, next_confirmation: str, risk_state: str) -> dict[str, Any]:
        return {
            "action": action,
            "why_now": why,
            "blocker": blocker,
            "next_confirmation": next_confirmation,
            "risk_state": risk_state,
            "rule": rule,
            "research_conclusion": conclusion,
            "audit": audit_inputs,
        }

    if invalidation_triggered:
        if has_position and side == "SHORT":
            return result(
                "LOCKED_INVALIDATION_COVER", "COVER",
                "A locked pre-investment thesis invalidation is triggered; the short thesis no longer has permission to remain open.",
                "Nothing stronger is required from valuation or price. Invalidation has precedence.",
                _text(exit_conditions) or "Cover according to the locked invalidation discipline; do not rewrite the failed threshold.",
                "LOCKED THESIS INVALIDATION TRIGGERED",
            )
        if has_position:
            return result(
                "LOCKED_INVALIDATION_EXIT", "EXIT / SELL",
                "A locked pre-investment thesis invalidation is triggered; valuation does not override a failed thesis.",
                "Nothing stronger is required from valuation or price. Invalidation has precedence.",
                _text(exit_conditions) or "Exit according to the locked invalidation discipline; do not rewrite the failed threshold.",
                "LOCKED THESIS INVALIDATION TRIGGERED",
            )
        return result(
            "LOCKED_INVALIDATION_NO_ENTRY", "WAIT",
            "A locked pre-investment thesis invalidation is triggered, so new exposure is not permitted.",
            "The thesis must be rebuilt as a new research decision; the old threshold cannot be rewritten retroactively.",
            "Resolve the invalidated thesis with new evidence before considering a new position.",
            "LOCKED THESIS INVALIDATION TRIGGERED",
        )

    if has_position and exit_condition_confirmed:
        if side == "SHORT":
            return result(
                "PORTFOLIO_EXIT_CONDITION_COVER", "COVER",
                "The explicit Portfolio exit condition has been confirmed by its linked Monitoring evidence.",
                "The Portfolio exit discipline has precedence over valuation comfort or P/L.",
                _text(exit_conditions) or "Cover according to the pre-defined Portfolio exit condition.",
                f"EXIT CONDITION CONFIRMED · {_text(exit_condition_status).upper() or 'CONFIRMED'}",
            )
        return result(
            "PORTFOLIO_EXIT_CONDITION_SELL", "EXIT / SELL",
            "The explicit Portfolio exit condition has been confirmed by its linked Monitoring evidence.",
            "The Portfolio exit discipline has precedence over valuation comfort or P/L.",
            _text(exit_conditions) or "Exit according to the pre-defined Portfolio exit condition.",
            f"EXIT CONDITION CONFIRMED · {_text(exit_condition_status).upper() or 'CONFIRMED'}",
        )

    if risk_breach:
        limit_text = f"{effective_limit:.1f}%" if effective_limit is not None else "the configured limit"
        if side == "SHORT":
            return result(
                "RISK_BREACH_REDUCE_SHORT", "REDUCE SHORT",
                f"Current gross weight exceeds the effective Portfolio risk limit of {limit_text}.",
                "Research direction cannot bypass a money-risk breach.",
                f"Bring the short back within {limit_text}; then reassess only when evidence changes.",
                "PORTFOLIO RISK LIMIT BREACH",
            )
        return result(
            "RISK_BREACH_REDUCE_LONG", "REDUCE",
            f"Current gross weight exceeds the effective Portfolio risk limit of {limit_text}.",
            "Research direction cannot bypass a money-risk breach.",
            f"Bring the position back within {limit_text}; then reassess only when evidence changes.",
            "PORTFOLIO RISK LIMIT BREACH",
        )

    if has_position and trim_condition_confirmed:
        if side == "SHORT":
            return result(
                "PORTFOLIO_TRIM_CONDITION_SHORT", "REDUCE SHORT",
                "The explicit Portfolio trim condition has been confirmed by its linked Monitoring evidence.",
                "A confirmed trim condition authorizes less exposure, not a larger position.",
                _text(trim_conditions) or "Reduce the short according to the stored trim discipline.",
                f"TRIM CONDITION CONFIRMED · {_text(trim_condition_status).upper() or 'CONFIRMED'}",
            )
        return result(
            "PORTFOLIO_TRIM_CONDITION_LONG", "REDUCE",
            "The explicit Portfolio trim condition has been confirmed by its linked Monitoring evidence.",
            "A confirmed trim condition authorizes less exposure, not a larger position.",
            _text(trim_conditions) or "Reduce according to the stored trim discipline.",
            f"TRIM CONDITION CONFIRMED · {_text(trim_condition_status).upper() or 'CONFIRMED'}",
        )

    if not research_attached:
        return result(
            "PORTFOLIO_ONLY", "HOLD / WAIT" if has_position else "WAIT",
            "Portfolio tracking is available, but no Research conclusion is attached to this security.",
            "No complete Research file exists, so BUY / ADD / SHORT actions are blocked.",
            "Start and complete Research before taking a stronger directional action.",
            "PORTFOLIO ONLY · RESEARCH NOT ATTACHED",
        )

    if conclusion == "RESEARCH INCOMPLETE":
        return result(
            "RESEARCH_INCOMPLETE", "HOLD / WAIT" if has_position else "WAIT",
            "Research is incomplete; Portfolio cannot manufacture a directional conclusion from position data.",
            "Incomplete Research blocks BUY, ADD and new SHORT exposure.",
            "Complete the outstanding Research gates and re-evaluate the canonical Research Conclusion.",
            f"THESIS {thesis}",
        )
    if conclusion == "DATA REVIEW":
        return result(
            "DATA_REVIEW", "DATA REVIEW",
            "Research has a data-quality/model-review condition, so Portfolio keeps the directional action fail-closed.",
            "Data review blocks stronger directional action even if valuation appears attractive or expensive.",
            "Resolve the Research data warning and regenerate the canonical Research Conclusion.",
            f"THESIS {thesis}",
        )
    if conclusion == "READY TO VALIDATE":
        return result(
            "READY_TO_VALIDATE", "HOLD / WAIT" if has_position else "WAIT",
            "Research is complete but has not cleared Validate.",
            "Validation is the next gate; position data cannot bypass it.",
            "Run Validate and wait for the Research Conclusion to update.",
            f"THESIS {thesis}",
        )

    if has_position and side == "LONG" and conclusion == "SHORT READY":
        return result(
            "OPPOSITE_SHORT_READY", "REDUCE",
            "Research is SHORT READY while the live position is LONG; the evidence now conflicts with the existing exposure.",
            "A full exit still requires locked invalidation or an explicit exit discipline; price movement alone is not enough.",
            _text(exit_conditions) or "Reassess the long against the new SHORT READY evidence and the locked invalidation.",
            f"THESIS {thesis} · DIRECTION CONFLICT",
        )
    if has_position and side == "SHORT" and conclusion == "LONG READY":
        return result(
            "OPPOSITE_LONG_READY", "REDUCE SHORT",
            "Research is LONG READY while the live position is SHORT; the evidence now conflicts with the existing exposure.",
            "A full cover still requires locked invalidation or an explicit exit discipline; price movement alone is not enough.",
            _text(exit_conditions) or "Reassess the short against the new LONG READY evidence and the locked invalidation.",
            f"THESIS {thesis} · DIRECTION CONFLICT",
        )

    if not has_position:
        if conclusion == "LONG READY":
            return result(
                "NO_POSITION_LONG_READY", "BUY CANDIDATE",
                "Research is LONG READY; Portfolio may now evaluate entry and sizing.",
                "Candidate status is not an order. Entry conditions and money-risk sizing still govern execution.",
                _text(entry_conditions) or "Define/confirm entry evidence and a Portfolio risk plan before opening exposure.",
                f"THESIS {thesis} · NO LIVE POSITION",
            )
        if conclusion == "SHORT READY":
            return result(
                "NO_POSITION_SHORT_READY", "SHORT CANDIDATE",
                "Research is SHORT READY; Portfolio may now evaluate entry and sizing.",
                "Candidate status is not an order. Entry conditions, borrow/liquidity context and money-risk sizing still govern execution.",
                _text(entry_conditions) or "Define/confirm short-entry evidence and a Portfolio risk plan before opening exposure.",
                f"THESIS {thesis} · NO LIVE POSITION",
            )
        return result(
            "NO_POSITION_WAIT", "WAIT",
            f"Research conclusion is {conclusion}; it does not justify new exposure.",
            "WATCH / NO EDGE states do not create a new position.",
            "Wait for new evidence that changes the canonical Research Conclusion.",
            f"THESIS {thesis} · NO LIVE POSITION",
        )

    if side == "LONG" and conclusion == "LONG READY":
        add_evidence_ok = (not add_evidence_required) or add_evidence_confirmed
        add_ready = (
            validation == "VALIDATED"
            and thesis == "CONTROLLED"
            and path == "SUPPORTIVE"
            and risk_headroom
            and bool(_text(add_conditions))
            and add_evidence_ok
        )
        if add_ready:
            evidence_suffix = " Linked Monitoring evidence is confirmed." if add_evidence_required else ""
            return result(
                "LONG_READY_ADD_ON_EVIDENCE", "ADD ON EVIDENCE",
                "LONG READY research is validated, thesis control is intact and Portfolio risk has room for more exposure." + evidence_suffix,
                "This is conditional, not an automatic buy. Price movement is not confirmation.",
                _text(add_conditions),
                f"WITHIN RISK LIMIT · EFFECTIVE LIMIT {effective_limit:.1f}%",
            )
        blockers = []
        if validation != "VALIDATED": blockers.append(f"Validate is {validation}")
        if thesis != "CONTROLLED": blockers.append(f"thesis control is {thesis}")
        if path != "SUPPORTIVE": blockers.append(f"path is {path}")
        if not risk_headroom: blockers.append("no proven sizing headroom")
        if not _text(add_conditions): blockers.append("evidence-to-add condition is not defined")
        if add_evidence_required and not add_evidence_confirmed:
            blockers.append(f"linked add evidence is {_text(add_evidence_status).upper() or 'NOT OBSERVED'}")
        return result(
            "LONG_READY_HOLD", "HOLD",
            "Research remains LONG READY, but Portfolio discipline does not yet permit a conditional add.",
            "; ".join(blockers) or "A stronger action is not currently justified.",
            _text(add_conditions) or "Define the evidence required to add; a lower price by itself is not evidence.",
            f"THESIS {thesis} · WITHIN CURRENT RISK",
        )

    if side == "SHORT" and conclusion == "SHORT READY":
        add_evidence_ok = (not add_evidence_required) or add_evidence_confirmed
        add_ready = (
            validation == "VALIDATED"
            and thesis == "CONTROLLED"
            and path == "HOSTILE"
            and risk_headroom
            and bool(_text(add_conditions))
            and add_evidence_ok
        )
        if add_ready:
            evidence_suffix = " Linked Monitoring evidence is confirmed." if add_evidence_required else ""
            return result(
                "SHORT_READY_ADD_ON_EVIDENCE", "ADD SHORT ON EVIDENCE",
                "SHORT READY research is validated, thesis control is intact and Portfolio risk has room for more short exposure." + evidence_suffix,
                "This is conditional, not an automatic short. Price movement is not confirmation.",
                _text(add_conditions),
                f"WITHIN RISK LIMIT · EFFECTIVE LIMIT {effective_limit:.1f}%",
            )
        blockers = []
        if validation != "VALIDATED": blockers.append(f"Validate is {validation}")
        if thesis != "CONTROLLED": blockers.append(f"thesis control is {thesis}")
        if path != "HOSTILE": blockers.append(f"path is {path}")
        if not risk_headroom: blockers.append("no proven sizing headroom")
        if not _text(add_conditions): blockers.append("evidence-to-add condition is not defined")
        if add_evidence_required and not add_evidence_confirmed:
            blockers.append(f"linked add evidence is {_text(add_evidence_status).upper() or 'NOT OBSERVED'}")
        return result(
            "SHORT_READY_HOLD", "HOLD SHORT",
            "Research remains SHORT READY, but Portfolio discipline does not yet permit a conditional add.",
            "; ".join(blockers) or "A stronger action is not currently justified.",
            _text(add_conditions) or "Define the evidence required to add; a higher price by itself is not evidence.",
            f"THESIS {thesis} · WITHIN CURRENT RISK",
        )

    if conclusion in {"LONG WATCH", "SHORT WATCH", "NO EDGE · WAIT"}:
        return result(
            "WATCH_OR_NO_EDGE", "HOLD / WAIT",
            f"Research conclusion is {conclusion}; existing exposure may remain while evidence develops, but no stronger action is justified.",
            "WATCH / NO EDGE cannot authorize an add or a new directional position.",
            "Wait for evidence to move Research to READY or trigger the locked invalidation/risk discipline.",
            f"THESIS {thesis} · WITHIN CURRENT RISK",
        )

    return result(
        "UNKNOWN_FAIL_CLOSED", "DATA REVIEW",
        "The Research conclusion is not recognized by the Portfolio action policy.",
        "Unknown state blocks directional action.",
        "Review the stored Research conclusion and Portfolio inputs.",
        f"THESIS {thesis}",
    )


def build_position_action(
    *,
    position: Any,
    side: str,
    research_attached: bool,
    decision_lenses: dict[str, Any],
    readiness: dict[str, Any],
    research_risk: Any,
    money_risk: Any,
    portfolio_weight_pct: float | None,
    sizing: dict[str, Any],
    monitoring_state: dict[str, Any],
    condition_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapter from stored/materialized web state into the pure decision policy."""
    has_position = bool(position is not None and abs(_num(getattr(position, "shares", 0)) or 0) > 0)
    validation = dict(readiness.get("validation") or {})
    conditions = dict((condition_state or {}).get("conditions") or {})
    add_state = dict(conditions.get("ADD") or {})
    trim_state = dict(conditions.get("TRIM") or {})
    exit_state = dict(conditions.get("EXIT") or {})
    return decide_position_action(
        has_position=has_position,
        side=side,
        research_attached=research_attached,
        research_conclusion=decision_lenses.get("research_conclusion") or "RESEARCH INCOMPLETE",
        validation_state=validation.get("state") or "NOT RUN",
        value_state=decision_lenses.get("value") or "UNVERIFIED",
        variant_state=decision_lenses.get("variant") or "UNPROVEN",
        path_state=decision_lenses.get("path") or "UNCLEAR",
        model_confidence=decision_lenses.get("model_confidence") or "UNVALIDATED",
        thesis_control=decision_lenses.get("thesis_control") or "UNRESOLVED",
        invalidation_triggered=bool(monitoring_state.get("triggered")),
        portfolio_weight_pct=portfolio_weight_pct,
        max_position_pct=getattr(money_risk, "max_position_pct", None) if money_risk else None,
        suggested_position_pct=sizing.get("suggested_position_pct"),
        entry_conditions=getattr(money_risk, "entry_conditions", "") if money_risk else "",
        add_conditions=getattr(money_risk, "add_conditions", "") if money_risk else "",
        trim_conditions=getattr(money_risk, "trim_conditions", "") if money_risk else "",
        exit_conditions=getattr(money_risk, "exit_conditions", "") if money_risk else "",
        add_evidence_required=bool(add_state.get("rule_id")),
        add_evidence_confirmed=bool(add_state.get("confirmed")),
        add_evidence_status=add_state.get("latest_status") or "NOT LINKED",
        trim_condition_confirmed=bool(trim_state.get("confirmed")),
        trim_condition_status=trim_state.get("latest_status") or "NOT LINKED",
        exit_condition_confirmed=bool(exit_state.get("confirmed")),
        exit_condition_status=exit_state.get("latest_status") or "NOT LINKED",
    )


__all__ = [
    "CONDITION_KINDS",
    "build_position_action",
    "decide_position_action",
    "load_position_condition_links",
    "monitoring_condition_state",
    "monitoring_invalidation_state",
    "save_position_condition_links",
]
