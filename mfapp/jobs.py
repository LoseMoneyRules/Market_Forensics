from __future__ import annotations

import csv
import io
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests

from .calculations import CALCULATION_VERSION, build_cash_flow, build_income_statement_flow, calculate_valuation, financial_metrics
from .core_models import (
    Alert, CalculationRun, Company, Coverage, DataQualityIssue, Event, FinancialFlow,
    FinancialPeriod, Job, NormalizedFinancial, RefreshRun, Security, Source,
    ValuationModel,
)
from .data_providers import latest_snapshot, provider_status, refresh_security_quote
from .extensions import db
from .secdata import SEC_DATA, _json as sec_json, _ticker_meta as sec_ticker_meta, _ua as sec_user_agent, refresh_company_fundamentals


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_job(job_type: str, *, user_id: int, company_id: int | None = None, security_id: int | None = None,
                payload: dict[str, Any] | None = None, priority: int = 100, run_after: datetime | None = None) -> Job:
    job = Job(job_type=str(job_type).upper(), status="QUEUED", priority=priority, user_id=user_id,
              company_id=company_id, security_id=security_id, payload=payload or {}, run_after=run_after or utcnow())
    db.session.add(job); db.session.commit(); return job


def _flow_row(period: FinancialPeriod, n: NormalizedFinancial) -> dict[str, Any]:
    return {
        "period_label": f"FY{period.fiscal_year}", "fiscal_year": period.fiscal_year,
        "revenue": n.revenue, "cogs": n.cogs, "gross_profit": n.gross_profit,
        "operating_expenses": n.operating_expenses, "operating_income": n.operating_income,
        "pretax_income": n.pretax_income, "income_tax": n.income_tax, "net_income": n.net_income,
        "cfo": n.cfo, "capex": n.capex, "fcf": n.fcf, "buybacks": n.buybacks, "dividends": n.dividends,
    }


def recalculate_company(company_id: int, coverage_id: int | None = None) -> dict[str, Any]:
    periods = FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").order_by(FinancialPeriod.fiscal_year.asc()).all()
    previous = None; metrics_out = []; calculated = 0
    for period in periods:
        n = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if not n: continue
        row = _flow_row(period, n)
        enriched = row | {"receivables": n.receivables, "inventory": n.inventory, "payables": n.payables, "cash": n.cash, "debt": n.debt}
        metrics_out.append({"fiscal_year": period.fiscal_year, "metrics": financial_metrics(enriched, previous)})
        previous = enriched
        for flow_type, payload in (("INCOME_STATEMENT", build_income_statement_flow(row)), ("CASH_FLOW", build_cash_flow(row))):
            flow = FinancialFlow.query.filter_by(financial_period_id=period.id, flow_type=flow_type, calculation_version=CALCULATION_VERSION).first()
            if flow is None:
                flow = FinancialFlow(financial_period_id=period.id, flow_type=flow_type, calculation_version=CALCULATION_VERSION)
                db.session.add(flow)
            flow.payload = payload
        calculated += 1
    valuation = None
    if coverage_id:
        coverage = db.session.get(Coverage, coverage_id)
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).order_by(ValuationModel.id.desc()).first()
        if coverage and model:
            scenarios = {s.name.upper(): s for s in model.scenarios}
            if all(name in scenarios for name in ("BEAR", "BASE", "BULL")):
                market = latest_snapshot(coverage.security_id)
                valuation = calculate_valuation(
                    bear=scenarios["BEAR"].equity_value_per_share, base=scenarios["BASE"].equity_value_per_share,
                    bull=scenarios["BULL"].equity_value_per_share, bear_probability=scenarios["BEAR"].probability,
                    base_probability=scenarios["BASE"].probability, bull_probability=scenarios["BULL"].probability,
                    current_price=market.price if market else None,
                ).as_dict()
                model.assumptions = dict(model.assumptions or {}) | {"latest_result": valuation}
                model.calculation_version = CALCULATION_VERSION
    db.session.commit()
    return {"periods": calculated, "metrics": metrics_out[-5:], "valuation": valuation}


def _finra(ticker: str, lookback_days: int = 35) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []; today = date.today()
    for offset in range(max(1, min(lookback_days, 90)), -1, -1):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5: continue
        try:
            r = requests.get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day:%Y%m%d}.txt", timeout=15)
            if r.status_code != 200: continue
            for row in csv.DictReader(io.StringIO(r.text), delimiter="|"):
                if str(row.get("Symbol") or "").upper() != ticker.upper(): continue
                short = float(row.get("ShortVolume") or 0); exempt = float(row.get("ShortExemptVolume") or 0); total = float(row.get("TotalVolume") or 0)
                rows.append({"trade_date": day.isoformat(), "short_volume": short, "short_exempt_volume": exempt,
                             "total_reported_volume": total, "short_pct": ((short + exempt) / total) if total else None,
                             "market": row.get("Market") or ""})
        except Exception:
            continue
    return rows


