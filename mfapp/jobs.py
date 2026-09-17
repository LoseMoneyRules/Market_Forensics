from __future__ import annotations

import time
import uuid
import requests
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .autofill import prefill_coverage
from .calculations import CALCULATION_VERSION, build_cash_flow, build_income_statement_flow, calculate_valuation, financial_metrics
from .core_models import (
    Alert, CalculationRun, Company, Coverage, DataQualityIssue, Event, FinancialFlow,
    FinancialPeriod, Job, NormalizedFinancial, RefreshRun, Security, Source,
    ValuationModel,
)
from .data_providers import latest_snapshot, provider_status, refresh_security_quote
from .extensions import db
from .finra import FINRA_DAILY_CDN, refresh_bundle as refresh_finra_bundle
from .historical_engine import run_historical_test
from .management_promises import extract_promises, html_to_text, store_promises
from .positioning import refresh_positioning_bundle
from .secdata import SEC_DATA, _json as sec_json, _ticker_meta as sec_ticker_meta, _ua as sec_user_agent, refresh_company_fundamentals

ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")


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


def recover_stale_running_jobs(user_id: int | None = None, stale_after_minutes: int = 30) -> int:
    cutoff = utcnow() - timedelta(minutes=max(5, int(stale_after_minutes)))
    query = Job.query.filter(Job.status == "RUNNING", Job.locked_at.is_not(None), Job.locked_at < cutoff)
    if user_id is not None:
        query = query.filter(Job.user_id == user_id)
    rows = query.all()
    for job in rows:
        job.status = "QUEUED"; job.locked_at = None; job.started_at = None; job.run_after = utcnow()
    if rows:
        db.session.commit()
    return len(rows)


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
    db.session.commit(); return {"periods": calculated, "metrics": metrics_out[-5:], "valuation": valuation}


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


def _management_scan(company: Company, security: Security, user_id: int, limit: int = 24) -> dict[str, Any]:
    ua = sec_user_agent(user_id); meta = sec_ticker_meta(security.ticker, ua); submissions = sec_json(f"{SEC_DATA}/submissions/CIK{meta['cik']}.json", ua)
    recent = (submissions.get("filings") or {}).get("recent") or {}; forms = recent.get("form") or []; accns = recent.get("accessionNumber") or []; filed = recent.get("filingDate") or []; docs = recent.get("primaryDocument") or []; stored = 0; promises_stored = 0
    for i, form in enumerate(forms[:max(1, min(limit, 100))]):
        if form not in {"10-K", "10-Q", "8-K", "DEF 14A"}: continue
        accn = str(accns[i] if i < len(accns) else "")
        if not accn or Source.query.filter_by(company_id=company.id, provider="SEC", accession_no=accn).first(): continue
        doc = str(docs[i] if i < len(docs) else ""); filing_date = str(filed[i] if i < len(filed) else ""); accession_path = accn.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/{accession_path}/{doc}" if doc else ""
        source = Source(company_id=company.id, provider="SEC", source_type="FILING", title=f"{security.ticker} {form} {filing_date}", url=url, accession_no=accn,
                        published_at=datetime.fromisoformat(filing_date) if filing_date else None, retrieved_at=utcnow(), meta={"form": form, "cik": meta["cik"]})
        db.session.add(source); db.session.flush(); db.session.add(Event(company_id=company.id, source_id=source.id, event_type=f"SEC_{form.replace(' ','_').replace('-','_')}", title=source.title, event_date=source.published_at or utcnow(), payload={"form": form, "accession_no": accn, "url": url})); stored += 1
        if url and form in {"10-K", "10-Q", "8-K"}:
            try:
                response = requests.get(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, timeout=12)
                if response.status_code == 200:
                    extracted = extract_promises(html_to_text(response.text), source_id=source.id)
                    promises_stored += store_promises(company.id, extracted, source_id=source.id)
            except Exception:
                pass
    db.session.commit(); return {"filings_stored": stored, "promises_stored": promises_stored, "cik": meta["cik"], "forms_scanned": min(len(forms), limit)}


