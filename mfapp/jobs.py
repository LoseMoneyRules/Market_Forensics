from __future__ import annotations

import os
import re
import time
import uuid
import signal
import requests
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .autofill import prefill_coverage
from .calculations import CALCULATION_VERSION, build_cash_flow, build_income_statement_flow, calculate_valuation, financial_metrics
from .current_financials import current_row
from .core_models import (
    Alert, CalculationRun, Company, Coverage, DataQualityIssue, Event, FinancialFlow,
    FinancialPeriod, HistoricalPrice, Job, NormalizedFinancial, RefreshRun, Security, Source,
    ValuationModel,
)
from .data_providers import latest_snapshot, provider_status, refresh_security_quote
from .extensions import db
from .finra import FINRA_DAILY_CDN, refresh_bundle as refresh_finra_bundle
from .historical_data import refresh_historical_prices
from .historical_engine import run_historical_test
from .management_promises import (
    MANAGEMENT_SCAN_VERSION,
    extract_promises,
    html_to_text,
    original_actuals_from_companyfacts,
    store_original_actuals,
    store_promises,
)
from .macro_context import refresh_macro_context
from .market_discovery import market_scan
from .positioning import refresh_positioning_bundle
from .secdata import SEC_DATA, _json as sec_json, _ticker_meta as sec_ticker_meta, _ua as sec_user_agent, refresh_company_fundamentals
from .valuation_engine import valuation_base_quality

ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")
TERMINAL_JOB_STATUSES = ("DONE", "FAILED", "CANCELLED", "SUPERSEDED")
DEFAULT_JOB_LEASE_SECONDS = 30 * 60
JOB_LEASE_SECONDS = {"DISCOVERY_SCAN": 10 * 60}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _active_job_query(job_type: str, *, user_id: int, company_id: int | None = None, security_id: int | None = None):
    query = Job.query.filter(Job.user_id == user_id, Job.job_type == str(job_type).upper(), Job.status.in_(ACTIVE_JOB_STATUSES))
    if security_id is not None:
        return query.filter(Job.security_id == security_id)
    if company_id is not None:
        return query.filter(Job.security_id.is_(None), Job.company_id == company_id)
    return query.filter(Job.security_id.is_(None), Job.company_id.is_(None))


def enqueue_job(job_type: str, *, user_id: int, company_id: int | None = None, security_id: int | None = None,
                payload: dict[str, Any] | None = None, priority: int = 100, run_after: datetime | None = None) -> Job:
    kind = str(job_type).upper()
    existing = _active_job_query(kind, user_id=user_id, company_id=company_id, security_id=security_id).order_by(Job.id.asc()).first()
    if existing is not None:
        # An explicit CONTROL Management scan must not silently lose its force
        # semantics just because an unattended scan is already queued.
        if kind == "MANAGEMENT_SCAN" and bool((payload or {}).get("force")):
            merged = dict(existing.payload or {})
            merged.update(payload or {})
            merged["force"] = True
            merged["limit"] = max(int(merged.get("limit") or 0), int((payload or {}).get("limit") or 0), 36)
            existing.payload = merged
            existing.priority = min(int(existing.priority or priority), int(priority))
            db.session.commit()
        existing._mf_reused = True
        return existing
    job = Job(job_type=kind, status="QUEUED", priority=priority, user_id=user_id, company_id=company_id,
              security_id=security_id, payload=payload or {}, run_after=run_after or utcnow())
    job._mf_reused = False
    db.session.add(job); db.session.commit(); return job


def compact_queue(user_id: int | None = None) -> int:
    query = Job.query.filter(Job.status.in_(ACTIVE_JOB_STATUSES))
    if user_id is not None:
        query = query.filter(Job.user_id == user_id)
    rows = query.order_by(Job.user_id.asc(), Job.id.asc()).all(); kept: dict[tuple, Job] = {}; superseded = 0
    for job in rows:
        target = ("security", job.security_id) if job.security_id is not None else (("company", job.company_id) if job.company_id is not None else ("global", None))
        key = (job.user_id, job.job_type, target); current = kept.get(key)
        if current is None:
            kept[key] = job; continue
        if current.status == "QUEUED" and job.status == "RUNNING":
            current.status = "SUPERSEDED"; current.finished_at = utcnow(); current.result = {"reason": "duplicate_active_job", "kept_job_id": job.id}; kept[key] = job; superseded += 1
        elif job.status == "QUEUED":
            job.status = "SUPERSEDED"; job.finished_at = utcnow(); job.result = {"reason": "duplicate_active_job", "kept_job_id": current.id}; superseded += 1
    if superseded:
        db.session.commit()
    return superseded


def _calculation_runs_for_job(job: Job) -> list[CalculationRun]:
    rows = CalculationRun.query.filter_by(status="RUNNING", calculation_type=job.job_type).all()
    return [row for row in rows if int((row.inputs or {}).get("job_id") or 0) == job.id]


def _finish_open_attempt_records(job: Job, status: str, message: str) -> None:
    finished = utcnow()
    summary = {"reason": message, "job_status": status}
    for row in RefreshRun.query.filter_by(job_id=job.id, status="RUNNING").all():
        row.status = status
        row.summary = summary
        row.finished_at = finished
    for row in _calculation_runs_for_job(job):
        row.status = status
        row.outputs = summary
        row.finished_at = finished


