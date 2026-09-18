from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import abort, flash, g, redirect, request, url_for

from .access import audit, require_control_view
from .core_models import (
    BearCaseItem, Catalyst, DecisionJournal, Event, Expectation, ManagementAssessment,
    MonitoringHistory, MonitoringRule, Position, Source, ValuationScenario,
)
from .extensions import db
from .jobs import enqueue_job
from .management_promises import add_manual_promise
from .routes import RESEARCH_FIELDS, SECTION_KEYS, _ctx, _research_version, bp, dec, parse_date, utcnow
from .security import role_required
from .services import create_snapshot


def _queue_recalc(ctx) -> None:
    enqueue_job(
        "RECALCULATE",
        user_id=g.user.id,
        company_id=ctx["company"].id,
        security_id=ctx["security"].id,
        payload={"coverage_id": ctx["coverage"].id},
        priority=95,
    )


@bp.post("/company/<ticker>/research/<section>")
@role_required("CONTROL")
def save_research(ticker, section):
    require_control_view(); ctx = _ctx(ticker)
    if section not in RESEARCH_FIELDS and section != "overview": abort(404)
    research = ctx["research"]
    if section in RESEARCH_FIELDS:
        setattr(research, RESEARCH_FIELDS[section], str(request.form.get("text") or "").strip())
    else:
        for field in ("thesis", "counter_evidence", "variant_market", "variant_us", "variant_evidence", "narrative_fit_notes", "confirmation_bias_notes", "thesis_drift_notes"):
            if field in request.form: setattr(research, field, str(request.form.get(field) or "").strip())
        state = str(request.form.get("research_state") or ctx["coverage"].research_state).upper()
        if state in {"UNRATED", "UNDER_REVIEW", "ATTRACTIVE", "NEUTRAL", "DETERIORATING", "READY", "ARCHIVED"}: ctx["coverage"].research_state = state
        status = str(request.form.get("coverage_status") or ctx["coverage"].status).upper()
        if status in {"MONITOR", "RESEARCH", "READY", "ARCHIVED"}: ctx["coverage"].status = status
        ctx["coverage"].owner_summary = str(request.form.get("owner_summary") or ctx["coverage"].owner_summary).strip()
    research.updated_by = g.user.id; _research_version(ctx["coverage"], research, f"Saved {section}")
    audit("research.save", "coverage", ctx["coverage"].id, {"section": section}); db.session.commit(); _queue_recalc(ctx); flash("Research saved. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section=section if section in SECTION_KEYS else "overview"))


@bp.post("/company/<ticker>/triangulation")
@role_required("CONTROL")
def add_triangulation(ticker):
    require_control_view()
    ctx = _ctx(ticker)
    relation = str(request.form.get("relation") or "INDUSTRY").upper().strip()
    if relation not in {"PEER", "COMPETITOR", "CUSTOMER", "SUPPLIER", "DISTRIBUTOR", "INDUSTRY"}:
        relation = "INDUSTRY"
    subject = str(request.form.get("subject") or "").strip()[:180]
    evidence = str(request.form.get("evidence") or "").strip()
    url = str(request.form.get("url") or "").strip()[:1000]
    if not subject or not evidence:
        flash("Triangulation needs a subject and evidence.", "error")
        return redirect(url_for("web.company_section", ticker=ticker.upper(), section="business"))
    source = None
    if url:
        source = Source(
            company_id=ctx["company"].id,
            provider="MANUAL",
            source_type=f"TRIANGULATION_{relation}",
            title=subject,
            url=url,
            retrieved_at=utcnow(),
            meta={"relation": relation},
        )
        db.session.add(source)
        db.session.flush()
    db.session.add(Event(
        company_id=ctx["company"].id,
        source_id=source.id if source else None,
        event_type=f"TRIANGULATION_{relation}",
        title=subject,
        event_date=utcnow(),
        payload={"relation": relation, "evidence": evidence, "url": url, "actor_user_id": g.user.id},
    ))
    audit("research.triangulation.add", "company", ctx["company"].id, {"relation": relation, "subject": subject})
    db.session.commit()
    _queue_recalc(ctx)
    flash("External evidence added. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="business"))


@bp.post("/company/<ticker>/expectation")
@role_required("CONTROL")
def add_expectation(ticker):
    require_control_view(); ctx = _ctx(ticker); metric = str(request.form.get("metric") or "").strip()
    if not metric:
        flash("Expectation metric is required.", "error"); return redirect(url_for("web.company_section", ticker=ticker.upper(), section="expectations"))
    row = Expectation(coverage_id=ctx["coverage"].id, metric=metric[:100], period_label=str(request.form.get("period_label") or "").strip()[:40],
                      market_value=dec(request.form.get("market_value")), internal_value=dec(request.form.get("internal_value")), unit=str(request.form.get("unit") or "").strip()[:32],
                      confidence=str(request.form.get("confidence") or "UNRATED").upper()[:24], notes=str(request.form.get("notes") or "").strip())
    db.session.add(row); audit("expectation.add", "coverage", ctx["coverage"].id, {"metric": row.metric, "period": row.period_label}); db.session.commit(); _queue_recalc(ctx); flash("Expectation row added. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="expectations"))


@bp.post("/company/<ticker>/valuation")
@role_required("CONTROL")
def save_valuation(ticker):
    require_control_view(); ctx = _ctx(ticker); model = ctx["model"]
    model.method = str(request.form.get("method") or model.method).strip().upper()[:48]; model.updated_by = g.user.id
    model.assumptions = dict(model.assumptions or {}) | {"notes": str(request.form.get("assumptions_notes") or "").strip()}
    scenarios = {s.name.upper(): s for s in model.scenarios}
    for name in ("BEAR", "BASE", "BULL"):
        row = scenarios.get(name)
        if row is None:
            row = ValuationScenario(model_id=model.id, name=name); db.session.add(row)
        row.equity_value_per_share = dec(request.form.get(f"{name.lower()}_value")); row.probability = dec(request.form.get(f"{name.lower()}_prob"), Decimal("0")); row.confidence = str(request.form.get(f"{name.lower()}_confidence") or "UNRATED").upper()[:24]
        row.inputs = {"manual_value": float(row.equity_value_per_share) if row.equity_value_per_share is not None else None}; row.calculated_at = utcnow()
    ctx["research"].valuation_notes = str(request.form.get("valuation_notes") or ctx["research"].valuation_notes).strip()
    audit("valuation.save", "coverage", ctx["coverage"].id, {"method": model.method}); db.session.commit(); _queue_recalc(ctx)
    flash("Valuation assumptions and scenarios saved. Recalculation queued.", "success"); return redirect(url_for("web.company_section", ticker=ticker.upper(), section="valuation"))


@bp.post("/company/<ticker>/bear-item")
@role_required("CONTROL")
def add_bear_item(ticker):
    require_control_view(); ctx = _ctx(ticker); title = str(request.form.get("title") or "").strip()
    if not title:
        flash("Bear-case item needs a title.", "error"); return redirect(url_for("web.company_section", ticker=ticker, section="bear-case"))
    db.session.add(BearCaseItem(coverage_id=ctx["coverage"].id, title=title, evidence=str(request.form.get("evidence") or "").strip(), probability=dec(request.form.get("probability")), severity=str(request.form.get("severity") or "MEDIUM").upper()[:24], invalidates=request.form.get("invalidates") == "1"))
    audit("bear_case.add", "coverage", ctx["coverage"].id, {"title": title}); db.session.commit(); _queue_recalc(ctx); flash("Bear-case item added. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="bear-case"))


@bp.post("/company/<ticker>/catalyst")
@role_required("CONTROL")
def add_catalyst(ticker):
    require_control_view(); ctx = _ctx(ticker); title = str(request.form.get("title") or "").strip()
    if not title:
        flash("Catalyst needs a title.", "error"); return redirect(url_for("web.company_section", ticker=ticker, section="catalysts"))
    db.session.add(Catalyst(coverage_id=ctx["coverage"].id, title=title, catalyst_type=str(request.form.get("catalyst_type") or "OTHER").upper()[:48], expected_date=parse_date(request.form.get("expected_date")), direction=str(request.form.get("direction") or "MIXED").upper()[:16], evidence=str(request.form.get("evidence") or "").strip()))
    audit("catalyst.add", "coverage", ctx["coverage"].id, {"title": title}); db.session.commit(); _queue_recalc(ctx); flash("Catalyst added. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="catalysts"))


@bp.post("/company/<ticker>/management")
@role_required("CONTROL")
def add_management(ticker):
    require_control_view(); ctx = _ctx(ticker); as_of = parse_date(request.form.get("as_of")) or date.today()
    row = ManagementAssessment(coverage_id=ctx["coverage"].id, as_of=as_of, capital_allocation=str(request.form.get("capital_allocation") or "").strip(), execution=str(request.form.get("execution") or "").strip(), incentives=str(request.form.get("incentives") or "").strip(), communication=str(request.form.get("communication") or "").strip(), red_flags=str(request.form.get("red_flags") or "").strip(), notes=str(request.form.get("notes") or "").strip())
    db.session.add(row); audit("management.add", "coverage", ctx["coverage"].id, {"as_of": as_of.isoformat()}); db.session.commit(); _queue_recalc(ctx); flash("Management assessment saved. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="management"))


@bp.post("/company/<ticker>/management/promise")
@role_required("CONTROL")
def add_management_promise(ticker):
    require_control_view()
    ctx = _ctx(ticker)
    metric = str(request.form.get("metric") or "").strip()
    target_year = int(dec(request.form.get("target_year"), 0) or 0)
    low = dec(request.form.get("low"))
    high = dec(request.form.get("high"))
    unit = str(request.form.get("unit") or "").strip()[:16]
    statement = str(request.form.get("statement") or "").strip()
    allowed = {"revenue", "revenue_growth_pct", "gross_margin_pct", "operating_margin_pct", "net_margin_pct", "fcf"}
    if metric not in allowed or target_year < 2000 or low is None:
        flash("Promise needs a supported metric, target fiscal year and numeric target.", "error")
        return redirect(url_for("web.company_section", ticker=ticker.upper(), section="management"))
    high = high if high is not None else low
    add_manual_promise(
        ctx["company"].id,
        metric=metric,
        target_year=target_year,
        low=float(low),
        high=float(high),
        unit=unit or ("%" if metric.endswith("_pct") else "USD"),
        statement=statement or f"Manual management target: {metric} for FY{target_year}.",
    )
    audit("management.promise.add", "company", ctx["company"].id, {"metric": metric, "target_year": target_year})
    db.session.commit()
    _queue_recalc(ctx)
    flash("Management promise added and will be scored against filed actuals.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="management"))


@bp.post("/company/<ticker>/tape/borrow-fee")
@role_required("CONTROL")
def add_borrow_fee(ticker):
    require_control_view()
    ctx = _ctx(ticker)
    fee = dec(request.form.get("annualized_fee_pct"))
    source = str(request.form.get("source") or "").strip()
    note = str(request.form.get("note") or "").strip()
    if fee is None or fee < 0 or not source:
        flash("Borrow fee needs a non-negative annualized % and a source.", "error")
        return redirect(url_for("web.company_section", ticker=ticker.upper(), section="tape"))
    db.session.add(Event(
        company_id=ctx["company"].id,
        event_type="BORROW_FEE_OBSERVATION",
        title=f"{ctx['security'].ticker} borrow fee observation",
        event_date=utcnow(),
        payload={"annualized_fee_pct": float(fee), "source": source[:180], "note": note[:1000], "actor_user_id": g.user.id},
    ))
    audit("tape.borrow_fee.add", "company", ctx["company"].id, {"ticker": ctx["security"].ticker, "annualized_fee_pct": float(fee), "source": source[:180]})
    db.session.commit()
    _queue_recalc(ctx)
    flash("Borrow fee observation saved. Tape cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="tape"))


@bp.post("/company/<ticker>/monitoring")
@role_required("CONTROL")
def add_monitoring(ticker):
    require_control_view(); ctx = _ctx(ticker); name = str(request.form.get("name") or "").strip()
    if not name:
        flash("Monitoring rule needs a name.", "error"); return redirect(url_for("web.company_section", ticker=ticker, section="monitoring"))
    active = ctx["investment"].state not in {"NO_POSITION", "WATCHLIST"} or bool(ctx["position"] and ctx["position"].shares != 0); lock = request.form.get("locked_pre_investment") == "1"
    if lock and active:
        flash("Numeric invalidation thresholds must be locked before the investment state becomes active.", "error"); return redirect(url_for("web.company_section", ticker=ticker, section="monitoring"))
    rule = MonitoringRule(coverage_id=ctx["coverage"].id, name=name, metric=str(request.form.get("metric") or "").strip(), operator=str(request.form.get("operator") or "NOTE").upper()[:12], threshold_value=dec(request.form.get("threshold_value")), threshold_text=str(request.form.get("threshold_text") or "").strip(), unit=str(request.form.get("unit") or "").strip()[:32], severity=str(request.form.get("severity") or "WATCH").upper()[:24], locked_pre_investment=lock, created_by=g.user.id)
    db.session.add(rule); audit("monitoring.add", "coverage", ctx["coverage"].id, {"name": name, "locked": lock}); db.session.commit(); _queue_recalc(ctx); flash("Monitoring rule added. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))


@bp.post("/company/<ticker>/monitoring/<int:rule_id>")
@role_required("CONTROL")
def update_monitoring(ticker, rule_id):
    require_control_view(); ctx = _ctx(ticker); rule = db.session.get(MonitoringRule, rule_id)
    if not rule or rule.coverage_id != ctx["coverage"].id: abort(404)
    new_value = dec(request.form.get("threshold_value")); new_text = str(request.form.get("threshold_text") or rule.threshold_text).strip()
    if rule.locked_pre_investment and (new_value != rule.threshold_value or new_text != rule.threshold_text):
        flash("This pre-investment invalidation threshold is locked and cannot be changed retroactively.", "error"); return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))
    status = str(request.form.get("status") or "WATCH").upper()[:24]; db.session.add(MonitoringHistory(rule_id=rule.id, observed_value=dec(request.form.get("observed_value")), status=status, note=str(request.form.get("note") or "").strip()))
    if request.form.get("archive") == "1" and not rule.locked_pre_investment: rule.is_active = False
    audit("monitoring.update", "monitoring_rule", rule.id, {"status": status}); db.session.commit(); _queue_recalc(ctx); flash("Monitoring observation recorded. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))


@bp.post("/company/<ticker>/thesis-invalidation")
@role_required("CONTROL")
def save_thesis_invalidation(ticker):
    """Research invalidation belongs to the thesis, not to portfolio sizing."""
    require_control_view()
    ctx = _ctx(ticker)
    risk = ctx["risk"]
    proposed = str(request.form.get("thesis_invalidation") or "").strip()
    if risk.invalidation_locked_at and proposed != risk.thesis_invalidation:
        flash("The locked thesis invalidation cannot be changed retroactively. Record a new thesis instead.", "error")
        return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))
    risk.thesis_invalidation = proposed
    if request.form.get("lock_invalidation") == "1" and not risk.invalidation_locked_at:
        if not proposed:
            flash("Enter thesis invalidation before locking it.", "error")
            return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))
        risk.invalidation_locked_at = utcnow()
    risk.updated_by = g.user.id
    ctx["research"].risk_summary = proposed
    ctx["research"].updated_by = g.user.id
    _research_version(ctx["coverage"], ctx["research"], "Updated thesis invalidation")
    audit("research.invalidation.save", "coverage", ctx["coverage"].id, {"locked": bool(risk.invalidation_locked_at)})
    db.session.commit()
    _queue_recalc(ctx)
    flash("Thesis invalidation saved. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="monitoring"))


@bp.post("/portfolio/<ticker>/risk")
@role_required("CONTROL")
def save_risk(ticker):
    """Private monetary risk belongs to Portfolio and never changes the research thesis."""
    require_control_view()
    ctx = _ctx(ticker)
    risk = ctx["risk"]
    risk.max_loss_pct = dec(request.form.get("max_loss_pct"))
    risk.max_position_pct = dec(request.form.get("max_position_pct"))
    risk.entry_conditions = str(request.form.get("entry_conditions") or "").strip()
    risk.add_conditions = str(request.form.get("add_conditions") or "").strip()
    risk.trim_conditions = str(request.form.get("trim_conditions") or "").strip()
    risk.exit_conditions = str(request.form.get("exit_conditions") or "").strip()
    risk.notes = str(request.form.get("notes") or "").strip()
    risk.updated_by = g.user.id
    audit("portfolio.risk.save", "coverage", ctx["coverage"].id, {"max_loss_pct": float(risk.max_loss_pct) if risk.max_loss_pct is not None else None, "max_position_pct": float(risk.max_position_pct) if risk.max_position_pct is not None else None})
    db.session.commit()
    flash("Portfolio risk plan saved.", "success")
    return redirect(url_for("web.portfolio_security", ticker=ticker.upper()))


@bp.post("/portfolio/<ticker>/position")
@role_required("CONTROL")
def save_position(ticker):
    require_control_view()
    ctx = _ctx(ticker)
    shares = dec(request.form.get("shares"))
    avg_cost = dec(request.form.get("avg_cost"))
    if shares is None or avg_cost is None or avg_cost < 0:
        flash("Enter valid shares and average cost.", "error")
        return redirect(url_for("web.portfolio_security", ticker=ticker.upper()))
    position = ctx["position"] or Position(user_id=g.user.id, security_id=ctx["security"].id)
    if position.id is None:
        db.session.add(position)
    position.shares = shares
    position.avg_cost = avg_cost
    position.currency = ctx["security"].currency
    position.notes = str(request.form.get("notes") or "").strip()
    state = str(request.form.get("investment_state") or ctx["investment"].state).upper()
    if state in {"NO_POSITION", "WATCHLIST", "EXISTING_LONG", "EXISTING_SHORT", "LONG_UNDER_REVIEW", "SHORT_UNDER_REVIEW", "EXITING"}:
        ctx["investment"].state = state
    ctx["investment"].action = str(request.form.get("action") or ctx["investment"].action).upper()[:80]
    ctx["investment"].review_reason = str(request.form.get("review_reason") or "").strip()
    ctx["investment"].updated_by = g.user.id
    audit("portfolio.position.save", "coverage", ctx["coverage"].id, {"shares": float(shares), "investment_state": ctx["investment"].state})
    db.session.commit()
    flash("Position saved.", "success")
    return redirect(url_for("web.portfolio_security", ticker=ticker.upper()))


@bp.post("/company/<ticker>/journal")
@role_required("CONTROL")
def add_journal(ticker):
    require_control_view(); ctx = _ctx(ticker); decision = str(request.form.get("decision") or ctx["investment"].action or "REVIEW").strip()[:100]
    evidence_for = str(request.form.get("evidence_for") or "").strip(); evidence_against = str(request.form.get("evidence_against") or "").strip(); bias_notes = str(request.form.get("bias_notes") or "").strip(); outcome = str(request.form.get("outcome") or "").strip(); post_mortem = str(request.form.get("post_mortem") or "").strip(); lessons = str(request.form.get("lessons") or "").strip()
    snap = create_snapshot(ctx["coverage"], g.user.id, snapshot_type="DECISION", decision_context={"decision": decision, "evidence_for": evidence_for, "evidence_against": evidence_against, "bias_notes": bias_notes, "outcome": outcome, "post_mortem": post_mortem, "lessons": lessons})
    db.session.add(DecisionJournal(coverage_id=ctx["coverage"].id, user_id=g.user.id, decision=decision, research_state=ctx["coverage"].research_state, investment_state=ctx["investment"].state, thesis_snapshot={"snapshot_id": snap.id, "thesis": ctx["research"].thesis, "outcome": outcome, "post_mortem": post_mortem, "lessons": lessons}, risk_snapshot={"invalidation": ctx["risk"].thesis_invalidation, "locked_at": ctx["risk"].invalidation_locked_at.isoformat() if ctx["risk"].invalidation_locked_at else None}, valuation_snapshot=ctx["valuation"], evidence_for=evidence_for, evidence_against=evidence_against, bias_notes=bias_notes))
    audit("journal.create", "coverage", ctx["coverage"].id, {"snapshot_id": snap.id}); db.session.commit(); _queue_recalc(ctx); flash("Decision Journal entry and snapshot saved. Research cache queued for update.", "success")
    return redirect(url_for("web.company_section", ticker=ticker.upper(), section="journal"))
