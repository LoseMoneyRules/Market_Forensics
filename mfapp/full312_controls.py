from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .extensions import db as web_db
from .full312 import initialize_engine, load_state, sync_engine_credentials
from .models import AuditEvent, Company
from .security import login_required, role_required

bp = Blueprint("full312_controls", __name__, url_prefix="/workstation")


def _control_view() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if str(g.user.role or "").upper() != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


def _audit(action: str, ticker: str, meta: dict | None = None) -> None:
    web_db.session.add(AuditEvent(
        actor_user_id=g.user.id,
        action=action,
        object_type="company",
        object_id=ticker.upper(),
        meta={"engine": "3.1.12 FULL", **(meta or {})},
    ))


def _float(name: str, default=None):
    raw = str(request.form.get(name, "")).strip().replace(",", "")
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(request.form.get(name, default)))))
    except Exception:
        return default


def _valid_iso_date(value: str) -> str:
    text = str(value or "").strip()[:10]
    if not text:
        return ""
    return date.fromisoformat(text).isoformat()


def _company(ticker: str) -> Company:
    row = Company.query.filter_by(ticker=ticker.upper()).first()
    if row is None:
        row = Company(ticker=ticker.upper(), name=ticker.upper(), status="RESEARCH", summary="Market Forensics V3.1.12 FULL research file.")
        web_db.session.add(row)
        web_db.session.flush()
    return row


@bp.get("/<ticker>/controls")
@login_required
def controls(ticker):
    _control_view()
    t = ticker.upper()
    state = load_state(t)
    company = _company(t)
    web_db.session.commit()
    return render_template("full312_controls.html", ticker=t, company=company, state=state)


