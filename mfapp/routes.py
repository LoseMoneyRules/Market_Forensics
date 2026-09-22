from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import or_

from .access import audit, effective_role, require_control_view
from .current_financials import annual_history_grid, annual_rows, canonical_annual_pairs, current_row, forecast_rows, history_with_current, numbers_completeness, quarterly_rows, scenario_forecasts
from .decision_support import company_brief, journal_prefill, management_accountability, management_engine, monitoring_plan, tape_context_metrics, tape_series
from .management_promises import evaluate_promises
from .extensions import db
from .finra import stored_summary as finra_stored_summary
from .jobs import enqueue_job
from .data_providers import latest_snapshot, provider_status
from .models import AuditEvent, Invite, User
from .core_models import (
    Alert, BearCaseItem, Catalyst, Company, Coverage, DataQualityIssue, DecisionJournal, DecisionOutcome,
    Event, Expectation, FinancialFlow, FinancialPeriod, InvestmentState, Job,
    ManagementAssessment, MarketSnapshot, MonitoringHistory, MonitoringRule, Position, Provenance,
    Publication, RefreshRun, ResearchState, ResearchVersion, RiskPlan, Security,
    Snapshot, Source, ValuationModel,
)
from .readiness import research_readiness
from .decision_engine import build_research_intelligence
from .decision_lenses import build_decision_lenses
from .expectations_engine import price_implied_expectations
from .discovery_engine import classify_coverage, search_universe
from .research_synthesis import build_synthesis
from .research_cache import cache_is_stale, latest_research_cache, latest_cache_map
from .triangulation_engine import automatic_triangulation
from .security import login_required, role_required
from .services import can_view_publication, coverage_for_ticker, ensure_security_from_validation, ensure_workspace, valuation_result
from .secdata import SEC_NORMALIZER_VERSION
from .symbols import validate_ticker
from .valuation_engine import infer_company_type, stored_model_base_quality, valuation_base_quality, valuation_is_decision_grade

bp = Blueprint("web", __name__)