def cancel_job(job: Job, *, reason: str = "Cancelled by CONTROL") -> int | None:
    """Move an active job to a terminal CANCELLED state without deleting produced data.

    Returns the recorded executor PID for a RUNNING job so the caller may
    best-effort terminate that worker after committing the cancellation.
    """
    if str(job.status or "").upper() not in ACTIVE_JOB_STATUSES:
        return None
    executor = dict((job.result or {}).get("_executor") or {})
    pid = int(executor.get("pid") or 0) or None
    now = utcnow()
    prior = dict(job.result or {})
    prior["cancelled"] = {"at": now.isoformat(), "reason": reason}
    job.result = prior
    job.status = "CANCELLED"
    job.error_message = reason
    job.finished_at = now
    job.locked_at = None
    _finish_open_attempt_records(job, "CANCELLED", reason)
    return pid


def terminate_job_executor(pid: int | None) -> bool:
    """Safely terminate only a verified Market Forensics CLI executor.

    On Linux/shared hosting we verify ownership and /proc command line before
    sending SIGTERM. If verification is unavailable, cancellation remains
    cooperative and the DB terminal state still wins.
    """
    if not pid or pid <= 1 or pid == os.getpid():
        return False
    proc = f"/proc/{int(pid)}"
    try:
        stat = os.stat(proc)
        if hasattr(os, "getuid") and stat.st_uid != os.getuid():
            return False
        with open(f"{proc}/cmdline", "rb") as handle:
            command = handle.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
        if "manage.py" not in command or "run-jobs" not in command:
            return False
        os.kill(int(pid), signal.SIGTERM)
        return True
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False


def _lease_seconds(job: Job, stale_after_minutes: int | None = None) -> int:
    if stale_after_minutes is not None:
        return max(1, int(stale_after_minutes)) * 60
    return int(JOB_LEASE_SECONDS.get(str(job.job_type).upper(), DEFAULT_JOB_LEASE_SECONDS))


def recover_stale_running_jobs(user_id: int | None = None, stale_after_minutes: int | None = None) -> int:
    """Recover dead workers without leaving RUNNING rows forever.

    A stale attempt is closed in audit tables. Retryable jobs return to QUEUED;
    exhausted jobs become FAILED. Discovery has a shorter lease because its
    hard execution deadline is 90 seconds.
    """
    now = utcnow()
    query = Job.query.filter(Job.status == "RUNNING", Job.locked_at.is_not(None))
    if user_id is not None:
        query = query.filter(Job.user_id == user_id)
    recovered = 0
    for job in query.all():
        lease = _lease_seconds(job, stale_after_minutes)
        if not job.locked_at or (now - job.locked_at).total_seconds() <= lease:
            continue
        recovered += 1
        message = f"Recovered stale RUNNING lease after {lease}s; previous executor is no longer trusted."
        _finish_open_attempt_records(job, "FAILED", message)
        job.locked_at = None
        job.error_message = message
        if int(job.attempts or 0) < int(job.max_attempts or 0):
            job.status = "QUEUED"
            job.started_at = None
            job.finished_at = None
            job.run_after = now
        else:
            job.status = "FAILED"
            job.finished_at = now
    if recovered:
        db.session.commit()
    return recovered


def _flow_row(period: FinancialPeriod, row: NormalizedFinancial) -> dict[str, Any]:
    return {
        "period_label": f"FY{period.fiscal_year}", "fiscal_year": period.fiscal_year,
        "revenue": row.revenue, "cogs": row.cogs, "gross_profit": row.gross_profit,
        "operating_expenses": row.operating_expenses, "operating_income": row.operating_income,
        "pretax_income": row.pretax_income, "income_tax": row.income_tax, "net_income": row.net_income,
        "cfo": row.cfo, "capex": row.capex, "fcf": row.fcf, "buybacks": row.buybacks, "dividends": row.dividends,
    }


def recalculate_company(company_id: int, coverage_id: int | None = None) -> dict[str, Any]:
    periods = FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").order_by(FinancialPeriod.fiscal_year.asc()).all()
    previous = None; metrics_out = []; calculated = 0
    for period in periods:
        row = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if not row:
            continue
        flow_row = _flow_row(period, row)
        enriched = flow_row | {"receivables": row.receivables, "inventory": row.inventory, "payables": row.payables, "cash": row.cash, "debt": row.debt}
        metrics_out.append({"fiscal_year": period.fiscal_year, "metrics": financial_metrics(enriched, previous)}); previous = enriched
        for flow_type, payload in (("INCOME_STATEMENT", build_income_statement_flow(flow_row)), ("CASH_FLOW", build_cash_flow(flow_row))):
            flow = FinancialFlow.query.filter_by(financial_period_id=period.id, flow_type=flow_type, calculation_version=CALCULATION_VERSION).first()
            if flow is None:
                flow = FinancialFlow(financial_period_id=period.id, flow_type=flow_type, calculation_version=CALCULATION_VERSION); db.session.add(flow)
            flow.payload = payload
        calculated += 1
    valuation = None
    if coverage_id:
        coverage = db.session.get(Coverage, coverage_id); model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).order_by(ValuationModel.id.desc()).first()
        if coverage and model:
            scenarios = {s.name.upper(): s for s in model.scenarios}
            if all(name in scenarios for name in ("BEAR", "BASE", "BULL")):
                market = latest_snapshot(coverage.security_id)
                valuation = calculate_valuation(bear=scenarios["BEAR"].equity_value_per_share, base=scenarios["BASE"].equity_value_per_share,
                    bull=scenarios["BULL"].equity_value_per_share, bear_probability=scenarios["BEAR"].probability,
                    base_probability=scenarios["BASE"].probability, bull_probability=scenarios["BULL"].probability,
                    current_price=market.price if market else None).as_dict()
                model.assumptions = dict(model.assumptions or {}) | {"latest_result": valuation}; model.calculation_version = CALCULATION_VERSION
    db.session.commit()
    cache = None
    autofill = None
    if coverage_id:
        coverage = db.session.get(Coverage, coverage_id)
        if coverage is not None:
            # Canonical ordering: statement/flow calculations -> current 0.3 valuation
            # and auto research -> one materialized Research cache. Never publish a
            # cache from pre-prefill scenarios and then update valuation behind it.
            autofill = prefill_coverage(coverage_id, coverage.user_id)
        from .research_cache import refresh_research_cache
        cache = refresh_research_cache(coverage_id)
    return {
        "periods": calculated,
        "metrics": metrics_out[-5:],
        "valuation": valuation,
        "autofill": autofill,
        "research_cache": {
            "event_id": cache.get("_event_id"),
            "generated_at": cache.get("_generated_at"),
            "conclusion": (cache.get("decision_lenses") or {}).get("research_conclusion"),
        } if cache else None,
    }


