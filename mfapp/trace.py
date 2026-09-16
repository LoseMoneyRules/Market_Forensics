from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from .security import login_required

bp = Blueprint("trace", __name__, url_prefix="/trace")
TRACE_BUILD = "0.0.4-trace1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _trace_path() -> Path:
    configured = current_app.config.get("TRACE_LOG_PATH")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "trace.jsonl"


def _should_trace() -> bool:
    if not current_app.config.get("TRACE_ENABLED", True):
        return False
    path = request.path or ""
    return not (path.startswith("/static/") or path in {"/health"} or path.startswith("/trace"))


def _safe_role() -> str | None:
    user = getattr(g, "user", None)
    if not user:
        return None
    return str(getattr(user, "role", "") or "").upper() or None


def write_trace(kind: str, **fields) -> None:
    """Best-effort JSONL tracing. Never raises into the application path."""
    try:
        path = _trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": _utc_now(),
            "kind": kind,
            "pid": os.getpid(),
            "trace_id": getattr(g, "mf_trace_id", None),
            **fields,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
    except Exception:
        # Tracing must never be capable of taking production down.
        pass


def install_trace(app) -> None:
    app.config.setdefault("TRACE_ENABLED", True)
    app.config.setdefault("TRACE_BUILD", TRACE_BUILD)
    app.config.setdefault("TRACE_LOG_PATH", str(Path(app.instance_path) / "trace.jsonl"))

    # Record each Passenger/WSGI process startup. If a user sees a 500 with no request
    # entry, the failure occurred before Flask handled the request.
    try:
        path = Path(app.config["TRACE_LOG_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "kind": "PROCESS_START",
                "pid": os.getpid(),
                "trace_id": None,
                "build": app.config["TRACE_BUILD"],
            }, separators=(",", ":")) + "\n")
    except Exception:
        pass

    @app.before_request
    def _trace_request_start():
        if not _should_trace():
            return None
        g.mf_trace_id = uuid.uuid4().hex[:12]
        g.mf_trace_started = time.perf_counter()
        write_trace(
            "REQUEST_START",
            method=request.method,
            path=request.path,
            endpoint=request.endpoint,
        )
        return None

    @app.after_request
    def _trace_request_end(response):
        trace_id = getattr(g, "mf_trace_id", None)
        started = getattr(g, "mf_trace_started", None)
        if trace_id and started is not None:
            write_trace(
                "REQUEST_END",
                method=request.method,
                path=request.path,
                endpoint=request.endpoint,
                status=int(response.status_code),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                role=_safe_role(),
            )
            response.headers["X-MF-Trace-ID"] = trace_id
        return response

    @app.errorhandler(Exception)
    def _trace_unhandled_exception(exc):
        if isinstance(exc, HTTPException):
            return exc
        if not getattr(g, "mf_trace_id", None):
            g.mf_trace_id = uuid.uuid4().hex[:12]
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        write_trace(
            "UNHANDLED_EXCEPTION",
            method=request.method,
            path=request.path,
            endpoint=request.endpoint,
            role=_safe_role(),
            exception_type=type(exc).__name__,
            message=str(exc)[:2000],
            traceback=tb[-20000:],
        )
        current_app.logger.exception("MF TRACE %s unhandled exception", g.mf_trace_id)
        trace_id = g.mf_trace_id
        return (
            "<!doctype html><html><head><title>Internal Server Error</title></head>"
            "<body><h1>Internal Server Error</h1>"
            "<p>The server encountered an internal error and was unable to complete your request.</p>"
            f"<p>Market Forensics trace ID: <strong>{trace_id}</strong></p>"
            "</body></html>",
            500,
            {"Content-Type": "text/html; charset=utf-8", "X-MF-Trace-ID": trace_id},
        )


def _control_only() -> None:
    user = getattr(g, "user", None)
    if not user:
        abort(401)
    if str(getattr(user, "role", "") or "").upper() != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


def read_trace(limit: int = 250) -> list[dict]:
    path = _trace_path()
    if not path.exists():
        return []
    rows: deque[dict] = deque(maxlen=max(1, min(limit, 1000)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    rows.append({"kind": "RAW", "raw": line})
    except Exception as exc:
        return [{"kind": "TRACE_READ_ERROR", "message": f"{type(exc).__name__}: {exc}"}]
    return list(rows)


@bp.get("")
@login_required
def console():
    _control_only()
    events = list(reversed(read_trace(300)))
    return render_template(
        "trace_console.html",
        events=events,
        trace_build=current_app.config.get("TRACE_BUILD", TRACE_BUILD),
        trace_path=str(_trace_path()),
    )


@bp.post("/clear")
@login_required
def clear():
    _control_only()
    try:
        path = _trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        flash("Trace log cleared.", "success")
    except Exception as exc:
        flash(f"Unable to clear trace log: {type(exc).__name__}", "error")
    return redirect(url_for("trace.console"))
