from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any, Iterable

import requests

from .extensions import db
from .data_providers import get_secret
from .core_models import Company, DataQualityIssue, FinancialPeriod, NormalizedFinancial, Provenance, RawFinancialFact, Security, Source

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
CALCULATION_VERSION = "0.2.0"

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
            item = dict(raw)
            item["unit"] = unit
            item["tag"] = tag
            item["namespace"] = namespace
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


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (str(r.get("filed") or ""), str(r.get("end") or ""), str(r.get("accn") or "")))


def _fiscal_year_from_end(row: dict[str, Any], fiscal_year_end: str = "") -> int | None:
    """Resolve the fiscal year from the fact's own period end, not the later filing's fy.

    SEC Companyfacts repeats comparative periods in later filings. Using row['fy']
    alone can therefore attach an old comparative fact to the newest fiscal year.
    """
    try:
        end = date.fromisoformat(str(row.get("end") or "")[:10])
    except Exception:
        return None
    fye = str(fiscal_year_end or "").strip()
    if len(fye) == 4 and fye.isdigit():
        month, day = int(fye[:2]), int(fye[2:])
        if 1 <= month <= 12 and 1 <= day <= 31:
            return end.year + (1 if (end.month, end.day) > (month, day) else 0)
    return end.year


def _annual_duration(companyfacts: dict, tags: Iterable[str], fiscal_year_end: str = "") -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, "us-gaap", tag):
            if row.get("form") not in {"10-K", "10-K/A"} or str(row.get("fp") or "") != "FY":
                continue
            days = _duration_days(row)
            if days is None or not 300 <= days <= 430:
                continue
            fy = _fiscal_year_from_end(row, fiscal_year_end)
            if fy is None:
                continue
            if _as_decimal(row.get("val")) is None:
                continue
            candidates[fy].append(row)
        for fy, rows in candidates.items():
            if fy not in output:
                output[fy] = _sort_rows(rows)[-1]
    return output


def _annual_instant(companyfacts: dict, tags: Iterable[str], namespace: str = "us-gaap", fiscal_year_end: str = "") -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, namespace, tag):
            if row.get("form") not in {"10-K", "10-K/A"} or row.get("start"):
                continue
            fy = _fiscal_year_from_end(row, fiscal_year_end)
            if fy is None:
                continue            if _as_decimal(row.get("val")) is None:
                continue
            candidates[fy].append(row)
        for fy, rows in candidates.items():
            if fy not in output:
                output[fy] = _sort_rows(rows)[-1]
    return output


def _quarter_duration_sources(companyfacts: dict, tags: Iterable[str], fiscal_year_end: str = "") -> tuple[dict[tuple[int, str], dict[str, Any]], dict[tuple[int, str], dict[str, Any]]]:
    """Return quarter-only and YTD 10-Q facts, preserving tag priority.

    Income-statement facts often expose a ~90-day quarter and a YTD context. Cash-flow
    facts commonly expose YTD only, so Q2/Q3 must be derived by differencing YTD facts.
    """
    direct: dict[tuple[int, str], dict[str, Any]] = {}
    ytd: dict[tuple[int, str], dict[str, Any]] = {}
    for tag in tags:
        direct_candidates: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        ytd_candidates: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, "us-gaap", tag):
            fp = str(row.get("fp") or "").upper()
            if row.get("form") not in {"10-Q", "10-Q/A"} or fp not in {"Q1", "Q2", "Q3"}:
                continue
            days = _duration_days(row)
            if days is None or _as_decimal(row.get("val")) is None:
                continue
            fy = _fiscal_year_from_end(row, fiscal_year_end)
            if fy is None:
                continue            key = (fy, fp)
            if 60 <= days <= 120:
                direct_candidates[key].append(row)
            elif 121 <= days <= 310:
                ytd_candidates[key].append(row)
        for key, rows in direct_candidates.items():
            if key not in direct:
                direct[key] = _sort_rows(rows)[-1]
        for key, rows in ytd_candidates.items():
            if key not in ytd:
                ytd[key] = _sort_rows(rows)[-1]
    return direct, ytd


