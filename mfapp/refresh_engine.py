"""0.4.0: one durable, staged refresh per covered security.

Provider reads are reused from the existing canonical engines. A refresh may
partially succeed, but does not discard already committed provider evidence.
Only one final Research recalculation runs after its evidence stages.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from .core_models import Company, Coverage, Event, HistoricalPrice, Job, RefreshRun, Security, Source
from .data_providers import latest_snapshot, provider_status, refresh_security_quote
from .extensions import db
from .finra import refresh_bundle as refresh_finra_bundle
from .historical_data import refresh_historical_prices
from .macro_context import refresh_macro_context
from .positioning import refresh_positioning_bundle
from .secdata import SEC_NORMALIZER_VERSION, refresh_company_fundamentals

STAGES = (
    "MARKET_REFRESH", "PRICE_HISTORY_REFRESH", "SEC_INGEST",
    "MACRO_REFRESH", "FINRA_IMPORT", "POSITIONING_REFRESH",
    "MANAGEMENT_SCAN", "RECALCULATE",
)
# Minimum ages for manual comprehensive refresh; avoid immediately downloading
# the same immutable source again after a second click.
FULL_MIN_AGE = {
    "MARKET_REFRESH": timedelta(minutes=5),
    "PRICE_HISTORY_REFRESH": timedelta(days=7),
    "SEC_INGEST": timedelta(minutes=15),
    "MACRO_REFRESH": timedelta(hours=24),
    "FINRA_IMPORT": timedelta(hours=1),
    "POSITIONING_REFRESH": timedelta(hours=1),
    "MANAGEMENT_SCAN": timedelta(days=7),
}


def _latest_done(kind: str, security: Security):
    query = RefreshRun.query.filter(RefreshRun.refresh_type == kind, RefreshRun.status == "DONE")
    if kind in {"FINRA_IMPORT", "POSITIONING_REFRESH", "PRICE_HISTORY_REFRESH", "MARKET_REFRESH"}:
        query = query.filter(RefreshRun.security_id == security.id)
    else:
        query = query.filter(RefreshRun.company_id == security.company_id)
    return query.order_by(RefreshRun.finished_at.desc(), RefreshRun.id.desc()).first()


def _due(kind: str, security: Security, mode: str, now, sec_ready: bool) -> bool:
    if kind in {"SEC_INGEST", "MANAGEMENT_SCAN"} and not sec_ready:
        return False
    if kind == "RECALCULATE":
        return True
    done = _latest_done(kind, security)
    elapsed = now - done.finished_at if done and done.finished_at else None
    if kind == "MARKET_REFRESH":
        quote = latest_snapshot(security.id)
        stale = quote is None or quote.as_of is None or (now - quote.as_of) > timedelta(minutes=30)
        return stale or (mode == "all" and (quote is None or quote.as_of is None or (now - quote.as_of) > FULL_MIN_AGE[kind]))
    if kind == "PRICE_HISTORY_REFRESH":
        n, last_date = db.session.query(db.func.count(HistoricalPrice.id), db.func.max(HistoricalPrice.trade_date)).filter(
            HistoricalPrice.security_id == security.id
        ).first()
        outdated = not last_date or last_date < (now.date() - timedelta(days=10))
        # Young IPOs and providers with partial histories must not be downloaded
        # again on every page/refresh. A successful attempt has a bounded cooldown.
        if elapsed is not None and elapsed < timedelta(days=7):
            return False
        return bool(not n or int(n) < 300 or outdated or mode == "all")
    if kind == "SEC_INGEST":
        source = Source.query.filter_by(
            company_id=security.company_id, provider="SEC", source_type="COMPANYFACTS",
        ).order_by(Source.retrieved_at.desc(), Source.id.desc()).first()
        normalizer_old = not source or str((source.meta or {}).get("normalizer_version") or "") != SEC_NORMALIZER_VERSION
        return bool(normalizer_old or elapsed is None or elapsed > (FULL_MIN_AGE[kind] if mode == "all" else timedelta(hours=24)))
    if kind == "MANAGEMENT_SCAN":
        return elapsed is None or elapsed > timedelta(days=7)
    threshold = FULL_MIN_AGE[kind] if mode == "all" else timedelta(hours=24)
    return elapsed is None or elapsed > threshold


def schedule_research_refresh(user_id: int, mode: str) -> dict:
    """One queue row per security, not seven network rows plus duplicate recalcs."""
    from .jobs import enqueue_job, utcnow

    mode = "all" if mode == "all" else "stale"
    now = utcnow()
    sec_ready = bool(provider_status(user_id).get("sec"))
    queued = reused = scanned = 0
    coverages = Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").all()
    for coverage in coverages:
        security = db.session.get(Security, coverage.security_id)
        if security is None:
            continue
        scanned += 1
        if mode == "stale" and not any(
            _due(kind, security, mode, now, sec_ready) for kind in STAGES if kind != "RECALCULATE"
        ):
            continue
        job = enqueue_job(
            "RESEARCH_REFRESH", user_id=user_id, company_id=security.company_id,
            security_id=security.id,
            payload={"coverage_id": coverage.id, "mode": mode}, priority=28,
        )
        if getattr(job, "_mf_reused", False):
            reused += 1
            if mode == "all" and job.status == "QUEUED":
                payload = dict(job.payload or {})
                payload["mode"] = "all"
                job.payload = payload
                db.session.commit()
        else:
            queued += 1
    if queued:
        job = enqueue_job("PORTFOLIO_RECALCULATE", user_id=user_id, payload={}, priority=99)
        if getattr(job, "_mf_reused", False):
            reused += 1
        else:
            queued += 1
    return {
        "coverage_scanned": scanned, "jobs_queued": queued,
        "jobs_reused": reused, "sec_enabled": sec_ready,
        "execution": "staged_per_security", "mode": mode,
    }


def _execute_stage(kind: str, security: Security, user_id: int, coverage_id: int):
    from .jobs import _management_scan, _store_finra_bundle, recalculate_company, utcnow

    company = db.session.get(Company, security.company_id)
    if company is None:
        raise RuntimeError("Company not found")
    if kind == "MARKET_REFRESH":
        quote = refresh_security_quote(security, user_id)
        if not quote.ok:
            raise RuntimeError(quote.message or "Market provider unavailable")
        return {"provider": quote.provider, "as_of": quote.as_of.isoformat() if quote.as_of else None}
    if kind == "PRICE_HISTORY_REFRESH":
        return refresh_historical_prices(security, user_id, 10)
    if kind == "SEC_INGEST":
        return refresh_company_fundamentals(company, security, user_id)
    if kind == "MACRO_REFRESH":
        return refresh_macro_context(company.id)
    if kind == "FINRA_IMPORT":
        return _store_finra_bundle(security, refresh_finra_bundle(security.ticker, user_id, 35))
    if kind == "POSITIONING_REFRESH":
        result = refresh_positioning_bundle(security.ticker, user_id)
        db.session.add(Event(
            company_id=company.id, event_type="ALPACA_POSITIONING",
            title=f"{security.ticker} institutional-flow / borrow / options positioning",
            event_date=utcnow(), payload=result,
        ))
        db.session.commit()
        return result
    if kind == "MANAGEMENT_SCAN":
        return _management_scan(company, security, user_id, 36, force=False)
    if kind == "RECALCULATE":
        return recalculate_company(company.id, coverage_id)
    raise RuntimeError(f"Unsupported refresh stage {kind}")


def run_research_refresh(job: Job) -> dict:
    """Durably record every stage; a provider failure cannot hide a partial refresh."""
    from .jobs import utcnow

    security = db.session.get(Security, job.security_id)
    coverage_id = int((job.payload or {}).get("coverage_id") or 0)
    coverage = db.session.get(Coverage, coverage_id)
    if not security or not coverage or coverage.security_id != security.id or coverage.user_id != job.user_id:
        raise RuntimeError("Refresh target is not an active owned Coverage")
    if coverage.status == "ARCHIVED":
        return {"state": "SKIPPED", "reason": "ARCHIVED"}
    mode = "all" if (job.payload or {}).get("mode") == "all" else "stale"
    sec_ready = bool(provider_status(job.user_id).get("sec"))
    stages = {}
    failures = []
    for kind in STAGES:
        # Financial-period changes may require a fresh guidance scan even if the
        # last scan was recent.
        due = _due(kind, security, mode, utcnow(), sec_ready)
        if kind == "MANAGEMENT_SCAN" and stages.get("SEC_INGEST") == "DONE" and sec_ready:
            due = True
        if not due:
            stages[kind] = "UNAVAILABLE" if not sec_ready and kind in {"SEC_INGEST", "MANAGEMENT_SCAN"} else "FRESH"
            continue
        state = dict(job.result or {})
        state["current_stage"] = kind
        state["stages"] = dict(stages)
        job.result = state
        db.session.add(job)
        attempt = RefreshRun(
            company_id=security.company_id, security_id=security.id,
            job_id=job.id, refresh_type=kind, status="RUNNING", started_at=utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = attempt.id
        try:
            output = _execute_stage(kind, security, job.user_id, coverage_id)
            attempt = db.session.get(RefreshRun, attempt_id)
            attempt.status = "DONE"
            attempt.finished_at = utcnow()
            attempt.summary = {"orchestrator": job.id, "mode": mode,
                               "result": output if isinstance(output, dict) else {"completed": True}}
            stages[kind] = "DONE"
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            attempt = db.session.get(RefreshRun, attempt_id)
            attempt.status = "FAILED"
            attempt.finished_at = utcnow()
            attempt.error_id = uuid.uuid4().hex[:12]
            attempt.summary = {"error": f"{type(exc).__name__}: {exc}"[:400]}
            stages[kind] = "FAILED"
            failures.append({"stage": kind, "error_id": attempt.error_id, "message": str(exc)[:250]})
            db.session.commit()
        # Reacquire ORM instance after stage functions that commit their own work.
        job = db.session.get(Job, job.id)
        job.result = {"current_stage": kind, "stages": dict(stages),
                      "finished_steps": len(stages), "total_steps": len(STAGES)}
        db.session.commit()
    return {
        "state": "PARTIAL" if failures else "COMPLETE",
        "stages": stages, "failures": failures,
        "finished_steps": len(stages), "total_steps": len(STAGES),
        "mode": mode, "ticker": security.ticker,
    }
