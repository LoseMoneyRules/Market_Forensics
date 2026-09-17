from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index, UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Event(db.Model):
    __tablename__ = "mf_event"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), nullable=False, index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    event_type = db.Column(db.String(48), nullable=False)
    title = db.Column(db.String(240), nullable=False)
    event_date = db.Column(db.DateTime, nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class Provenance(db.Model):
    __tablename__ = "mf_provenance"
    __table_args__ = (Index("ix_mf_provenance_object", "object_type", "object_id"),)
    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("mf_source.id"), index=True)
    object_type = db.Column(db.String(80), nullable=False)
    object_id = db.Column(db.String(80), nullable=False)
    field_name = db.Column(db.String(120), nullable=False, default="")
    raw_or_normalized = db.Column(db.String(16), nullable=False, default="RAW")
    financial_period_id = db.Column(db.Integer, db.ForeignKey("mf_financial_period.id"), index=True)
    provider = db.Column(db.String(80), nullable=False, default="")
    manual_override = db.Column(db.Boolean, nullable=False, default=False)
    restated = db.Column(db.Boolean, nullable=False, default=False)
    freshness_at = db.Column(db.DateTime)
    calculation_version = db.Column(db.String(32), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class DataQualityIssue(db.Model):
    __tablename__ = "mf_data_quality_issue"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), nullable=False, index=True)
    object_type = db.Column(db.String(80), nullable=False)
    object_id = db.Column(db.String(80), nullable=False, default="")
    code = db.Column(db.String(80), nullable=False)
    severity = db.Column(db.String(24), nullable=False, default="REVIEW")
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(24), nullable=False, default="OPEN")
    detected_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime)

class Job(db.Model):
    __tablename__ = "mf_job"
    __table_args__ = (Index("ix_mf_job_queue", "status", "priority", "run_after"),)
    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(48), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="QUEUED", index=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), index=True)
    security_id = db.Column(db.Integer, db.ForeignKey("mf_security.id"), index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    result = db.Column(db.JSON, nullable=False, default=dict)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=3)
    run_after = db.Column(db.DateTime, nullable=False, default=utcnow)
    locked_at = db.Column(db.DateTime)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class RefreshRun(db.Model):
    __tablename__ = "mf_refresh_run"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("mf_company.id"), index=True)
    security_id = db.Column(db.Integer, db.ForeignKey("mf_security.id"), index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("mf_job.id"), index=True)
    refresh_type = db.Column(db.String(48), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="RUNNING")
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    error_id = db.Column(db.String(32), nullable=False, default="")

class Alert(db.Model):
    __tablename__ = "mf_alert"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), index=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("mf_monitoring_rule.id"), index=True)
    severity = db.Column(db.String(24), nullable=False, default="WATCH")
    title = db.Column(db.String(220), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
