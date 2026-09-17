from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index, UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Snapshot(db.Model):
    __tablename__ = "mf_snapshot"
    __table_args__ = (UniqueConstraint("coverage_id", "version", name="uq_mf_snapshot_version"),)
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    snapshot_type = db.Column(db.String(24), nullable=False, default="DECISION")
    payload = db.Column(db.JSON, nullable=False, default=dict)
    calculation_version = db.Column(db.String(32), nullable=False, default="0.2.0")
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

class Publication(db.Model):
    __tablename__ = "mf_publication"
    __table_args__ = (
        UniqueConstraint("coverage_id", "version", name="uq_mf_publication_version"),
        UniqueConstraint("slug", "version", name="uq_mf_publication_slug_version"),
    )
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("mf_snapshot.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    visibility = db.Column(db.String(16), nullable=False, default="FRIEND")
    title = db.Column(db.String(240), nullable=False)
    slug = db.Column(db.String(160), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    published_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    published_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    revoked_at = db.Column(db.DateTime)
    coverage = db.relationship("Coverage", backref="publications")

class CalculationRun(db.Model):
    __tablename__ = "mf_calculation_run"
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), index=True)
    financial_period_id = db.Column(db.Integer, db.ForeignKey("mf_financial_period.id"), index=True)
    calculation_type = db.Column(db.String(48), nullable=False, index=True)
    calculation_version = db.Column(db.String(32), nullable=False, default="0.2.0")
    inputs = db.Column(db.JSON, nullable=False, default=dict)
    outputs = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(16), nullable=False, default="DONE")
    error_id = db.Column(db.String(32), nullable=False, default="")
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime)
    elapsed_ms = db.Column(db.Numeric(18, 3))

class SchemaMigration(db.Model):
    __tablename__ = "mf_schema_migration"
    id = db.Column(db.Integer, primary_key=True)
    migration_key = db.Column(db.String(120), nullable=False, unique=True, index=True)
    applied_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    details = db.Column(db.JSON, nullable=False, default=dict)
