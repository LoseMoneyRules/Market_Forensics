from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .core_models import (
    Company, Coverage, Event, InvestmentState, Position, ResearchState, RiskPlan,
    Security, ValuationModel,
)
from .data_providers import latest_snapshot
from .decision_lenses import build_decision_lenses
from .decision_support import company_brief, management_accountability, management_engine, tape_series
from .discovery_engine import classify_coverage
from .management_promises import evaluate_promises
from .macro_context import macro_context
from .research_synthesis import build_synthesis
from .triangulation_engine import apply_peer_valuation_overlay, automatic_triangulation
from .extensions import db
from .readiness import research_readiness
from .services import valuation_result
from .valuation_engine import stored_model_base_quality, valuation_is_decision_grade


CACHE_PREFIX = "RESEARCH_CACHE_"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def cache_event_type(coverage_id: int) -> str:
    return f"{CACHE_PREFIX}{int(coverage_id)}"[:48]


def _canonical_discovery_labels(payload: dict[str, Any]) -> list[str]:
    intelligence = dict(payload.get("intelligence") or {})
    if intelligence.get("valuation_base_quality") is None:
        valuation = dict(payload.get("valuation") or {})
        intelligence["valuation_base_quality"] = (
            valuation.get("base_quality")
            or valuation.get("quality")
            or "DATA_WARNING"
        )
    return classify_coverage(
        intelligence,
        dict(payload.get("readiness") or {}),
    )


def latest_research_cache(coverage_id: int, company_id: int | None = None) -> dict[str, Any] | None:
    query = Event.query.filter_by(event_type=cache_event_type(coverage_id))
    if company_id is not None:
        query = query.filter_by(company_id=company_id)
    row = query.order_by(Event.event_date.desc(), Event.id.desc()).first()
    if row is None:
        return None
    payload = dict(row.payload or {})
    # Recompute Discovery labels from current fail-closed policy on read. Old
    # materialized labels must not preserve a value signal after quality rules tighten.
    payload["discovery_labels"] = _canonical_discovery_labels(payload)
    payload["_event_id"] = row.id
    payload["_generated_at"] = row.event_date.isoformat() if row.event_date else None
    return payload


def latest_cache_map(coverage_ids: list[int]) -> dict[int, dict[str, Any]]:
    ids = {int(x) for x in coverage_ids if x is not None}
    if not ids:
        return {}
    prefixes = [cache_event_type(x) for x in ids]
    rows = Event.query.filter(Event.event_type.in_(prefixes)).order_by(Event.event_date.desc(), Event.id.desc()).all()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            coverage_id = int(str(row.event_type).split(CACHE_PREFIX, 1)[1])
        except Exception:
            continue
        if coverage_id not in ids or coverage_id in out:
            continue
        payload = dict(row.payload or {})
        payload["discovery_labels"] = _canonical_discovery_labels(payload)
        payload["_event_id"] = row.id
        payload["_generated_at"] = row.event_date.isoformat() if row.event_date else None
        out[coverage_id] = payload
    return out


