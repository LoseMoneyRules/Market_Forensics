from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="FRIEND")
    password_hash = db.Column(db.Text, nullable=False)
    totp_secret_enc = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=False))


class Invite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, index=True)
    role = db.Column(db.String(16), nullable=False, default="FRIEND")
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=False), nullable=False)
    used_at = db.Column(db.DateTime(timezone=False))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), unique=True, nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="MONITOR")
    score = db.Column(db.Integer)
    summary = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)


class ResearchWorkspace(db.Model):
    """Mutable CONTROL-only research state. Published snapshots never read from this table directly."""

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, unique=True, index=True)
    research_state = db.Column(db.String(24), nullable=False, default="DRAFT")
    payload = db.Column(db.JSON, nullable=False, default=dict)
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)
    company = db.relationship("Company", backref=db.backref("workspace", uselist=False))


class MarketSnapshot(db.Model):
    """Last-good quote cache. A failed refresh never overwrites a good snapshot."""

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, unique=True, index=True)
    provider = db.Column(db.String(80), nullable=False)
    price = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(12), nullable=False, default="USD")
    as_of = db.Column(db.DateTime(timezone=False))
    quality = db.Column(db.String(24), nullable=False, default="OBSERVED")
    payload = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)
    company = db.relationship("Company", backref=db.backref("market_snapshot", uselist=False))


class MonitoringItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    label = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="WATCH")
    current_value = db.Column(db.String(120), nullable=False, default="")
    threshold = db.Column(db.String(180), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)
    company = db.relationship("Company", backref="monitoring_items")


class DecisionJournal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    decision = db.Column(db.String(80), nullable=False)
    conviction = db.Column(db.String(24), nullable=False, default="")
    thesis_snapshot = db.Column(db.Text, nullable=False, default="")
    invalidation_snapshot = db.Column(db.Text, nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, index=True)
    company = db.relationship("Company", backref="journal_entries")


class ValidationRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="REVIEW")
    reliability_score = db.Column(db.Float)
    valuation_reliability = db.Column(db.Float)
    thesis_reliability = db.Column(db.Float)
    direction_reliability = db.Column(db.Float)
    bias_control = db.Column(db.Float)
    sample_size = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, index=True)
    company = db.relationship("Company", backref="validation_runs")


class AppSecret(db.Model):
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_secret_name"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    value_enc = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)


class Publication(db.Model):
    __table_args__ = (UniqueConstraint("company_id", "version", name="uq_publication_version"),)
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    visibility = db.Column(db.String(16), nullable=False, default="FRIEND")
    payload = db.Column(db.JSON, nullable=False, default=dict)
    is_current = db.Column(db.Boolean, nullable=False, default=False)
    published_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow)
    model_version = db.Column(db.String(32), nullable=False, default="0.0.2")
    company = db.relationship("Company", backref="publications")


class PrivatePosition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    shares = db.Column(db.Float, nullable=False, default=0)
    avg_cost = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)
    company = db.relationship("Company", backref="private_positions")


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    action = db.Column(db.String(100), nullable=False)
    object_type = db.Column(db.String(80))
    object_id = db.Column(db.String(80))
    meta = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, index=True)
