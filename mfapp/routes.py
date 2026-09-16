from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

import pyotp
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from .extensions import db, limiter
from .models import AuditEvent, Company, Invite, Publication, User
from .security import decrypt_secret, encrypt_secret, hash_password, login_required, role_required, verify_password
from .symbols import validate_ticker

bp=Blueprint("web", __name__)

ROLE_RANK={"FRIEND":1,"INSIDER":2,"CONTROL":3}

def audit(action, object_type=None, object_id=None, meta=None):
    db.session.add(AuditEvent(actor_user_id=getattr(g.user,"id",None), action=action, object_type=object_type, object_id=str(object_id) if object_id is not None else None, meta=meta or {}))

def can_view_publication(pub, role):
    return ROLE_RANK.get(role,0) >= ROLE_RANK.get(pub.visibility,99)

@bp.get("/health")
def health():
    return {"status":"ok","version":"0.0.1"}

@bp.route("/login", methods=["GET","POST"])
@limiter.limit("10 per minute")
def login():
    if g.user:
        return redirect(url_for("web.dashboard"))
    if request.method=="POST":
        email=request.form.get("email","").lower().strip()
        password=request.form.get("password","")
        user=User.query.filter_by(email=email).first()
        if not user or not user.is_active or not verify_password(user.password_hash,password):
            flash("Email or password not recognized.", "error")
            return render_template("login.html"), 401
        session.clear()
        session["pending_2fa_user_id"]=user.id
        session.permanent=True
        return redirect(url_for("web.verify_2fa"))
    return render_template("login.html")

@bp.route("/verify", methods=["GET","POST"])
@limiter.limit("12 per minute")
def verify_2fa():
    uid=session.get("pending_2fa_user_id")
    if not uid:
        return redirect(url_for("web.login"))
    user=db.session.get(User,uid)
    if not user:
        session.clear(); return redirect(url_for("web.login"))
    if request.method=="POST":
        code=re.sub(r"\D","",request.form.get("code",""))
        secret=decrypt_secret(user.totp_secret_enc)
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            flash("Authenticator code not valid.","error")
            return render_template("verify.html"),401
        session.clear(); session["user_id"]=user.id; session.permanent=True
        user.last_login_at=datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.add(AuditEvent(actor_user_id=user.id,action="auth.login",object_type="user",object_id=str(user.id),meta={}))
        db.session.commit()
        return redirect(url_for("web.dashboard"))
    return render_template("verify.html")

@bp.post("/logout")
@login_required
def logout():
    audit("auth.logout","user",g.user.id); db.session.commit(); session.clear()
    return redirect(url_for("web.login"))

@bp.get("/")
@login_required
def dashboard():
    pubs=Publication.query.filter_by(is_current=True).order_by(Publication.published_at.desc()).all()
    rows=[p for p in pubs if can_view_publication(p,g.user.role)]
    return render_template("dashboard.html", publications=rows)

@bp.get("/company/<ticker>")
@login_required
def company(ticker):
    company=Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    pub=Publication.query.filter_by(company_id=company.id,is_current=True).first()
    if not pub or not can_view_publication(pub,g.user.role):
        abort(404)
    audit("research.view","company",company.id,{"ticker":company.ticker})
    db.session.commit()
    return render_template("company.html", company=company, pub=pub, insider=ROLE_RANK[g.user.role]>=2, control=g.user.role=="CONTROL")

@bp.get("/control")
@role_required("CONTROL")
def control():
    users=User.query.order_by(User.created_at.desc()).all()
    invites=Invite.query.filter(Invite.used_at.is_(None)).order_by(Invite.created_at.desc()).limit(20).all()
    companies=Company.query.order_by(Company.ticker).all()
    events=AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(15).all()
    return render_template("control.html",users=users,invites=invites,companies=companies,events=events)

