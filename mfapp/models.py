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


class AppSecret(db.Model):
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_secret_name"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    value_enc = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)


class UserPreference(db.Model):
    __tablename__ = "mf_user_preference"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_mf_user_preference"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    key = db.Column(db.String(80), nullable=False)
    value = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, onupdate=utcnow)


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    action = db.Column(db.String(100), nullable=False)
    object_type = db.Column(db.String(80))
    object_id = db.Column(db.String(80))
    meta = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=False), nullable=False, default=utcnow, index=True)


from .core_models import MarketSnapshot  # noqa: E402,F401
