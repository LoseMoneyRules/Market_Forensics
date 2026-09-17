from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index, UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ResearchState(db.Model):
    __tablename__ = "mf_research_state"
    __table_args__ = (UniqueConstraint("coverage_id", name="uq_mf_research_state_coverage"),)
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, unique=True, index=True)
    business = db.Column(db.Text, nullable=False, default="")
    numbers = db.Column(db.Text, nullable=False, default="")
    expectations = db.Column(db.Text, nullable=False, default="")
    valuation_notes = db.Column(db.Text, nullable=False, default="")
    bear_case_summary = db.Column(db.Text, nullable=False, default="")
    catalysts_summary = db.Column(db.Text, nullable=False, default="")
    flows_summary = db.Column(db.Text, nullable=False, default="")
    management_summary = db.Column(db.Text, nullable=False, default="")
    tape_summary = db.Column(db.Text, nullable=False, default="")
    risk_summary = db.Column(db.Text, nullable=False, default="")
    thesis = db.Column(db.Text, nullable=False, default="")
    counter_evidence = db.Column(db.Text, nullable=False, default="")
    variant_market = db.Column(db.Text, nullable=False, default="")
    variant_us = db.Column(db.Text, nullable=False, default="")
    variant_evidence = db.Column(db.Text, nullable=False, default="")
    narrative_fit_notes = db.Column(db.Text, nullable=False, default="")
    confirmation_bias_notes = db.Column(db.Text, nullable=False, default="")
    thesis_drift_notes = db.Column(db.Text, nullable=False, default="")
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    coverage = db.relationship("Coverage", backref=db.backref("research", uselist=False))

class ResearchVersion(db.Model):
    __tablename__ = "mf_research_version"
    __table_args__ = (UniqueConstraint("coverage_id", "version", name="uq_mf_research_version"),)
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    reason = db.Column(db.String(160), nullable=False, default="")
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class Expectation(db.Model):
    __tablename__ = "mf_expectation"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    metric = db.Column(db.String(100), nullable=False)
    period_label = db.Column(db.String(40), nullable=False, default="")
    market_value = db.Column(db.Numeric(34, 8))
    internal_value = db.Column(db.Numeric(34, 8))
    unit = db.Column(db.String(32), nullable=False, default="")
    confidence = db.Column(db.String(24), nullable=False, default="UNRATED")
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    notes = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

class ValuationModel(db.Model):
    __tablename__ = "mf_valuation_model"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False, default="Primary")
    method = db.Column(db.String(48), nullable=False, default="MANUAL_PER_SHARE")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    assumptions = db.Column(db.JSON, nullable=False, default=dict)
    calculation_version = db.Column(db.String(32), nullable=False, default="0.2.0")
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    coverage = db.relationship("Coverage", backref="valuation_models")

class ValuationScenario(db.Model):
    __tablename__ = "mf_valuation_scenario"
    __table_args__ = (UniqueConstraint("model_id", "name", name="uq_mf_valuation_scenario"),)
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey("mf_valuation_model.id"), nullable=False, index=True)
    name = db.Column(db.String(16), nullable=False)
    probability = db.Column(db.Numeric(12, 8), nullable=False, default=0)
    equity_value_per_share = db.Column(db.Numeric(24, 8))
    enterprise_value = db.Column(db.Numeric(34, 8))
    confidence = db.Column(db.String(24), nullable=False, default="UNRATED")
    inputs = db.Column(db.JSON, nullable=False, default=dict)
    outputs = db.Column(db.JSON, nullable=False, default=dict)
    calculated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    model = db.relationship("ValuationModel", backref="scenarios")

class BearCaseItem(db.Model):
    __tablename__ = "mf_bear_case_item"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    title = db.Column(db.String(220), nullable=False)
    evidence = db.Column(db.Text, nullable=False, default="")
    probability = db.Column(db.Numeric(12, 8))
    severity = db.Column(db.String(24), nullable=False, default="MEDIUM")
    invalidates = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(24), nullable=False, default="OPEN")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