@bp.post("/control/invite")
@role_required("CONTROL")
def create_invite():
    email=request.form.get("email","").lower().strip()
    role=request.form.get("role","FRIEND").upper()
    if role not in {"FRIEND","INSIDER"}: abort(400)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email):
        flash("Enter a valid email.","error"); return redirect(url_for("web.control"))
    if User.query.filter_by(email=email).first():
        flash("That email already has an account.","error"); return redirect(url_for("web.control"))
    raw=token_urlsafe(32)
    inv=Invite(email=email,role=role,token_hash=hashlib.sha256(raw.encode()).hexdigest(),expires_at=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(days=7),created_by=g.user.id)
    db.session.add(inv); audit("invite.create","invite",email,{"role":role}); db.session.commit()
    link=url_for("web.accept_invite",token=raw,_external=True)
    flash("Invite created. Copy the link below.","success")
    return render_template("invite_created.html",link=link,email=email,role=role)

@bp.route("/invite/<token>", methods=["GET","POST"])
@limiter.limit("20 per hour")
def accept_invite(token):
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    inv=Invite.query.filter_by(token_hash=token_hash,used_at=None).first_or_404()
    if inv.expires_at < datetime.now(timezone.utc).replace(tzinfo=None): abort(410)
    existing=User.query.filter_by(email=inv.email).first()
    if existing:
        if not existing.is_active:
            session.clear(); session["setup_user_id"]=existing.id; session["setup_invite_id"]=inv.id
            return redirect(url_for("web.setup_2fa"))
        abort(410)
    if request.method=="POST":
        name=request.form.get("name","").strip()
        password=request.form.get("password","")
        if len(name)<2: flash("Enter your name.","error"); return render_template("invite.html",invite=inv)
        if len(password)<12: flash("Use at least 12 characters.","error"); return render_template("invite.html",invite=inv)
        secret=pyotp.random_base32()
        user=User(email=inv.email,display_name=name,role=inv.role,password_hash=hash_password(password),totp_secret_enc=encrypt_secret(secret),is_active=False)
        db.session.add(user); db.session.flush()
        session.clear(); session["setup_user_id"]=user.id; session["setup_invite_id"]=inv.id
        db.session.commit()
        return redirect(url_for("web.setup_2fa"))
    return render_template("invite.html",invite=inv)

@bp.route("/setup-2fa", methods=["GET","POST"])
def setup_2fa():
    uid=session.get("setup_user_id"); iid=session.get("setup_invite_id")
    user=db.session.get(User,uid) if uid else None; inv=db.session.get(Invite,iid) if iid else None
    if not user or not inv or inv.used_at: return redirect(url_for("web.login"))
    secret=decrypt_secret(user.totp_secret_enc)
    uri=pyotp.TOTP(secret).provisioning_uri(name=user.email,issuer_name="Market Forensics")
    if request.method=="POST":
        code=re.sub(r"\D","",request.form.get("code",""))
        if not pyotp.TOTP(secret).verify(code,valid_window=1):
            flash("Code not valid yet. Check your authenticator and try again.","error")
        else:
            user.is_active=True; inv.used_at=datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.add(AuditEvent(actor_user_id=user.id,action="account.activate",object_type="user",object_id=str(user.id),meta={"role":user.role}))
            db.session.commit(); session.clear(); flash("Account activated. Sign in to continue.","success")
            return redirect(url_for("web.login"))
    import io, base64, qrcode
    buf=io.BytesIO(); qrcode.make(uri).save(buf,format="PNG")
    qr=base64.b64encode(buf.getvalue()).decode()
    return render_template("setup_2fa.html",user=user,secret=secret,qr=qr)

@bp.post("/control/company")
@role_required("CONTROL")
def add_company():
    ticker=request.form.get("ticker","").strip().upper()
    name=request.form.get("name","").strip()
    validation=validate_ticker(ticker)
    if not validation.valid:
        flash(validation.message or "Ticker not found / symbol not recognized.","error"); return redirect(url_for("web.control"))
    if not name: flash("Company name is required in v0.0.1.","error"); return redirect(url_for("web.control"))
    if Company.query.filter_by(ticker=ticker).first(): flash("Ticker already exists.","error"); return redirect(url_for("web.control"))
    c=Company(ticker=ticker,name=name,status="MONITOR",summary="Private research shell. Not published yet.")
    db.session.add(c); db.session.flush(); audit("company.create","company",c.id,{"ticker":ticker}); db.session.commit()
    flash(f"{ticker} added to CONTROL. Research execution arrives in a later 0.x build.","success")
    return redirect(url_for("web.control"))