SECTIONS = [
    ("overview", "Overview"), ("business", "Business"), ("fundamentals", "Fundamentals"),
    ("expectations", "Expectations"), ("valuation", "Valuation"), ("bear-case", "Bear Case"),
    ("catalysts", "Catalysts"), ("financial-flows", "Financial Flows"),
    ("management", "Management"), ("tape", "Tape / Flows"), ("monitoring", "Monitoring"),
    ("journal", "Decision Journal"), ("audit", "Sources / Audit"), ("validate", "Validate"),
]
SECTION_KEYS = {key for key, _ in SECTIONS}
RESEARCH_FIELDS = {
    "business": "business", "fundamentals": "numbers", "expectations": "expectations",
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


def _research_readiness(coverage: Coverage) -> dict:
    return research_readiness(coverage)


def _safe_research_readiness(coverage: Coverage) -> dict:
    """Keep stored evidence readable when a derived Research-control read fails.

    Normal GET navigation is a data-room read. A readiness/control exception must
    not hide filed Fundamentals, Expectations, Financial Flows, Sources or other
    stored evidence. The decision layer fails closed instead: no gate is treated
    as approved, validation/publication remain blocked, and the page surfaces a
    degraded-control banner. Mutation routes continue to use research_readiness()
    directly so CONTROL actions never proceed on a degraded state.
    """
    try:
        return research_readiness(coverage)
    except Exception as exc:
        current_app.logger.exception(
            "Research readiness unavailable on GET for coverage_id=%s",
            coverage.id,
        )
        fallback = _fallback_readiness()
        fallback.update({
            "degraded": True,
            "degraded_reason": type(exc).__name__,
            "review_required": True,
            "review_required_count": 0,
            "reopened_gates": [],
            "financial_review_required": False,
            "financial_review_required_count": 0,
            "thesis_review_required": False,
            "thesis_review_required_count": 0,
            "financial_basis": {"available": False},
            "review_banner": "RESEARCH CONTROL UNAVAILABLE — STORED EVIDENCE REMAINS VISIBLE",
        })
        return fallback


def _intelligence(coverage: Coverage, company: Company, model: ValuationModel, market, valuation: dict, readiness: dict | None = None) -> dict:
    rows = list(reversed(history_with_current(company.id, 8)))
    issues = DataQualityIssue.query.filter_by(company_id=company.id, status="OPEN").count()
    latest_engine = dict((model.assumptions or {}).get("latest_engine_result") or {})
    return build_research_intelligence(
        rows,
        valuation,
        market_price=(market.price if market else None),
        valuation_quality=str(latest_engine.get("quality") or ""),
        data_quality_issues=issues,
        finra_summary=finra_stored_summary(company.id),
        readiness=readiness or _research_readiness(coverage),
    )


def _fallback_readiness() -> dict:
    return {
        "done": 0, "evidence_ready": 0, "total": 13, "gates": [],
        "ready_to_validate": False,
        "validation": {"state": "NOT RUN", "run_id": None, "status": "NOT RUN", "samples": 0, "reliability": None},
        "bias_flags": [],
    }


def _fallback_intelligence(readiness: dict | None = None, *, updating: bool = True) -> dict:
    readiness = readiness or _fallback_readiness()
    message = "Research cache is updating." if updating else "Research cache is not available."
    return {
        "action": "WAIT",
        "stance": "DATA REVIEW",
        "bias": "NEUTRAL",
        "confidence": "LOW",
        "score": 0.0,
        "positives": 0,
        "negatives": 0,
        "warnings": [message],
        "blockers": [message],
        "signals": [],
        "top_signals": [],
        "supporting_evidence": [],
        "opposing_evidence": [],
        "base_gap_pct": None,
        "validation_state": (readiness.get("validation") or {}).get("state", "NOT RUN"),
        "buy_threshold": 2.5,
        "sell_threshold": -2.5,
    }


def _fallback_lenses(valuation: dict, cache_pending: bool) -> dict:
    price = valuation.get("current_price")
    base = valuation.get("base")
    try:
        gap = ((float(base) / float(price) - 1.0) * 100.0) if base is not None and price not in (None, 0) else None
    except (TypeError, ValueError, ZeroDivisionError):
        gap = None
    decision_grade = valuation_is_decision_grade(valuation)
    value = "UNVERIFIED" if gap is None or not decision_grade else ("ATTRACTIVE" if gap >= 20 else "EXPENSIVE" if gap <= -15 else "FAIR")
    return {
        "rows": [],
        "business": "CALCULATING" if cache_pending else "UNPROVEN",
        "value": value,
        "expectations": "CALCULATING" if cache_pending else "UNAVAILABLE",
        "variant": "CALCULATING" if cache_pending else "UNPROVEN",
        "path": "CALCULATING" if cache_pending else "UNCLEAR",
        "model_confidence": "CALCULATING" if cache_pending else "UNVALIDATED",
        "thesis_control": "CALCULATING" if cache_pending else "UNRESOLVED",
        "research_conclusion": "UPDATING" if cache_pending else "DATA REVIEW",
        "implied_expectations": {"available": False, "classification": "CALCULATING" if cache_pending else "UNAVAILABLE", "drivers": []},
    }


def _fail_closed_cached_research(
    valuation: dict,
    readiness: dict,
    intelligence: dict,
    lenses: dict,
) -> tuple[dict, dict]:
    """Make old/materialized payloads obey current valuation-quality policy on read."""
    if valuation_is_decision_grade(valuation):
        return intelligence, lenses

    quality = valuation_base_quality(valuation)
    intelligence = dict(intelligence or {})
    intelligence["action"] = "WAIT"
    intelligence["stance"] = "DATA REVIEW"
    intelligence["valuation_base_quality"] = quality
    intelligence["valuation_decision_grade"] = False
    warnings = list(intelligence.get("warnings") or [])
    warning = f"Valuation quality is {quality.replace('_', ' ')}; target remains visible but cannot create an edge."
    if warning not in warnings:
        warnings.append(warning)
    intelligence["warnings"] = warnings

    lenses = dict(lenses or {})
    lenses["value"] = "UNVERIFIED"
    if str(lenses.get("variant") or "") in {"POSITIVE EDGE", "NEGATIVE EDGE", "POSSIBLE"}:
        lenses["variant"] = "DEFINED · UNPROVEN"
    lenses["valuation_base_quality"] = quality
    lenses["valuation_decision_grade"] = False
    lenses["research_conclusion"] = (
        "RESEARCH INCOMPLETE"
        if not readiness.get("ready_to_validate")
        else "DATA REVIEW"
    )
    rows = []
    for row in list(lenses.get("rows") or []):
        item = dict(row)
        if item.get("key") == "value":
            item["state"] = "UNVERIFIED"
        elif item.get("key") == "variant" and str(item.get("state") or "") in {"POSITIVE EDGE", "NEGATIVE EDGE", "POSSIBLE"}:
            item["state"] = "DEFINED · UNPROVEN"
        rows.append(item)
    lenses["rows"] = rows
    return intelligence, lenses


def _fail_closed_degraded_control(
    readiness: dict,
    intelligence: dict,
    lenses: dict,
) -> tuple[dict, dict]:
    """Never let a cached decision survive a degraded Research-control read."""
    if not readiness.get("degraded"):
        return intelligence, lenses

    intelligence = dict(intelligence or {})
    intelligence["action"] = "WAIT"
    intelligence["stance"] = "DATA REVIEW"
    intelligence["confidence"] = "LOW"
    warning = "Research control is unavailable; stored evidence remains visible but decisions are blocked."
    warnings = list(intelligence.get("warnings") or [])
    if warning not in warnings:
        warnings.insert(0, warning)
    intelligence["warnings"] = warnings
    blockers = list(intelligence.get("blockers") or [])
    if warning not in blockers:
        blockers.insert(0, warning)
    intelligence["blockers"] = blockers

    lenses = dict(lenses or {})
    lenses["research_conclusion"] = "DATA REVIEW"
    lenses["model_confidence"] = "UNVALIDATED"
    lenses["thesis_control"] = "CONTROL UNAVAILABLE"
    rows = []
    for row in list(lenses.get("rows") or []):
        item = dict(row)
        if item.get("key") == "model_confidence":
            item["state"] = "UNVALIDATED"
        elif item.get("key") == "thesis_control":
            item["state"] = "CONTROL UNAVAILABLE"
        rows.append(item)
    lenses["rows"] = rows
    return intelligence, lenses


def _fast_brief(valuation: dict, model: ValuationModel | None, lenses: dict) -> dict:
    price = valuation.get("current_price"); base = valuation.get("base")
    try:
        gap = ((float(base) / float(price) - 1.0) * 100.0) if base is not None and price not in (None, 0) else None
    except (TypeError, ValueError, ZeroDivisionError):
        gap = None
    horizon = int((model.assumptions or {}).get("horizon_years") or 5) if model else 5
    return {
        "price": price, "bear": valuation.get("bear"), "base": base, "bull": valuation.get("bull"),
        "base_gap_pct": gap, "confidence": lenses.get("model_confidence") or "UNVALIDATED",
        "horizon_years": horizon, "target_year": date.today().year + horizon, "reasons": [],
    }


def _ctx(ticker: str, *, queue_recalc: bool = True) -> dict:
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
    cache = latest_research_cache(coverage.id, company.id)
    stale_cache = cache_is_stale(cache, coverage, model)
    valuation = dict((cache or {}).get("valuation") or valuation_result(coverage))
    if stale_cache and cache:
        # Preserve the last visible Bear/Base/Bull while refusing to combine an
        # old target with newly-saved model state. The background recalculation
        # will replace this materialized snapshot.
        valuation["base_quality"] = "DATA_WARNING"
        valuation["quality"] = "DATA_WARNING"
        valuation["decision_grade"] = False
    elif not valuation.get("base_quality"):
        base_quality = stored_model_base_quality(model)
        valuation["base_quality"] = base_quality
        valuation["quality"] = valuation.get("quality") or base_quality
        valuation["decision_grade"] = valuation_is_decision_grade({"base_quality": base_quality})

    active_recalc = Job.query.filter(
        Job.user_id == g.user.id,
        Job.company_id == company.id,
        Job.job_type == "RECALCULATE",
        Job.status.in_(["QUEUED", "RUNNING"]),
    ).first()
    if stale_cache and active_recalc is None and queue_recalc:
        active_recalc = enqueue_job(
            "RECALCULATE",
            user_id=g.user.id,
            company_id=company.id,
            security_id=security.id,
            payload={"coverage_id": coverage.id},
            priority=95,
        )

    active_sec = Job.query.filter(
        Job.user_id == g.user.id,
        Job.company_id == company.id,
        Job.job_type == "SEC_INGEST",
        Job.status.in_(["QUEUED", "RUNNING"]),
    ).first()
    latest_sec_source = (
        Source.query
        .filter_by(company_id=company.id, provider="SEC", source_type="COMPANYFACTS")
        .order_by(Source.retrieved_at.desc(), Source.id.desc())
        .first()
    )
    stored_normalizer_version = str(((latest_sec_source.meta or {}).get("normalizer_version") if latest_sec_source else "") or "")
    core_financial_recovery_needed = stored_normalizer_version != SEC_NORMALIZER_VERSION
    if (
        core_financial_recovery_needed
        and active_sec is None
        and queue_recalc
        and provider_status(g.user.id).get("sec")
    ):
        # One-time non-destructive parser migration for existing Coverage. The
        # GET only queues work; SEC/network access remains in the background job.
        active_sec = enqueue_job(
            "SEC_INGEST",
            user_id=g.user.id,
            company_id=company.id,
            security_id=security.id,
            payload={"coverage_id": coverage.id, "normalizer_version": SEC_NORMALIZER_VERSION},
            priority=30,
        )
    economic_reclass_pending = active_sec is not None
    core_financial_recovery_pending = core_financial_recovery_needed and active_sec is not None

    cache_pending = active_recalc is not None or economic_reclass_pending
    # Readiness is intentionally live DB state. It is lightweight and user-edited;
    # serving a materialized copy made Monitoring / Journal and gate approvals look
    # stale until a heavy recalculation happened.
    readiness = _safe_research_readiness(coverage)
    intelligence = dict((cache or {}).get("intelligence") or _fallback_intelligence(readiness, updating=cache_pending))
    decision_lenses = dict((cache or {}).get("decision_lenses") or _fallback_lenses(valuation, cache_pending))
    intelligence, decision_lenses = _fail_closed_cached_research(valuation, readiness, intelligence, decision_lenses)
    intelligence, decision_lenses = _fail_closed_degraded_control(readiness, intelligence, decision_lenses)
    brief = dict((cache or {}).get("brief") or _fast_brief(valuation, model, decision_lenses))
    if readiness.get("degraded"):
        brief["confidence"] = "UNVALIDATED"

    return {
        "coverage": coverage, "security": security, "company": company, "research": research, "risk": risk,
        "investment": investment, "model": model, "market": market, "position": position,
        "valuation": valuation, "readiness": readiness, "company_sections": SECTIONS,
        "intelligence": intelligence, "decision_lenses": decision_lenses, "brief": brief,
        "research_cache": cache or {}, "cache_pending": cache_pending,
        "economic_reclass_pending": economic_reclass_pending,
        "core_financial_recovery_pending": core_financial_recovery_pending,
        "financial_normalizer_version": stored_normalizer_version,
    }

def _fallback_synthesis(ctx: dict) -> dict:
    pending = bool(ctx.get("cache_pending"))
    message = "Research cache is updating in the job queue." if pending else "No calculated synthesis is stored yet."
    return {
        "why_now": [message],
        "why_not_yet": [message],
        "what_changes": ["Complete the queued recalculation; the page will refresh automatically when it finishes."],
        "what_kills": [ctx["risk"].thesis_invalidation or "No explicit thesis-kill condition is locked yet."],
        "micro_for": [],
        "micro_against": [],
        "macro": [],
        "macro_for": [],
        "macro_against": [],
        "macro_watch": [],
        "macro_source": None,
        "macro_as_of": None,
        "invalidation": ctx["risk"].thesis_invalidation or "",
        "next": ["Wait for the current evidence job to finish." if pending else "Queue a recalculation."],
    }


def _materialized_tape_for_display(security: Security, cache: dict[str, Any]) -> dict:
    """Return the freshest DB-materialized Tape view without provider calls.

    Research cache remains the fast path. If stored Tape/FINRA evidence is newer
    than that cache (or the cache has no Tape payload), rebuild only the Tape
    surface from local database evidence so charts and Large/Whale do not render
    blank while a full RECALCULATE job is still queued.
    """
    cached = dict((cache or {}).get("tape") or {})
    generated_raw = (cache or {}).get("_generated_at")
    try:
        generated_at = datetime.fromisoformat(str(generated_raw)) if generated_raw else None
    except (TypeError, ValueError):
        generated_at = None

    latest_evidence = (
        Event.query
        .filter(
            Event.company_id == security.company_id,
            Event.event_type.in_((
                "ALPACA_POSITIONING",
                "FINRA_SHORT_VOLUME_SERIES",
                "FINRA_SHORT_INTEREST_SERIES",
                "FINRA_ATS_SERIES",
                "BORROW_FEE_OBSERVATION",
            )),
        )
        .order_by(Event.event_date.desc(), Event.id.desc())
        .first()
    )
    cache_current = bool(
        cached
        and (
            latest_evidence is None
            or generated_at is not None
            and latest_evidence.event_date is not None
            and latest_evidence.event_date <= generated_at
        )
    )
    if cache_current:
        return cached

    try:
        return tape_series(security, 12)
    except Exception:
        current_app.logger.exception("Tape DB-only display rebuild failed")
        return cached


def _cached_tape_for_months(tape: dict, months: int) -> dict:
    metric_defaults = {
        "return_1m_pct": None, "return_3m_pct": None, "volume_ratio_20d": None, "turnover_ratio_20d": None,
        "short_5d_pct": None, "short_20d_pct": None, "put_call_oi": None, "put_open_interest": None,
        "call_open_interest": None, "borrow_status": "unknown", "borrow_fee_pct": None, "borrow_fee_source": "",
        "borrow_fee_as_of": None, "shortable": None, "locate_price": None, "locate_available_qty": None,
        "institutional_flow": None, "absorption": None, "price_resilience": None, "long_demand": None, "bear_pressure": None,
        "battle_intensity": None, "net_tape": None, "rank_score": None, "rank": "—", "forensic_regime": "LOW DATA",
        "machine_read": "Evidence is incomplete; do not force a tape regime.", "data_confidence": 0,
        "flow_feed": "", "flow_feed_scope": "", "flow_source_status": "", "flow_method": "", "flow_confidence_pct": None,
        "flow_method_version": "", "flow_withheld_count": 0, "flow_sanity_status": "NO_DATA",
        "flow_coverage_status": "", "flow_observation_usable": False, "flow_decision_usable": False,
        "flow_sample_volume_pct": None, "flow_eligible_volume_pct": None, "flow_reference_volume": None,
        "large_threshold": None, "very_large_threshold": None, "whale_threshold": None,
        "large_share_pct": None, "whale_share_pct": None,
        "large_buy": None, "large_sell": None, "net_large": None, "very_large_buy": None, "very_large_sell": None,
        "net_very_large": None, "whale_buy": None, "whale_sell": None, "net_whale": None,
        "regime": "MIXED", "confidence": "LOW",
    }
    base = {
        "months": months, "market": [], "daily_market": [], "short_interest": [], "short_volume": [],
        "institutional_flow": [], "ats": [], "tape_daily": [], "what_changed": [], "what_would_change_regime": [],
        "positioning": {},
        "metrics": metric_defaults | dict((tape or {}).get("metrics") or {}),
    } | dict(tape or {})
    base["metrics"] = metric_defaults | dict((tape or {}).get("metrics") or {})
    base["metrics"].update(tape_context_metrics(base["metrics"]))
    if months >= 12:
        return base
    tape = base
    cutoff = date.today().replace(day=1)
    # Six calendar months is close enough for display slicing; calculations remain the cached 12M context.
    for _ in range(6):
        cutoff = (cutoff.replace(day=1) - timedelta(days=1)).replace(day=1)
    cutoff_iso = cutoff.isoformat()
    out = dict(tape)
    out["months"] = months
    for key in ("market", "daily_market", "short_interest", "short_volume", "institutional_flow", "ats", "tape_daily"):
        rows = list(tape.get(key) or [])
        date_key = "date"
        out[key] = [row for row in rows if str(row.get(date_key) or row.get("settlement_date") or row.get("trade_date") or row.get("week_start") or "") >= cutoff_iso]
    return out


def _research_version(
    coverage: Coverage,
    research: ResearchState,
    reason: str,
    *,
    version_meta: dict | None = None,
) -> None:
    version = (db.session.query(db.func.max(ResearchVersion.version)).filter(ResearchVersion.coverage_id == coverage.id).scalar() or 0) + 1
    payload = {column.name: getattr(research, column.name) for column in research.__table__.columns if column.name not in {"id", "coverage_id", "updated_at"}}
    for key, value in list(payload.items()):
        if isinstance(value, datetime): payload[key] = value.isoformat()

    # Thesis invalidation is versioned with the thesis. The live RiskPlan keeps
    # only the CURRENT thesis control; ResearchVersion preserves prior pairs.
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    payload["_thesis_control"] = {
        "invalidation": str(getattr(risk, "thesis_invalidation", "") or ""),
        "locked_at": (
            risk.invalidation_locked_at.isoformat()
            if risk and risk.invalidation_locked_at
            else None
        ),
        "locked": bool(risk and risk.invalidation_locked_at),
    }
    if version_meta:
        payload["_version_meta"] = dict(version_meta)

    db.session.add(ResearchVersion(
        coverage_id=coverage.id,
        version=version,
        payload=payload,
        reason=reason[:160],
        created_by=g.user.id,
    ))


def _published_for_role(role: str):
    rows = Publication.query.filter(Publication.revoked_at.is_(None)).order_by(Publication.published_at.desc()).all()
    return [row for row in rows if can_view_publication(row, role)]


@bp.get("/health")
def health():
    from .reporting import report_backend_status
    return {
        "status": "ok",
        "version": current_app.config["VERSION"],
        "architecture": "web-native",
        "database": "primary",
        "reports": report_backend_status()["backend"],
    }


def _cached_coverage_rows(user_id: int) -> tuple[list[dict], bool]:
    coverages = Coverage.query.filter(
        Coverage.user_id == user_id,
        Coverage.status != "ARCHIVED",
    ).order_by(Coverage.priority.desc(), Coverage.updated_at.desc()).all()
    if not coverages:
        return [], False

    security_ids = [row.security_id for row in coverages]
    securities = {row.id: row for row in Security.query.filter(Security.id.in_(security_ids)).all()}
    company_ids = [row.company_id for row in securities.values()]
    companies = {row.id: row for row in Company.query.filter(Company.id.in_(company_ids)).all()} if company_ids else {}

    latest_times = db.session.query(
        MarketSnapshot.security_id.label("security_id"),
        db.func.max(MarketSnapshot.as_of).label("max_as_of"),
    ).filter(MarketSnapshot.security_id.in_(security_ids)).group_by(MarketSnapshot.security_id).subquery()
    snapshot_rows = MarketSnapshot.query.join(
        latest_times,
        and_(
            MarketSnapshot.security_id == latest_times.c.security_id,
            MarketSnapshot.as_of == latest_times.c.max_as_of,
        ),
    ).order_by(MarketSnapshot.id.desc()).all()
    snapshots = {}
    for row in snapshot_rows:
        snapshots.setdefault(row.security_id, row)

    coverage_ids = [row.id for row in coverages]
    caches = latest_cache_map(coverage_ids)
    model_rows = ValuationModel.query.filter(
        ValuationModel.coverage_id.in_(coverage_ids),
        ValuationModel.is_active.is_(True),
    ).order_by(ValuationModel.id.desc()).all()
    models: dict[int, ValuationModel] = {}
    for model_row in model_rows:
        models.setdefault(model_row.coverage_id, model_row)

    missing = [row.id for row in coverages if row.id not in caches]
    if missing:
        active_prime = Job.query.filter(
            Job.user_id == user_id,
            Job.job_type == "CACHE_PRIME",
            Job.status.in_(["QUEUED", "RUNNING"]),
        ).first()
        if active_prime is None:
            enqueue_job("CACHE_PRIME", user_id=user_id, payload={}, priority=94)

    now = utcnow()
    rows = []
    for coverage in coverages:
        security = securities.get(coverage.security_id)
        if security is None:
            continue
        company = companies.get(security.company_id)
        cache = dict(caches.get(coverage.id) or {})
        market = snapshots.get(security.id)
        model = models.get(coverage.id)
        stale_cache = cache_is_stale(cache, coverage, model)
        valuation = dict(cache.get("valuation") or {
            "current_price": float(market.price) if market and market.price is not None else None,
            "bear": None, "base": None, "bull": None, "expected_value": None,
        })
        if stale_cache and cache:
            valuation["base_quality"] = "DATA_WARNING"
            valuation["quality"] = "DATA_WARNING"
            valuation["decision_grade"] = False
            enqueue_job(
                "RECALCULATE",
                user_id=user_id,
                company_id=company.id,
                security_id=security.id,
                payload={"coverage_id": coverage.id},
                priority=95,
            )
        elif not valuation.get("base_quality"):
            base_quality = stored_model_base_quality(model)
            valuation["base_quality"] = base_quality
            valuation["quality"] = valuation.get("quality") or base_quality
            valuation["decision_grade"] = valuation_is_decision_grade({"base_quality": base_quality})
        readiness = dict(cache.get("readiness") or _fallback_readiness())
        intelligence = dict(cache.get("intelligence") or _fallback_intelligence(readiness, updating=True))
        lenses = dict(cache.get("decision_lenses") or _fallback_lenses(valuation, True))
        intelligence, lenses = _fail_closed_cached_research(valuation, readiness, intelligence, lenses)
        discovery_labels = classify_coverage(intelligence, readiness)
        pending = [gate.get("label") for gate in readiness.get("gates", []) if not gate.get("approved")]
        validation_state = str((readiness.get("validation") or {}).get("state") or "NOT RUN")
        conclusion = str(lenses.get("research_conclusion") or "DATA REVIEW")
        if not cache:
            next_action = "Building research cache"
        elif pending:
            next_action = f"Complete / approve {pending[0]}"
        elif validation_state == "NOT RUN":
            next_action = "Validate study"
        elif validation_state in {"LIMITED", "REVIEW"}:
            next_action = "Review validation"
        elif conclusion in {"LONG READY", "SHORT READY"}:
            next_action = "Portfolio review"
        else:
            next_action = "Monitor evidence"
        freshness_hours = None
        if market and market.as_of:
            freshness_hours = max(0.0, (now - market.as_of).total_seconds() / 3600.0)
        rows.append({
            "coverage": coverage, "security": security, "company": company, "market": market,
            "valuation": valuation, "readiness": readiness, "intelligence": intelligence,
            "decision_lenses": lenses, "discovery_labels": discovery_labels,
            "next_action": next_action, "freshness_hours": freshness_hours,
        })
    rows.sort(key=lambda row: str(row["security"].ticker or "").upper())
    return rows, bool(missing)


@bp.get("/")
@login_required
def dashboard():
    role = effective_role()
    if role != "CONTROL":
        return render_template("published_index.html", publications=_published_for_role(role), role=role)
    require_control_view()
    rows, cache_building = _cached_coverage_rows(g.user.id)
    queued = Job.query.filter(Job.user_id == g.user.id, Job.status.in_(["QUEUED", "RUNNING"])).count()
    alerts = Alert.query.filter_by(user_id=g.user.id, is_read=False).order_by(Alert.created_at.desc()).limit(8).all()
    coverage_ids = [alert.coverage_id for alert in alerts if alert.coverage_id]
    ticker_by_coverage = {}
    if coverage_ids:
        mapped = (
            db.session.query(Coverage.id, Security.ticker)
            .join(Security, Coverage.security_id == Security.id)
            .filter(Coverage.id.in_(coverage_ids))
            .all()
        )
        ticker_by_coverage = {int(coverage_id): str(ticker).upper() for coverage_id, ticker in mapped}
    alert_items = [{"alert": alert, "ticker": ticker_by_coverage.get(alert.coverage_id)} for alert in alerts]
    return render_template("dashboard.html", rows=rows, queued_jobs=queued, alerts=alerts, alert_items=alert_items, cache_building=cache_building)


def _normalized_market_scan(job: Job | None) -> dict:
    def as_float(value, default=None):
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError, ArithmeticError):
            return default

    def as_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    raw_result = dict(job.result or {}) if job and isinstance(job.result, dict) else {}
    raw_scan = raw_result.get("market_scan")
    scan = dict(raw_scan) if isinstance(raw_scan, dict) else {}
    if scan and scan.get("contract_version") != "FULL_MARKET_MISPRICING_DISCOVERY_V5":
        return {
            "candidates": [], "long_candidates": [], "short_candidates": [], "watch_candidates": [],
            "candidate_count": 0, "long_count": 0, "short_count": 0, "watch_count": 0,
            "p1_count": 0, "p2_count": 0, "known_enriched": 0,
            "excluded_count": 0, "excluded_breakdown": {}, "guardrails": {},
            "errors": [], "stale_contract": True,
            "contract_version": scan.get("contract_version") or "LEGACY",
        }

    raw_candidates = scan.get("candidates")
    candidates = []
    if isinstance(raw_candidates, list):
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                continue
            ticker = str(raw.get("ticker") or "").strip().upper()
            side = str(raw.get("research_side") or raw.get("direction") or "").upper()
            if not ticker or side not in {"LONG", "SHORT"}:
                continue
            row = dict(raw)
            row.update({
                "ticker": ticker,
                "research_side": side,
                "direction": side,
                "move_pct": as_float(raw.get("move_pct")),
                "base_gap_pct": as_float(raw.get("base_gap_pct")),
                "bear": as_float(raw.get("bear")),
                "fair_value": as_float(raw.get("fair_value") if raw.get("fair_value") is not None else raw.get("base")),
                "base": as_float(raw.get("base") if raw.get("base") is not None else raw.get("fair_value")),
                "bull": as_float(raw.get("bull")),
                "valuation_methods": as_int(raw.get("valuation_methods"), 0),
                "forensic_score": as_int(raw.get("forensic_score"), 0),
                "forensic_signals": list(raw.get("forensic_signals") or []) if isinstance(raw.get("forensic_signals") or [], list) else [],
                "operating_confirmation": list(raw.get("operating_confirmation") or []) if isinstance(raw.get("operating_confirmation") or [], list) else [],
                "fair_value_quality": str(raw.get("fair_value_quality") or ""),
                "forensic_source": str(raw.get("forensic_source") or ""),
                "price": as_float(raw.get("price")),
                "dollar_volume": as_float(raw.get("dollar_volume")),
                "target_status": str(raw.get("target_status") or "TARGET UNKNOWN"),
                "radar_label": str(raw.get("radar_label") or "Valuation Dislocation"),
                "priority": str(raw.get("priority") or "P2"),
                "priority_rank": as_int(raw.get("priority_rank"), 2),
                "priority_reason": str(raw.get("priority_reason") or ""),
                "opportunity_tier": str(raw.get("opportunity_tier") or raw.get("priority") or "P2").upper(),
                "operating_state": str(raw.get("operating_state") or ""),
                "why_found": list(raw.get("why_found") or []) if isinstance(raw.get("why_found") or [], list) else [],
                "what_invalidates": str(raw.get("what_invalidates") or ""),
                "data_freshness": raw.get("data_freshness"),
                "market_freshness": raw.get("market_freshness"),
                "materialized_at": raw.get("materialized_at"),
                "freshness": dict(raw.get("freshness") or {}) if isinstance(raw.get("freshness") or {}, dict) else {},
                "corporate_action_review": list(raw.get("corporate_action_review") or []) if isinstance(raw.get("corporate_action_review") or [], list) else [],
                "warning": str(raw.get("warning") or ""),
                "stage1_lanes": list(raw.get("stage1_lanes") or []) if isinstance(raw.get("stage1_lanes") or [], list) else [],
                "stage2_selection_reason": str(raw.get("stage2_selection_reason") or ""),
                "lenses": list(raw.get("lenses") or []) if isinstance(raw.get("lenses") or [], list) else [],
                "in_coverage": bool(raw.get("in_coverage")),
            })
            candidates.append(row)

    candidates.sort(key=lambda row: (
        row["priority_rank"],
        -abs(row["base_gap_pct"] or 0.0),
        -row["valuation_methods"],
        -row["forensic_score"],
        row["ticker"],
    ))
    errors = scan.get("errors")
    scan["candidates"] = candidates
    scan["long_candidates"] = [row for row in candidates if row["research_side"] == "LONG" and row.get("priority") != "WATCH"]
    scan["short_candidates"] = [row for row in candidates if row["research_side"] == "SHORT" and row.get("priority") != "WATCH"]
    scan["watch_candidates"] = [row for row in candidates if row.get("priority") == "WATCH"]
    scan["errors"] = [str(x) for x in errors] if isinstance(errors, list) else ([] if not errors else [str(errors)])
    scan["candidate_count"] = len(candidates)
    scan["known_enriched"] = as_int(scan.get("known_enriched"), sum(1 for x in candidates if x.get("in_coverage")))
    scan["long_count"] = len(scan["long_candidates"])
    scan["short_count"] = len(scan["short_candidates"])
    scan["watch_count"] = len(scan["watch_candidates"])
    scan["p1_count"] = sum(1 for x in candidates if x.get("priority") == "P1")
    scan["p2_count"] = sum(1 for x in candidates if x.get("priority") == "P2")
    for key in (
        "stage0_count", "stage0_raw_count", "stage0_excluded_count",
        "stage1_scanned_count", "stage1_qualified_count", "stage1_broad_rotation_count",
        "stage1_quiet_broad_count", "stage1_activity_count", "stage1_full_universe_count", "stage1_cursor_start",
        "stage1_cursor_end", "stage1_snapshot_requested_count", "stage1_snapshot_received_count",
        "stage15_mispricing_count", "stage15_long_count", "stage15_short_count",
        "stage2_selected_count", "stage2_enriched_count", "provider_call_total", "excluded_count",
    ):
        scan[key] = as_int(scan.get(key), 0)
    scan["excluded_breakdown"] = dict(scan.get("excluded_breakdown") or {}) if isinstance(scan.get("excluded_breakdown") or {}, dict) else {}
    scan["stage0_excluded_breakdown"] = dict(scan.get("stage0_excluded_breakdown") or {}) if isinstance(scan.get("stage0_excluded_breakdown") or {}, dict) else {}
    scan["provider_calls"] = dict(scan.get("provider_calls") or {}) if isinstance(scan.get("provider_calls") or {}, dict) else {}
    scan["guardrails"] = dict(scan.get("guardrails") or {}) if isinstance(scan.get("guardrails") or {}, dict) else {}
    scan["ranking_basis"] = list(scan.get("ranking_basis") or []) if isinstance(scan.get("ranking_basis") or [], list) else []
    scan["coverage_progress"] = dict(scan.get("coverage_progress") or {}) if isinstance(scan.get("coverage_progress") or {}, dict) else {}
    scan["universe_health"] = dict(scan.get("universe_health") or {}) if isinstance(scan.get("universe_health") or {}, dict) else {}
    scan["scan_cadence"] = dict(scan.get("scan_cadence") or {}) if isinstance(scan.get("scan_cadence") or {}, dict) else {}
    scan["fundamental_screen"] = dict(scan.get("fundamental_screen") or {}) if isinstance(scan.get("fundamental_screen") or {}, dict) else {}
    raw_rejections = list(scan.get("rejection_log") or []) if isinstance(scan.get("rejection_log") or [], list) else []
    rejection_log = []
    for raw in raw_rejections:
        if not isinstance(raw, dict):
            continue
        rejection_log.append({
            "ticker": str(raw.get("ticker") or "").upper(),
            "stage": str(raw.get("stage") or ""),
            "reason": str(raw.get("reason") or ""),
            "detail": str(raw.get("detail") or ""),
            "base": as_float(raw.get("base")),
            "base_gap_pct": as_float(raw.get("base_gap_pct")),
            "quality": str(raw.get("quality") or ""),
            "valuation_methods": as_int(raw.get("valuation_methods"), 0),
        })
    scan["rejection_log"] = rejection_log
    return scan