def _issue(company_id: int, period_id: int, code: str, message: str) -> None:
    row = DataQualityIssue.query.filter_by(company_id=company_id, object_type="financial_period", object_id=str(period_id), code=code, status="OPEN").first()
    if row is None:
        db.session.add(DataQualityIssue(company_id=company_id, object_type="financial_period", object_id=str(period_id), code=code, severity="FAIL", message=message))
    else:
        row.message = message; row.detected_at = utcnow()


def _deep_validation(company_id: int, coverage_id: int | None) -> dict[str, Any]:
    recalc = recalculate_company(company_id, coverage_id); checked = 0; issues = 0
    for period in FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").all():
        row = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if not row:
            _issue(company_id, period.id, "MISSING_NORMALIZED", f"FY{period.fiscal_year}: normalized row missing."); issues += 1; continue
        checked += 1; bridges = []
        if row.revenue is not None and row.cogs is not None and row.gross_profit is not None: bridges.append(("GROSS_PROFIT_BRIDGE", row.revenue - row.cogs, row.gross_profit))
        if row.gross_profit is not None and row.operating_expenses is not None and row.operating_income is not None: bridges.append(("OPERATING_BRIDGE", row.gross_profit - row.operating_expenses, row.operating_income))
        if row.cfo is not None and row.capex is not None and row.fcf is not None: bridges.append(("FCF_BRIDGE", row.cfo - row.capex, row.fcf))
        if row.assets is not None and row.liabilities is not None and row.equity is not None: bridges.append(("BALANCE_SHEET_BRIDGE", row.liabilities + row.equity, row.assets))
        for code, expected, actual in bridges:
            scale = max(Decimal("1"), abs(expected), abs(actual))
            if abs(expected - actual) > scale * Decimal("0.015"):
                _issue(company_id, period.id, code, f"FY{period.fiscal_year}: {code} failed reconciliation."); issues += 1
    db.session.commit(); return {"checked_periods": checked, "issues": issues, "recalculation": recalc}


def _management_filing_documents(cik: str, accn: str, primary: str, form: str, ua: str) -> list[dict[str, str]]:
    """Return a bounded set of SEC filing documents worth parsing for guidance."""
    cik_number = str(int(cik))
    accession_path = str(accn).replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession_path}"
    docs: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: str, kind: str) -> None:
        clean = str(name or "").strip()
        if not clean or clean in seen:
            return
        if not re.search(r"\.(?:html?|txt)$", clean, re.I):
            return
        seen.add(clean)
        docs.append({"name": clean, "url": f"{base}/{clean}", "kind": kind})

    add(primary, "PRIMARY")
    if str(form).upper() != "8-K":
        return docs[:1]

    # Earnings guidance is often filed as EX-99.1 rather than in the 8-K shell.
    try:
        response = requests.get(
            f"{base}/index.json",
            headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"},
            timeout=10,
        )
        if response.status_code == 200:
            items = (((response.json() or {}).get("directory") or {}).get("item") or [])
            candidates: list[tuple[int, str]] = []
            for item in items:
                name = str((item or {}).get("name") or "")
                low = name.lower()
                if not re.search(r"\.(?:html?)$", low):
                    continue
                score = 0
                if re.search(r"(?:ex(?:hibit)?[-_]?99(?:[-_.]?1)?|ex99|99[-_.]?1)", low):
                    score += 100
                if any(token in low for token in ("earn", "release", "press", "results", "guidance")):
                    score += 40
                if score:
                    candidates.append((-score, name))
            for _, name in sorted(candidates)[:4]:
                add(name, "EXHIBIT")
    except Exception:
        # Primary filing scan can still succeed. A later forced/versioned scan can
        # retry exhibit discovery without poisoning the filing permanently.
        pass
    return docs[:5]