def _quarter_duration_values(companyfacts: dict, tags: Iterable[str], annual: dict[int, dict[str, Any]], *, shares_metric: bool = False, fiscal_year_end: str = "") -> dict[tuple[int, str], dict[str, Any]]:
    direct, ytd = _quarter_duration_sources(companyfacts, tags, fiscal_year_end)
    years = sorted(set(fy for fy, _ in set(direct) | set(ytd)) | set(annual))
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for fy in years:
        q1 = direct.get((fy, "Q1")) or ytd.get((fy, "Q1"))
        if q1:
            out[(fy, "Q1")] = {"value": _as_decimal(q1.get("val")), "record": q1, "method": "DIRECT_QUARTER"}
        for fp, prev_fp in (("Q2", "Q1"), ("Q3", "Q2")):
            record = direct.get((fy, fp))
            if record:
                out[(fy, fp)] = {"value": _as_decimal(record.get("val")), "record": record, "method": "DIRECT_QUARTER"}
                continue
            current_ytd = ytd.get((fy, fp))
            if shares_metric:
                if current_ytd:
                    out[(fy, fp)] = {"value": _as_decimal(current_ytd.get("val")), "record": current_ytd, "method": "YTD_SHARE_PROXY"}
                continue
            previous_ytd = ytd.get((fy, prev_fp)) or (direct.get((fy, "Q1")) if prev_fp == "Q1" else None)
            cur_val = _as_decimal((current_ytd or {}).get("val"))
            prev_val = _as_decimal((previous_ytd or {}).get("val"))
            if cur_val is not None and prev_val is not None:
                out[(fy, fp)] = {
                    "value": cur_val - prev_val,
                    "record": current_ytd,
                    "derived_from": [previous_ytd, current_ytd],
                    "method": "YTD_DIFFERENCE",
                }
        annual_record = annual.get(fy)
        annual_value = _as_decimal((annual_record or {}).get("val"))
        if annual_value is not None:
            if shares_metric:
                out[(fy, "Q4")] = {"value": annual_value, "record": annual_record, "method": "FY_SHARE_PROXY"}
            else:
                values = [out.get((fy, fp), {}).get("value") for fp in ("Q1", "Q2", "Q3")]
                if all(v is not None for v in values):
                    out[(fy, "Q4")] = {
                        "value": annual_value - sum(values, Decimal("0")),
                        "record": annual_record,
                        "method": "FY_MINUS_Q1_Q2_Q3",
                    }
    return out


def _quarter_instants(companyfacts: dict, tags: Iterable[str], namespace: str = "us-gaap", fiscal_year_end: str = "") -> dict[tuple[int, str], dict[str, Any]]:
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, namespace, tag):
            if row.get("start") or _as_decimal(row.get("val")) is None:
                continue
            form = str(row.get("form") or "")
            fp = str(row.get("fp") or "").upper()
            if form in {"10-Q", "10-Q/A"} and fp in {"Q1", "Q2", "Q3"}:
                quarter = fp
            elif form in {"10-K", "10-K/A"} and fp == "FY":
                quarter = "Q4"
            else:
                continue
            fy = _fiscal_year_from_end(row, fiscal_year_end)
            if fy is None:
                continue            candidates[(fy, quarter)].append(row)
        for key, rows in candidates.items():
            if key not in out:
                out[key] = _sort_rows(rows)[-1]
    return out


def _source_for(company: Company, meta: dict, user_agent: str, facts: dict) -> Source:
    stamp = f"{facts.get('entityName') or meta['name']}|{datetime.now(timezone.utc).date().isoformat()}"
    content_hash = hashlib.sha256(stamp.encode()).hexdigest()
    source = Source(
        company_id=company.id,
        provider="SEC",
        source_type="COMPANYFACTS",
        title=f"{meta['name']} SEC Companyfacts",
        url=f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json",
        retrieved_at=utcnow(),
        content_hash=content_hash,
        meta={
            "cik": meta["cik"],
            "sic": meta.get("sic") or "",
            "sic_description": meta.get("sic_description") or "",
            "user_agent_present": bool(user_agent),
        },
    )
    db.session.add(source)
    db.session.flush()
    return source


def _record_raw(period: FinancialPeriod, source: Source, record: dict | None) -> None:
    if not record:
        return
    value = _as_decimal(record.get("val"))
    if value is None:
        return
    context = f"{record.get('start','')}|{record.get('end','')}|{record.get('accn','')}|{record.get('unit','')}"
    context_hash = hashlib.sha256(context.encode()).hexdigest()
    existing = RawFinancialFact.query.filter_by(financial_period_id=period.id, context_hash=context_hash, tag=str(record.get("tag") or "")).first()
    if existing:
        return
    db.session.add(RawFinancialFact(
        financial_period_id=period.id,
        source_id=source.id,
        taxonomy=str(record.get("namespace") or "us-gaap"),
        tag=str(record.get("tag") or ""),
        unit=str(record.get("unit") or ""),
        value=value,
        context_hash=context_hash,
        filed_at=date.fromisoformat(str(record.get("filed"))[:10]) if record.get("filed") else None,
        raw_payload={k: record.get(k) for k in ("fy", "fp", "form", "start", "end", "filed", "accn", "frame")},
    ))