def _issue(company_id: int, period_id: int, code: str, message: str) -> None:
    row = DataQualityIssue.query.filter_by(company_id=company_id, object_type="financial_period", object_id=str(period_id), code=code, status="OPEN").first()
    if row is None:
        db.session.add(DataQualityIssue(company_id=company_id, object_type="financial_period", object_id=str(period_id), code=code, severity="FAIL", message=message))
    else:
        row.message = message; row.detected_at = utcnow()


def _deep_validation(company_id: int, coverage_id: int | None) -> dict[str, Any]:
    recalc = recalculate_company(company_id, coverage_id); checked = 0; issues = 0
    for period in FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").all():
        n = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if not n:
            _issue(company_id, period.id, "MISSING_NORMALIZED", f"FY{period.fiscal_year}: normalized row missing."); issues += 1; continue
        checked += 1
        bridges = []
        if n.revenue is not None and n.cogs is not None and n.gross_profit is not None:
            bridges.append(("GROSS_PROFIT_BRIDGE", n.revenue - n.cogs, n.gross_profit))
        if n.gross_profit is not None and n.operating_expenses is not None and n.operating_income is not None:
            bridges.append(("OPERATING_BRIDGE", n.gross_profit - n.operating_expenses, n.operating_income))
        if n.cfo is not None and n.capex is not None and n.fcf is not None:
            bridges.append(("FCF_BRIDGE", n.cfo - n.capex, n.fcf))
        if n.assets is not None and n.liabilities is not None and n.equity is not None:
            bridges.append(("BALANCE_SHEET_BRIDGE", n.liabilities + n.equity, n.assets))
        for code, expected, actual in bridges:
            scale = max(Decimal("1"), abs(expected), abs(actual))
            if abs(expected - actual) > scale * Decimal("0.015"):
                _issue(company_id, period.id, code, f"FY{period.fiscal_year}: {code} failed reconciliation."); issues += 1
    db.session.commit(); return {"checked_periods": checked, "issues": issues, "recalculation": recalc}


def _management_scan(company: Company, security: Security, user_id: int, limit: int = 24) -> dict[str, Any]:
    ua = sec_user_agent(user_id); meta = sec_ticker_meta(security.ticker, ua)
    submissions = sec_json(f"{SEC_DATA}/submissions/CIK{meta['cik']}.json", ua)
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []; accns = recent.get("accessionNumber") or []; filed = recent.get("filingDate") or []; docs = recent.get("primaryDocument") or []
    stored = 0
    for i, form in enumerate(forms[:max(1, min(limit, 100))]):
        if form not in {"10-K", "10-Q", "8-K", "DEF 14A"}: continue
        accn = str(accns[i] if i < len(accns) else "")
        if not accn or Source.query.filter_by(company_id=company.id, provider="SEC", accession_no=accn).first(): continue
        doc = str(docs[i] if i < len(docs) else ""); filing_date = str(filed[i] if i < len(filed) else "")
        accession_path = accn.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/{accession_path}/{doc}" if doc else ""
        source = Source(company_id=company.id, provider="SEC", source_type="FILING", title=f"{security.ticker} {form} {filing_date}", url=url, accession_no=accn,
                        published_at=datetime.fromisoformat(filing_date) if filing_date else None, retrieved_at=utcnow(), meta={"form": form, "cik": meta["cik"]})
        db.session.add(source); db.session.flush()
        db.session.add(Event(company_id=company.id, source_id=source.id, event_type=f"SEC_{form.replace(' ','_').replace('-','_')}", title=source.title,
                             event_date=source.published_at or utcnow(), payload={"form": form, "accession_no": accn, "url": url}))
        stored += 1
    db.session.commit(); return {"filings_stored": stored, "cik": meta["cik"], "forms_scanned": min(len(forms), limit)}


def _discovery(user_id: int) -> dict[str, Any]:
    from .services import readiness, valuation_result
    ranked = []
    for coverage in Coverage.query.filter_by(user_id=user_id).all():
        security = db.session.get(Security, coverage.security_id)
        if not security: continue
        ready = readiness(coverage); val = valuation_result(coverage)
        price, base = val.get("current_price"), val.get("base")
        gap = ((float(base) / float(price) - 1) * 100) if base is not None and price not in (None, 0) else None
        score = ready["done"] * 5 + (min(abs(gap), 50) if gap is not None else 0)
        ranked.append({"ticker": security.ticker, "readiness": f"{ready['done']}/{ready['total']}", "base_gap_pct": gap, "score": round(score, 2)})
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return {"coverage_scanned": len(ranked), "ranked": ranked[:25]}


def _bulk(user_id: int) -> dict[str, Any]:
    sec_ready = provider_status(user_id).get("sec", False); queued = 0
    for coverage in Coverage.query.filter_by(user_id=user_id).all():
        security = db.session.get(Security, coverage.security_id)
        if not security: continue
        specs = [("MARKET_REFRESH", 20), ("RECALCULATE", 60), ("FINRA_IMPORT", 70)]
        if sec_ready: specs.insert(1, ("SEC_INGEST", 40))
        for kind, priority in specs:
            if Job.query.filter_by(job_type=kind, user_id=user_id, security_id=security.id, status="QUEUED").first(): continue
            db.session.add(Job(job_type=kind, status="QUEUED", priority=priority, user_id=user_id, company_id=security.company_id,
                               security_id=security.id, payload={"coverage_id": coverage.id}, run_after=utcnow())); queued += 1
    db.session.commit(); return {"jobs_queued": queued, "sec_enabled": sec_ready}