@bp.post("/<ticker>/controls/share-basis")
@role_required("CONTROL")
def save_share_basis(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    state = load_state(t)
    metrics = state.get("metrics") or {}
    if metrics.get("valuation_share_basis_usable") and not metrics.get("valuation_basis_override_active"):
        flash("Automatic current share basis is already usable. Manual recovery is only for unresolved corporate-action/share-basis cases.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#share-basis")

    shares = _float("current_basis_shares")
    basis_date = str(request.form.get("basis_date") or "").strip()[:10]
    source_note = str(request.form.get("source_note") or "").strip()
    mode = str(request.form.get("mode") or "USER_CURRENT_BASIS").upper()
    if mode not in {"USER_CURRENT_BASIS", "USER_VERIFIED_NO_SPLIT"}:
        abort(400)
    try:
        basis_date = _valid_iso_date(basis_date)
    except ValueError:
        flash("Basis date must be YYYY-MM-DD.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#share-basis")
    if not shares or shares <= 0 or not basis_date or not source_note or request.form.get("confirmed") != "1":
        flash("Verified current-basis shares, date, evidence/source note and explicit confirmation are all required.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#share-basis")

    db.save_share_basis_override(t, mode, shares, basis_date, source_note, True)
    _audit("full312.share_basis_override", t, {"mode": mode, "basis_date": basis_date})
    web_db.session.commit()
    flash("Current share-basis recovery saved. It affects current valuation only and remains excluded from historical Validate.", "success")
    return redirect(url_for("full312.company", ticker=t, section="decide"))


@bp.post("/<ticker>/controls/share-basis/clear")
@role_required("CONTROL")
def clear_share_basis(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    db.clear_share_basis_override(t)
    _audit("full312.share_basis_override_clear", t)
    web_db.session.commit()
    flash("Manual share-basis recovery removed. Automatic verification is active again.", "success")
    return redirect(url_for("full312.company", ticker=t, section="decide"))


@bp.post("/<ticker>/controls/gate")
@role_required("CONTROL")
def save_gate(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    state = load_state(t)
    gate_key = str(request.form.get("gate_key") or "").strip()
    allowed = {str(x.get("key") or "") for x in (state.get("gates") or [])}
    if not gate_key or gate_key not in allowed:
        abort(400)
    status = str(request.form.get("status") or "AUTO").upper()
    if status not in {"AUTO", "PASS", "WATCH", "FAIL", "INCOMPLETE"}:
        abort(400)
    notes = str(request.form.get("notes") or "").strip()
    db.save_gate_override(t, gate_key, None if status == "AUTO" else status, notes)
    _audit("full312.gate_override", t, {"gate_key": gate_key, "status": status})
    web_db.session.commit()
    flash("Investment-gate annotation saved. Underlying evidence remains the preferred source of truth.", "success")
    return redirect(url_for("full312_controls.controls", ticker=t) + "#gates")


@bp.post("/<ticker>/controls/peer")
@role_required("CONTROL")
def save_peer(ticker):
    _control_view()
    from market_forensics import db, symbols

    initialize_engine()
    t = ticker.upper()
    peer = str(request.form.get("peer_ticker") or "").strip().upper()
    relation = str(request.form.get("relation_type") or "PEER").upper()
    if relation not in {"PEER", "CUSTOMER", "SUPPLIER", "DISTRIBUTOR", "INDUSTRY"}:
        abort(400)
    result = symbols.validate_ticker(peer)
    if not result.valid:
        flash(result.message or "Ticker not found / symbol not recognized.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#peers")
    if result.ticker == t:
        flash("A company cannot be linked to itself.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#peers")
    db.save_peer_link(t, result.ticker, relation, "MANUAL", 1.0, str(request.form.get("notes") or "").strip())
    _audit("full312.peer_link", t, {"peer": result.ticker, "relation": relation})
    web_db.session.commit()
    flash(f"{result.ticker} linked as {relation}.", "success")
    return redirect(url_for("full312_controls.controls", ticker=t) + "#peers")


@bp.post("/<ticker>/controls/peer/delete")
@role_required("CONTROL")
def delete_peer(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    peer = str(request.form.get("peer_ticker") or "").strip().upper()
    relation = str(request.form.get("relation_type") or "PEER").upper()
    db.delete_peer_link(t, peer, relation)
    _audit("full312.peer_unlink", t, {"peer": peer, "relation": relation})
    web_db.session.commit()
    flash("Linked-company relation removed.", "success")
    return redirect(url_for("full312_controls.controls", ticker=t) + "#peers")


@bp.post("/<ticker>/controls/bear-case")
@role_required("CONTROL")
def save_bear_case(ticker):
    _control_view()
    from market_forensics import db, decision

    initialize_engine()
    t = ticker.upper()
    decision.seed_bear_case(t)
    key = str(request.form.get("item_key") or "").strip()
    existing = {str(x.get("item_key")): x for x in db.query("SELECT * FROM bear_case_items WHERE ticker=?", (t,))}
    if key not in existing:
        abort(400)
    status = str(request.form.get("status") or "OPEN").upper()
    if status not in {"OPEN", "WATCH", "MITIGATED", "CONFIRMED"}:
        abort(400)
    severity = _int("severity", int(existing[key].get("severity") or 3), 1, 5)
    db.execute(
        "UPDATE bear_case_items SET evidence_for=?,evidence_against=?,severity=?,status=?,updated_at=? WHERE ticker=? AND item_key=?",
        (
            str(request.form.get("evidence_for") or "").strip(),
            str(request.form.get("evidence_against") or "").strip(),
            severity, status, db.now_utc(), t, key,
        ),
    )
    _audit("full312.bear_case_update", t, {"item_key": key, "status": status, "severity": severity})
    web_db.session.commit()
    flash("Counter-thesis item saved.", "success")
    return redirect(url_for("full312_controls.controls", ticker=t) + "#bear-case")


@bp.post("/<ticker>/controls/monitoring/seed")
@role_required("CONTROL")
def seed_monitoring(ticker):
    _control_view()
    from market_forensics import decision

    t = ticker.upper()
    state = load_state(t)
    company_type = (state.get("coverage") or {}).get("company_type") or "Generic"
    n = decision.seed_default_kpis(t, company_type)
    _audit("full312.monitor_seed", t, {"count": n, "company_type": company_type})
    web_db.session.commit()
    flash(f"Created {n} default KPI rows. Replace seeded thresholds with thesis-specific thresholds before relying on them.", "success")
    return redirect(url_for("full312.company", ticker=t, section="monitoring"))


@bp.post("/<ticker>/controls/monitoring/manual")
@role_required("CONTROL")
def update_manual_kpi(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    try:
        kpi_id = int(request.form.get("kpi_id", ""))
    except Exception:
        abort(400)
    rows = db.query("SELECT * FROM monitoring_kpis WHERE id=? AND ticker=?", (kpi_id, t))
    if not rows or str(rows[0].get("source_type") or "").upper() != "MANUAL":
        abort(400)
    value = _float("manual_value")
    if value is None:
        flash("Enter a numeric manual value.", "error")
        return redirect(url_for("full312.company", ticker=t, section="monitoring"))
    stamp = db.now_utc()
    db.execute("UPDATE monitoring_kpis SET manual_value=?,updated_at=? WHERE id=?", (value, stamp, kpi_id))
    db.execute("INSERT INTO monitoring_history(kpi_id,ticker,captured_at,value) VALUES(?,?,?,?)", (kpi_id, t, stamp, value))
    _audit("full312.monitor_manual_value", t, {"kpi_id": kpi_id, "value": value})
    web_db.session.commit()
    flash("Manual KPI value recorded with history. Locked thresholds were not changed.", "success")
    return redirect(url_for("full312.company", ticker=t, section="monitoring"))


@bp.post("/<ticker>/controls/event")
@role_required("CONTROL")
def add_event(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    title = str(request.form.get("title") or "").strip()
    event_type = str(request.form.get("event_type") or "Catalyst")
    if event_type not in {"Earnings", "Investor Day", "Product", "Regulatory", "Macro", "Catalyst", "Other"}:
        abort(400)
    try:
        event_date = _valid_iso_date(request.form.get("event_date"))
    except ValueError:
        event_date = ""
    if not title or not event_date:
        flash("Event date and title are required.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#events")
    stamp = db.now_utc()
    db.execute(
        """INSERT INTO events(ticker,event_date,event_type,title,thesis_expectation,bear_case,base_case,bull_case,add_condition,reduce_condition,exit_condition,status,thesis_state,post_result,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            t, event_date, event_type, title,
            str(request.form.get("thesis_expectation") or "").strip(),
            str(request.form.get("bear_case") or "").strip(),
            str(request.form.get("base_case") or "").strip(),
            str(request.form.get("bull_case") or "").strip(),
            str(request.form.get("add_condition") or "").strip(),
            str(request.form.get("reduce_condition") or "").strip(),
            str(request.form.get("exit_condition") or "").strip(),
            "UPCOMING", "", "", stamp, stamp,
        ),
    )
    _audit("full312.event_freeze", t, {"event_date": event_date, "event_type": event_type, "title": title})
    web_db.session.commit()
    flash("Pre-event plan frozen. Closing the event can append the result but cannot rewrite this plan.", "success")
    return redirect(url_for("full312_controls.controls", ticker=t) + "#events")


@bp.post("/<ticker>/controls/event/close")
@role_required("CONTROL")
def close_event(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    try:
        event_id = int(request.form.get("event_id", ""))
    except Exception:
        abort(400)
    rows = db.query("SELECT * FROM events WHERE id=? AND ticker=?", (event_id, t))
    if not rows:
        abort(404)
    thesis_state = str(request.form.get("thesis_state") or "").upper()
    if thesis_state not in {"", "STRONGER", "SAME", "WEAKER", "BROKEN"}:
        abort(400)
    result = str(request.form.get("post_result") or "").strip()
    db.execute("UPDATE events SET status='DONE',thesis_state=?,post_result=?,updated_at=? WHERE id=?", (thesis_state, result, db.now_utc(), event_id))
    _audit("full312.event_close", t, {"event_id": event_id, "thesis_state": thesis_state})
    web_db.session.commit()
    flash("Event closed. Frozen pre-event Bear/Base/Bull and ADD/REDUCE/EXIT conditions were preserved.", "success")
    return redirect(url_for("full312_controls.controls", ticker=t) + "#events")


@bp.post("/<ticker>/controls/short-interest")
@role_required("CONTROL")
def save_short_interest(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    t = ticker.upper()
    try:
        settlement = _valid_iso_date(request.form.get("settlement_date"))
        publication = _valid_iso_date(request.form.get("publication_date"))
    except ValueError:
        settlement = publication = ""
    shares = _float("shares_short")
    dtc = _float("days_to_cover")
    if not settlement or not publication or shares is None or shares < 0:
        flash("Settlement date, publication date and non-negative shares short are required.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#short-interest")
    if publication < settlement:
        flash("Publication date cannot precede settlement date.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#short-interest")
    db.execute(
        """INSERT INTO short_interest(ticker,settlement_date,publication_date,shares_short,days_to_cover,created_at)
           VALUES(?,?,?,?,?,?) ON CONFLICT(ticker,settlement_date) DO UPDATE SET
           publication_date=excluded.publication_date,shares_short=excluded.shares_short,days_to_cover=excluded.days_to_cover""",
        (t, settlement, publication, shares, dtc, db.now_utc()),
    )
    _audit("full312.short_interest_manual", t, {"settlement_date": settlement, "publication_date": publication})
    web_db.session.commit()
    flash("Official Short Interest saved. Signals use publication date, never settlement date, to avoid look-ahead.", "success")
    return redirect(url_for("full312.company", ticker=t, section="tape"))


@bp.post("/<ticker>/controls/management-refresh")
@role_required("CONTROL")
def refresh_management(ticker):
    _control_view()
    from market_forensics import config, management

    initialize_engine()
    sync_engine_credentials(g.user.id)
    t = ticker.upper()
    user_agent = str(config.load_settings().get("sec_user_agent") or "").strip()
    if "@" not in user_agent:
        flash("Configure a valid SEC User-Agent with contact email in Settings before management deep scan.", "error")
        return redirect(url_for("full312_controls.controls", ticker=t) + "#management")
    try:
        result = management.refresh_management_documents(t, user_agent, filing_limit=40, include_exhibits=True)
        _audit("full312.management_refresh", t, {"result": result})
        web_db.session.commit()
        flash("Management filings/exhibits refreshed with the original V3.1.12 parser.", "success")
    except Exception as exc:
        web_db.session.rollback()
        flash(f"Management refresh failed safely: {type(exc).__name__}: {exc}", "error")
    return redirect(url_for("full312.company", ticker=t, section="management"))