def _upsert_period(company: Company, source: Source, *, period_type: str, fiscal_year: int, end_date: date, anchor: dict[str, Any] | None) -> FinancialPeriod:
    period = FinancialPeriod.query.filter_by(company_id=company.id, period_type=period_type, fiscal_year=fiscal_year, end_date=end_date).first()
    if period is None:
        period = FinancialPeriod(company_id=company.id, source_id=source.id, period_type=period_type, fiscal_year=fiscal_year, end_date=end_date, currency="USD")
        db.session.add(period)
        db.session.flush()
    period.source_id = source.id
    if anchor:
        period.filed_at = date.fromisoformat(str(anchor.get("filed"))[:10]) if anchor.get("filed") else period.filed_at
        period.accession_no = str(anchor.get("accn") or period.accession_no or "")
        if anchor.get("start"):
            period.start_date = date.fromisoformat(str(anchor.get("start"))[:10])
    return period


def _normalized(period: FinancialPeriod) -> NormalizedFinancial:
    row = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
    if row is None:
        row = NormalizedFinancial(financial_period_id=period.id)
        db.session.add(row)
    return row


def _finish_normalized(row: NormalizedFinancial, source_map: dict[str, Any], *, period_type: str) -> None:
    if row.revenue is not None and row.gross_profit is not None:
        row.cogs = row.revenue - row.gross_profit
    if row.gross_profit is not None and row.operating_income is not None:
        row.operating_expenses = row.gross_profit - row.operating_income
    if row.cfo is not None and row.capex is not None:
        row.fcf = row.cfo - row.capex
    row.source_map = source_map
    row.quality = {
        "provider": "SEC", "filing_aware": True, "raw_facts_persisted": True,
        "period_type": period_type, "ttm_eligible": period_type in {"Q1", "Q2", "Q3", "Q4"},
    }
    row.calculation_version = CALCULATION_VERSION