def _discovery_promotion_provenance(user_id: int, ticker: str) -> dict:
    latest = Job.query.filter_by(
        user_id=user_id, job_type="DISCOVERY_SCAN", status="DONE"
    ).order_by(Job.finished_at.desc(), Job.id.desc()).first()
    scan = _normalized_market_scan(latest)
    wanted = str(ticker or "").upper()
    row = next((item for item in scan.get("candidates") or [] if str(item.get("ticker") or "").upper() == wanted), None)
    if not row:
        return {}
    return {
        "contract_version": scan.get("contract_version"),
        "scan_job_id": latest.id if latest else None,
        "scan_finished_at": latest.finished_at.isoformat(timespec="seconds") if latest and latest.finished_at else None,
        "direction": row.get("direction"),
        "family": row.get("radar_label"),
        "price": row.get("price"),
        "bear": row.get("bear"),
        "base": row.get("base"),
        "bull": row.get("bull"),
        "base_gap_pct": row.get("base_gap_pct"),
        "fair_value_quality": row.get("fair_value_quality"),
        "valuation_methods": row.get("valuation_methods"),
        "opportunity_tier": row.get("opportunity_tier") or row.get("priority"),
        "operating_state": row.get("operating_state"),
        "operating_confirmation": list(row.get("operating_confirmation") or []),
        "why_found": list(row.get("why_found") or []),
        "what_invalidates": row.get("what_invalidates"),
        "freshness": dict(row.get("freshness") or {}),
    }


