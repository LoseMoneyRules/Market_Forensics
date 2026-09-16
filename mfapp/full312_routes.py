from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for

from .extensions import db as web_db
from .full312 import initialize_engine, load_state, publication_payload, sync_engine_credentials
from .models import AuditEvent, Company, Publication
from .security import login_required, role_required

bp = Blueprint("full312", __name__, url_prefix="/workstation")
SECTIONS = {
    "decide", "research", "fundamentals", "financial-flows", "management", "valuation",
    "tape", "monitoring", "validate", "risk", "portfolio", "sources",
}


def _control_view() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if str(g.user.role or "").upper() != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


def _audit(action: str, ticker: str = "", meta: dict | None = None) -> None:
    web_db.session.add(AuditEvent(
        actor_user_id=g.user.id,
        action=action,
        object_type="company" if ticker else "user",
        object_id=ticker or str(g.user.id),
        meta={"engine": "3.1.12 FULL", **(meta or {})},
    ))


def _float(name: str, default=None):
    raw = str(request.form.get(name, "")).strip().replace(",", "")
    if raw == "":
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


def _web_company(ticker: str, name: str | None = None) -> Company:
    t = ticker.upper()
    company = Company.query.filter_by(ticker=t).first()
    if company is None:
        company = Company(
            ticker=t,
            name=(name or t)[:180],
            status="RESEARCH",
            summary="Market Forensics V3.1.12 FULL research file.",
        )
        web_db.session.add(company)
        web_db.session.flush()
    elif name and (not company.name or company.name == company.ticker):
        company.name = name[:180]
    return company


@bp.get("")
@login_required
def workspace():
    _control_view()
    from market_forensics import db, service

    initialize_engine()
    coverage = db.list_coverage(include_archived=False)
    rows = []
    for cov in coverage:
        t = cov.get("ticker") or cov.get("Ticker")
        if not t:
            continue
        try:
            summary = service.coverage_summary_row(t)
        except Exception as exc:
            summary = {"Ticker": t, "Status": cov.get("status", "RESEARCH"), "Refresh Status": f"ERROR: {type(exc).__name__}"}
        web_company = Company.query.filter_by(ticker=str(t).upper()).first()
        rows.append({"coverage": cov, "summary": summary, "company": web_company})
    positions = db.list_portfolio_positions()
    return render_template("full312_workspace.html", rows=rows, positions=positions)


@bp.route("/discovery", methods=["GET", "POST"])
@login_required
def discovery():
    _control_view()
    from market_forensics import discovery as engine_discovery

    initialize_engine()
    if request.method == "POST":
        sync_engine_credentials(g.user.id)
        result = engine_discovery.start_scan(force_fundamentals=request.form.get("force") == "1")
        _audit("full312.discovery_start", meta=result)
        web_db.session.commit()
        flash("Discovery scan started." if result.get("started") else "Discovery scan is already running.", "success")
        return redirect(url_for("full312.discovery"))
    latest = engine_discovery.latest_run()
    rows = engine_discovery.interesting_rows(limit=100, side=request.args.get("side", "BOTH"))
    return render_template("full312_discovery.html", run=latest, rows=rows)


@bp.post("/add")
@role_required("CONTROL")
def add_ticker():
    _control_view()
    from market_forensics import db, symbols

    initialize_engine()
    sync_engine_credentials(g.user.id)
    ticker = str(request.form.get("ticker") or "").strip().upper()
    result = symbols.validate_ticker(ticker)
    if not result.valid:
        flash(result.message or "Ticker not found / symbol not recognized.", "error")
        return redirect(request.referrer or url_for("full312.workspace"))
    db.ensure_coverage(result.ticker)
    company = _web_company(result.ticker, result.name or result.ticker)
    _audit("full312.coverage_add", result.ticker, {"source": result.source})
    web_db.session.commit()
    flash(f"{result.ticker} added to V3.1.12 Coverage.", "success")
    return redirect(url_for("full312.company", ticker=result.ticker, section="decide"))