def refresh_company_fundamentals(company: Company, security: Security, user_id: int) -> dict[str, Any]:
    user_agent = _ua(user_id)
    meta = _ticker_meta(security.ticker, user_agent)
    companyfacts = _json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json", user_agent)
    source = _source_for(company, meta, user_agent, companyfacts)

    fiscal_year_end = meta.get("fiscal_year_end") or ""
    duration = {key: _annual_duration(companyfacts, tags, fiscal_year_end) for key, tags in DURATION_TAGS.items()}
    instant = {key: _annual_instant(companyfacts, tags, fiscal_year_end=fiscal_year_end) for key, tags in INSTANT_TAGS.items()}
    dei_shares = _annual_instant(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei", fiscal_year_end=fiscal_year_end)
    if dei_shares:
        instant["shares_outstanding"] = dei_shares
    years = sorted(set().union(*(set(rows) for rows in duration.values()), *(set(rows) for rows in instant.values())))

    annual_saved = 0
    for fy in years[-16:]:
        anchor = duration["revenue"].get(fy) or next((rows.get(fy) for rows in duration.values() if rows.get(fy)), None) or next((rows.get(fy) for rows in instant.values() if rows.get(fy)), None)
        if not anchor or not anchor.get("end"):
            continue
        end_date = date.fromisoformat(str(anchor["end"])[:10])
        period = _upsert_period(company, source, period_type="FY", fiscal_year=fy, end_date=end_date, anchor=anchor)
        normalized = _normalized(period)
        source_map: dict[str, Any] = {}
        for field, records in duration.items():
            rec = records.get(fy)
            _record_raw(period, source, rec)
            setattr(normalized, field, _as_decimal((rec or {}).get("val")))
            if rec:
                source_map[field] = {"tag": rec.get("tag"), "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id, "method": "DIRECT_FY"}
        for field, records in instant.items():
            rec = records.get(fy)
            _record_raw(period, source, rec)
            setattr(normalized, field, _as_decimal((rec or {}).get("val")))
            if rec:
                source_map[field] = {"tag": rec.get("tag"), "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id, "method": "DIRECT_FY"}
        _finish_normalized(normalized, source_map, period_type="FY")
        for field, ref in source_map.items():
            db.session.add(Provenance(source_id=source.id, object_type="normalized_financial", object_id=str(period.id), field_name=field, raw_or_normalized="NORMALIZED", financial_period_id=period.id, provider="SEC", freshness_at=utcnow(), calculation_version=CALCULATION_VERSION, notes=f"{ref.get('tag','')} / {ref.get('accession','')} / {ref.get('method','')}"))
        if normalized.revenue is None:
            exists = DataQualityIssue.query.filter_by(company_id=company.id, object_type="financial_period", object_id=str(period.id), code="MISSING_REVENUE", status="OPEN").first()
            if not exists:
                db.session.add(DataQualityIssue(company_id=company.id, object_type="financial_period", object_id=str(period.id), code="MISSING_REVENUE", severity="REVIEW", message=f"FY{fy}: revenue was not resolved from SEC Companyfacts."))
        annual_saved += 1

    quarter_duration: dict[str, dict[tuple[int, str], dict[str, Any]]] = {}
    for field, tags in DURATION_TAGS.items():
        quarter_duration[field] = _quarter_duration_values(companyfacts, tags, duration[field], shares_metric=(field == "diluted_shares"), fiscal_year_end=fiscal_year_end)
    quarter_instant = {field: _quarter_instants(companyfacts, tags, fiscal_year_end=fiscal_year_end) for field, tags in INSTANT_TAGS.items()}
    dei_quarter_shares = _quarter_instants(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei", fiscal_year_end=fiscal_year_end)
    if dei_quarter_shares:
        quarter_instant["shares_outstanding"] = dei_quarter_shares

    quarter_keys = sorted(set().union(*(set(rows) for rows in quarter_duration.values()), *(set(rows) for rows in quarter_instant.values())))
    quarter_saved = 0
    for fy, fp in quarter_keys[-24:]:
        anchor_meta = quarter_duration.get("revenue", {}).get((fy, fp)) or next((rows.get((fy, fp)) for rows in quarter_duration.values() if rows.get((fy, fp))), None)
        anchor = (anchor_meta or {}).get("record") if isinstance(anchor_meta, dict) else None
        if anchor is None:
            anchor = next((rows.get((fy, fp)) for rows in quarter_instant.values() if rows.get((fy, fp))), None)
        if not anchor or not anchor.get("end"):
            continue
        end_date = date.fromisoformat(str(anchor.get("end"))[:10])
        period = _upsert_period(company, source, period_type=fp, fiscal_year=fy, end_date=end_date, anchor=anchor)
        normalized = _normalized(period)
        source_map: dict[str, Any] = {}
        for field, records in quarter_duration.items():
            info = records.get((fy, fp)) or {}
            record = info.get("record")
            for raw in info.get("derived_from") or []:
                _record_raw(period, source, raw)
            _record_raw(period, source, record)
            setattr(normalized, field, info.get("value"))
            if info:
                source_map[field] = {
                    "tag": (record or {}).get("tag"), "accession": (record or {}).get("accn"),
                    "filed": (record or {}).get("filed"), "source_id": source.id, "method": info.get("method"),
                }
        for field, records in quarter_instant.items():
            rec = records.get((fy, fp))
            _record_raw(period, source, rec)
            setattr(normalized, field, _as_decimal((rec or {}).get("val")))
            if rec:
                source_map[field] = {"tag": rec.get("tag"), "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id, "method": "DIRECT_INSTANT"}
        _finish_normalized(normalized, source_map, period_type=fp)
        quarter_saved += 1

    company.legal_name = meta["name"] or company.legal_name
    company.display_name = meta["name"] or company.display_name
    company.cik = meta["cik"]
    company.industry = meta["sic_description"] or company.industry
    company.fiscal_year_end = meta["fiscal_year_end"] or company.fiscal_year_end
    db.session.commit()
    return {
        "saved": annual_saved + quarter_saved,
        "annual_saved": annual_saved,
        "quarter_saved": quarter_saved,
        "cik": meta["cik"], "name": company.display_name,
        "years": years[-16:], "quarter_periods": [f"FY{fy}-{fp}" for fy, fp in quarter_keys[-24:]],
        "source_id": source.id,
    }