def _management_scan(
    company: Company,
    security: Security,
    user_id: int,
    limit: int = 24,
    *,
    force: bool = False,
) -> dict[str, Any]:
    ua = sec_user_agent(user_id)
    meta = sec_ticker_meta(security.ticker, ua)
    submissions = sec_json(f"{SEC_DATA}/submissions/CIK{meta['cik']}.json", ua)
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    filed = recent.get("filingDate") or []
    docs = recent.get("primaryDocument") or []
    stored = promises_stored = guidance_scanned = documents_scanned = promises_found = 0
    skipped_current = scan_errors = 0

    original_actuals_stored = 0
    original_actuals_found = 0
    try:
        companyfacts = sec_json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json", ua)
        original_rows = original_actuals_from_companyfacts(
            companyfacts,
            str(meta.get("fiscal_year_end") or ""),
        )
        original_actuals_found = len(original_rows)
        original_actuals_stored = store_original_actuals(company.id, original_rows)
    except Exception:
        # Promise extraction should still run if Companyfacts is transiently
        # unavailable. Scoring will remain PENDING/EVIDENCE_ONLY until verified.
        scan_errors += 1

    for i, form in enumerate(forms[:max(1, min(limit, 100))]):
        if form not in {"10-K", "10-Q", "8-K", "DEF 14A"}:
            continue
        accn = str(accns[i] if i < len(accns) else "")
        if not accn:
            continue
        primary_doc = str(docs[i] if i < len(docs) else "")
        filing_date = str(filed[i] if i < len(filed) else "")
        accession_path = accn.replace("-", "")
        primary_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/{accession_path}/{primary_doc}"
            if primary_doc else ""
        )

        source = Source.query.filter_by(company_id=company.id, provider="SEC", accession_no=accn).first()
        if source is None:
            source = Source(
                company_id=company.id,
                provider="SEC",
                source_type="FILING",
                title=f"{security.ticker} {form} {filing_date}",
                url=primary_url,
                accession_no=accn,
                published_at=datetime.fromisoformat(filing_date) if filing_date else None,
                retrieved_at=utcnow(),
                meta={"form": form, "cik": meta["cik"]},
            )
            db.session.add(source)
            db.session.flush()
            db.session.add(Event(
                company_id=company.id,
                source_id=source.id,
                event_type=f"SEC_{form.replace(' ','_').replace('-','_')}",
                title=source.title,
                event_date=source.published_at or utcnow(),
                payload={"form": form, "accession_no": accn, "url": primary_url},
            ))
            stored += 1
        else:
            source.url = source.url or primary_url
            source.meta = dict(source.meta or {}) | {"form": form, "cik": meta["cik"]}

        prior_scans = Event.query.filter_by(
            company_id=company.id,
            source_id=source.id,
            event_type="MANAGEMENT_GUIDANCE_SCAN",
        ).order_by(Event.id.desc()).all()
        already_current = next(
            (
                row for row in prior_scans
                if str((row.payload or {}).get("scan_version") or "") == MANAGEMENT_SCAN_VERSION
            ),
            None,
        )
        if already_current is not None and not force:
            skipped_current += 1
            continue
        if form not in {"10-K", "10-Q", "8-K"}:
            continue

        filing_docs = _management_filing_documents(
            str(meta["cik"]), accn, primary_doc, form, ua
        )
        if not filing_docs:
            scan_errors += 1
            continue

        filing_extracted: list[dict[str, Any]] = []
        successful_docs: list[str] = []
        for document in filing_docs:
            try:
                response = requests.get(
                    document["url"],
                    headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"},
                    timeout=12,
                )
                if response.status_code != 200:
                    continue
                successful_docs.append(document["name"])
                documents_scanned += 1
                filing_extracted.extend(extract_promises(
                    html_to_text(response.text),
                    source_id=source.id,
                    document_url=document["url"],
                    document_name=document["name"],
                ))
            except Exception:
                scan_errors += 1
                continue

        if not successful_docs:
            # Never write a completion marker for an unfetched filing; it must be
            # eligible for retry after a transient SEC/network failure.
            continue

        # Deduplicate the same sentence/target copied into both the 8-K shell and
        # Exhibit 99.1 before storage.
        deduped: list[dict[str, Any]] = []
        fingerprints: set[str] = set()
        for row in filing_extracted:
            fp = str(row.get("fingerprint") or "")
            if fp and fp in fingerprints:
                continue
            if fp:
                fingerprints.add(fp)
            deduped.append(row)

        count = store_promises(company.id, deduped, source_id=source.id)
        promises_stored += count
        promises_found += len(deduped)
        guidance_scanned += 1
        db.session.add(Event(
            company_id=company.id,
            source_id=source.id,
            event_type="MANAGEMENT_GUIDANCE_SCAN",
            title=f"{security.ticker} guidance scan · {accn}",
            event_date=source.published_at or source.retrieved_at or utcnow(),
            payload={
                "form": form,
                "accession_no": accn,
                "scan_version": MANAGEMENT_SCAN_VERSION,
                "forced": bool(force),
                "documents_scanned": successful_docs,
                "promises_found": len(deduped),
                "promises_stored": count,
            },
        ))

    db.session.commit()
    return {
        "scan_version": MANAGEMENT_SCAN_VERSION,
        "filings_stored": stored,
        "guidance_filings_scanned": guidance_scanned,
        "documents_scanned": documents_scanned,
        "promises_found": promises_found,
        "promises_stored": promises_stored,
        "original_actuals_found": original_actuals_found,
        "original_actuals_stored": original_actuals_stored,
        "skipped_current_version": skipped_current,
        "scan_errors": scan_errors,
        "forced": bool(force),
        "cik": meta["cik"],
        "forms_considered": min(len(forms), limit),
    }