def _execute(job: Job) -> dict[str, Any]:
    kind = job.job_type.upper(); security = db.session.get(Security, job.security_id) if job.security_id else None
    if kind == "MARKET_REFRESH":
        if not security: raise RuntimeError("Security not found")
        result = refresh_security_quote(security, job.user_id)
        if not result.ok: raise RuntimeError(result.message or "Market refresh failed")
        return {"provider": result.provider, "price": result.price, "as_of": result.as_of.isoformat() if result.as_of else None}
    if kind == "SEC_INGEST":
        company = db.session.get(Company, job.company_id or (security.company_id if security else None))
        if not security or not company: raise RuntimeError("Company/security not found")
        result = refresh_company_fundamentals(company, security, job.user_id)
        result["recalculation"] = recalculate_company(company.id, int((job.payload or {}).get("coverage_id") or 0) or None); return result
    if kind == "RECALCULATE":
        if not job.company_id: raise RuntimeError("company_id is required")
        return recalculate_company(job.company_id, int((job.payload or {}).get("coverage_id") or 0) or None)
    if kind == "FINRA_IMPORT":
        if not security: raise RuntimeError("Security not found")
        rows = _finra(security.ticker, int((job.payload or {}).get("lookback_days") or 35))
        source = Source(company_id=security.company_id, provider="FINRA", source_type="REGSHO_DAILY_SHORT_VOLUME", title=f"{security.ticker} FINRA daily short volume",
                        url="https://cdn.finra.org/equity/regsho/daily/", retrieved_at=utcnow(), meta={"rows": len(rows)})
        db.session.add(source); db.session.flush(); db.session.add(Event(company_id=security.company_id, source_id=source.id, event_type="FINRA_SHORT_VOLUME_SERIES",
            title=f"{security.ticker} FINRA short-volume refresh", event_date=utcnow(), payload={"rows": rows[-30:]})); db.session.commit(); return {"rows": len(rows), "source_id": source.id}
    if kind == "DEEP_VALIDATION":
        if not job.company_id: raise RuntimeError("company_id is required")
        return _deep_validation(job.company_id, int((job.payload or {}).get("coverage_id") or 0) or None)
    if kind == "MANAGEMENT_SCAN":
        company = db.session.get(Company, job.company_id or (security.company_id if security else None))
        if not security or not company: raise RuntimeError("Company/security not found")
        return _management_scan(company, security, job.user_id, int((job.payload or {}).get("limit") or 24))
    if kind == "DISCOVERY_SCAN": return _discovery(job.user_id)
    if kind == "BULK_REFRESH": return _bulk(job.user_id)
    raise RuntimeError(f"Unknown job type: {kind}")


def run_jobs(limit: int = 5) -> list[dict[str, Any]]:
    results = []
    for _ in range(max(1, min(int(limit), 50))):
        job = Job.query.filter(Job.status == "QUEUED", Job.run_after <= utcnow()).order_by(Job.priority.asc(), Job.id.asc()).first()
        if job is None: break
        job.status = "RUNNING"; job.locked_at = utcnow(); job.started_at = utcnow(); job.attempts += 1; db.session.commit()
        refresh = RefreshRun(company_id=job.company_id, security_id=job.security_id, job_id=job.id, refresh_type=job.job_type, status="RUNNING", started_at=job.started_at)
        calc = CalculationRun(coverage_id=int((job.payload or {}).get("coverage_id") or 0) or None, calculation_type=job.job_type, calculation_version=CALCULATION_VERSION,
                              inputs={"job_id": job.id, "payload": job.payload or {}}, status="RUNNING", started_at=job.started_at)
        db.session.add_all([refresh, calc]); db.session.commit(); started = time.perf_counter()
        try:
            payload = _execute(job); finished = utcnow(); job.status = "DONE"; job.result = payload; job.error_message = ""; job.finished_at = finished
            refresh.status = "DONE"; refresh.summary = payload; refresh.finished_at = finished; calc.status = "DONE"; calc.outputs = payload; calc.finished_at = finished
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]; finished = utcnow(); job.error_message = f"{type(exc).__name__}: {exc}"[:4000]; job.finished_at = finished
            if job.attempts < job.max_attempts:
                job.status = "QUEUED"; job.run_after = utcnow() + timedelta(minutes=min(60, 5 * job.attempts))
            else: job.status = "FAILED"
            refresh.status = "FAILED"; refresh.error_id = error_id; refresh.summary = {"error": job.error_message}; refresh.finished_at = finished
            calc.status = "FAILED"; calc.error_id = error_id; calc.outputs = {"error": job.error_message}; calc.finished_at = finished
        elapsed = (time.perf_counter() - started) * 1000; calc.elapsed_ms = Decimal(str(round(elapsed, 3))); db.session.commit()
        results.append({"job_id": job.id, "status": job.status, "elapsed_ms": round(elapsed, 1), "error": job.error_message})
    return results
