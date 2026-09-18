from __future__ import annotations

import hashlib
import json
from typing import Any

from .core_models import (
    BearCaseItem, Catalyst, Company, Coverage, DecisionJournal, Expectation, FinancialFlow,
    FinancialPeriod, HistoricalTestRun, ManagementAssessment, MonitoringRule,
    ResearchGateApproval, ResearchState, Security, Source, ValuationModel,
)
from .extensions import db
from .services import valuation_result


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _gate(label: str, key: str, evidence: Any, evidence_ready: bool) -> dict[str, Any]:
    return {"key": key, "label": label, "evidence": evidence, "evidence_ready": bool(evidence_ready), "evidence_hash": _hash(evidence)}


def research_readiness(coverage: Coverage) -> dict[str, Any]:
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id) if security else None
    valuation = valuation_result(coverage)
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    scenario_rows = {row.name.upper(): row for row in model.scenarios} if model else {}

    annual_count = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY").count() if company else 0
    quarter_count = FinancialPeriod.query.filter(FinancialPeriod.company_id == company.id, FinancialPeriod.period_type.in_(["Q1", "Q2", "Q3", "Q4"])).count() if company else 0
    expectation_count = Expectation.query.filter_by(coverage_id=coverage.id).count()
    bear_count = BearCaseItem.query.filter_by(coverage_id=coverage.id).count()
    catalyst_count = Catalyst.query.filter_by(coverage_id=coverage.id).count()
    management_count = ManagementAssessment.query.filter_by(coverage_id=coverage.id).count()
    monitor_count = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).count()
    journal_count = DecisionJournal.query.filter_by(coverage_id=coverage.id).count()
    source_count = Source.query.filter_by(company_id=company.id).count() if company else 0
    flow_count = 0
    if company:
        flow_count = FinancialFlow.query.join(FinancialPeriod, FinancialFlow.financial_period_id == FinancialPeriod.id).filter(FinancialPeriod.company_id == company.id).count()
    hist = HistoricalTestRun.query.filter_by(coverage_id=coverage.id).order_by(HistoricalTestRun.created_at.desc()).first()

    gates = [
        _gate(
            "Thesis / Variant", "overview",
            {
                "thesis": _text(research.thesis if research else ""),
                "counter": _text(research.counter_evidence if research else ""),
                "market": _text(research.variant_market if research else ""),
                "variant": _text(research.variant_us if research else ""),
                "evidence": _text(research.variant_evidence if research else ""),
            },
            bool(research and _text(research.thesis) and _text(research.counter_evidence) and _text(research.variant_us)),
        ),
        _gate("Business", "business", {"text": _text(research.business if research else ""), "company": company.display_name if company else ""}, bool(research and _text(research.business))),
        _gate("Fundamentals", "fundamentals", {"annual_periods": annual_count, "quarter_periods": quarter_count, "text": _text(research.numbers if research else "")}, annual_count >= 2 and bool(research and _text(research.numbers))),
        _gate("Expectations", "expectations", {"structured": expectation_count, "text": _text(research.expectations if research else ""), "base_inputs": (scenario_rows.get("BASE").inputs if scenario_rows.get("BASE") else {})}, expectation_count > 0 or bool(research and _text(research.expectations))),
        _gate("Valuation", "valuation", {"bear": valuation.get("bear"), "base": valuation.get("base"), "bull": valuation.get("bull"), "quality": ((model.assumptions or {}).get("latest_engine_result") or {}).get("quality") if model else None}, all(valuation.get(k) is not None for k in ("bear", "base", "bull"))),
        _gate("Bear Case", "bear-case", {"structured": bear_count, "text": _text(research.bear_case_summary if research else "")}, bear_count > 0 or bool(research and _text(research.bear_case_summary))),
        _gate("Catalysts", "catalysts", {"structured": catalyst_count, "text": _text(research.catalysts_summary if research else "")}, catalyst_count > 0 or bool(research and _text(research.catalysts_summary))),
        _gate("Financial Flows", "financial-flows", {"flows": flow_count, "text": _text(research.flows_summary if research else "")}, flow_count > 0),
        _gate("Management", "management", {"assessments": management_count, "text": _text(research.management_summary if research else "")}, management_count > 0 or bool(research and _text(research.management_summary))),
        _gate("Tape / Flows", "tape", {"text": _text(research.tape_summary if research else ""), "sources": source_count}, bool(research and _text(research.tape_summary))),
        _gate("Monitoring", "monitoring", {"rules": monitor_count}, monitor_count > 0),
        _gate("Decision Journal", "journal", {"entries": journal_count}, journal_count > 0),
        _gate("Sources / Audit", "audit", {"sources": source_count}, source_count > 0),
    ]

    approvals = {row.gate_key: row for row in ResearchGateApproval.query.filter_by(coverage_id=coverage.id).all()}
    for gate in gates:
        approval = approvals.get(gate["key"])
        fresh = bool(approval and approval.evidence_hash == gate["evidence_hash"])
        gate["approved"] = fresh
        gate["stale_approval"] = bool(approval and not fresh)
        gate["approved_at"] = approval.approved_at if fresh else None
        gate["status"] = "APPROVED" if fresh else ("PENDING APPROVAL" if gate["evidence_ready"] else "MISSING EVIDENCE")

    done = sum(1 for gate in gates if gate["approved"])
    reliability = float(hist.reliability_score) if hist and hist.reliability_score is not None else None
    if hist is None:
        validation_state = "NOT RUN"
    elif str(hist.status or "").upper() in {"FAILED", "ERROR"}:
        validation_state = "REVIEW"
    elif (hist.sample_size or 0) < 3 or reliability is None:
        validation_state = "LIMITED"
    elif reliability >= 65 and (hist.sample_size or 0) >= 5:
        validation_state = "VALIDATED"
    elif reliability < 40:
        validation_state = "REVIEW"
    else:
        validation_state = "LIMITED"

    return {
        "done": done,
        "evidence_ready": sum(1 for gate in gates if gate["evidence_ready"]),
        "total": len(gates),
        "gates": gates,
        "ready_to_validate": bool(gates) and done == len(gates),
        "validation": {
            "state": validation_state,
            "run_id": hist.id if hist else None,
            "status": hist.status if hist else None,
            "samples": hist.sample_size if hist else 0,
            "reliability": reliability,
        },
        "bias_flags": [],
    }


def evidence_hash_for_gate(coverage: Coverage, gate_key: str) -> str | None:
    row = next((gate for gate in research_readiness(coverage)["gates"] if gate["key"] == gate_key), None)
    return row["evidence_hash"] if row else None


__all__ = ["research_readiness", "evidence_hash_for_gate"]
