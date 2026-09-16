from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

import pyotp
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from .extensions import db, limiter
from .marketdata import provider_status, refresh_quote, set_secret
from .models import (
    AppSecret,
    AuditEvent,
    Company,
    DecisionJournal,
    Invite,
    MarketSnapshot,
    MonitoringItem,
    PrivatePosition,
    Publication,
    ResearchWorkspace,
    User,
    ValidationRun,
)
from .research_core import build_decision, merged_payload, publication_payload, safe_float, update_payload_from_form
from .security import decrypt_secret, encrypt_secret, hash_password, login_required, role_required, verify_password
from .symbols import validate_ticker

bp = Blueprint("web", __name__)

ROLE_RANK = {"FRIEND": 1, "INSIDER": 2, "CONTROL": 3}
MONITOR_STATUSES = {"OK", "WATCH", "FAIL"}
COMPANY_STATUSES = {"MONITOR", "RESEARCH", "READY", "NO EDGE", "ARCHIVED"}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def audit(action, object_type=None, object_id=None, meta=None):
    db.session.add(
        AuditEvent(
            actor_user_id=getattr(g.user, "id", None),
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id is not None else None,
            meta=meta or {},
        )
    )


def effective_role() -> str:
    return str(getattr(g, "view_role", None) or getattr(getattr(g, "user", None), "role", "FRIEND")).upper()