@bp.get("/discovery")
@login_required
def discovery():
    require_control_view()
    q = str(request.args.get("q") or "").strip().upper()
    external_error = ""
    if q:
        try:
            external = search_universe(q, g.user.id)
        except Exception as exc:
            current_app.logger.exception("Discovery universe search failed")
            external = {"query": q, "results": [], "outside_coverage": [], "covered_matches": [], "provider": "Unavailable"}
            external_error = f"{type(exc).__name__}: provider search unavailable"
    else:
        external = {"query": "", "results": [], "outside_coverage": [], "covered_matches": [], "provider": ""}

    latest_scan_job = Job.query.filter_by(
        user_id=g.user.id, job_type="DISCOVERY_SCAN", status="DONE"
    ).order_by(Job.finished_at.desc(), Job.id.desc()).first()
    latest_scan_attempt = Job.query.filter_by(
        user_id=g.user.id, job_type="DISCOVERY_SCAN"
    ).order_by(Job.created_at.desc(), Job.id.desc()).first()
    market_scan = _normalized_market_scan(latest_scan_job)
    scan_failure = ""
    if latest_scan_attempt and latest_scan_attempt.status == "FAILED":
        done_is_older = latest_scan_job is None or latest_scan_attempt.id > latest_scan_job.id
        if done_is_older:
            scan_failure = f"Job #{latest_scan_attempt.id} failed: {latest_scan_attempt.error_message or 'provider scan failed'}"
    rows, cache_building = _cached_coverage_rows(g.user.id)
    return render_template(
        "discovery.html", rows=rows, q=q, external=external, external_error=external_error,
        market_scan=market_scan, market_scan_job=latest_scan_job, market_scan_attempt=latest_scan_attempt,
        scan_failure=scan_failure, cache_building=cache_building,
    )