class Catalyst(db.Model):
    __tablename__ = "mf_catalyst"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    title = db.Column(db.String(220), nullable=False)
    catalyst_type = db.Column(db.String(48), nullable=False, default="OTHER")
    expected_date = db.Column(db.Date)
    direction = db.Column(db.String(16), nullable=False, default="MIXED")
    status = db.Column(db.String(24), nullable=False, default="OPEN")
    evidence = db.Column(db.Text, nullable=False, default="")
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class ManagementAssessment(db.Model):
    __tablename__ = "mf_management_assessment"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    as_of = db.Column(db.Date, nullable=False)
    capital_allocation = db.Column(db.Text, nullable=False, default="")
    execution = db.Column(db.Text, nullable=False, default="")
    incentives = db.Column(db.Text, nullable=False, default="")
    communication = db.Column(db.Text, nullable=False, default="")
    red_flags = db.Column(db.Text, nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class FinancialFlow(db.Model):
    __tablename__ = "mf_financial_flow"
    __table_args__ = (UniqueConstraint("financial_period_id", "flow_type", "calculation_version", name="uq_mf_financial_flow"),)
    id = db.Column(db.Integer, primary_key=True)
    financial_period_id = db.Column(db.Integer, db.ForeignKey("mf_financial_period.id"), nullable=False, index=True)
    flow_type = db.Column(db.String(16), nullable=False)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    calculation_version = db.Column(db.String(32), nullable=False, default="0.2.0")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class MonitoringRule(db.Model):
    __tablename__ = "mf_monitoring_rule"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    metric = db.Column(db.String(100), nullable=False, default="")
    operator = db.Column(db.String(12), nullable=False, default="NOTE")
    threshold_value = db.Column(db.Numeric(34, 8))
    threshold_text = db.Column(db.String(220), nullable=False, default="")
    unit = db.Column(db.String(32), nullable=False, default="")
    severity = db.Column(db.String(24), nullable=False, default="WATCH")
    locked_pre_investment = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

class MonitoringHistory(db.Model):
    __tablename__ = "mf_monitoring_history"
    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("mf_monitoring_rule.id"), nullable=False, index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    observed_value = db.Column(db.Numeric(34, 8))
    status = db.Column(db.String(24), nullable=False, default="WATCH")
    note = db.Column(db.Text, nullable=False, default="")
    observed_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class RiskPlan(db.Model):
    __tablename__ = "mf_risk_plan"
    __table_args__ = (UniqueConstraint("coverage_id", name="uq_mf_risk_plan_coverage"),)
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, unique=True, index=True)
    thesis_invalidation = db.Column(db.Text, nullable=False, default="")
    invalidation_locked_at = db.Column(db.DateTime)
    max_loss_pct = db.Column(db.Numeric(12, 6))
    max_position_pct = db.Column(db.Numeric(12, 6))
    entry_conditions = db.Column(db.Text, nullable=False, default="")
    add_conditions = db.Column(db.Text, nullable=False, default="")
    trim_conditions = db.Column(db.Text, nullable=False, default="")
    exit_conditions = db.Column(db.Text, nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    coverage = db.relationship("Coverage", backref=db.backref("risk_plan", uselist=False))

class InvestmentState(db.Model):
    __tablename__ = "mf_investment_state"
    __table_args__ = (UniqueConstraint("coverage_id", name="uq_mf_investment_state_coverage"),)
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, unique=True, index=True)
    state = db.Column(db.String(40), nullable=False, default="NO_POSITION")
    action = db.Column(db.String(80), nullable=False, default="WAIT")
    review_reason = db.Column(db.Text, nullable=False, default="")
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    coverage = db.relationship("Coverage", backref=db.backref("investment", uselist=False))

class Position(db.Model):
    __tablename__ = "mf_position"
    __table_args__ = (UniqueConstraint("user_id", "security_id", name="uq_mf_position_user_security"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    security_id = db.Column(db.Integer, db.ForeignKey("mf_security.id"), nullable=False, index=True)
    shares = db.Column(db.Numeric(24, 8), nullable=False, default=0)
    avg_cost = db.Column(db.Numeric(24, 8), nullable=False, default=0)
    currency = db.Column(db.String(8), nullable=False, default="USD")
    notes = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    security = db.relationship("Security", backref="positions")

class DecisionJournal(db.Model):
    __tablename__ = "mf_decision_journal"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    decision = db.Column(db.String(100), nullable=False)
    research_state = db.Column(db.String(32), nullable=False, default="UNRATED")
    investment_state = db.Column(db.String(40), nullable=False, default="NO_POSITION")
    thesis_snapshot = db.Column(db.JSON, nullable=False, default=dict)
    risk_snapshot = db.Column(db.JSON, nullable=False, default=dict)
    valuation_snapshot = db.Column(db.JSON, nullable=False, default=dict)
    evidence_for = db.Column(db.Text, nullable=False, default="")
    evidence_against = db.Column(db.Text, nullable=False, default="")
    bias_notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
