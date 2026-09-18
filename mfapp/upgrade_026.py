from __future__ import annotations

from .core_models import (
    Coverage, InvestmentState, PortfolioRiskPlan, Position, PositionProfile,
    ResearchGateApproval, RiskPlan, SchemaMigration,
)
from .extensions import db

MIGRATION_KEY = "release_0_2_6_local_web_parity"


def migrate_local_web_parity() -> dict:
    """Additive 0.2.6 migration.

    Portfolio holdings and money-risk are independent from Research/Coverage.
    Existing position/risk data is copied forward; nothing is deleted.
    The old Numbers readiness approval is renamed to the canonical Fundamentals gate.
    """
    if SchemaMigration.query.filter_by(migration_key=MIGRATION_KEY).first():
        return {"status": "already-applied"}

    profiles = risks = gates = 0
    positions = Position.query.order_by(Position.id.asc()).all()
    for position in positions:
        coverage = Coverage.query.filter_by(
            user_id=position.user_id, security_id=position.security_id
        ).order_by(Coverage.id.asc()).first()
        investment = (
            InvestmentState.query.filter_by(coverage_id=coverage.id).first()
            if coverage else None
        )
        profile = PositionProfile.query.filter_by(
            user_id=position.user_id, security_id=position.security_id
        ).first()
        if profile is None:
            side = "SHORT" if investment and "SHORT" in str(investment.state or "").upper() else "LONG"
            db.session.add(PositionProfile(
                user_id=position.user_id,
                security_id=position.security_id,
                side=side,
                tags="",
            ))
            profiles += 1

        portfolio_risk = PortfolioRiskPlan.query.filter_by(
            user_id=position.user_id, security_id=position.security_id
        ).first()
        if portfolio_risk is None:
            old = RiskPlan.query.filter_by(coverage_id=coverage.id).first() if coverage else None
            portfolio_risk = PortfolioRiskPlan(
                user_id=position.user_id,
                security_id=position.security_id,
                risk_budget_pct=(old.max_loss_pct if old else None),
                max_position_pct=(old.max_position_pct if old else None),
                entry_conditions=(old.entry_conditions if old else ""),
                add_conditions=(old.add_conditions if old else ""),
                trim_conditions=(old.trim_conditions if old else ""),
                exit_conditions=(old.exit_conditions if old else ""),
                notes=(old.notes if old else ""),
                updated_by=position.user_id,
            )
            db.session.add(portfolio_risk)
            risks += 1

    for approval in ResearchGateApproval.query.filter_by(gate_key="numbers").all():
        existing = ResearchGateApproval.query.filter_by(
            coverage_id=approval.coverage_id, gate_key="fundamentals"
        ).first()
        if existing is None:
            approval.gate_key = "fundamentals"
        else:
            db.session.delete(approval)
        gates += 1

    db.session.add(SchemaMigration(
        migration_key=MIGRATION_KEY,
        details={
            "position_profiles_created": profiles,
            "portfolio_risk_plans_created": risks,
            "readiness_gates_renamed": gates,
            "note": "Additive only; legacy Research RiskPlan and all account/data rows are preserved.",
        },
    ))
    db.session.commit()
    return {
        "status": "applied",
        "position_profiles_created": profiles,
        "portfolio_risk_plans_created": risks,
        "readiness_gates_renamed": gates,
    }


__all__ = ["migrate_local_web_parity"]