@bp.post("/coverage")
@role_required("CONTROL")
def add_coverage():
    require_control_view(); ticker = str(request.form.get("ticker") or "").strip().upper(); validation = validate_ticker(ticker)
    discovery_provenance = _discovery_promotion_provenance(g.user.id, ticker) if str(request.form.get("origin") or "").lower() == "discovery" else {}
    if not validation.valid:
        flash(validation.message or "Ticker not found / symbol not recognized.", "error"); return redirect(request.referrer or url_for("web.dashboard"))
    security, _ = ensure_security_from_validation(validation)
    existing = Coverage.query.filter_by(user_id=g.user.id, security_id=security.id).first()
    if existing:
        if str(existing.status or "").upper() == "ARCHIVED":
            existing.status = "MONITOR"
            existing.research_state = existing.research_state if existing.research_state != "ARCHIVED" else "UNDER_REVIEW"
            existing.updated_at = utcnow()
            restore_meta = {"ticker": ticker}
            if discovery_provenance:
                restore_meta["discovery_provenance"] = discovery_provenance
            audit("coverage.restore", "coverage", existing.id, restore_meta)
            db.session.commit()
            flash(f"{ticker} restored to active Coverage.", "success")
            return redirect(url_for("web.company_section", ticker=ticker, section="overview"))
        flash("Ticker already exists in Coverage.", "error")
        return redirect(url_for("web.company_section", ticker=ticker, section="overview"))
    coverage = Coverage(user_id=g.user.id, security_id=security.id, status="MONITOR", research_state="UNRATED")
    db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, g.user.id)
    create_meta = {"ticker": ticker, "validation_source": validation.source}
    if discovery_provenance:
        create_meta["discovery_provenance"] = discovery_provenance
    audit("coverage.create", "coverage", coverage.id, create_meta); db.session.commit()
    enqueue_job("MARKET_REFRESH", user_id=g.user.id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=20)
    enqueue_job("PRICE_HISTORY_REFRESH", user_id=g.user.id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id, "lookback_years": 10}, priority=35)
    if provider_status(g.user.id).get("sec"):
        enqueue_job("SEC_INGEST", user_id=g.user.id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=40)
    else:
        enqueue_job("RESEARCH_PREFILL", user_id=g.user.id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=50)
    flash(f"{ticker} added to Coverage. Quote, price history and evidence jobs queued where available.", "success")
    return redirect(url_for("web.company_section", ticker=ticker, section="overview"))


