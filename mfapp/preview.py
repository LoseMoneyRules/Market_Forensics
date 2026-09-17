from __future__ import annotations

from flask import Blueprint, abort, g, make_response, redirect, request, session, url_for

from .access import audit
from .extensions import db
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
    audit("control.preview_role", "user", g.user.id, {"view_as": role, "release": "0.1.0"})
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
    _control()
    _set("CONTROL")
    response = make_response(redirect(url_for("web.dashboard")))
    response.headers["Cache-Control"] = "no-store"
    return response
