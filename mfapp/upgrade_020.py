from __future__ import annotations

from .core_models import SchemaMigration
from .extensions import db
from .models import UserPreference

MIGRATION_KEY = "release_0_2_0_semantic_preferences"


def migrate_semantic_preferences() -> dict:
    if SchemaMigration.query.filter_by(migration_key=MIGRATION_KEY).first():
        return {"status": "already-applied"}

    renamed = removed = 0
    rows = UserPreference.query.order_by(UserPreference.id.asc()).all()
    for row in rows:
        old = str(row.key or "")
        new = None
        if old == "notifications_016":
            new = "notifications"
        elif old.startswith("alert_subscription_017_"):
            new = "alert_subscription_" + old.split("alert_subscription_017_", 1)[1]
        elif old.startswith("alert_catalog_017_"):
            new = "alert_catalog_" + old.split("alert_catalog_017_", 1)[1]
        elif old == "alert_email_017":
            db.session.delete(row)
            removed += 1
            continue
        if not new or new == old:
            continue
        existing = UserPreference.query.filter_by(user_id=row.user_id, key=new).first()
        if existing is None:
            row.key = new
            renamed += 1
        else:
            if isinstance(row.value, dict) and isinstance(existing.value, dict):
                existing.value = dict(row.value) | dict(existing.value)
            db.session.delete(row)
            removed += 1

    db.session.add(SchemaMigration(
        migration_key=MIGRATION_KEY,
        details={"renamed": renamed, "removed_or_merged": removed, "note": "Account email remains notification source of truth."},
    ))
    db.session.commit()
    return {"status": "applied", "renamed": renamed, "removed_or_merged": removed}


__all__ = ["migrate_semantic_preferences"]
