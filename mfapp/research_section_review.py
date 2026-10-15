from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ResearchSectionReview(db.Model):
    """Human-authored interpretation. Never confused with sourced financial facts.

    The 0.4.2 table is additive: existing ResearchState notes, gate approvals,
    ResearchVersion histories and valuation-model assumptions remain untouched.
    """
    __tablename__ = "mf_research_section_review"
    __table_args__ = (UniqueConstraint("coverage_id", "section_key", name="uq_mf_section_review"),)

    id = db.Column(db.Integer, primary_key=True)
    coverage_id = db.Column(db.Integer, db.ForeignKey("mf_coverage.id"), nullable=False, index=True)
    section_key = db.Column(db.String(48), nullable=False)
    verdict = db.Column(db.String(24), nullable=False, default="UNASSESSED")
    confidence = db.Column(db.String(16), nullable=False, default="UNRATED")
    metric_label = db.Column(db.String(96), nullable=False, default="")
    metric_value = db.Column(db.Numeric(28, 8))
    metric_unit = db.Column(db.String(16), nullable=False, default="")
    horizon_months = db.Column(db.Integer)
    evidence_note = db.Column(db.Text, nullable=False, default="")
    reconsider_if = db.Column(db.Text, nullable=False, default="")
    source_url = db.Column(db.String(1000), nullable=False, default="")
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def frozen_publication(self) -> dict:
        return {
            "section": self.section_key,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "metric_label": self.metric_label,
            "metric_value": float(self.metric_value) if self.metric_value is not None else None,
            "metric_unit": self.metric_unit,
            "horizon_months": self.horizon_months,
            "evidence_note": self.evidence_note,
            "reconsider_if": self.reconsider_if,
            "source_url": self.source_url,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