@bp.post("/coverage/<ticker>/manage")
@role_required("CONTROL")
def manage_coverage(ticker):
    """Manage list membership without deleting research/audit history."""
    require_control_view()
    coverage = _coverage(ticker)
    action = str(request.form.get("action") or "save").lower()
    if action == "archive":
        coverage.status = "ARCHIVED"
        coverage.research_state = "ARCHIVED"
        audit("coverage.archive", "coverage", coverage.id, {"ticker": ticker.upper()})
        db.session.commit()
        flash(f"{ticker.upper()} removed from active Coverage. Research history is preserved.", "success")
        return redirect(url_for("web.dashboard"))

    raw_priority = request.form.get("priority")
    if raw_priority not in (None, ""):
        try:
            coverage.priority = max(-999, min(999, int(raw_priority)))
        except (TypeError, ValueError):
            flash("Priority must be a whole number.", "error")
            return redirect(request.referrer or url_for("web.dashboard"))
    status = str(request.form.get("status") or coverage.status).upper()
    if status in {"MONITOR", "RESEARCH", "READY"}:
        coverage.status = status
    coverage.updated_at = utcnow()
    audit("coverage.manage", "coverage", coverage.id, {"ticker": ticker.upper(), "priority": coverage.priority, "status": coverage.status})
    db.session.commit()
    flash(f"{ticker.upper()} Coverage settings saved.", "success")
    return redirect(request.referrer or url_for("web.dashboard"))


