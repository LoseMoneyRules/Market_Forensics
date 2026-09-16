from __future__ import annotations

from sqlalchemy import UniqueConstraint

from .extensions import db
from .models import utcnow


class DailyShortVolume(db.Model):
    __tablename__ = "daily_short_volume"
    __table_args__ = (UniqueConstraint("company_id", "trade_date", name="uq_company_short_volume_day"),)
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    trade_date = db.Column(db.String(10), nullable=False, index=True)
    short_volume = db.Column(db.Float)
    short_exempt_volume = db.Column(db.Float)
    total_reported_volume = db.Column(db.Float)
    short_pct = db.Column(db.Float)
    market = db.Column(db.String(32), nullable=False, default="")
    source = db.Column(db.String(80), nullable=False, default="FINRA Reg SHO")
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)


class ShortInterest(db.Model):
    __tablename__ = "short_interest"
    __table_args__ = (UniqueConstraint("company_id", "settlement_date", name="uq_company_short_interest_date"),)
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    settlement_date = db.Column(db.String(10), nullable=False, index=True)
    publication_date = db.Column(db.String(10))
    shares_short = db.Column(db.Float)
    avg_daily_volume = db.Column(db.Float)
    days_to_cover = db.Column(db.Float)
    source = db.Column(db.String(80), nullable=False, default="FINRA")
    provenance = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)


class SourceEvidence(db.Model):
    __tablename__ = "source_evidence"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), index=True)
    category = db.Column(db.String(64), nullable=False, index=True)
    source_name = db.Column(db.String(120), nullable=False)
    source_url = db.Column(db.Text, nullable=False, default="")
    as_of = db.Column(db.String(32), nullable=False, default="")
    quality = db.Column(db.String(24), nullable=False, default="OBSERVED")
    publishability = db.Column(db.String(24), nullable=False, default="INTERNAL_ONLY")
    payload = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, index=True)


class RefreshRun(db.Model):
    __tablename__ = "refresh_run"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    scope = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="RUNNING")
    provider = db.Column(db.String(120), nullable=False, default="")
    rows_written = db.Column(db.Integer, nullable=False, default=0)
    warnings = db.Column(db.JSON, nullable=False, default=list)
    started_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime(timezone=False))


class PriceCheck(db.Model):
    __tablename__ = "price_check"
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="LOW DATA")
    primary_provider = db.Column(db.String(80), nullable=False, default="")
    primary_price = db.Column(db.Float)
    cross_provider = db.Column(db.String(80), nullable=False, default="")
    cross_price = db.Column(db.Float)
    gap_pct = db.Column(db.Float)
    checked_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
