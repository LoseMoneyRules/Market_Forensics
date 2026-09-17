from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

import pyotp
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from .access import audit, require_control_view
from .extensions import db, limiter
from .models import Invite, User
from .security import decrypt_secret, encrypt_secret, hash_password, login_required, role_required, verify_password

bp = Blueprint("auth", __name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if g.user:
        return redirect(url_for("web.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not user.is_active or not verify_password(user.password_hash, password):
            flash("Email or password not recognized.", "error")
            return render_template("login.html"), 401
        session.clear()
        session["pending_2fa_user_id"] = user.id
        session.permanent = True
        return redirect(url_for("auth.verify_2fa"))
    return render_template("login.html")


@bp.route("/verify", methods=["GET", "POST"])
@limiter.limit("12 per minute")
def verify_2fa():
    uid = session.get("pending_2fa_user_id")
    if not uid:
        return redirect(url_for("auth.login"))
    user = db.session.get(User, uid)
    if not user:
        session.clear()
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        secret = decrypt_secret(user.totp_secret_enc)
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            flash("Authenticator code not valid.", "error")
            return render_template("verify.html"), 401
        session.clear()
        session["user_id"] = user.id
        session["view_as"] = user.role
        session.permanent = True
        user.last_login_at = utcnow()
        audit("auth.login", "user", user.id)
        db.session.commit()
        return redirect(url_for("web.dashboard"))
    return render_template("verify.html")


@bp.post("/logout")
@login_required
def logout():
    audit("auth.logout", "user", g.user.id)
    db.session.commit()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.post("/control/invite")
@role_required("CONTROL")
def create_invite():
    require_control_view()
    email = request.form.get("email", "").lower().strip()
    role = request.form.get("role", "FRIEND").upper()
    if role not in {"FRIEND", "INSIDER"}:
        abort(400)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Enter a valid email.", "error")
        return redirect(url_for("web.control"))
    if User.query.filter_by(email=email).first():
        flash("That email already has an account.", "error")
        return redirect(url_for("web.control"))
    raw = token_urlsafe(32)
    inv = Invite(
        email=email,
        role=role,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=utcnow() + timedelta(days=7),
        created_by=g.user.id,
    )
    db.session.add(inv)
    audit("invite.create", "invite", email, {"role": role})
    db.session.commit()
    link = url_for("auth.accept_invite", token=raw, _external=True)
    return render_template("invite_created.html", link=link, email=email, role=role)


@bp.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour")
def accept_invite(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    inv = Invite.query.filter_by(token_hash=token_hash, used_at=None).first_or_404()
    if inv.expires_at < utcnow():
        abort(410)
    existing = User.query.filter_by(email=inv.email).first()
    if existing:
        if not existing.is_active:
            session.clear(); session["setup_user_id"] = existing.id; session["setup_invite_id"] = inv.id
            return redirect(url_for("auth.setup_2fa"))
        abort(410)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        if len(name) < 2:
            flash("Enter your name.", "error")
            return render_template("invite.html", invite=inv)
        if len(password) < 12:
            flash("Use at least 12 characters.", "error")
            return render_template("invite.html", invite=inv)
        secret = pyotp.random_base32()
        user = User(
            email=inv.email,
            display_name=name,
            role=inv.role,
            password_hash=hash_password(password),
            totp_secret_enc=encrypt_secret(secret),
            is_active=False,
        )
        db.session.add(user); db.session.flush()
        session.clear(); session["setup_user_id"] = user.id; session["setup_invite_id"] = inv.id
        db.session.commit()
        return redirect(url_for("auth.setup_2fa"))
    return render_template("invite.html", invite=inv)


@bp.route("/setup-2fa", methods=["GET", "POST"])
def setup_2fa():
    uid = session.get("setup_user_id"); iid = session.get("setup_invite_id")
    user = db.session.get(User, uid) if uid else None
    inv = db.session.get(Invite, iid) if iid else None
    if not user or not inv or inv.used_at:
        return redirect(url_for("auth.login"))
    secret = decrypt_secret(user.totp_secret_enc)
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Market Forensics")
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            flash("Code not valid yet. Check your authenticator and try again.", "error")
        else:
            user.is_active = True
            inv.used_at = utcnow()
            audit("account.activate", "user", user.id, {"role": user.role})
            db.session.commit(); session.clear()
            flash("Account activated. Sign in to continue.", "success")
            return redirect(url_for("auth.login"))
    import base64, io, qrcode
    buf = io.BytesIO(); qrcode.make(uri).save(buf, format="PNG")
    qr = base64.b64encode(buf.getvalue()).decode()
    return render_template("setup_2fa.html", user=user, secret=secret, qr=qr)