@bp.post("/<ticker>/refresh")
@role_required("CONTROL")
def refresh(ticker):
    _control_view()
    from market_forensics import db, service

    initialize_engine()
    sync_engine_credentials(g.user.id)
    t = ticker.upper()
    try:
        result = service.refresh_core(t, include_flow=request.form.get("include_flow", "1") != "0")
        meta = db.query("SELECT company_name FROM company_meta WHERE ticker=?", (t,))
        company = _web_company(t, (meta[0].get("company_name") if meta else None) or t)
        _audit("full312.refresh", t, {"status": result.get("status"), "bars": result.get("bars"), "fundamentals": result.get("fundamentals")})
        web_db.session.commit()
        warnings = result.get("warnings") or []
        message = f"{t} refreshed with the V3.1.12 FULL engine: {result.get('status','PASS')}."
        if warnings:
            message += " " + " | ".join(str(x) for x in warnings[:3])
        flash(message, "success" if result.get("status") == "PASS" else "warning")
    except Exception as exc:
        web_db.session.rollback()
        flash(f"{t} refresh failed safely: {type(exc).__name__}: {exc}. Last-good engine data was preserved.", "error")
    section = request.form.get("section") or "decide"
    return redirect(url_for("full312.company", ticker=t, section=section))


@bp.get("/<ticker>/<section>")
@login_required
def company(ticker, section):
    _control_view()
    if section not in SECTIONS:
        abort(404)
    t = ticker.upper()
    try:
        state = load_state(t)
    except Exception as exc:
        flash(f"Unable to load {t} from the V3.1.12 engine: {type(exc).__name__}: {exc}", "error")
        return redirect(url_for("full312.workspace"))
    meta = (state.get("snap") or {}).get("meta") or []
    name = (dict(meta[-1]).get("company_name") if meta else None) or t
    web_company = _web_company(t, name)
    web_db.session.commit()
    return render_template("full312_company.html", company=web_company, ticker=t, section=section, state=state)


