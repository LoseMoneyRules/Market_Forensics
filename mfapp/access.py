from __future__ import annotations

from flask import abort, g

from .extensions import db
from .models import AuditEvent

ROLE_RANK = {"FRIEND": 1, "INSIDER": 2, "CONTROL": 3}


def effective_role() -> str:
    return str(getattr(g, "view_role", None) or getattr(getattr(g, "user", None), "role", "FRIEND")).upper()


def require_control_view() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if str(g.user.role or "").upper() != "CONTROL":
        abort(403)
    if effective_role() != "CONTROL":
        abort(404)


def audit(action: str, object_type: str | None = None, object_id=None, meta: dict | None = None) -> None:
    db.session.add(AuditEvent(
        actor_user_id=getattr(getattr(g, "user", None), "id", None),
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        meta=meta or {},
    ))
