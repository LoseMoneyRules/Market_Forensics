from __future__ import annotations

from flask import Blueprint, abort, g, make_response, redirect, request, session, url_for

from .extensions import db
from .models import AuditEvent
from .security import login_required

bp = Blueprint("preview", __name__)
VALID = {"FRIEND", "INSIDER", "CONTROL"}


def _control() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if str(g.user.role or "").upper() != "CONTROL":
        abort(403)


def _set(role: str):
    session["view_as"] = role
    session.modified = True
    db.session.add(AuditEvent(
        actor_user_id=g.user.id,
        action="control.preview_role",
        object_type="user",
        object_id=str(g.user.id),
        meta={"view_as": role, "release": "0.0.4"},
    ))
    db.session.commit()


@bp.get("/preview/<role>")
@login_required
def set_preview(role):
    _control()
    target = str(role or "").upper()
    if target not in VALID:
        abort(404)
    _set(target)
    next_url = request.args.get("next") or url_for("web.dashboard")
    if not str(next_url).startswith("/"):
        next_url = url_for("web.dashboard")
    response = make_response(redirect(next_url))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/control-mode")
@login_required
def control_mode():
    """Permanent fail-safe: a real CONTROL can always leave a simulated view."""
    _control()
    _set("CONTROL")
    response = make_response(redirect(url_for("full312.workspace")))
    response.headers["Cache-Control"] = "no-store"
    return response
