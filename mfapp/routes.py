from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from .access import audit, effective_role, require_control_view
from .extensions import db
from .jobs import enqueue_job
from .data_providers import latest_snapshot, provider_status
from .models import AuditEvent, Invite, User
from .core_models import (
    Alert, BearCaseItem, Catalyst, Company, Coverage, DataQualityIssue, DecisionJournal,
    Event, Expectation, FinancialFlow, FinancialPeriod, InvestmentState, Job,
    ManagementAssessment, MonitoringHistory, MonitoringRule, Position, Provenance,
    Publication, RefreshRun, ResearchState, ResearchVersion, RiskPlan, Security,
    Snapshot, Source, ValuationModel,
)
from .security import login_required, role_required
from .services import can_view_publication, coverage_for_ticker, ensure_workspace, financial_rows, readiness, valuation_result
from .symbols import validate_ticker

bp = Blueprint("web", __name__)

SECTIONS = [
    ("overview", "Overview"), ("business", "Business"), ("numbers", "Numbers"),
    ("expectations", "Expectations"), ("valuation", "Valuation"), ("bear-case", "Bear Case"),
    ("catalysts", "Catalysts"), ("financial-flows", "Financial Flows"),
    ("management", "Management"), ("tape", "Tape / Flows"), ("monitoring", "Monitoring"),
    ("risk", "Risk"), ("position", "Position"), ("journal", "Decision Journal"), ("audit", "Sources / Audit"),
]
SECTION_KEYS = {key for key, _ in SECTIONS}
RESEARCH_FIELDS = {
    "business": "business", "numbers": "numbers", "expectations": "expectations",
    "bear-case": "bear_case_summary", "catalysts": "catalysts_summary",
    "management": "management_summary", "tape": "tape_summary", "financial-flows": "flows_summary",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dec(value, default=None):
    if value in (None, ""): return default
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError): return default


def parse_date(value: str):
    try: return date.fromisoformat(str(value or "")[:10])
    except Exception: return None


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:140] or "research"


def valuation_sensitivity(base_value, current_price):
    try: base = float(base_value)
    except (TypeError, ValueError): return []
    try: price = float(current_price)
    except (TypeError, ValueError): price = None
    out = []
    for shock in (-0.20, -0.10, 0.0, 0.10, 0.20):
        value = base * (1 + shock)
        gap = (value / price - 1) * 100 if price not in (None, 0) else None
        out.append({"shock_pct": shock * 100, "value": value, "gap_pct": gap})
    return out


def _coverage(ticker: str) -> Coverage:
    row = coverage_for_ticker(g.user.id, ticker)
    if row is None: abort(404)
    return row


def _ctx(ticker: str) -> dict:
    coverage = _coverage(ticker)
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id)
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    investment = InvestmentState.query.filter_by(coverage_id=coverage.id).first()
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    if not all((research, risk, investment, model)):
        raise RuntimeError(f"Coverage workspace incomplete for coverage_id={coverage.id}; run migration/repair before serving it")
    market = latest_snapshot(security.id)
    position = Position.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    return {"coverage": coverage, "security": security, "company": company, "research": research, "risk": risk,
            "investment": investment, "model": model, "market": market, "position": position,
            "valuation": valuation_result(coverage), "readiness": readiness(coverage), "company_sections": SECTIONS}


def _research_version(coverage: Coverage, research: ResearchState, reason: str) -> None:
    version = (db.session.query(db.func.max(ResearchVersion.version)).filter(ResearchVersion.coverage_id == coverage.id).scalar() or 0) + 1
    payload = {column.name: getattr(research, column.name) for column in research.__table__.columns if column.name not in {"id", "coverage_id", "updated_at"}}
    for key, value in list(payload.items()):
        if isinstance(value, datetime): payload[key] = value.isoformat()
    db.session.add(ResearchVersion(coverage_id=coverage.id, version=version, payload=payload, reason=reason[:160], created_by=g.user.id))