def can_view_publication(pub: Publication, role: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(pub.visibility, 99)


def require_control_view() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if g.user.role != "CONTROL":
        abort(403)
    if effective_role() != "CONTROL":
        abort(404)


def workspace_for(company: Company, create: bool = True) -> ResearchWorkspace | None:
    row = ResearchWorkspace.query.filter_by(company_id=company.id).first()
    if row is None and create:
        row = ResearchWorkspace(company_id=company.id, payload=merged_payload(None), updated_by=g.user.id if g.user else None)
        db.session.add(row)
        db.session.commit()
    return row


def position_for(company_id: int) -> PrivatePosition | None:
    if not getattr(g, "user", None):
        return None
    return PrivatePosition.query.filter_by(user_id=g.user.id, company_id=company_id).first()


def decision_for(company: Company, workspace: ResearchWorkspace | None = None):
    workspace = workspace or workspace_for(company)
    snapshot = MarketSnapshot.query.filter_by(company_id=company.id).first()
    position = position_for(company.id)
    monitoring_count = MonitoringItem.query.filter_by(company_id=company.id, is_active=True).count()
    journal_count = DecisionJournal.query.filter_by(company_id=company.id, user_id=g.user.id).count()
    validation_count = ValidationRun.query.filter_by(company_id=company.id).count()
    decision = build_decision(
        workspace.payload if workspace else {},
        snapshot.price if snapshot else None,
        position.shares if position else None,
        monitoring_count,
        journal_count,
        validation_count,
    )
    return decision, snapshot, position


def published_rows(role: str):
    pubs = Publication.query.filter_by(is_current=True).order_by(Publication.published_at.desc()).all()
    return [p for p in pubs if can_view_publication(p, role)]


@bp.get("/health")
def health():
    return {"status": "ok", "version": "0.0.2"}


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
        return redirect(url_for("web.verify_2fa"))
    return render_template("login.html")


@bp.route("/verify", methods=["GET", "POST"])
@limiter.limit("12 per minute")
def verify_2fa():
    uid = session.get("pending_2fa_user_id")
    if not uid:
        return redirect(url_for("web.login"))
    user = db.session.get(User, uid)
    if not user:
        session.clear()
        return redirect(url_for("web.login"))
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
        db.session.add(AuditEvent(actor_user_id=user.id, action="auth.login", object_type="user", object_id=str(user.id), meta={}))
        db.session.commit()
        return redirect(url_for("web.dashboard"))
    return render_template("verify.html")


@bp.post("/logout")
@login_required
def logout():
    audit("auth.logout", "user", g.user.id)
    db.session.commit()
    session.clear()
    return redirect(url_for("web.login"))


@bp.post("/view-as")
@role_required("CONTROL")
def view_as():
    role = request.form.get("role", "CONTROL").upper()
    if role not in ROLE_RANK:
        abort(400)
    session["view_as"] = role
    audit("control.preview_role", "user", g.user.id, {"view_as": role})
    db.session.commit()
    return redirect(url_for("web.dashboard"))


@bp.get("/")
@login_required
def dashboard():
    role = effective_role()
    if role != "CONTROL":
        rows = published_rows(role)
        return render_template("dashboard.html", publications=rows, public_mode=True)

    companies = Company.query.order_by(Company.created_at.desc()).all()
    published = Publication.query.filter_by(is_current=True).count()
    positions = PrivatePosition.query.filter_by(user_id=g.user.id).count()
    active_monitors = MonitoringItem.query.filter_by(is_active=True).count()
    queue = []
    for company in companies[:12]:
        ws = workspace_for(company)
        decision, snapshot, position = decision_for(company, ws)
        queue.append({"company": company, "workspace": ws, "decision": decision, "snapshot": snapshot, "position": position})
    return render_template(
        "dashboard.html",
        public_mode=False,
        companies=companies,
        published_count=published,
        position_count=positions,
        monitor_count=active_monitors,
        queue=queue,
    )


@bp.get("/company/<ticker>")
@login_required
def company(ticker):
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    pub = Publication.query.filter_by(company_id=company.id, is_current=True).first()
    role = effective_role()
    if not pub or not can_view_publication(pub, role):
        abort(404)
    audit("research.view", "company", company.id, {"ticker": company.ticker, "view_role": role})
    db.session.commit()
    return render_template(
        "company.html",
        company=company,
        pub=pub,
        insider=ROLE_RANK[role] >= ROLE_RANK["INSIDER"],
        control=g.user.role == "CONTROL" and role == "CONTROL",
    )


@bp.get("/discover")
@login_required
def discover():
    require_control_view()
    companies = Company.query.order_by(Company.created_at.desc()).all()
    rows = []
    for company in companies:
        ws = workspace_for(company)
        decision, snapshot, position = decision_for(company, ws)
        rows.append({"company": company, "workspace": ws, "decision": decision, "snapshot": snapshot, "position": position})
    return render_template("discover.html", rows=rows)


@bp.post("/company")
@role_required("CONTROL")
def add_company():
    require_control_view()
    ticker = request.form.get("ticker", "").strip().upper()
    supplied_name = request.form.get("name", "").strip()
    validation = validate_ticker(ticker)
    if not validation.valid:
        flash(validation.message or "Ticker not found / symbol not recognized.", "error")
        return redirect(request.referrer or url_for("web.discover"))
    if Company.query.filter_by(ticker=ticker).first():
        flash("Ticker already exists.", "error")
        return redirect(request.referrer or url_for("web.discover"))
    name = supplied_name or validation.name or ticker
    company = Company(ticker=ticker, name=name, status="MONITOR", summary="Research file created. Thesis not yet defined.")
    db.session.add(company)
    db.session.flush()
    db.session.add(ResearchWorkspace(company_id=company.id, payload=merged_payload(None), updated_by=g.user.id))
    audit("company.create", "company", company.id, {"ticker": ticker})
    db.session.commit()
    quote = refresh_quote(company.id, company.ticker, g.user.id)
    if quote.ok:
        flash(f"{ticker} added. Quote refreshed from {quote.provider}.", "success")
    else:
        flash(f"{ticker} added. Quote refresh unavailable; research file is intact.", "success")
    return redirect(url_for("web.decide", ticker=ticker))


@bp.post("/company/<ticker>/refresh-price")
@role_required("CONTROL")
def refresh_price(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    result = refresh_quote(company.id, company.ticker, g.user.id)
    if result.ok:
        audit("market.refresh", "company", company.id, {"provider": result.provider, "price": result.price})
        db.session.commit()
        flash(f"{ticker} price refreshed from {result.provider}.", "success")
    else:
        flash(f"Price refresh failed. Last-good data was preserved. {result.message}", "error")
    return redirect(request.referrer or url_for("web.decide", ticker=ticker))


@bp.get("/decision-queue")
@login_required
def decision_queue():
    require_control_view()
    companies = Company.query.order_by(Company.ticker).all()
    rows = []
    for company in companies:
        ws = workspace_for(company)
        decision, snapshot, position = decision_for(company, ws)
        rows.append({"company": company, "workspace": ws, "decision": decision, "snapshot": snapshot, "position": position})
    rows.sort(key=lambda x: (x["decision"].decision in {"NO EDGE / WAIT"}, -x["decision"].readiness_count, x["company"].ticker))
    return render_template("decision_queue.html", rows=rows)


@bp.get("/decide/<ticker>")
@login_required
def decide(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    ws = workspace_for(company)
    decision, snapshot, position = decision_for(company, ws)
    validation = ValidationRun.query.filter_by(company_id=company.id).order_by(ValidationRun.created_at.desc()).first()
    journal = DecisionJournal.query.filter_by(company_id=company.id, user_id=g.user.id).order_by(DecisionJournal.created_at.desc()).limit(5).all()
    return render_template(
        "decide.html",
        company=company,
        ws=ws,
        p=merged_payload(ws.payload),
        decision=decision,
        snapshot=snapshot,
        position=position,
        validation=validation,
        journal=journal,
    )


@bp.route("/research/<ticker>", methods=["GET", "POST"])
@login_required
def research(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    ws = workspace_for(company)
    if request.method == "POST":
        ws.payload = update_payload_from_form(ws.payload or {}, request.form)
        state = request.form.get("research_state", ws.research_state).upper()
        if state in {"DRAFT", "REVIEW", "READY"}:
            ws.research_state = state
        company.status = request.form.get("company_status", company.status).upper()
        if company.status not in COMPANY_STATUSES:
            company.status = "RESEARCH"
        summary = request.form.get("company_summary")
        if summary is not None:
            company.summary = summary.strip()
        ws.updated_by = g.user.id
        audit("research.update", "company", company.id, {"section": request.form.get("_section", "research")})
        db.session.commit()
        flash("Research workspace saved.", "success")
        return redirect(url_for("web.research", ticker=company.ticker))
    decision, snapshot, position = decision_for(company, ws)
    return render_template(
        "research.html",
        company=company,
        ws=ws,
        p=merged_payload(ws.payload),
        decision=decision,
        snapshot=snapshot,
        position=position,
    )


@bp.get("/monitor/<ticker>")
@login_required
def monitor(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    ws = workspace_for(company)
    decision, snapshot, position = decision_for(company, ws)
    items = MonitoringItem.query.filter_by(company_id=company.id, is_active=True).order_by(MonitoringItem.updated_at.desc()).all()
    return render_template("monitor.html", company=company, ws=ws, p=merged_payload(ws.payload), decision=decision, snapshot=snapshot, position=position, items=items)


@bp.post("/monitor/<ticker>/item")
@role_required("CONTROL")
def add_monitor_item(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    label = request.form.get("label", "").strip()
    if not label:
        flash("KPI / monitoring label is required.", "error")
        return redirect(url_for("web.monitor", ticker=company.ticker))
    status = request.form.get("status", "WATCH").upper()
    if status not in MONITOR_STATUSES:
        status = "WATCH"
    item = MonitoringItem(
        company_id=company.id,
        label=label,
        status=status,
        current_value=request.form.get("current_value", "").strip(),
        threshold=request.form.get("threshold", "").strip(),
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(item)
    audit("monitor.create", "company", company.id, {"label": label, "status": status})
    db.session.commit()
    flash("Monitoring item added.", "success")
    return redirect(url_for("web.monitor", ticker=company.ticker))


@bp.post("/monitor/<int:item_id>/update")
@role_required("CONTROL")
def update_monitor_item(item_id):
    require_control_view()
    item = db.session.get(MonitoringItem, item_id)
    if not item:
        abort(404)
    status = request.form.get("status", item.status).upper()
    if status in MONITOR_STATUSES:
        item.status = status
    item.current_value = request.form.get("current_value", item.current_value).strip()
    item.notes = request.form.get("notes", item.notes).strip()
    if request.form.get("archive") == "1":
        item.is_active = False
    audit("monitor.update", "monitor", item.id, {"status": item.status, "active": item.is_active})
    db.session.commit()
    company = db.session.get(Company, item.company_id)
    return redirect(url_for("web.monitor", ticker=company.ticker))


@bp.get("/validate/<ticker>")
@login_required
def validate(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    ws = workspace_for(company)
    decision, snapshot, position = decision_for(company, ws)
    runs = ValidationRun.query.filter_by(company_id=company.id).order_by(ValidationRun.created_at.desc()).all()
    return render_template("validate.html", company=company, ws=ws, p=merged_payload(ws.payload), decision=decision, snapshot=snapshot, position=position, runs=runs)


@bp.post("/validate/<ticker>/record")
@role_required("CONTROL")
def record_validation(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    def pct(name):
        value = safe_float(request.form.get(name))
        return max(0.0, min(100.0, value)) if value is not None else None
    score = pct("reliability_score")
    sample = request.form.get("sample_size", "0")
    try:
        sample_size = max(0, int(sample))
    except ValueError:
        sample_size = 0
    run = ValidationRun(
        company_id=company.id,
        user_id=g.user.id,
        status=request.form.get("status", "REVIEW").upper()[:24],
        reliability_score=score,
        valuation_reliability=pct("valuation_reliability"),
        thesis_reliability=pct("thesis_reliability"),
        direction_reliability=pct("direction_reliability"),
        bias_control=pct("bias_control"),
        sample_size=sample_size,
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(run)
    audit("validation.record", "company", company.id, {"score": score, "sample": sample_size})
    db.session.commit()
    flash("Validation result recorded. Historical replay automation is tracked separately from this audit record.", "success")
    return redirect(url_for("web.validate", ticker=company.ticker))


@bp.get("/portfolio")
@login_required
def portfolio():
    require_control_view()
    positions = PrivatePosition.query.filter_by(user_id=g.user.id).all()
    rows = []
    for position in positions:
        company = db.session.get(Company, position.company_id)
        ws = workspace_for(company)
        decision, snapshot, _ = decision_for(company, ws)
        market_value = (snapshot.price * position.shares) if snapshot and snapshot.price is not None else None
        cost_value = position.avg_cost * position.shares
        pnl = market_value - cost_value if market_value is not None else None
        rows.append({"position": position, "company": company, "decision": decision, "snapshot": snapshot, "market_value": market_value, "pnl": pnl})
    rows.sort(key=lambda x: x["company"].ticker)
    return render_template("portfolio.html", rows=rows)


@bp.post("/portfolio/position")
@role_required("CONTROL")
def save_position():
    require_control_view()
    ticker = request.form.get("ticker", "").strip().upper()
    company = Company.query.filter_by(ticker=ticker).first()
    if company is None:
        validation = validate_ticker(ticker)
        if not validation.valid:
            flash(validation.message or "Ticker not found / symbol not recognized.", "error")
            return redirect(url_for("web.portfolio"))
        company = Company(ticker=ticker, name=validation.name or ticker, status="MONITOR", summary="Portfolio holding; research not yet complete.")
        db.session.add(company)
        db.session.flush()
        db.session.add(ResearchWorkspace(company_id=company.id, payload=merged_payload(None), updated_by=g.user.id))
    shares = safe_float(request.form.get("shares"))
    avg_cost = safe_float(request.form.get("avg_cost"))
    if shares is None or avg_cost is None or avg_cost < 0:
        db.session.rollback()
        flash("Enter valid shares and average cost.", "error")
        return redirect(url_for("web.portfolio"))
    position = PrivatePosition.query.filter_by(user_id=g.user.id, company_id=company.id).first()
    if position is None:
        position = PrivatePosition(user_id=g.user.id, company_id=company.id, shares=shares, avg_cost=avg_cost)
        db.session.add(position)
    else:
        position.shares = shares
        position.avg_cost = avg_cost
    position.notes = request.form.get("notes", "").strip()
    audit("portfolio.save", "company", company.id, {"ticker": ticker, "shares": shares})
    db.session.commit()
    if MarketSnapshot.query.filter_by(company_id=company.id).first() is None:
        refresh_quote(company.id, company.ticker, g.user.id)
    flash(f"{ticker} position saved.", "success")
    return redirect(url_for("web.portfolio"))


@bp.post("/journal/<ticker>")
@role_required("CONTROL")
def add_journal(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    ws = workspace_for(company)
    decision, _, _ = decision_for(company, ws)
    entry = DecisionJournal(
        company_id=company.id,
        user_id=g.user.id,
        decision=request.form.get("decision", decision.position_action).strip()[:80],
        conviction=request.form.get("conviction", "").strip()[:24],
        thesis_snapshot=str((ws.payload or {}).get("thesis", "")),
        invalidation_snapshot=str((ws.payload or {}).get("invalidation", "")),
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(entry)
    audit("journal.create", "company", company.id, {"decision": entry.decision})
    db.session.commit()
    flash("Decision Journal entry saved.", "success")
    return redirect(url_for("web.decide", ticker=company.ticker))


@bp.post("/publish/<ticker>")
@role_required("CONTROL")
def publish(ticker):
    require_control_view()
    company = Company.query.filter_by(ticker=ticker.upper()).first_or_404()
    ws = workspace_for(company)
    decision, _, _ = decision_for(company, ws)
    visibility = request.form.get("visibility", "FRIEND").upper()
    if visibility not in {"FRIEND", "INSIDER"}:
        abort(400)
    current = Publication.query.filter_by(company_id=company.id, is_current=True).all()
    for row in current:
        row.is_current = False
    max_version = db.session.query(db.func.max(Publication.version)).filter(Publication.company_id == company.id).scalar() or 0
    pub = Publication(
        company_id=company.id,
        version=int(max_version) + 1,
        visibility=visibility,
        payload=publication_payload(ws.payload or {}, decision),
        is_current=True,
        model_version="0.0.2",
    )
    db.session.add(pub)
    ws.research_state = "READY"
    audit("publication.publish", "company", company.id, {"version": pub.version, "visibility": visibility})
    db.session.commit()
    flash(f"{ticker} published as immutable snapshot v{pub.version} for {visibility}+.", "success")
    return redirect(url_for("web.decide", ticker=company.ticker))


@bp.get("/settings")
@login_required
def settings():
    require_control_view()
    statuses = provider_status(g.user.id)
    return render_template("settings.html", providers=statuses)


@bp.post("/settings/providers")
@role_required("CONTROL")
def save_provider_settings():
    require_control_view()
    fields = {
        "alpaca_key": "alpaca_key",
        "alpaca_secret": "alpaca_secret",
        "tiingo_token": "tiingo_token",
        "alpha_vantage_key": "alpha_vantage_key",
        "massive_key": "massive_key",
    }
    for form_name, secret_name in fields.items():
        if request.form.get(f"remove_{form_name}") == "1":
            set_secret(g.user.id, secret_name, "")
        else:
            value = request.form.get(form_name, "").strip()
            if value:
                set_secret(g.user.id, secret_name, value)
    audit("settings.providers", "user", g.user.id, {"configured": provider_status(g.user.id)})
    db.session.commit()
    flash("Provider settings saved. Secrets remain encrypted in the database.", "success")
    return redirect(url_for("web.settings"))


@bp.get("/control")
@role_required("CONTROL")
def control():
    require_control_view()
    users = User.query.order_by(User.created_at.desc()).all()
    invites = Invite.query.filter(Invite.used_at.is_(None)).order_by(Invite.created_at.desc()).limit(20).all()
    companies = Company.query.order_by(Company.ticker).all()
    events = AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(25).all()
    return render_template("control.html", users=users, invites=invites, companies=companies, events=events)


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
    link = url_for("web.accept_invite", token=raw, _external=True)
    flash("Invite created. Copy the link below.", "success")
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
            session.clear()
            session["setup_user_id"] = existing.id
            session["setup_invite_id"] = inv.id
            return redirect(url_for("web.setup_2fa"))
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
        db.session.add(user)
        db.session.flush()
        session.clear()
        session["setup_user_id"] = user.id
        session["setup_invite_id"] = inv.id
        db.session.commit()
        return redirect(url_for("web.setup_2fa"))
    return render_template("invite.html", invite=inv)


@bp.route("/setup-2fa", methods=["GET", "POST"])
def setup_2fa():
    uid = session.get("setup_user_id")
    iid = session.get("setup_invite_id")
    user = db.session.get(User, uid) if uid else None
    inv = db.session.get(Invite, iid) if iid else None
    if not user or not inv or inv.used_at:
        return redirect(url_for("web.login"))
    secret = decrypt_secret(user.totp_secret_enc)
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Market Forensics")
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            flash("Code not valid yet. Check your authenticator and try again.", "error")
        else:
            user.is_active = True
            inv.used_at = utcnow()
            db.session.add(AuditEvent(actor_user_id=user.id, action="account.activate", object_type="user", object_id=str(user.id), meta={"role": user.role}))
            db.session.commit()
            session.clear()
            flash("Account activated. Sign in to continue.", "success")
            return redirect(url_for("web.login"))
    import base64
    import io
    import qrcode

    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    qr = base64.b64encode(buf.getvalue()).decode()
    return render_template("setup_2fa.html", user=user, secret=secret, qr=qr)