def _discovery(user_id: int) -> dict[str, Any]:
    """Market-wide scan plus cache-only ranking for current Coverage."""
    from .research_cache import cache_is_stale, latest_cache_map

    scan = market_scan(user_id)
    coverages = Coverage.query.filter(
        Coverage.user_id == user_id,
        Coverage.status != "ARCHIVED",
    ).all()
    coverage_ids = [row.id for row in coverages]
    caches = latest_cache_map(coverage_ids)
    model_rows = ValuationModel.query.filter(
        ValuationModel.coverage_id.in_(coverage_ids),
        ValuationModel.is_active.is_(True),
    ).order_by(ValuationModel.id.desc()).all() if coverage_ids else []
    models: dict[int, ValuationModel] = {}
    for model in model_rows:
        models.setdefault(model.coverage_id, model)

    security_ids = [row.security_id for row in coverages]
    securities = {
        row.id: row
        for row in Security.query.filter(Security.id.in_(security_ids)).all()
    } if security_ids else {}

    ranked = []
    for coverage in coverages:
        security = securities.get(coverage.security_id)
        if not security:
            continue
        cache = dict(caches.get(coverage.id) or {})
        readiness = dict(cache.get("readiness") or {})
        intelligence = dict(cache.get("intelligence") or {})
        valuation = dict(cache.get("valuation") or {})
        done = int(readiness.get("done") or 0)
        total = int(readiness.get("total") or 13)
        gap = intelligence.get("base_gap_pct")
        intrinsic_gap = (
            gap is not None
            and valuation_base_quality(valuation) == "INTRINSIC"
            and not cache_is_stale(cache, coverage, models.get(coverage.id))
        )
        score = done * 5 + (min(abs(float(gap)), 50) if intrinsic_gap else 0)
        ranked.append({
            "ticker": security.ticker,
            "readiness": f"{done}/{total}",
            "base_gap_pct": gap,
            "score": round(score, 2),
            "cache_ready": bool(cache),
        })
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return {
        "coverage_scanned": len(ranked),
        "ranked": ranked[:25],
        "market_scan": scan,
        "ranking_mode": "CACHE_ONLY",
    }

def _prime_research_cache(user_id: int) -> dict[str, Any]:
    queued = reused = 0
    for coverage in Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").all():
        security = db.session.get(Security, coverage.security_id)
        if not security:
            continue
        job = enqueue_job(
            "RECALCULATE",
            user_id=user_id,
            company_id=security.company_id,
            security_id=security.id,
            payload={"coverage_id": coverage.id},
            priority=95,
        )
        if getattr(job, "_mf_reused", False):
            reused += 1
        else:
            queued += 1
    return {"jobs_queued": queued, "jobs_reused": reused}


def _bulk(user_id: int) -> dict[str, Any]:
    sec_ready = provider_status(user_id).get("sec", False); queued = 0; reused = 0
    for coverage in Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").all():
        security = db.session.get(Security, coverage.security_id)
        if not security: continue
        specs = [("MARKET_REFRESH", 20), ("PRICE_HISTORY_REFRESH", 35), ("MACRO_REFRESH", 55), ("FINRA_IMPORT", 70), ("POSITIONING_REFRESH", 75), ("RECALCULATE", 95)]
        if sec_ready:
            specs.insert(1, ("SEC_INGEST", 40))
            specs.append(("MANAGEMENT_SCAN", 80))
        for kind, priority in specs:
            payload = {"coverage_id": coverage.id}
            if kind == "PRICE_HISTORY_REFRESH":
                payload["lookback_years"] = 3
            job = enqueue_job(kind, user_id=user_id, company_id=security.company_id, security_id=security.id, payload=payload, priority=priority)
            if getattr(job, "_mf_reused", False): reused += 1
            else: queued += 1
    portfolio_job = enqueue_job("PORTFOLIO_RECALCULATE", user_id=user_id, payload={}, priority=99)
    if getattr(portfolio_job, "_mf_reused", False): reused += 1
    else: queued += 1
    return {"jobs_queued": queued, "jobs_reused": reused, "sec_enabled": sec_ready}