@bp.post("/<ticker>/research")
@role_required("CONTROL")
def save_research(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    fields = [
        "thesis_title", "business_notes", "value_drivers", "market_narrative", "variant_view",
        "key_evidence", "catalysts", "evidence_to_add", "invalidation", "primary_risk",
        "secondary_risks", "portfolio_risk_notes",
    ]
    payload = {name: str(request.form.get(name) or "").strip() for name in fields}
    db.save_research(ticker, payload)
    _audit("full312.research_save", ticker)
    web_db.session.commit()
    flash("V3.1.12 research saved with history preserved.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="research"))


@bp.post("/<ticker>/variant")
@role_required("CONTROL")
def save_variant(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    db.save_variant_case(ticker, {
        "market_belief": request.form.get("market_belief", "").strip(),
        "our_belief": request.form.get("our_belief", "").strip(),
        "disagreement_evidence": request.form.get("disagreement_evidence", "").strip(),
        "resolution_signal": request.form.get("resolution_signal", "").strip(),
        "resolution_horizon": request.form.get("resolution_horizon", "").strip(),
        "path_status": request.form.get("path_status", "UNCLEAR").strip().upper(),
    })
    _audit("full312.variant_save", ticker)
    web_db.session.commit()
    flash("Variant perception saved.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="research"))


@bp.post("/<ticker>/risk")
@role_required("CONTROL")
def save_risk(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    existing = db.risk_plan(ticker) or {}
    # Once an invalidation is present and explicitly locked, do not silently move it.
    locked = str(existing.get("kill_switch") or "").startswith("LOCKED:")
    requested_invalidation = _float("invalidation_price", existing.get("invalidation_price"))
    if locked and requested_invalidation != existing.get("invalidation_price"):
        flash("Invalidation is locked and cannot be moved retroactively. Create a new thesis/research version instead.", "error")
        return redirect(url_for("full312.company", ticker=ticker.upper(), section="risk"))
    kill = request.form.get("kill_switch", "").strip()
    if request.form.get("lock_invalidation") == "1" and not kill.startswith("LOCKED:"):
        kill = "LOCKED: " + kill
    db.save_risk_plan(ticker, {
        "portfolio_risk_budget": _float("portfolio_risk_budget"),
        "invalidation_price": requested_invalidation,
        "event_liquidity_haircut": _float("event_liquidity_haircut"),
        "max_position_cap": _float("max_position_cap"),
        "correlation_notes": request.form.get("correlation_notes", "").strip(),
        "kill_switch": kill,
    })
    _audit("full312.risk_save", ticker, {"invalidation_locked": kill.startswith("LOCKED:")})
    web_db.session.commit()
    flash("Risk plan saved.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="risk"))


@bp.post("/<ticker>/portfolio")
@role_required("CONTROL")
def save_portfolio(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    shares = _float("shares", 0.0) or 0.0
    avg_cost = _float("avg_cost", 0.0) or 0.0
    db.upsert_portfolio_position(
        ticker,
        request.form.get("side", "LONG"),
        shares,
        avg_cost,
        request.form.get("tags", "").strip(),
        request.form.get("notes", "").strip(),
    )
    _audit("full312.portfolio_save", ticker, {"shares": shares, "side": request.form.get("side", "LONG")})
    web_db.session.commit()
    flash("Portfolio position saved in the V3.1.12 engine.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="portfolio"))


@bp.post("/<ticker>/valuation")
@role_required("CONTROL")
def save_valuation(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    state = load_state(ticker)
    assumptions = deepcopy(state.get("assumptions") or {})
    assumptions["model_version"] = "2.5"
    assumptions["reviewed_at"] = db.now_utc()
    assumptions["horizon_years"] = _int("horizon_years", int(assumptions.get("horizon_years") or 5), 1, 15)
    for case in ("bear", "base", "bull"):
        current = dict(assumptions.get(case) or {})
        for field in ("growth", "operating_margin", "net_margin", "fcf_margin", "tax_rate", "pe", "ev_sales", "target_fcf_yield", "wacc", "terminal_growth", "prob", "manual_override"):
            value = _float(f"{case}_{field}", current.get(field))
            if value is not None:
                current[field] = value
        assumptions[case] = current
    weights = dict(assumptions.get("weights") or {})
    for field in ("pe", "ev_sales", "fcf_yield"):
        value = _float(f"weight_{field}", weights.get(field))
        if value is not None:
            weights[field] = max(0.0, value)
    assumptions["weights"] = weights
    db.save_json_assumptions(ticker, assumptions)
    _audit("full312.valuation_save", ticker, {"model_version": "2.5"})
    web_db.session.commit()
    flash("V3.1.12 valuation assumptions reviewed and saved.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="valuation"))


@bp.post("/<ticker>/forecast")
@role_required("CONTROL")
def save_forecast(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    state = load_state(ticker)
    assumptions = deepcopy(state.get("forecast_assumptions") or {})
    assumptions["model_version"] = "2.7"
    assumptions["reviewed_at"] = db.now_utc()
    assumptions["years"] = _int("years", int(assumptions.get("years") or 5), 1, 10)
    fields = (
        "growth_start", "growth_terminal", "gross_margin_target", "operating_margin_target",
        "net_margin_target", "fcf_margin_target", "share_change", "exit_pe", "required_return",
    )
    for case in ("bear", "base", "bull"):
        current = dict(assumptions.get(case) or {})
        for field in fields:
            value = _float(f"{case}_{field}", current.get(field))
            if value is not None:
                current[field] = value
        assumptions[case] = current
    db.save_forecast_assumptions(ticker, assumptions)
    _audit("full312.forecast_save", ticker, {"model_version": "2.7"})
    web_db.session.commit()
    flash("Five-year forecast assumptions saved.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="management"))


@bp.post("/<ticker>/monitor")
@role_required("CONTROL")
def add_monitor(ticker):
    _control_view()
    from market_forensics import db

    initialize_engine()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Monitoring KPI name is required.", "error")
        return redirect(url_for("full312.company", ticker=ticker.upper(), section="monitoring"))
    now = db.now_utc()
    db.execute(
        """INSERT INTO monitoring_kpis(ticker,name,category,metric_key,source_type,unit,manual_value,operator,threshold,linked_to,importance,locked,notes,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ticker.upper(), name, request.form.get("category", "Business"), request.form.get("metric_key", ""),
            request.form.get("source_type", "AUTO"), request.form.get("unit", ""), _float("manual_value"),
            request.form.get("operator", ">="), _float("threshold"), request.form.get("linked_to", "THESIS"),
            _int("importance", 3, 1, 5), 1 if request.form.get("locked") == "1" else 0,
            request.form.get("notes", "").strip(), now, now,
        ),
    )
    _audit("full312.monitor_add", ticker, {"name": name})
    web_db.session.commit()
    flash("Monitoring KPI added.", "success")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="monitoring"))


@bp.post("/<ticker>/validate")
@role_required("CONTROL")
def run_validation(ticker):
    _control_view()
    from market_forensics import backtest

    initialize_engine()
    sync_engine_credentials(g.user.id)
    start_year = _int("start_year", datetime.now().year - 6, 1990, datetime.now().year - 1)
    end_year = _int("end_year", datetime.now().year - 2, start_year, datetime.now().year - 1)
    try:
        result = backtest.run_validation(ticker, start_year, end_year)
        summary = result.get("summary") or {}
        _audit("full312.validate", ticker, {"start_year": start_year, "end_year": end_year, "reliability": summary.get("reliability_score")})
        web_db.session.commit()
        flash(f"Point-in-time validation completed: {summary.get('reliability_label','REVIEW')}.", "success")
    except Exception as exc:
        web_db.session.rollback()
        flash(f"Validation failed safely: {type(exc).__name__}: {exc}", "error")
    return redirect(url_for("full312.company", ticker=ticker.upper(), section="validate"))


@bp.get("/<ticker>/export/<kind>")
@login_required
def export(ticker, kind):
    _control_view()
    from market_forensics import exporter

    state = load_state(ticker)
    t = ticker.upper()
    if kind == "pdf":
        data = exporter.research_report_pdf(t, state)
        return send_file(data, mimetype="application/pdf", as_attachment=True, download_name=f"Market_Forensics_{t}_V312.pdf")
    if kind == "docx":
        data = exporter.research_report_docx(t, state)
        return send_file(data, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document", as_attachment=True, download_name=f"Market_Forensics_{t}_V312.docx")
    if kind == "one-page-pdf":
        data = exporter.one_page_pdf(t, state)
        return send_file(data, mimetype="application/pdf", as_attachment=True, download_name=f"Market_Forensics_{t}_One_Page.pdf")
    abort(404)


@bp.post("/<ticker>/publish")
@role_required("CONTROL")
def publish(ticker):
    _control_view()
    t = ticker.upper()
    state = load_state(t)
    web_company = _web_company(t)
    visibility = request.form.get("visibility", "FRIEND").upper()
    if visibility not in {"FRIEND", "INSIDER"}:
        abort(400)
    for row in Publication.query.filter_by(company_id=web_company.id, is_current=True).all():
        row.is_current = False
    max_version = web_db.session.query(web_db.func.max(Publication.version)).filter(Publication.company_id == web_company.id).scalar() or 0
    pub = Publication(
        company_id=web_company.id,
        version=int(max_version) + 1,
        visibility=visibility,
        payload=publication_payload(t, state),
        is_current=True,
        model_version="0.0.4 / V3.1.12 FULL",
    )
    web_db.session.add(pub)
    _audit("full312.publish", t, {"version": pub.version, "visibility": visibility})
    web_db.session.commit()
    flash(f"{t} published as immutable snapshot v{pub.version} for {visibility}+.", "success")
    return redirect(url_for("full312.company", ticker=t, section="decide"))
