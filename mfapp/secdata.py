from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import math
from typing import Any, Iterable

import requests

from .extensions import db
from .data_providers import get_secret
from .core_models import Company, DataQualityIssue, FinancialPeriod, NormalizedFinancial, Provenance, RawFinancialFact, Security, Source

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"

DURATION_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForAdditionsToPropertyPlantAndEquipment"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
}
INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "payables": ["AccountsPayableCurrent"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "debt": ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndFinanceLeaseObligations", "LongTermDebt"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
}


class SECRefreshError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ua(user_id: int) -> str:
    value = get_secret(user_id, "sec_user_agent").strip()
    if not value or "@" not in value:
        raise SECRefreshError("Configure a truthful SEC User-Agent with contact email in Settings & Data.")
    return value


def _json(url: str, user_agent: str) -> dict:
    response = requests.get(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, timeout=35)
    if response.status_code != 200:
        raise SECRefreshError(f"SEC HTTP {response.status_code}")
    return response.json() or {}


def _ticker_meta(ticker: str, user_agent: str) -> dict[str, str]:
    mapping = _json(f"{SEC_WWW}/files/company_tickers.json", user_agent)
    target = ticker.upper().strip()
    for row in mapping.values():
        if str(row.get("ticker") or "").upper() != target:
            continue
        cik = str(row.get("cik_str") or "").zfill(10)
        submission = _json(f"{SEC_DATA}/submissions/CIK{cik}.json", user_agent)
        return {
            "cik": cik,
            "name": str(submission.get("name") or row.get("title") or target),
            "sic": str(submission.get("sic") or ""),
            "sic_description": str(submission.get("sicDescription") or ""),
            "fiscal_year_end": str(submission.get("fiscalYearEnd") or ""),
        }
    raise SECRefreshError(f"{target} not found in SEC ticker mapping.")


def _facts(companyfacts: dict, namespace: str, tag: str) -> list[dict[str, Any]]:
    node = (((companyfacts.get("facts") or {}).get(namespace) or {}).get(tag) or {})
    out: list[dict[str, Any]] = []
    for unit, rows in (node.get("units") or {}).items():
        for raw in rows or []:
            item = dict(raw); item["unit"] = unit; item["tag"] = tag; item["namespace"] = namespace
            out.append(item)
    return out


def _as_decimal(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _duration_days(row: dict[str, Any]) -> int | None:
    try:
        return (date.fromisoformat(str(row.get("end"))[:10]) - date.fromisoformat(str(row.get("start"))[:10])).days
    except Exception:
        return None


def _annual_duration(companyfacts: dict, tags: Iterable[str]) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, "us-gaap", tag):
            if row.get("form") not in {"10-K", "10-K/A"} or str(row.get("fp") or "") != "FY":
                continue
            days = _duration_days(row)
            if days is None or not 300 <= days <= 430:
                continue
            try: fy = int(row.get("fy"))
            except Exception: continue
            if _as_decimal(row.get("val")) is None: continue
            candidates[fy].append(row)
        for fy, rows in candidates.items():
            if fy in output: continue
            rows.sort(key=lambda r: (str(r.get("filed") or ""), str(r.get("end") or ""), str(r.get("accn") or "")))
            output[fy] = rows[-1]
    return output


def _annual_instant(companyfacts: dict, tags: Iterable[str], namespace: str = "us-gaap") -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, namespace, tag):
            if row.get("form") not in {"10-K", "10-K/A"} or row.get("start"):
                continue
            try: fy = int(row.get("fy"))
            except Exception: continue
            if _as_decimal(row.get("val")) is None: continue
            candidates[fy].append(row)
        for fy, rows in candidates.items():
            if fy in output: continue
            rows.sort(key=lambda r: (str(r.get("filed") or ""), str(r.get("end") or ""), str(r.get("accn") or "")))
            output[fy] = rows[-1]
    return output


def _source_for(company: Company, meta: dict, user_agent: str, facts: dict) -> Source:
    content_hash = hashlib.sha256(str(facts.get("entityName") or meta["name"]).encode()).hexdigest()
    source = Source(
        company_id=company.id,
        provider="SEC",
        source_type="COMPANYFACTS",
        title=f"{meta['name']} SEC Companyfacts",
        url=f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json",
        retrieved_at=utcnow(),
        content_hash=content_hash,
        meta={"cik": meta["cik"], "user_agent_present": bool(user_agent)},
    )
    db.session.add(source); db.session.flush()
    return source