def _stale(user_id: int) -> dict[str, Any]:
    """Queue only stale evidence, keeping shared-hosting work bounded."""
    now = utcnow()
    sec_ready = provider_status(user_id).get("sec", False)
    queued = reused = scanned = 0
    for coverage in Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").all():
        security = db.session.get(Security, coverage.security_id)
        if not security:
            continue
        scanned += 1
        specs: list[tuple[str, int]] = []
        market = latest_snapshot(security.id)
        if market is None or market.as_of is None or (now - market.as_of).total_seconds() > 30 * 60:
            specs.append(("MARKET_REFRESH", 20))
        latest_hist = (
            db.session.query(db.func.count(HistoricalPrice.id), db.func.max(HistoricalPrice.trade_date))
            .filter(HistoricalPrice.security_id == security.id)
            .first()
        )
        hist_count = int((latest_hist or (0, None))[0] or 0)
        hist_last = (latest_hist or (0, None))[1]
        if hist_count < 300 or hist_last is None or hist_last < (now.date() - timedelta(days=10)):
            specs.append(("PRICE_HISTORY_REFRESH", 35))
        if sec_ready:
            last_sec = RefreshRun.query.filter_by(company_id=security.company_id, refresh_type="SEC_INGEST", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
            if last_sec is None or last_sec.finished_at is None or (now - last_sec.finished_at).total_seconds() > 24 * 3600:
                specs.append(("SEC_INGEST", 40))
        last_macro = RefreshRun.query.filter_by(company_id=security.company_id, refresh_type="MACRO_REFRESH", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
        if last_macro is None or last_macro.finished_at is None or (now - last_macro.finished_at).total_seconds() > 24 * 3600:
            specs.append(("MACRO_REFRESH", 55))
        last_finra = RefreshRun.query.filter_by(security_id=security.id, refresh_type="FINRA_IMPORT", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
        if last_finra is None or last_finra.finished_at is None or (now - last_finra.finished_at).total_seconds() > 24 * 3600:
            specs.append(("FINRA_IMPORT", 70))
        last_positioning = RefreshRun.query.filter_by(security_id=security.id, refresh_type="POSITIONING_REFRESH", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
        if last_positioning is None or last_positioning.finished_at is None or (now - last_positioning.finished_at).total_seconds() > 24 * 3600:
            specs.append(("POSITIONING_REFRESH", 75))
        if sec_ready:
            last_management = RefreshRun.query.filter_by(company_id=security.company_id, refresh_type="MANAGEMENT_SCAN", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
            if last_management is None or last_management.finished_at is None or (now - last_management.finished_at).total_seconds() > 7 * 24 * 3600:
                specs.append(("MANAGEMENT_SCAN", 80))
        if specs:
            specs.append(("RECALCULATE", 90))
        for kind, priority in specs:
            payload = {"coverage_id": coverage.id}
            if kind == "PRICE_HISTORY_REFRESH":
                payload["lookback_years"] = 3
            job = enqueue_job(kind, user_id=user_id, company_id=security.company_id, security_id=security.id, payload=payload, priority=priority)
            if getattr(job, "_mf_reused", False):
                reused += 1
            else:
                queued += 1
    if queued:
        portfolio_job = enqueue_job("PORTFOLIO_RECALCULATE", user_id=user_id, payload={}, priority=99)
        if getattr(portfolio_job, "_mf_reused", False): reused += 1
        else: queued += 1
    return {"coverage_scanned": scanned, "jobs_queued": queued, "jobs_reused": reused, "sec_enabled": sec_ready}


def _store_finra_bundle(security: Security, bundle: dict[str, Any]) -> dict[str, Any]:
    result = {"daily_rows": 0, "short_interest_rows": 0, "threshold_rows": 0, "ats_rows": 0, "api_configured": bool(bundle.get("api_configured")), "errors": bundle.get("errors") or []}
    daily = list(bundle.get("daily_short_volume") or [])
    if daily:
        source = Source(company_id=security.company_id, provider="FINRA", source_type="REGSHO_DAILY_SHORT_VOLUME", title=f"{security.ticker} FINRA daily short-sale volume", url=FINRA_DAILY_CDN + "/", retrieved_at=utcnow(), meta={"rows": len(daily)})
        db.session.add(source); db.session.flush(); db.session.add(Event(company_id=security.company_id, source_id=source.id, event_type="FINRA_SHORT_VOLUME_SERIES", title=f"{security.ticker} FINRA daily short-volume refresh", event_date=utcnow(), payload={"rows": daily[-60:]})); result["daily_rows"] = len(daily)
    interest = list(bundle.get("short_interest") or [])
    if interest:
        source = Source(company_id=security.company_id, provider="FINRA", source_type="CONSOLIDATED_SHORT_INTEREST", title=f"{security.ticker} FINRA consolidated short interest", url="https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", retrieved_at=utcnow(), meta={"rows": len(interest)})
        db.session.add(source); db.session.flush(); db.session.add(Event(company_id=security.company_id, source_id=source.id, event_type="FINRA_SHORT_INTEREST_SERIES", title=f"{security.ticker} FINRA short-interest refresh", event_date=utcnow(), payload={"rows": interest[-36:]})); result["short_interest_rows"] = len(interest)
    threshold = list(bundle.get("threshold_history") or [])
    if threshold:
        source = Source(company_id=security.company_id, provider="FINRA", source_type="THRESHOLD_HISTORY", title=f"{security.ticker} FINRA threshold history", url="https://api.finra.org/data/group/otcMarket/name/thresholdList", retrieved_at=utcnow(), meta={"rows": len(threshold)})
        db.session.add(source); db.session.flush(); db.session.add(Event(company_id=security.company_id, source_id=source.id, event_type="FINRA_THRESHOLD_HISTORY", title=f"{security.ticker} FINRA threshold-list refresh", event_date=utcnow(), payload={"rows": threshold[-120:]})); result["threshold_rows"] = len(threshold)
    ats = list(bundle.get("weekly_otc") or [])
    if ats:
        source = Source(company_id=security.company_id, provider="FINRA", source_type="OTC_WEEKLY_SUMMARY", title=f"{security.ticker} FINRA ATS / non-ATS weekly summary", url="https://api.finra.org/data/group/otcMarket/name/weeklySummary", retrieved_at=utcnow(), meta={"rows": len(ats), "delayed": True})
        db.session.add(source); db.session.flush(); db.session.add(Event(company_id=security.company_id, source_id=source.id, event_type="FINRA_ATS_SERIES", title=f"{security.ticker} FINRA ATS / non-ATS refresh", event_date=utcnow(), payload={"rows": ats[-60:], "delayed": True})); result["ats_rows"] = len(ats)
    db.session.commit(); return result


def _queue_recalculate_after_evidence(job: Job, security: Security | None, coverage_id: int | None) -> int | None:
    if not coverage_id or security is None:
        return None
    queued = enqueue_job(
        "RECALCULATE",
        user_id=job.user_id,
        company_id=security.company_id,
        security_id=security.id,
        payload={"coverage_id": coverage_id},
        priority=95,
    )
    return queued.id


def _execute(job: Job) -> dict[str, Any]:
    kind = job.job_type.upper(); security = db.session.get(Security, job.security_id) if job.security_id else None; coverage_id = int((job.payload or {}).get("coverage_id") or 0) or None
    if kind == "MARKET_REFRESH":
        if not security: raise RuntimeError("Security not found")
        result = refresh_security_quote(security, job.user_id)
        if not result.ok: raise RuntimeError(result.message or "Market refresh failed")
        recalc_job_id = _queue_recalculate_after_evidence(job, security, coverage_id)
        return {"provider": result.provider, "price": result.price, "quality": result.quality, "as_of": result.as_of.isoformat() if result.as_of else None, "evidence": result.payload or {}, "recalculate_job_id": recalc_job_id}
    if kind == "PRICE_HISTORY_REFRESH":
        if not security: raise RuntimeError("Security not found")
        lookback_years = max(2, min(int((job.payload or {}).get("lookback_years") or 3), 10))
        return refresh_historical_prices(security, job.user_id, lookback_years)
    if kind == "SEC_INGEST":
        company = db.session.get(Company, job.company_id or (security.company_id if security else None))
        if not security or not company: raise RuntimeError("Company/security not found")
        result = refresh_company_fundamentals(company, security, job.user_id); result["recalculation"] = recalculate_company(company.id, coverage_id)
        if coverage_id:
            result["autofill"] = (result.get("recalculation") or {}).get("autofill")
        management_job = enqueue_job(
            "MANAGEMENT_SCAN",
            user_id=job.user_id,
            company_id=company.id,
            security_id=security.id,
            payload={"coverage_id": coverage_id, "limit": 36, "force": False},
            priority=80,
        )
        result["management_scan_job_id"] = management_job.id
        return result
    if kind == "MACRO_REFRESH":
        if not job.company_id: raise RuntimeError("company_id is required")
        result = refresh_macro_context(job.company_id)
        result["recalculate_job_id"] = _queue_recalculate_after_evidence(job, security, coverage_id)
        return result
    if kind == "RECALCULATE":
        if not job.company_id: raise RuntimeError("company_id is required")
        result = recalculate_company(job.company_id, coverage_id)
        current = current_row(job.company_id)
        economic = dict(((current or {}).get("quality") or {}).get("economic_reality") or {})
        if current and not economic and security and provider_status(job.user_id).get("sec"):
            sec_job = enqueue_job(
                "SEC_INGEST",
                user_id=job.user_id,
                company_id=job.company_id,
                security_id=security.id,
                payload={"coverage_id": coverage_id},
                priority=40,
            )
            result["economic_reality_refresh_job_id"] = sec_job.id
            result["economic_reality_state"] = "REFRESH_QUEUED"
        elif current and economic:
            result["economic_reality_state"] = "MATERIALIZED"
        return result
    if kind == "RESEARCH_PREFILL":
        if not coverage_id: raise RuntimeError("coverage_id is required")
        return prefill_coverage(coverage_id, job.user_id)
    if kind == "HISTORICAL_TEST":
        if not coverage_id: raise RuntimeError("coverage_id is required")
        result = run_historical_test(coverage_id, job.user_id, int((job.payload or {}).get("lookback_years") or 10))
        result["current_valuation_refresh"] = prefill_coverage(coverage_id, job.user_id)
        from .research_cache import refresh_research_cache
        cache = refresh_research_cache(coverage_id)
        result["research_cache"] = {"event_id": cache.get("_event_id"), "generated_at": cache.get("_generated_at")}
        return result
    if kind == "FINRA_IMPORT":
        if not security: raise RuntimeError("Security not found")
        payload = _store_finra_bundle(security, refresh_finra_bundle(security.ticker, job.user_id, int((job.payload or {}).get("lookback_days") or 35)))
        payload["recalculate_job_id"] = _queue_recalculate_after_evidence(job, security, coverage_id)
        return payload
    if kind == "POSITIONING_REFRESH":
        if not security: raise RuntimeError("Security not found")
        bundle = refresh_positioning_bundle(security.ticker, job.user_id)
        db.session.add(Event(
            company_id=security.company_id,
            event_type="ALPACA_POSITIONING",
            title=f"{security.ticker} institutional-flow / borrow / options positioning",
            event_date=utcnow(),
            payload=bundle,
        ))
        db.session.commit()
        bundle["recalculate_job_id"] = _queue_recalculate_after_evidence(job, security, coverage_id)
        return bundle
    if kind == "DEEP_VALIDATION":
        if not job.company_id: raise RuntimeError("company_id is required")
        return _deep_validation(job.company_id, coverage_id)
    if kind == "MANAGEMENT_SCAN":
        company = db.session.get(Company, job.company_id or (security.company_id if security else None))
        if not security or not company: raise RuntimeError("Company/security not found")
        payload = _management_scan(
            company,
            security,
            job.user_id,
            int((job.payload or {}).get("limit") or 36),
            force=bool((job.payload or {}).get("force")),
        )
        payload["recalculate_job_id"] = _queue_recalculate_after_evidence(job, security, coverage_id)
        return payload
    if kind == "DISCOVERY_SCAN": return _discovery(job.user_id)
    if kind == "CACHE_PRIME": return _prime_research_cache(job.user_id)
    if kind == "PORTFOLIO_RECALCULATE":
        from .portfolio_engine import refresh_portfolio_analytics
        return refresh_portfolio_analytics(job.user_id)
    if kind == "BULK_REFRESH": return _bulk(job.user_id)
    if kind == "STALE_REFRESH": return _stale(job.user_id)
    raise RuntimeError(f"Unknown job type: {kind}")


class JobDeadlineExceeded(TimeoutError):
    pass


def _job_deadline_seconds(job_type: str) -> int | None:
    # Full-market Discovery checks the entire eligible market, refreshes the
    # cached SEC-frame baseline when needed, then deeply verifies a bounded
    # finalist set. Keep a hard ceiling, but size it for the full-market contract.
    return 300 if str(job_type).upper() == "DISCOVERY_SCAN" else None


def _execute_with_deadline(job: Job):
    seconds = _job_deadline_seconds(job.job_type)
    if not seconds or not hasattr(signal, "SIGALRM"):
        return _execute(job)

    def _timeout_handler(signum, frame):
        raise JobDeadlineExceeded(f"{job.job_type} exceeded {seconds}s hard deadline")

    previous = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(seconds)
    try:
        return _execute(job)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def run_jobs(limit: int = 5, user_id: int | None = None) -> list[dict[str, Any]]:
    recover_stale_running_jobs(user_id=user_id); compact_queue(user_id=user_id); results = []
    for _ in range(max(1, min(int(limit), 50))):
        query = Job.query.filter(Job.status == "QUEUED", Job.run_after <= utcnow())
        if user_id is not None: query = query.filter(Job.user_id == user_id)
        query = query.order_by(Job.priority.asc(), Job.id.asc()); bind = db.session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", "") in {"mysql", "mariadb", "postgresql"}: query = query.with_for_update(skip_locked=True)
        job = query.first()
        if job is None: break
        job.status = "RUNNING"; job.locked_at = utcnow(); job.started_at = utcnow(); job.attempts += 1
        job.result = {"_executor": {"pid": os.getpid(), "started_at": job.started_at.isoformat()}}
        db.session.commit()
        refresh = RefreshRun(company_id=job.company_id, security_id=job.security_id, job_id=job.id, refresh_type=job.job_type, status="RUNNING", started_at=job.started_at)
        calc_coverage = int((job.payload or {}).get("coverage_id") or 0) or None
        calc = CalculationRun(coverage_id=calc_coverage, calculation_type=job.job_type, calculation_version=CALCULATION_VERSION,
                              inputs={"job_id": job.id, "payload": job.payload or {}}, status="RUNNING", started_at=job.started_at)
        db.session.add_all([refresh, calc]); db.session.commit(); started = time.perf_counter()
        try:
            payload = _execute_with_deadline(job)
            db.session.refresh(job)
            finished = utcnow()
            if job.status == "CANCELLED":
                refresh.status = "CANCELLED"; refresh.summary = {"reason": job.error_message or "Cancelled"}; refresh.finished_at = finished
                calc.status = "CANCELLED"; calc.outputs = {"reason": job.error_message or "Cancelled"}; calc.finished_at = finished
            else:
                job.status = "DONE"; job.result = payload; job.error_message = ""; job.finished_at = finished; job.locked_at = None
                refresh.status = "DONE"; refresh.summary = payload; refresh.finished_at = finished; calc.status = "DONE"; calc.outputs = payload; calc.finished_at = finished
        except Exception as exc:
            db.session.refresh(job)
            error_id = uuid.uuid4().hex[:12]; finished = utcnow()
            if job.status == "CANCELLED":
                refresh.status = "CANCELLED"; refresh.summary = {"reason": job.error_message or "Cancelled"}; refresh.finished_at = finished
                calc.status = "CANCELLED"; calc.outputs = {"reason": job.error_message or "Cancelled"}; calc.finished_at = finished
            else:
                job.error_message = f"{type(exc).__name__}: {exc}"[:4000]; job.finished_at = finished
                if isinstance(exc, JobDeadlineExceeded):
                    job.status = "FAILED"; job.locked_at = None
                elif job.attempts < job.max_attempts:
                    job.status = "QUEUED"; job.run_after = utcnow() + timedelta(minutes=min(60, 5 * job.attempts)); job.locked_at = None; job.finished_at = None
                else:
                    job.status = "FAILED"; job.locked_at = None
                refresh.status = "FAILED"; refresh.error_id = error_id; refresh.summary = {"error": job.error_message}; refresh.finished_at = finished
                calc.status = "FAILED"; calc.error_id = error_id; calc.outputs = {"error": job.error_message}; calc.finished_at = finished
        elapsed = (time.perf_counter() - started) * 1000; calc.elapsed_ms = Decimal(str(round(elapsed, 3))); db.session.commit()
        results.append({"job_id": job.id, "status": job.status, "attempts": job.attempts, "max_attempts": job.max_attempts, "elapsed_ms": round(elapsed, 1), "error": job.error_message})
    return results


__all__ = [
    "enqueue_job", "run_jobs", "cancel_job", "terminate_job_executor",
    "recover_stale_running_jobs", "compact_queue", "ACTIVE_JOB_STATUSES",
]