def _discovery(user_id: int) -> dict[str, Any]:
    from .services import readiness, valuation_result
    ranked = []
    for coverage in Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").all():
        security = db.session.get(Security, coverage.security_id)
        if not security: continue
        ready = readiness(coverage); val = valuation_result(coverage); price, base = val.get("current_price"), val.get("base")
        gap = ((float(base) / float(price) - 1) * 100) if base is not None and price not in (None, 0) else None
        score = ready["done"] * 5 + (min(abs(gap), 50) if gap is not None else 0); ranked.append({"ticker": security.ticker, "readiness": f"{ready['done']}/{ready['total']}", "base_gap_pct": gap, "score": round(score, 2)})
    ranked.sort(key=lambda row: row["score"], reverse=True); return {"coverage_scanned": len(ranked), "ranked": ranked[:25]}


def _bulk(user_id: int) -> dict[str, Any]:
    sec_ready = provider_status(user_id).get("sec", False); queued = 0; reused = 0
    for coverage in Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").all():
        security = db.session.get(Security, coverage.security_id)
        if not security: continue
        specs = [("MARKET_REFRESH", 20), ("RECALCULATE", 60), ("FINRA_IMPORT", 70)]
        if sec_ready: specs.insert(1, ("SEC_INGEST", 40))
        for kind, priority in specs:
            job = enqueue_job(kind, user_id=user_id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=priority)
            if getattr(job, "_mf_reused", False): reused += 1
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
        if sec_ready:
            last_sec = RefreshRun.query.filter_by(company_id=security.company_id, refresh_type="SEC_INGEST", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
            if last_sec is None or last_sec.finished_at is None or (now - last_sec.finished_at).total_seconds() > 24 * 3600:
                specs.append(("SEC_INGEST", 40))
        last_finra = RefreshRun.query.filter_by(security_id=security.id, refresh_type="FINRA_IMPORT", status="DONE").order_by(RefreshRun.finished_at.desc()).first()
        if last_finra is None or last_finra.finished_at is None or (now - last_finra.finished_at).total_seconds() > 24 * 3600:
            specs.append(("FINRA_IMPORT", 70))
        if specs:
            specs.append(("RECALCULATE", 80))
        for kind, priority in specs:
            job = enqueue_job(kind, user_id=user_id, company_id=security.company_id, security_id=security.id, payload={"coverage_id": coverage.id}, priority=priority)
            if getattr(job, "_mf_reused", False):
                reused += 1
            else:
                queued += 1
    return {"coverage_scanned": scanned, "jobs_queued": queued, "jobs_reused": reused, "sec_enabled": sec_ready}


def _store_finra_bundle(security: Security, bundle: dict[str, Any]) -> dict[str, Any]:
    result = {"daily_rows": 0, "short_interest_rows": 0, "threshold_rows": 0, "api_configured": bool(bundle.get("api_configured")), "errors": bundle.get("errors") or []}
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
    db.session.commit(); return result


def _execute(job: Job) -> dict[str, Any]:
    kind = job.job_type.upper(); security = db.session.get(Security, job.security_id) if job.security_id else None; coverage_id = int((job.payload or {}).get("coverage_id") or 0) or None
    if kind == "MARKET_REFRESH":
        if not security: raise RuntimeError("Security not found")
        result = refresh_security_quote(security, job.user_id)
        if not result.ok: raise RuntimeError(result.message or "Market refresh failed")
        return {"provider": result.provider, "price": result.price, "quality": result.quality, "as_of": result.as_of.isoformat() if result.as_of else None, "evidence": result.payload or {}}
    if kind == "SEC_INGEST":
        company = db.session.get(Company, job.company_id or (security.company_id if security else None))
        if not security or not company: raise RuntimeError("Company/security not found")
        result = refresh_company_fundamentals(company, security, job.user_id); result["recalculation"] = recalculate_company(company.id, coverage_id)
        if coverage_id: result["autofill"] = prefill_coverage(coverage_id, job.user_id)
        return result
    if kind == "RECALCULATE":
        if not job.company_id: raise RuntimeError("company_id is required")
        result = recalculate_company(job.company_id, coverage_id)
        if coverage_id: result["autofill"] = prefill_coverage(coverage_id, job.user_id)
        return result
    if kind == "RESEARCH_PREFILL":
        if not coverage_id: raise RuntimeError("coverage_id is required")
        return prefill_coverage(coverage_id, job.user_id)
    if kind == "HISTORICAL_TEST":
        if not coverage_id: raise RuntimeError("coverage_id is required")
        result = run_historical_test(coverage_id, job.user_id, int((job.payload or {}).get("lookback_years") or 10))
        result["current_valuation_refresh"] = prefill_coverage(coverage_id, job.user_id)
        return result
    if kind == "FINRA_IMPORT":
        if not security: raise RuntimeError("Security not found")
        return _store_finra_bundle(security, refresh_finra_bundle(security.ticker, job.user_id, int((job.payload or {}).get("lookback_days") or 35)))
    if kind == "POSITIONING_REFRESH":
        if not security: raise RuntimeError("Security not found")
        bundle = refresh_positioning_bundle(security.ticker, job.user_id)
        db.session.add(Event(
            company_id=security.company_id,
            event_type="ALPACA_POSITIONING",
            title=f"{security.ticker} borrow/options positioning",
            event_date=utcnow(),
            payload=bundle,
        ))
        db.session.commit()
        return bundle
    if kind == "DEEP_VALIDATION":
        if not job.company_id: raise RuntimeError("company_id is required")
        return _deep_validation(job.company_id, coverage_id)
    if kind == "MANAGEMENT_SCAN":
        company = db.session.get(Company, job.company_id or (security.company_id if security else None))
        if not security or not company: raise RuntimeError("Company/security not found")
        return _management_scan(company, security, job.user_id, int((job.payload or {}).get("limit") or 24))
    if kind == "DISCOVERY_SCAN": return _discovery(job.user_id)
    if kind == "BULK_REFRESH": return _bulk(job.user_id)
    if kind == "STALE_REFRESH": return _stale(job.user_id)
    raise RuntimeError(f"Unknown job type: {kind}")


def run_jobs(limit: int = 5, user_id: int | None = None) -> list[dict[str, Any]]:
    recover_stale_running_jobs(user_id=user_id); compact_queue(user_id=user_id); results = []
    for _ in range(max(1, min(int(limit), 50))):
        query = Job.query.filter(Job.status == "QUEUED", Job.run_after <= utcnow())
        if user_id is not None: query = query.filter(Job.user_id == user_id)
        query = query.order_by(Job.priority.asc(), Job.id.asc()); bind = db.session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", "") in {"mysql", "mariadb", "postgresql"}: query = query.with_for_update(skip_locked=True)
        job = query.first()
        if job is None: break
        job.status = "RUNNING"; job.locked_at = utcnow(); job.started_at = utcnow(); job.attempts += 1; db.session.commit()
        refresh = RefreshRun(company_id=job.company_id, security_id=job.security_id, job_id=job.id, refresh_type=job.job_type, status="RUNNING", started_at=job.started_at)
        calc_coverage = int((job.payload or {}).get("coverage_id") or 0) or None
        calc = CalculationRun(coverage_id=calc_coverage, calculation_type=job.job_type, calculation_version=CALCULATION_VERSION,
                              inputs={"job_id": job.id, "payload": job.payload or {}}, status="RUNNING", started_at=job.started_at)
        db.session.add_all([refresh, calc]); db.session.commit(); started = time.perf_counter()
        try:
            payload = _execute(job); finished = utcnow(); job.status = "DONE"; job.result = payload; job.error_message = ""; job.finished_at = finished
            refresh.status = "DONE"; refresh.summary = payload; refresh.finished_at = finished; calc.status = "DONE"; calc.outputs = payload; calc.finished_at = finished
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]; finished = utcnow(); job.error_message = f"{type(exc).__name__}: {exc}"[:4000]; job.finished_at = finished
            if job.attempts < job.max_attempts:
                job.status = "QUEUED"; job.run_after = utcnow() + timedelta(minutes=min(60, 5 * job.attempts)); job.locked_at = None
            else: job.status = "FAILED"
            refresh.status = "FAILED"; refresh.error_id = error_id; refresh.summary = {"error": job.error_message}; refresh.finished_at = finished
            calc.status = "FAILED"; calc.error_id = error_id; calc.outputs = {"error": job.error_message}; calc.finished_at = finished
        elapsed = (time.perf_counter() - started) * 1000; calc.elapsed_ms = Decimal(str(round(elapsed, 3))); db.session.commit()
        results.append({"job_id": job.id, "status": job.status, "attempts": job.attempts, "max_attempts": job.max_attempts, "elapsed_ms": round(elapsed, 1), "error": job.error_message})
    return results
