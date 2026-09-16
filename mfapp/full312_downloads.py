from __future__ import annotations

from io import BytesIO

from flask import Blueprint, abort, g, send_file

from .full312 import load_state, sync_engine_credentials
from .security import login_required

bp = Blueprint("full312_downloads", __name__, url_prefix="/downloads/v312")


def _control() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if str(g.user.role or "").upper() != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


@bp.get("/<ticker>/<kind>")
@login_required
def download(ticker, kind):
    _control()
    from market_forensics import exporter

    sync_engine_credentials(g.user.id)
    t = ticker.upper()
    state = load_state(t)
    if kind == "pdf":
        payload = exporter.research_report_pdf(t, state)
        mimetype = "application/pdf"
        filename = f"Market_Forensics_{t}_V312.pdf"
    elif kind == "docx":
        payload = exporter.research_report_docx(t, state)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"Market_Forensics_{t}_V312.docx"
    elif kind == "one-page-pdf":
        payload = exporter.one_page_pdf(t, state)
        mimetype = "application/pdf"
        filename = f"Market_Forensics_{t}_One_Page.pdf"
    else:
        abort(404)
    bio = BytesIO(payload)
    bio.seek(0)
    return send_file(bio, mimetype=mimetype, as_attachment=True, download_name=filename, max_age=0)