def _record_raw(period: FinancialPeriod, source: Source, record: dict | None) -> None:
    if not record: return
    value = _as_decimal(record.get("val"))
    if value is None: return
    context = f"{record.get('start','')}|{record.get('end','')}|{record.get('accn','')}|{record.get('unit','')}"
    db.session.add(RawFinancialFact(
        financial_period_id=period.id,
        source_id=source.id,
        taxonomy=str(record.get("namespace") or "us-gaap"),
        tag=str(record.get("tag") or ""),
        unit=str(record.get("unit") or ""),
        value=value,
        context_hash=hashlib.sha256(context.encode()).hexdigest(),
        filed_at=date.fromisoformat(str(record.get("filed"))[:10]) if record.get("filed") else None,
        raw_payload={k: record.get(k) for k in ("fy", "fp", "form", "start", "end", "filed", "accn", "frame")},
    ))


def refresh_company_fundamentals(company: Company, security: Security, user_id: int) -> dict[str, Any]:
    user_agent = _ua(user_id)
    meta = _ticker_meta(security.ticker, user_agent)
    companyfacts = _json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json", user_agent)
    source = _source_for(company, meta, user_agent, companyfacts)
    duration = {key: _annual_duration(companyfacts, tags) for key, tags in DURATION_TAGS.items()}
    instant = {key: _annual_instant(companyfacts, tags) for key, tags in INSTANT_TAGS.items()}
    dei_shares = _annual_instant(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei")
    if dei_shares: instant["shares_outstanding"] = dei_shares
    years = sorted(set().union(*(set(rows) for rows in duration.values()), *(set(rows) for rows in instant.values())))
    saved = 0
    for fy in years[-10:]:
        anchor = duration["revenue"].get(fy) or next((rows.get(fy) for rows in duration.values() if rows.get(fy)), None) or next((rows.get(fy) for rows in instant.values() if rows.get(fy)), None)
        if not anchor or not anchor.get("end"): continue
        end_date = date.fromisoformat(str(anchor["end"])[:10])
        period = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY", fiscal_year=fy, end_date=end_date).first()
        if period is None:
            period = FinancialPeriod(company_id=company.id, source_id=source.id, period_type="FY", fiscal_year=fy, end_date=end_date, currency="USD")
            db.session.add(period); db.session.flush()
        period.source_id = source.id
        period.filed_at = date.fromisoformat(str(anchor.get("filed"))[:10]) if anchor.get("filed") else None
        period.accession_no = str(anchor.get("accn") or "")
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if normalized is None:
            normalized = NormalizedFinancial(financial_period_id=period.id)
            db.session.add(normalized)
        source_map: dict[str, Any] = {}
        for field, records in duration.items():
            rec = records.get(fy); _record_raw(period, source, rec)
            value = _as_decimal((rec or {}).get("val")); setattr(normalized, field, value)
            if rec: source_map[field] = {"tag": rec.get("tag"), "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id}
        for field, records in instant.items():
            rec = records.get(fy); _record_raw(period, source, rec)
            value = _as_decimal((rec or {}).get("val")); setattr(normalized, field, value)
            if rec: source_map[field] = {"tag": rec.get("tag"), "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id}
        if normalized.revenue is not None and normalized.gross_profit is not None:
            normalized.cogs = normalized.revenue - normalized.gross_profit
        if normalized.gross_profit is not None and normalized.operating_income is not None:
            normalized.operating_expenses = normalized.gross_profit - normalized.operating_income
        if normalized.cfo is not None and normalized.capex is not None:
            normalized.fcf = normalized.cfo - normalized.capex
        normalized.source_map = source_map
        normalized.quality = {"provider": "SEC", "filing_aware": True, "raw_facts_persisted": True}
        for field, ref in source_map.items():
            db.session.add(Provenance(source_id=source.id, object_type="normalized_financial", object_id=str(period.id), field_name=field, raw_or_normalized="NORMALIZED", financial_period_id=period.id, provider="SEC", freshness_at=utcnow(), calculation_version="0.1.0", notes=f"{ref.get('tag','')} / {ref.get('accession','')}"))
        if normalized.revenue is None:
            db.session.add(DataQualityIssue(company_id=company.id, object_type="financial_period", object_id=str(period.id), code="MISSING_REVENUE", severity="REVIEW", message=f"FY{fy}: revenue was not resolved from SEC Companyfacts."))
        saved += 1
    company.legal_name = meta["name"] or company.legal_name
    company.display_name = meta["name"] or company.display_name
    company.cik = meta["cik"]
    company.industry = meta["sic_description"] or company.industry
    company.fiscal_year_end = meta["fiscal_year_end"] or company.fiscal_year_end
    db.session.commit()
    return {"saved": saved, "cik": meta["cik"], "name": company.display_name, "years": years[-10:], "source_id": source.id}
