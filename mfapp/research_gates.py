from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ResearchGateApproval(db.Model):
    __tablename__ = "mf_research_gate_approval"
    __table_args__ = (UniqueConstraint("coverage_id", "gate_key", name="uq_mf_gate_approval"),)
    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    gate_key = db.Column(db.String(48), nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    approved_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    evidence_hash = db.Column(db.String(64), nullable=False, default="")
    note = db.Column(db.String(240), nullable=False, default="")


__all__ = ["ResearchGateApproval"]
