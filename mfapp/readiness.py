from __future__ import annotations

import hashlib
import json
from typing import Any

from .core_models import (
    BearCaseItem, Catalyst, Company, Coverage, DecisionJournal, Expectation, FinancialFlow,
    FinancialPeriod, HistoricalTestRun, ManagementAssessment, MonitoringRule,
    ResearchGateApproval, ResearchState, RiskPlan, Security, Source, ValuationModel,
)
from .extensions import db
from .services import valuation_result
from .validation_policy import validation_payload
from .research_basis import FINANCIAL_REVIEW_GATES, latest_financial_basis


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
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    invalidation_text = _text(risk.thesis_invalidation if risk else "")
    source_count = Source.query.filter_by(company_id=company.id).count() if company else 0
    flow_count = 0
    if company:
        flow_count = FinancialFlow.query.join(FinancialPeriod, FinancialFlow.financial_period_id == FinancialPeriod.id).filter(FinancialPeriod.company_id == company.id).count()
    hist = HistoricalTestRun.query.filter_by(coverage_id=coverage.id).order_by(HistoricalTestRun.created_at.desc()).first()
    financial_basis = latest_financial_basis(company.id if company else None)

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
        _gate(
            "Monitoring", "monitoring",
            {"rules": monitor_count, "thesis_invalidation": invalidation_text},
            monitor_count > 0 or bool(invalidation_text),
        ),
        _gate("Decision Journal", "journal", {"entries": journal_count}, journal_count > 0),
        _gate("Sources / Audit", "audit", {"sources": source_count}, source_count > 0),
    ]

    approvals = {row.gate_key: row for row in ResearchGateApproval.query.filter_by(coverage_id=coverage.id).all()}
    basis_materialized_at = None
    try:
        from datetime import datetime
        basis_materialized_at = datetime.fromisoformat(str(financial_basis.get("materialized_at"))) if financial_basis.get("materialized_at") else None
    except Exception:
        basis_materialized_at = None

    for gate in gates:
        approval = approvals.get(gate["key"])
        evidence_changed = bool(approval and approval.evidence_hash != gate["evidence_hash"])
        financial_review_required = bool(
            approval
            and gate["key"] in FINANCIAL_REVIEW_GATES
            and basis_materialized_at is not None
            and approval.approved_at is not None
            and basis_materialized_at > approval.approved_at
        )
        # Preserve the historical human approval row. A new material financial
        # basis invalidates only its CURRENT effectiveness; ordinary evidence
        # drift remains visible without reopening unrelated gates.
        gate["prior_approval_exists"] = bool(approval)
        gate["approved"] = bool(approval) and not financial_review_required
        gate["financial_review_required"] = financial_review_required
        gate["review_required"] = financial_review_required
        gate["stale_approval"] = evidence_changed or financial_review_required
        gate["evidence_changed"] = evidence_changed
        gate["approved_at"] = approval.approved_at if approval else None
        if financial_review_required:
            gate["status"] = "REVIEW REQUIRED"
        else:
            gate["status"] = "APPROVED" if approval else ("PENDING APPROVAL" if gate["evidence_ready"] else "MISSING EVIDENCE")

    done = sum(1 for gate in gates if gate["approved"])
    validation = validation_payload(hist)
    reopened = [gate["key"] for gate in gates if gate.get("financial_review_required")]

    return {
        "done": done,
        "evidence_ready": sum(1 for gate in gates if gate["evidence_ready"]),
        "total": len(gates),
        "gates": gates,
        "ready_to_validate": bool(gates) and done == len(gates),
        "review_required": bool(reopened),
        "review_required_count": len(reopened),
        "reopened_gates": reopened,
        "financial_basis": financial_basis,
        "review_banner": "NEW FINANCIAL EVIDENCE — REVIEW REQUIRED" if reopened else "",
        "validation": validation,
        "bias_flags": [],
    }


def evidence_hash_for_gate(coverage: Coverage, gate_key: str) -> str | None:
    row = next((gate for gate in research_readiness(coverage)["gates"] if gate["key"] == gate_key), None)
    return row["evidence_hash"] if row else None


__all__ = ["research_readiness", "evidence_hash_for_gate"]
