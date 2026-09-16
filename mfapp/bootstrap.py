from __future__ import annotations

import base64
import io
import os
import re
from secrets import compare_digest

import pyotp
import qrcode
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from .extensions import db, limiter
from .models import AuditEvent, User
from .security import decrypt_secret, encrypt_secret, hash_password, verify_password

bp = Blueprint("bootstrap", __name__)


def _bootstrap_token() -> str:
    return os.environ.get("MF_BOOTSTRAP_TOKEN", "").strip()


def _active_control_exists() -> bool:
    return User.query.filter_by(role="CONTROL", is_active=True).first() is not None


@bp.get("/bootstrap-status")
@limiter.limit("20 per minute")
def bootstrap_status():
    users = User.query.count()
    active_controls = User.query.filter_by(role="CONTROL", is_active=True).count()
    inactive_controls = User.query.filter_by(role="CONTROL", is_active=False).count()
    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    driver = db_uri.split("://", 1)[0] if "://" in db_uri else "unknown"
    return {
        "bootstrap_token_configured": bool(_bootstrap_token()),
        "production_env": os.environ.get("MF_ENV", "") == "production",
        "database_url_configured": bool(os.environ.get("MF_DATABASE_URL", "").strip()),
        "database_driver": driver,
        "encryption_key_configured": bool(os.environ.get("MF_ENCRYPTION_KEY", "").strip()),
        "user_count": users,
        "active_control_count": active_controls,
        "inactive_control_count": inactive_controls,
        "bootstrap_available": bool(_bootstrap_token()) and active_controls == 0,
    }


@bp.route("/bootstrap-control", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def bootstrap_control():
    expected = _bootstrap_token()
    if not expected or _active_control_exists():
        abort(404)

    if request.method == "POST":
        supplied = request.form.get("token", "")
        email = request.form.get("email", "").lower().strip()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not compare_digest(supplied, expected):
            flash("Bootstrap token not recognized.", "error")
            return render_template("bootstrap_control.html"), 403
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email.", "error")
            return render_template("bootstrap_control.html"), 400
        if len(name) < 2:
            flash("Enter your name.", "error")
            return render_template("bootstrap_control.html"), 400
        if len(password) < 12:
            flash("Use at least 12 characters.", "error")
            return render_template("bootstrap_control.html"), 400

        existing = User.query.filter_by(email=email).first()
        if existing:
            if existing.role != "CONTROL" or existing.is_active or not verify_password(existing.password_hash, password):
                flash("Unable to resume CONTROL setup.", "error")
                return render_template("bootstrap_control.html"), 409
            user = existing
        else:
            # Initial bootstrap is only allowed against an otherwise-empty user table.
            if User.query.first() is not None:
                abort(409)
            secret = pyotp.random_base32()
            user = User(
                email=email,
                display_name=name,
                role="CONTROL",
                password_hash=hash_password(password),
                totp_secret_enc=encrypt_secret(secret),
                is_active=False,
            )
            db.session.add(user)
            db.session.commit()

        session.clear()
        session["bootstrap_user_id"] = user.id
        session.permanent = True
        return redirect(url_for("bootstrap.bootstrap_2fa"))

    return render_template("bootstrap_control.html")


@bp.route("/bootstrap-control/2fa", methods=["GET", "POST"])
@limiter.limit("12 per minute")
def bootstrap_2fa():
    uid = session.get("bootstrap_user_id")
    user = db.session.get(User, uid) if uid else None
    if not user or user.role != "CONTROL" or user.is_active or _active_control_exists():
        session.clear()
        return redirect(url_for("bootstrap.bootstrap_control"))

    secret = decrypt_secret(user.totp_secret_enc)
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Market Forensics")

    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            flash("Authenticator code not valid.", "error")
        else:
            try:
                user.is_active = True
                db.session.flush()
                db.session.add(
                    AuditEvent(
                        actor_user_id=user.id,
                        action="account.bootstrap_control",
                        object_type="user",
                        object_id=str(user.id),
                        meta={"role": "CONTROL"},
                    )
                )
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception("CONTROL bootstrap activation failed")
                flash(
                    f"CONTROL activation could not be completed ({type(exc).__name__}). Your account setup was preserved; try again after the server fix.",
                    "error",
                )
            else:
                session.clear()
                flash("CONTROL account activated. Sign in to continue.", "success")
                return redirect(url_for("web.login"))

    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    qr = base64.b64encode(buf.getvalue()).decode()
    return render_template("setup_2fa.html", user=user, secret=secret, qr=qr)