def _published_for_role(role: str):
    rows = Publication.query.filter(Publication.revoked_at.is_(None)).order_by(Publication.published_at.desc()).all()
    return [row for row in rows if can_view_publication(row, role)]


@bp.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0", "architecture": "web-native", "database": "primary"}


@bp.get("/")
@login_required
def dashboard():
    role = effective_role()
    if role != "CONTROL":
        return render_template("published_index.html", publications=_published_for_role(role), role=role)
    require_control_view()
    rows = []
    for coverage in Coverage.query.filter_by(user_id=g.user.id).order_by(Coverage.priority.desc(), Coverage.updated_at.desc()).all():
        security = db.session.get(Security, coverage.security_id); company = db.session.get(Company, security.company_id)
        rows.append({"coverage": coverage, "security": security, "company": company, "market": latest_snapshot(security.id),
                     "investment": InvestmentState.query.filter_by(coverage_id=coverage.id).first(),
                     "valuation": valuation_result(coverage), "readiness": readiness(coverage)})
    queued = Job.query.filter(Job.status.in_(["QUEUED", "RUNNING"])).count()
    alerts = Alert.query.filter_by(user_id=g.user.id, is_read=False).order_by(Alert.created_at.desc()).limit(8).all()
    return render_template("dashboard.html", rows=rows, queued_jobs=queued, alerts=alerts)


@bp.get("/discovery")
@login_required
def discovery():
    require_control_view(); q = str(request.args.get("q") or "").strip().upper()
    query = Coverage.query.join(Security, Coverage.security_id == Security.id).filter(Coverage.user_id == g.user.id)
    if q: query = query.filter(or_(db.func.upper(Security.ticker).like(f"%{q}%"), db.func.upper(Coverage.owner_summary).like(f"%{q}%")))
    rows = []
    for coverage in query.order_by(Coverage.updated_at.desc()).all():
        security = db.session.get(Security, coverage.security_id); company = db.session.get(Company, security.company_id)
        rows.append({"coverage": coverage, "security": security, "company": company, "market": latest_snapshot(security.id), "readiness": readiness(coverage)})
    return render_template("discovery.html", rows=rows, q=q)


@bp.post("/coverage")
@role_required("CONTROL")
def add_coverage():
    require_control_view(); ticker = str(request.form.get("ticker") or "").strip().upper(); validation = validate_ticker(ticker)
    if not validation.valid:
        flash(validation.message or "Ticker not found / symbol not recognized.", "error"); return redirect(request.referrer or url_for("web.dashboard"))
    security = Security.query.filter(db.func.upper(Security.ticker) == ticker, Security.active.is_(True)).order_by(Security.is_primary.desc()).first()
    if security:
        existing = Coverage.query.filter_by(user_id=g.user.id, security_id=security.id).first()
        if existing:
            flash("Ticker already exists in Coverage.", "error"); return redirect(url_for("web.company_section", ticker=ticker, section="overview"))
    else:
        company = Company(legal_name=validation.name or ticker, display_name=validation.name or ticker); db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker=ticker, exchange=validation.exchange, security_type=validation.instrument_type or "COMMON_STOCK",
                            currency=validation.currency or "USD", provider_symbol=ticker.replace(".", "-"), validation_source=validation.source, validated_at=utcnow())
        db.session.add(security); db.session.flush()
    coverage = Coverage(user_id=g.user.id, security_id=security.id, status="MONITOR", research_state="UNRATED")
    db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, g.user.id)
    audit("coverage.create", "coverage", coverage.id, {"ticker": ticker, "validation_source": validation.source}); db.session.commit()
    enqueue_job("MARKET_REFRESH", user_id=g.user.id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=20)
    if provider_status(g.user.id).get("sec"):
        enqueue_job("SEC_INGEST", user_id=g.user.id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=40)
    flash(f"{ticker} added to Coverage. Refresh jobs queued.", "success")
    return redirect(url_for("web.company_section", ticker=ticker, section="overview"))