def refresh_research_cache(coverage_id: int) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if coverage is None:
        raise RuntimeError(f"Coverage {coverage_id} not found")
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id) if security else None
    if security is None or company is None:
        raise RuntimeError(f"Coverage {coverage_id} security/company missing")

    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    investment = InvestmentState.query.filter_by(coverage_id=coverage.id).first()
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    if not all((research, risk, investment, model)):
        raise RuntimeError(f"Coverage workspace incomplete for coverage_id={coverage.id}")

    market = latest_snapshot(security.id)
    intrinsic_valuation = valuation_result(coverage)
    triangulation = automatic_triangulation(company.id, coverage.user_id)
    macro = macro_context(company.id)
    valuation = apply_peer_valuation_overlay(intrinsic_valuation, triangulation)
    readiness = research_readiness(coverage)

    # Heavy analytical reads are intentionally executed here, from a job, never
    # on normal GET navigation.
    from .routes import _intelligence
    intelligence = _intelligence(coverage, company, model, market, valuation, readiness)
    management = management_engine(company.id)
    management_accountability_rows = management_accountability(company.id)
    management_promises = evaluate_promises(company.id)
    tape = tape_series(security, 12)
    lenses = build_decision_lenses(
        coverage=coverage,
        company=company,
        research=research,
        risk=risk,
        model=model,
        market=market,
        valuation=valuation,
        intelligence=intelligence,
        readiness=readiness,
        management=management,
        tape=tape,
    )
    brief = company_brief(company.id, valuation, intelligence, model)
    synthesis = build_synthesis(
        coverage=coverage, security=security, company=company, research=research, risk=risk,
        model=model, market=market, valuation=valuation, intelligence=intelligence, readiness=readiness,
    )
    discovery_labels = classify_coverage(intelligence, readiness)

    payload = _jsonable({
        "coverage_id": coverage.id,
        "user_id": coverage.user_id,
        "security_id": security.id,
        "company_id": company.id,
        "ticker": security.ticker,
        "valuation": valuation,
        "readiness": readiness,
        "intelligence": intelligence,
        "decision_lenses": lenses,
        "brief": {
            "price": brief.get("price"),
            "bear": brief.get("bear"),
            "base": brief.get("base"),
            "bull": brief.get("bull"),
            "base_gap_pct": brief.get("base_gap_pct"),
            "confidence": brief.get("confidence"),
            "horizon_years": brief.get("horizon_years"),
            "target_year": brief.get("target_year"),
            "reasons": brief.get("reasons") or [],
        },
        "management": management,
        "management_accountability": management_accountability_rows,
        "management_promises": [{
            "metric": row.get("metric"), "target_year": row.get("target_year"),
            "target_period": row.get("target_period"), "target_period_type": row.get("target_period_type"),
            "low": row.get("low"), "high": row.get("high"), "target_text": row.get("target_text"),
            "unit": row.get("unit"), "operator": row.get("operator"),
            "basis": row.get("basis"), "definition": row.get("definition"),
            "comparability": row.get("comparability"), "comparability_reason": row.get("comparability_reason"),
            "statement": row.get("statement"), "origin": row.get("origin"),
            "source_id": row.get("source_id"), "source_provider": row.get("source_provider"),
            "source_title": row.get("source_title"), "source_accession": row.get("source_accession"),
            "source_form": row.get("source_form"), "source_date": row.get("source_date"),
            "document_name": row.get("document_name"), "document_url": row.get("document_url"),
            "actual": row.get("actual"), "actual_provenance": row.get("actual_provenance") or {},
            "status": row.get("status"),
        } for row in management_promises],
        "tape": tape,
        "tape_metrics": (tape.get("metrics") or {}),
        "triangulation": triangulation,
        "macro": macro,
        "synthesis": synthesis,
        "discovery_labels": discovery_labels,
        "market_as_of": market.as_of if market else None,
        "market_price": market.price if market else None,
        "coverage_updated_at": coverage.updated_at,
        "model_updated_at": model.updated_at,
    })

    row = Event(
        company_id=company.id,
        event_type=cache_event_type(coverage.id),
        title=f"{security.ticker} research cache",
        event_date=utcnow(),
        payload=payload,
    )
    db.session.add(row)
    # Keep only the most recent few cache snapshots to prevent unbounded growth.
    old_rows = Event.query.filter_by(company_id=company.id, event_type=cache_event_type(coverage.id)).order_by(Event.event_date.desc(), Event.id.desc()).offset(4).all()
    for old in old_rows:
        db.session.delete(old)
    db.session.commit()
    payload["_event_id"] = row.id
    payload["_generated_at"] = row.event_date.isoformat()
    return payload


def patch_research_cache_readiness(coverage_id: int, readiness: dict[str, Any]) -> dict[str, Any] | None:
    """Patch live readiness and re-evaluate lightweight Decision Lenses.

    Gate approve/reopen is a synchronous CONTROL action. It must not require a
    heavy RECALCULATE job. Decision Lenses reuse already-materialized evidence
    and valuation, so Research conclusion can reflect the new readiness state
    immediately without calling providers or rebuilding heavy analytics.
    """
    row = Event.query.filter_by(event_type=cache_event_type(coverage_id)).order_by(Event.event_date.desc(), Event.id.desc()).first()
    if row is None:
        return None
    payload = dict(row.payload or {})
    payload["readiness"] = _jsonable(readiness)

    updated_lenses = None
    coverage = db.session.get(Coverage, coverage_id)
    security = db.session.get(Security, coverage.security_id) if coverage else None
    company = db.session.get(Company, security.company_id) if security else None
    research = ResearchState.query.filter_by(coverage_id=coverage_id).first() if coverage else None
    risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first() if coverage else None
    model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).order_by(ValuationModel.id.desc()).first() if coverage else None
    if all((coverage, security, company, research, risk, model)):
        market = latest_snapshot(security.id)
        valuation = dict(payload.get("valuation") or valuation_result(coverage))
        if not valuation.get("base_quality"):
            base_quality = stored_model_base_quality(model)
            valuation["base_quality"] = base_quality
            valuation["quality"] = valuation.get("quality") or base_quality
            valuation["decision_grade"] = valuation_is_decision_grade({"base_quality": base_quality})
            payload["valuation"] = _jsonable(valuation)
        updated_lenses = build_decision_lenses(
            coverage=coverage,
            company=company,
            research=research,
            risk=risk,
            model=model,
            market=market,
            valuation=valuation,
            intelligence=dict(payload.get("intelligence") or {}),
            readiness=readiness,
            management=dict(payload.get("management") or {}),
            tape=dict(payload.get("tape") or {}),
        )
        payload["decision_lenses"] = _jsonable(updated_lenses)

    row.payload = payload
    return updated_lenses


def cache_is_stale(cache: dict[str, Any] | None, coverage: Coverage, model: ValuationModel | None = None) -> bool:
    if not cache:
        return True
    generated = cache.get("_generated_at")
    if not generated:
        return True
    try:
        generated_at = datetime.fromisoformat(str(generated))
    except Exception:
        return True
    if coverage.updated_at and coverage.updated_at > generated_at:
        return True
    if model and model.updated_at and model.updated_at > generated_at:
        return True
    return False


__all__ = [
    "cache_event_type", "latest_research_cache", "latest_cache_map",
    "refresh_research_cache", "patch_research_cache_readiness", "cache_is_stale",
]
