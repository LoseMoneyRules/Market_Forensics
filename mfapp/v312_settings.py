from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from .extensions import db
from .marketdata import provider_status, set_secret
from .models import AuditEvent
from .security import role_required

bp = Blueprint("v312_settings", __name__)


def _control_view():
    if not getattr(g, "user", None) or g.user.role != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


@bp.post("/settings/v312-data")
@role_required("CONTROL")
def save_data_identity():
    _control_view()
    fields = {
        "sec_user_agent": "sec_user_agent",
        "finra_token": "finra_token",
    }
    changed = []
    for form_name, secret_name in fields.items():
        if request.form.get(f"remove_{form_name}") == "1":
            set_secret(g.user.id, secret_name, "")
            changed.append(f"removed:{secret_name}")
            continue
        value = request.form.get(form_name, "").strip()
        if value:
            if secret_name == "sec_user_agent" and "@" not in value:
                flash("SEC User-Agent must include a real contact email, e.g. Market Forensics your@email.com.", "error")
                return redirect(url_for("web.settings"))
            set_secret(g.user.id, secret_name, value)
            changed.append(f"saved:{secret_name}")
    db.session.add(AuditEvent(
        actor_user_id=g.user.id,
        action="settings.v312_data",
        object_type="user",
        object_id=str(g.user.id),
        meta={"changed": changed, "engine": "3.1.12"},
    ))
    db.session.commit()
    flash("V3.1.12 data-source settings saved encrypted.", "success")
    return redirect(url_for("web.settings"))