@bp.get("/company/<ticker>")
@login_required
def company_default(ticker):
    require_control_view(); return redirect(url_for("web.company_section", ticker=ticker.upper(), section="overview"))


@bp.get("/company/<ticker>/<section>")
@login_required
def company_section(ticker, section):
    require_control_view()
    if section not in SECTION_KEYS: abort(404)
    ctx = _ctx(ticker); company = ctx["company"]; coverage = ctx["coverage"]; extra = {}
    if section == "expectations": extra["expectation_rows"] = Expectation.query.filter_by(coverage_id=coverage.id).order_by(Expectation.period_label, Expectation.metric).all()
    elif section == "numbers":
        extra["financials"] = financial_rows(company.id, 10); extra["quality_issues"] = DataQualityIssue.query.filter_by(company_id=company.id, status="OPEN").order_by(DataQualityIssue.detected_at.desc()).all()
    elif section == "valuation":
        extra["scenarios"] = {s.name.upper(): s for s in ctx["model"].scenarios}; extra["sensitivity"] = valuation_sensitivity(ctx["valuation"].get("base"), ctx["valuation"].get("current_price"))
    elif section == "bear-case": extra["bear_items"] = BearCaseItem.query.filter_by(coverage_id=coverage.id).order_by(BearCaseItem.created_at.desc()).all()
    elif section == "catalysts": extra["catalyst_rows"] = Catalyst.query.filter_by(coverage_id=coverage.id).order_by(Catalyst.expected_date.asc(), Catalyst.id.desc()).all()
    elif section == "financial-flows":
        periods = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY").order_by(FinancialPeriod.fiscal_year.desc()).all(); year = int(request.args.get("year") or (periods[0].fiscal_year if periods else 0)); period = next((p for p in periods if p.fiscal_year == year), None); flows = {}
        if period:
            for row in FinancialFlow.query.filter_by(financial_period_id=period.id, calculation_version="0.1.0").all(): flows[row.flow_type] = row.payload
        extra.update({"periods": periods, "selected_year": year, "flows": flows})
    elif section == "management": extra["management_rows"] = ManagementAssessment.query.filter_by(coverage_id=coverage.id).order_by(ManagementAssessment.as_of.desc()).all()
    elif section == "tape": extra["tape_events"] = Event.query.filter_by(company_id=company.id).order_by(Event.event_date.desc()).limit(20).all()
    elif section == "monitoring":
        rules = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(MonitoringRule.updated_at.desc()).all(); histories = {r.id: MonitoringHistory.query.filter_by(rule_id=r.id).order_by(MonitoringHistory.observed_at.desc()).limit(5).all() for r in rules}; extra.update({"monitor_rules": rules, "monitor_histories": histories})
    elif section == "journal":
        extra["journal_rows"] = DecisionJournal.query.filter_by(coverage_id=coverage.id, user_id=g.user.id).order_by(DecisionJournal.created_at.desc()).all(); extra["snapshots"] = Snapshot.query.filter_by(coverage_id=coverage.id).order_by(Snapshot.created_at.desc()).limit(20).all()
    elif section == "audit":
        extra["sources"] = Source.query.filter_by(company_id=company.id).order_by(Source.retrieved_at.desc()).all(); extra["provenance"] = Provenance.query.join(FinancialPeriod, Provenance.financial_period_id == FinancialPeriod.id).filter(FinancialPeriod.company_id == company.id).order_by(Provenance.created_at.desc()).limit(200).all(); extra["refresh_runs"] = RefreshRun.query.filter(or_(RefreshRun.company_id == company.id, RefreshRun.security_id == ctx["security"].id)).order_by(RefreshRun.started_at.desc()).limit(50).all()
    return render_template("company_section.html", section=section, **ctx, **extra)


# Register mutation/publication routes onto this blueprint after shared helpers exist.
from . import routes_edit as _routes_edit  # noqa: E402,F401
from . import routes_publish as _routes_publish  # noqa: E402,F401