@bp.get("/company/<ticker>")
@login_required
def company_default(ticker):
    require_control_view(); return redirect(url_for("web.company_section", ticker=ticker.upper(), section="overview"))


@bp.get("/company/<ticker>/<section>")
@login_required
def company_section(ticker, section):
    require_control_view()
    if section == "numbers":
        return redirect(url_for("web.company_section", ticker=ticker.upper(), section="fundamentals"), code=301)
    if section == "validate":
        return redirect(url_for("web.validate_company", ticker=ticker.upper()), code=302)
    if section not in SECTION_KEYS: abort(404)
    ctx = _ctx(ticker); company = ctx["company"]; coverage = ctx["coverage"]; extra = {}
    cache = ctx.get("research_cache") or {}
    if section in {"overview", "business"}:
        extra["synthesis"] = dict(cache.get("synthesis") or _fallback_synthesis(ctx))
        if section == "business":
            extra["auto_triangulation"] = dict(cache.get("triangulation") or {
                "available": False, "reason": "Peer triangulation is updating in the job queue.",
                "peers": [], "comparisons": [], "signals": [], "method": "CALCULATING", "sic": "", "sic_description": "",
            })
            extra["triangulation_rows"] = Event.query.filter(
                Event.company_id == company.id,
                Event.event_type.like("TRIANGULATION_%"),
            ).order_by(Event.event_date.desc(), Event.id.desc()).limit(60).all()
            peer_query = Company.query.filter(Company.id != company.id)
            if company.industry:
                peer_query = peer_query.filter(Company.industry == company.industry)
            elif company.sector:
                peer_query = peer_query.filter(Company.sector == company.sector)
            else:
                peer_query = peer_query.filter(db.text("1=0"))
            extra["peer_candidates"] = peer_query.order_by(Company.display_name.asc()).limit(12).all()
    if section == "expectations":
        # Expectations is forward-looking by design. Current operating facts live in
        # Fundamentals and are not repeated here.
        extra["expectation_rows"] = Expectation.query.filter_by(coverage_id=coverage.id).order_by(Expectation.period_label, Expectation.metric).all()
        extra["forecast_rows"] = forecast_rows(company.id, ctx["model"], 5)
        extra["scenario_forecasts"] = scenario_forecasts(company.id, ctx["model"], 5)
        extra["implied_expectations"] = dict(
            (ctx["decision_lenses"].get("implied_expectations") or {})
            or {"available": False, "classification": "CALCULATING" if ctx.get("cache_pending") else "UNAVAILABLE", "drivers": [], "errors": []}
        )
    elif section == "fundamentals":
        financials = annual_rows(company.id, 16)
        annual_history_rows = annual_history_grid(company.id, target_years=10, display_years=16)
        quarterly_financials = quarterly_rows(company.id, 12)
        current_financial = current_row(company.id)
        forecasts = forecast_rows(company.id, ctx["model"], 3)
        scale_series = [
            {
                "label": f"FY{row.get('fiscal_year')}",
                "revenue": row.get("revenue"),
                "fcf": row.get("fcf"),
                "missing_year": bool(row.get("missing_year")),
            }
            for row in reversed(annual_history_rows)
        ]
        if current_financial and current_financial.get("period_type") == "TTM":
            scale_series.append({
                "label": "TTM",
                "revenue": current_financial.get("revenue"),
                "fcf": current_financial.get("fcf"),
            })
        scale_series.extend({
            "label": row.get("period_label"), "forecast_revenue": row.get("revenue")
        } for row in forecasts)
        leverage_display = {"value": None, "basis": "", "reason": "Debt/cash or positive FCF unavailable on the current basis."}
        current_metrics = dict((current_financial or {}).get("metrics") or {})
        if current_metrics.get("net_debt_to_fcf") is not None:
            leverage_display = {
                "value": current_metrics.get("net_debt_to_fcf"),
                "basis": f"{(current_financial or {}).get('period_label') or 'Current'} basis",
                "reason": "",
            }
        else:
            for row in financials:
                ratio = (row.get("metrics") or {}).get("net_debt_to_fcf")
                if ratio is not None:
                    leverage_display = {
                        "value": ratio,
                        "basis": f"Latest available FY{row.get('fiscal_year')} fallback",
                        "reason": "",
                    }
                    break
        economic_reality = dict(((current_financial or {}).get("quality") or {}).get("economic_reality") or {})
        completeness = numbers_completeness(company.id)
        company_type = str((ctx["model"].assumptions or {}).get("company_type") or infer_company_type(company.sector, company.industry))
        fundamentals_forensics = dict(cache.get("fundamentals_forensics") or {
            "engine_version": "fundamentals-forensics-v2",
            "state": "CALCULATING" if ctx.get("cache_pending") else "INSUFFICIENT EVIDENCE",
            "headline": "Fundamentals forensics is updating in the research job queue." if ctx.get("cache_pending") else "No materialized Fundamentals forensic read is stored yet.",
            "strengths": [], "red_flags": [], "watches": [], "inconsistencies": [], "data_gaps": [],
            "trend_cards": [], "counts": {"strengths": 0, "red_flags": 0, "watches": 0, "inconsistencies": 0, "data_gaps": 0},
        })
        economic_refresh_queued = bool(ctx.get("economic_reclass_pending"))

        extra.update({
            "financials": financials,
            "annual_history_rows": annual_history_rows,
            "quarterly_financials": quarterly_financials,
            "current_financial": current_financial,
            "economic_reality": economic_reality,
            "leverage_display": leverage_display,
            "numbers_completeness": completeness,
            "fundamentals_forensics": fundamentals_forensics,
            "fundamentals_company_type": company_type,
            "economic_refresh_queued": economic_refresh_queued,
            "forecast_rows": forecasts,
            "numbers_scale_series": scale_series,
        })
        extra["quality_issues"] = DataQualityIssue.query.filter_by(company_id=company.id, status="OPEN").order_by(DataQualityIssue.detected_at.desc()).all()
    elif section == "valuation":
        extra["scenarios"] = {s.name.upper(): s for s in ctx["model"].scenarios}; extra["sensitivity"] = valuation_sensitivity(ctx["valuation"].get("base"), ctx["valuation"].get("current_price"))
    elif section == "bear-case":
        extra["bear_items"] = BearCaseItem.query.filter_by(coverage_id=coverage.id).order_by(BearCaseItem.created_at.desc()).all()
    elif section == "catalysts":
        extra["catalyst_rows"] = Catalyst.query.filter_by(coverage_id=coverage.id).order_by(Catalyst.expected_date.asc(), Catalyst.id.desc()).all()
    elif section == "financial-flows":
        periods = [period for period, _ in canonical_annual_pairs(company.id)]
        year = int(request.args.get("year") or (periods[0].fiscal_year if periods else 0))
        period = next((p for p in periods if p.fiscal_year == year), None)
        flows = {}
        if period:
            for row in FinancialFlow.query.filter_by(financial_period_id=period.id).order_by(FinancialFlow.id.desc()).all():
                flows.setdefault(row.flow_type, row.payload)
        extra.update({"periods": periods, "selected_year": year, "flows": flows})
    elif section == "management":
        extra["management_rows"] = ManagementAssessment.query.filter_by(coverage_id=coverage.id).order_by(ManagementAssessment.as_of.desc()).all()
        extra["management_engine"] = dict(cache.get("management") or {"score": None, "coverage_pct": 0, "components": []})
        extra["management_accountability"] = list(cache.get("management_accountability") or [])
        extra["management_promises"] = list(cache.get("management_promises") or [])
        latest_management_scan = RefreshRun.query.filter_by(
            company_id=company.id,
            refresh_type="MANAGEMENT_SCAN",
        ).order_by(RefreshRun.started_at.desc(), RefreshRun.id.desc()).first()
        extra["management_scan"] = {
            "status": latest_management_scan.status if latest_management_scan else "NOT RUN",
            "started_at": latest_management_scan.started_at if latest_management_scan else None,
            "finished_at": latest_management_scan.finished_at if latest_management_scan else None,
            "summary": dict(latest_management_scan.summary or {}) if latest_management_scan else {},
            "error_id": latest_management_scan.error_id if latest_management_scan else "",
        }
    elif section == "tape":
        months = 6 if str(request.args.get("months") or "12") == "6" else 12
        extra["tape_events"] = Event.query.filter(
            Event.company_id == company.id,
            ~Event.event_type.like("RESEARCH_CACHE_%"),
        ).order_by(Event.event_date.desc()).limit(30).all()
        extra["finra_summary"] = finra_stored_summary(company.id)
        extra["finra_api_ready"] = provider_status(g.user.id).get("finra_api", False)
        tape_materialized = _materialized_tape_for_display(ctx["security"], cache)
        extra["tape_series"] = _cached_tape_for_months(tape_materialized, months)
    elif section == "monitoring":
        rules = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(MonitoringRule.updated_at.desc()).all()
        histories = {r.id: MonitoringHistory.query.filter_by(rule_id=r.id).order_by(MonitoringHistory.observed_at.desc()).limit(5).all() for r in rules}
        exceptions = []
        for rule in rules:
            latest_history = (histories.get(rule.id) or [None])[0]
            if latest_history and str(latest_history.status or "").upper() in {"WATCH", "FAIL"}:
                exceptions.append({"rule": rule, "history": latest_history})
        alerts = Alert.query.filter_by(user_id=g.user.id, coverage_id=coverage.id).order_by(Alert.created_at.desc()).limit(20).all()

        thesis_invalidation_history = []
        archived_versions = (
            ResearchVersion.query
            .filter_by(coverage_id=coverage.id)
            .filter(ResearchVersion.reason.like("Thesis revised · archived prior thesis%"))
            .order_by(ResearchVersion.created_at.desc(), ResearchVersion.id.desc())
            .limit(12)
            .all()
        )
        for row in archived_versions:
            payload = dict(row.payload or {})
            control = dict(payload.get("_thesis_control") or {})
            thesis_invalidation_history.append({
                "version": row.version,
                "created_at": row.created_at,
                "thesis": str(payload.get("thesis") or ""),
                "invalidation": str(control.get("invalidation") or ""),
                "locked_at": control.get("locked_at"),
            })

        extra.update({
            "monitor_rules": rules,
            "monitor_histories": histories,
            "monitor_exceptions": exceptions,
            "monitor_alerts": alerts,
            "monitor_plan": monitoring_plan(company.id, ctx["valuation"], ctx["intelligence"], ctx["model"]),
            "thesis_invalidation_history": thesis_invalidation_history,
        })
    elif section == "journal":
        extra["journal_rows"] = DecisionJournal.query.filter_by(coverage_id=coverage.id, user_id=g.user.id).order_by(DecisionJournal.created_at.desc()).all()
        extra["journal_outcomes"] = DecisionOutcome.query.filter_by(coverage_id=coverage.id, user_id=g.user.id).order_by(DecisionOutcome.created_at.desc()).all()
        extra["snapshots"] = Snapshot.query.filter_by(coverage_id=coverage.id).order_by(Snapshot.created_at.desc()).limit(20).all()
        extra["journal_prefill"] = journal_prefill(ctx["intelligence"], ctx["valuation"], ctx["model"])
    elif section == "audit":
        extra["sources"] = Source.query.filter_by(company_id=company.id).order_by(Source.retrieved_at.desc()).all()
        extra["provenance"] = Provenance.query.join(FinancialPeriod, Provenance.financial_period_id == FinancialPeriod.id).filter(FinancialPeriod.company_id == company.id).order_by(Provenance.created_at.desc()).limit(200).all()
        extra["refresh_runs"] = RefreshRun.query.filter(or_(RefreshRun.company_id == company.id, RefreshRun.security_id == ctx["security"].id)).order_by(RefreshRun.started_at.desc()).limit(50).all()
    return render_template("company_section.html", section=section, **ctx, **extra)


# Register mutation/publication routes onto this blueprint after shared helpers exist.
from . import routes_edit as _routes_edit  # noqa: E402,F401
from . import routes_publish as _routes_publish  # noqa: E402,F401
