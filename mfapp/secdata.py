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
from .economic_reality import DURATION_TAGS as ECONOMIC_DURATION_TAGS, INSTANT_TAGS as ECONOMIC_INSTANT_TAGS, build_economic_reality

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
CALCULATION_VERSION = "0.2.0"

DURATION_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfProductsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
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
    "receivables": ["AccountsReceivableNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent", "AccountsReceivableNet"],
    "inventory": ["InventoryNet", "InventoryCurrent", "InventoryNetOfAllowancesCustomerAdvancesAndProgressBillings"],
    "payables": ["AccountsPayableCurrent", "AccountsPayableTradeCurrent"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "debt": ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndFinanceLeaseObligations", "LongTermDebt"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
}

SEMANTIC_LABEL_ALIASES = {
    "revenue": {"revenue", "revenues", "net revenue", "net revenues", "net sales", "sales", "total revenue", "total revenues", "operating revenue", "operating revenues"},
    "cogs": {"cost of sales", "cost of revenue", "cost of revenues", "cost of goods sold", "cost of goods and services sold"},
    "gross_profit": {"gross profit"},
    "operating_income": {"operating income", "income from operations", "operating income loss"},
    "pretax_income": {"income before income taxes", "income before taxes", "earnings before income taxes"},
    "income_tax": {"income tax expense", "provision for income taxes", "income taxes"},
    "net_income": {"net income", "net income loss"},
    "cfo": {"net cash provided by operating activities", "cash provided by operations", "net cash provided by used in operating activities"},
    "capex": {"capital expenditures", "additions to property plant and equipment", "purchases of property plant and equipment"},
    "cash": {"cash and cash equivalents", "cash and equivalents"},
    "receivables": {"accounts receivable net", "accounts receivable"},
    "inventory": {"inventories", "inventory", "inventories net", "total inventories"},
    "payables": {"accounts payable", "accounts payable current"},
    "assets": {"total assets"},
    "liabilities": {"total liabilities"},
    "equity": {"total shareholders equity", "total stockholders equity", "shareholders equity", "stockholders equity"},
}

DEBT_CURRENT_TAGS = [
    "LongTermDebtCurrent", "CurrentPortionOfLongTermDebt",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
]
DEBT_NONCURRENT_TAGS = [
    "LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebt",
]
DEBT_SHORT_TERM_TAGS = ["ShortTermBorrowings", "ShortTermDebt", "CommercialPaper"]
DEBT_COMBINED_TAGS = {"DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndFinanceLeaseObligations"}


def _economic_maps_annual(companyfacts: dict, fiscal_year_end: str) -> tuple[dict, dict]:
    duration = {
        key: _annual_duration(companyfacts, list(tags), fiscal_year_end)
        for key, tags in ECONOMIC_DURATION_TAGS.items()
    }
    instant = {
        key: _annual_instant(companyfacts, list(tags), fiscal_year_end=fiscal_year_end)
        for key, tags in ECONOMIC_INSTANT_TAGS.items()
    }
    return duration, instant


def _economic_maps_quarter(companyfacts: dict, fiscal_year_end: str) -> tuple[dict, dict]:
    duration: dict[str, dict] = {}
    for key, tags in ECONOMIC_DURATION_TAGS.items():
        annual = _annual_duration(companyfacts, list(tags), fiscal_year_end)
        duration[key] = _quarter_duration_values(
            companyfacts, list(tags), annual, fiscal_year_end=fiscal_year_end
        )
    instant = {
        key: _quarter_instants(companyfacts, list(tags), fiscal_year_end=fiscal_year_end)
        for key, tags in ECONOMIC_INSTANT_TAGS.items()
    }
    return duration, instant


def _economic_fact_bundle(duration: dict, instant: dict, key: Any) -> tuple[dict, dict]:
    facts: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for field, rows in duration.items():
        info = rows.get(key) or {}
        record = info.get("record") if isinstance(info, dict) and "record" in info else info
        value = info.get("value") if isinstance(info, dict) and "value" in info else _as_decimal((record or {}).get("val"))
        if value is not None:
            facts[field] = value
            sources[field] = {
                "tag": (record or {}).get("tag"),
                "accession": (record or {}).get("accn"),
                "filed": (record or {}).get("filed"),
                "namespace": (record or {}).get("_mf_namespace") or (record or {}).get("namespace") or "us-gaap",
            }
    for field, rows in instant.items():
        record = rows.get(key) or {}
        value = _as_decimal(record.get("val"))
        if value is not None:
            facts[field] = value
            sources[field] = {
                "tag": record.get("tag"),
                "accession": record.get("accn"),
                "filed": record.get("filed"),
                "namespace": record.get("_mf_namespace") or record.get("namespace") or "us-gaap",
            }
    return facts, sources


def _economic_row_dict(row: NormalizedFinancial) -> dict[str, Any]:
    fields = (
        "revenue", "operating_income", "pretax_income", "income_tax", "cfo", "capex", "fcf",
        "cash", "debt", "liabilities", "equity",
    )
    out = {field: getattr(row, field, None) for field in fields}
    out["_debt_source_tag"] = str(((row.source_map or {}).get("debt") or {}).get("tag") or "")
    return out



def _normalize_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    for ch in ",.()[]{}:/_-":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _semantic_tag_groups(companyfacts: dict, field: str) -> dict[str, list[str]]:
    """Find exact statement-label concepts across standard and filer taxonomies.

    This runs only as a missing-field fallback. Matching is deliberately exact
    after punctuation/whitespace normalization so a segment or similarly named
    disclosure is not silently treated as a consolidated statement fact.
    """
    aliases = {_normalize_label(x) for x in SEMANTIC_LABEL_ALIASES.get(field, set())}
    out: dict[str, list[str]] = defaultdict(list)
    if not aliases:
        return out
    for namespace, concepts in (companyfacts.get("facts") or {}).items():
        for tag, node in (concepts or {}).items():
            if _normalize_label((node or {}).get("label")) in aliases:
                out[str(namespace)].append(str(tag))
    return out


def _merge_missing(target: dict, fallback: dict) -> None:
    for key, value in fallback.items():
        target.setdefault(key, value)


def _mark_semantic_records(rows: dict, namespace: str) -> dict:
    marked = {}
    for key, value in rows.items():
        if isinstance(value, dict) and "record" in value:
            info = dict(value)
            record = dict(info.get("record") or {})
            record["_mf_semantic_fallback"] = True
            record["_mf_namespace"] = namespace
            info["record"] = record
            if info.get("derived_from"):
                info["derived_from"] = [
                    dict(raw, _mf_semantic_fallback=True, _mf_namespace=namespace)
                    for raw in info.get("derived_from") or []
                ]
            marked[key] = info
        else:
            record = dict(value or {})
            record["_mf_semantic_fallback"] = True
            record["_mf_namespace"] = namespace
            marked[key] = record
    return marked


def _compose_debt(direct: dict | None, current: dict | None, noncurrent: dict | None, short_term: dict | None) -> tuple[Decimal | None, list[dict], str]:
    """Prefer a true combined debt fact; otherwise add separately reported components."""
    direct = dict(direct or {})
    direct_value = _as_decimal(direct.get("val"))
    direct_tag = str(direct.get("tag") or "")
    if direct_value is not None and direct_tag in DEBT_COMBINED_TAGS:
        return direct_value, [direct], "DIRECT_COMBINED_DEBT"

    parts: list[dict] = []
    seen_tags: set[str] = set()
    for raw in (current, short_term, noncurrent):
        rec = dict(raw or {})
        value = _as_decimal(rec.get("val"))
        tag = str(rec.get("tag") or "")
        if value is None or not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        parts.append(rec)
    if len(parts) >= 2:
        return sum((_as_decimal(rec.get("val")) or Decimal("0")) for rec in parts), parts, "SUM_CURRENT_NONCURRENT_DEBT"
    # A bare LongTermDebt fact is not proven total debt: a current portion or
    # short-term borrowing can live elsewhere. Leave it unresolved so the
    # secondary fallback may supply a true total instead of understating debt.
    return None, [], ""


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


def _fiscal_quarter_from_end(row: dict[str, Any], fiscal_year_end: str = "") -> str | None:
    """Classify the represented period from its own end date, not filing fp.

    SEC Companyfacts repeats comparative facts in later filings. The row fp
    describes the filing period and can therefore mislabel an older comparative
    fact as Q2/Q3. Deriving the quarter from the fact end date keeps non-calendar
    issuers on a consecutive fiscal sequence.
    """
    raw_fye = str(fiscal_year_end or "").strip()
    fallback = str(row.get("fp") or "").upper()
    if len(raw_fye) != 4 or not raw_fye.isdigit():
        return fallback if fallback in {"Q1", "Q2", "Q3", "Q4"} else None
    month, day = int(raw_fye[:2]), int(raw_fye[2:])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return fallback if fallback in {"Q1", "Q2", "Q3", "Q4"} else None
    try:
        end = date.fromisoformat(str(row.get("end") or "")[:10])
        fy = _fiscal_year_from_end(row, raw_fye)
        if fy is None:
            return None

        def fye(year: int) -> date:
            candidate_day = day
            while candidate_day >= 28:
                try:
                    return date(year, month, candidate_day)
                except ValueError:
                    candidate_day -= 1
            return date(year, month, candidate_day)

        previous_end = fye(fy - 1)
        current_end = fye(fy)
        span = max(1, (current_end - previous_end).days)
        elapsed = (end - previous_end).days
        if elapsed <= 0 or elapsed > span + 24:
            return None
        ratio = elapsed / span
        centers = {"Q1": 0.25, "Q2": 0.50, "Q3": 0.75, "Q4": 1.00}
        quarter, center = min(centers.items(), key=lambda item: abs(ratio - item[1]))
        return quarter if abs(ratio - center) <= 0.16 else None
    except (TypeError, ValueError):
        return fallback if fallback in {"Q1", "Q2", "Q3", "Q4"} else None


def _mark_last_good_retained(source_map: dict[str, Any], field: str) -> None:
    """Keep a previously sourced same-period value when a refresh cannot resolve it."""
    prior = dict(source_map.get(field) or {})
    prior["refresh_state"] = "LAST_GOOD_RETAINED"
    prior["refresh_note"] = "Current provider refresh missed this same-period fact; prior sourced value retained."
    source_map[field] = prior


def _annual_duration(companyfacts: dict, tags: Iterable[str], fiscal_year_end: str = "", namespace: str = "us-gaap") -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, namespace, tag):
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
                continue
            if _as_decimal(row.get("val")) is None:
                continue
            candidates[fy].append(row)
        for fy, rows in candidates.items():
            if fy not in output:
                output[fy] = _sort_rows(rows)[-1]
    return output


def _quarter_duration_sources(companyfacts: dict, tags: Iterable[str], fiscal_year_end: str = "", namespace: str = "us-gaap") -> tuple[dict[tuple[int, str], dict[str, Any]], dict[tuple[int, str], dict[str, Any]]]:
    """Return quarter-only and YTD facts keyed by represented fiscal quarter.

    Companyfacts can repeat comparative periods in later 10-Q filings. The
    filing-level fp marker is therefore not trusted as the represented quarter;
    the fact end date and issuer fiscal year-end define the key.
    """
    direct: dict[tuple[int, str], dict[str, Any]] = {}
    ytd: dict[tuple[int, str], dict[str, Any]] = {}
    for tag in tags:
        direct_candidates: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        ytd_candidates: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, namespace, tag):
            if row.get("form") not in {"10-Q", "10-Q/A"}:
                continue
            quarter = _fiscal_quarter_from_end(row, fiscal_year_end)
            if quarter not in {"Q1", "Q2", "Q3"}:
                continue
            days = _duration_days(row)
            if days is None or _as_decimal(row.get("val")) is None:
                continue
            fy = _fiscal_year_from_end(row, fiscal_year_end)
            if fy is None:
                continue
            key = (fy, quarter)
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

def _quarter_duration_values(companyfacts: dict, tags: Iterable[str], annual: dict[int, dict[str, Any]], *, shares_metric: bool = False, fiscal_year_end: str = "", namespace: str = "us-gaap") -> dict[tuple[int, str], dict[str, Any]]:
    direct, ytd = _quarter_duration_sources(companyfacts, tags, fiscal_year_end, namespace=namespace)
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
            if form in {"10-Q", "10-Q/A"}:
                quarter = _fiscal_quarter_from_end(row, fiscal_year_end)
                if quarter not in {"Q1", "Q2", "Q3"}:
                    continue
            elif form in {"10-K", "10-K/A"}:
                quarter = _fiscal_quarter_from_end(row, fiscal_year_end)
                if quarter != "Q4":
                    continue
            else:
                continue
            fy = _fiscal_year_from_end(row, fiscal_year_end)
            if fy is None:
                continue
            candidates[(fy, quarter)].append(row)
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


def _finish_normalized(
    row: NormalizedFinancial,
    source_map: dict[str, Any],
    *,
    period_type: str,
    economic_reality: dict[str, Any] | None = None,
) -> None:
    # Preserve direct filing facts first, then fill only exact accounting bridges.
    # Gross Profit is frequently absent from Companyfacts even when Revenue and
    # Cost of Revenue are both reported.
    if row.gross_profit is None and row.revenue is not None and row.cogs is not None:
        row.gross_profit = row.revenue - row.cogs
        source_map.setdefault("gross_profit", {"tag": "DERIVED", "method": "REVENUE_MINUS_COGS"})
    if row.cogs is None and row.revenue is not None and row.gross_profit is not None:
        row.cogs = row.revenue - row.gross_profit
        source_map.setdefault("cogs", {"tag": "DERIVED", "method": "REVENUE_MINUS_GROSS_PROFIT"})
    if row.operating_expenses is None and row.gross_profit is not None and row.operating_income is not None:
        row.operating_expenses = row.gross_profit - row.operating_income
        source_map.setdefault("operating_expenses", {"tag": "DERIVED", "method": "GROSS_PROFIT_MINUS_OPERATING_INCOME"})
    if row.cfo is not None and row.capex is not None:
        row.fcf = row.cfo - row.capex
    row.source_map = source_map
    quality = dict(row.quality or {})
    quality.update({
        "provider": "SEC", "filing_aware": True, "raw_facts_persisted": True,
        "period_type": period_type, "ttm_eligible": period_type in {"Q1", "Q2", "Q3", "Q4"},
    })
    if economic_reality is not None:
        quality["economic_reality"] = economic_reality
    row.quality = quality
    row.calculation_version = CALCULATION_VERSION



_AV_INCOME_FIELDS = {
    "revenue": ("totalRevenue",),
    "cogs": ("costOfRevenue",),
    "gross_profit": ("grossProfit",),
    "operating_income": ("operatingIncome",),
    "pretax_income": ("incomeBeforeTax",),
    "income_tax": ("incomeTaxExpense",),
    "net_income": ("netIncome",),
}
_AV_BALANCE_FIELDS = {
    "cash": ("cashAndCashEquivalentsAtCarryingValue",),
    "receivables": ("currentNetReceivables",),
    "inventory": ("inventory",),
    "payables": ("currentAccountsPayable",),
    "assets": ("totalAssets",),
    "liabilities": ("totalLiabilities",),
    "equity": ("totalShareholderEquity",),
}
_AV_CASH_FIELDS = {
    "cfo": ("operatingCashflow",),
    "capex": ("capitalExpenditures",),
}


def _alpha_vantage_statement(function: str, ticker: str, user_id: int) -> dict[str, Any]:
    """Fetch an optional normalized statement without ever weakening SEC refresh.

    Alpha Vantage is a secondary fill/triangulation source. Any provider error,
    throttle message, or missing key returns an empty payload so SEC remains the
    canonical path.
    """
    key = get_secret(user_id, "alpha_vantage_key")
    if not key:
        return {}
    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": function, "symbol": ticker, "apikey": key},
            timeout=15,
        )
        if response.status_code != 200:
            return {}
        payload = response.json() or {}
        if any(k in payload for k in ("Error Message", "Information", "Note")):
            return {}
        if not isinstance(payload.get("annualReports"), list) and not isinstance(payload.get("quarterlyReports"), list):
            return {}
        return payload
    except Exception:
        return {}


def _av_source(company: Company, ticker: str, function: str, payload: dict[str, Any]) -> Source:
    content_hash = hashlib.sha256(str(payload).encode()).hexdigest()
    source = Source.query.filter_by(
        company_id=company.id,
        provider="Alpha Vantage",
        source_type=function,
        content_hash=content_hash,
    ).first()
    if source:
        return source
    source = Source(
        company_id=company.id,
        provider="Alpha Vantage",
        source_type=function,
        title=f"{ticker} Alpha Vantage {function.replace('_', ' ').title()}",
        url=f"https://www.alphavantage.co/query?function={function}&symbol={ticker}",
        retrieved_at=utcnow(),
        content_hash=content_hash,
        meta={"symbol": ticker, "fallback_only": True},
    )
    db.session.add(source)
    db.session.flush()
    return source


def _av_decimal(report: dict[str, Any], keys: Iterable[str]) -> Decimal | None:
    for key in keys:
        value = _as_decimal(report.get(key))
        if value is not None:
            return value
    return None


def _av_debt(report: dict[str, Any]) -> tuple[Decimal | None, str]:
    total = _av_decimal(report, ("shortLongTermDebtTotal",))
    if total is not None:
        return total, "shortLongTermDebtTotal"
    current = _av_decimal(report, ("currentDebt", "shortTermDebt"))
    long_term = _av_decimal(report, ("longTermDebt",))
    if current is not None and long_term is not None:
        return current + long_term, "currentDebt + longTermDebt"
    return None, ""


def _alpha_vantage_fill_missing(company: Company, security: Security, user_id: int) -> dict[str, Any]:
    """Backfill missing fiscal periods and fields from configured Alpha Vantage.

    SEC remains canonical and always wins. Alpha Vantage is used only when a
    stored normalized field is missing or the annual history itself has a hole /
    insufficient depth. Provider-only fiscal periods are fully provenance-tagged
    and never overwrite an SEC-populated value.
    """
    if not get_secret(user_id, "alpha_vantage_key"):
        return {
            "configured": False, "filled": 0, "periods_created": 0,
            "years_backfilled": [], "sources": [],
        }

    def load_pairs() -> list[tuple[FinancialPeriod, NormalizedFinancial]]:
        return (
            db.session.query(FinancialPeriod, NormalizedFinancial)
            .join(NormalizedFinancial, NormalizedFinancial.financial_period_id == FinancialPeriod.id)
            .filter(FinancialPeriod.company_id == company.id)
            .order_by(FinancialPeriod.end_date.desc())
            .limit(80)
            .all()
        )

    pairs = load_pairs()
    annual_years = sorted({
        int(period.fiscal_year) for period, _ in pairs
        if period.period_type == "FY" and period.fiscal_year is not None
    }, reverse=True)
    history_needs_backfill = True
    if annual_years:
        latest = annual_years[0]
        target = set(range(latest, latest - 10, -1))
        history_needs_backfill = not target.issubset(set(annual_years))

    income_fields = tuple(_AV_INCOME_FIELDS)
    balance_fields = tuple(_AV_BALANCE_FIELDS) + ("debt",)
    cash_fields = tuple(_AV_CASH_FIELDS)
    needs = {
        "INCOME_STATEMENT": history_needs_backfill or not pairs or any(
            any(getattr(row, field, None) is None for field in income_fields)
            for _, row in pairs
        ),
        "BALANCE_SHEET": history_needs_backfill or not pairs or any(
            any(getattr(row, field, None) is None for field in balance_fields)
            for _, row in pairs
        ),
        "CASH_FLOW": history_needs_backfill or not pairs or any(
            any(getattr(row, field, None) is None for field in cash_fields)
            for _, row in pairs
        ),
    }

    payloads: dict[str, dict[str, Any]] = {}
    sources: dict[str, Source] = {}
    for function, needed in needs.items():
        if not needed:
            continue
        payload = _alpha_vantage_statement(function, security.ticker, user_id)
        if not payload:
            continue
        payloads[function] = payload
        sources[function] = _av_source(company, security.ticker, function, payload)

    periods_created = 0
    years_backfilled: list[int] = []

    # Create an FY period when the year itself is absent from SEC-normalized
    # history. This closes the previous blind spot where AV could fill fields only
    # for an already-existing FinancialPeriod but could not restore a missing year.
    annual_reports_by_end: dict[date, dict[str, dict[str, Any]]] = defaultdict(dict)
    for function, payload in payloads.items():
        for report in payload.get("annualReports") or []:
            try:
                end_date = date.fromisoformat(str(report.get("fiscalDateEnding") or "")[:10])
            except Exception:
                continue
            annual_reports_by_end[end_date][function] = report

    existing_periods = (
        FinancialPeriod.query
        .filter_by(company_id=company.id, period_type="FY")
        .order_by(FinancialPeriod.end_date.desc(), FinancialPeriod.id.desc())
        .all()
    )
    existing_by_end = {period.end_date: period for period in existing_periods}
    existing_by_year: dict[int, list[FinancialPeriod]] = defaultdict(list)
    for period in existing_periods:
        existing_by_year[int(period.fiscal_year)].append(period)

    for end_date in sorted(annual_reports_by_end, reverse=True)[:16]:
        fiscal_year = end_date.year
        near_existing = existing_by_end.get(end_date)
        if near_existing is None:
            near_existing = next((
                period for period in existing_by_year.get(fiscal_year, [])
                if abs((period.end_date - end_date).days) <= 14
            ), None)
        if near_existing is not None:
            continue

        bundle = annual_reports_by_end[end_date]
        candidates: dict[str, tuple[Decimal, str, str]] = {}
        for function, report in bundle.items():
            mapping = (
                _AV_INCOME_FIELDS if function == "INCOME_STATEMENT"
                else _AV_BALANCE_FIELDS if function == "BALANCE_SHEET"
                else _AV_CASH_FIELDS
            )
            for field, keys in mapping.items():
                value = _av_decimal(report, keys)
                if value is None:
                    continue
                if field == "capex":
                    value = abs(value)
                provider_field = next(
                    (key for key in keys if _as_decimal(report.get(key)) is not None),
                    keys[0],
                )
                candidates.setdefault(field, (value, provider_field, function))
            if function == "BALANCE_SHEET":
                debt, debt_field = _av_debt(report)
                if debt is not None:
                    candidates.setdefault("debt", (debt, debt_field, function))
        if not candidates:
            continue

        first_function = next(iter(bundle))
        first_report = bundle[first_function]
        source = sources[first_function]
        period = FinancialPeriod(
            company_id=company.id,
            source_id=source.id,
            period_type="FY",
            fiscal_year=fiscal_year,
            end_date=end_date,
            currency=str(first_report.get("reportedCurrency") or "USD")[:8],
        )
        db.session.add(period)
        db.session.flush()
        normalized = _normalized(period)
        source_map: dict[str, Any] = {}
        for field, (value, provider_field, function) in candidates.items():
            setattr(normalized, field, value)
            field_source = sources[function]
            source_map[field] = {
                "tag": provider_field,
                "source_id": field_source.id,
                "provider": "Alpha Vantage",
                "method": "MISSING_PERIOD_FALLBACK",
                "period_end": end_date.isoformat(),
            }
            db.session.add(Provenance(
                source_id=field_source.id,
                object_type="normalized_financial",
                object_id=str(period.id),
                field_name=field,
                raw_or_normalized="NORMALIZED",
                financial_period_id=period.id,
                provider="Alpha Vantage",
                freshness_at=utcnow(),
                calculation_version=CALCULATION_VERSION,
                notes=f"{provider_field} / missing-period fallback / SEC unavailable for FY{fiscal_year}",
            ))
        _finish_normalized(normalized, source_map, period_type="FY")
        quality = dict(normalized.quality or {})
        quality.update({
            "provider": "Alpha Vantage historical fallback",
            "secondary_fundamental_source": "Alpha Vantage",
            "sec_preferred": True,
            "provider_only_period": True,
        })
        normalized.quality = quality
        existing_by_end[end_date] = period
        existing_by_year[fiscal_year].append(period)
        periods_created += 1
        years_backfilled.append(fiscal_year)

    if periods_created:
        db.session.flush()
        pairs = load_pairs()

    filled = 0
    touched: set[int] = set()
    for period, row in pairs:
        period_key = "annualReports" if period.period_type == "FY" else "quarterlyReports"
        end = period.end_date.isoformat()
        source_map = dict(row.source_map or {})
        quality = dict(row.quality or {})
        for function, payload in payloads.items():
            report = next((
                item for item in payload.get(period_key, [])
                if str(item.get("fiscalDateEnding") or "")[:10] == end
            ), None)
            if not report:
                continue
            source = sources[function]
            mapping = (
                _AV_INCOME_FIELDS if function == "INCOME_STATEMENT"
                else _AV_BALANCE_FIELDS if function == "BALANCE_SHEET"
                else _AV_CASH_FIELDS
            )
            candidates: list[tuple[str, Decimal, str]] = []
            for field, keys in mapping.items():
                if getattr(row, field, None) is not None:
                    continue
                value = _av_decimal(report, keys)
                if value is None:
                    continue
                if field == "capex":
                    value = abs(value)
                candidates.append((
                    field,
                    value,
                    next((key for key in keys if _as_decimal(report.get(key)) is not None), keys[0]),
                ))
            if function == "BALANCE_SHEET" and row.debt is None:
                debt, debt_field = _av_debt(report)
                if debt is not None:
                    candidates.append(("debt", debt, debt_field))
            for field, value, provider_field in candidates:
                setattr(row, field, value)
                source_map[field] = {
                    "tag": provider_field,
                    "source_id": source.id,
                    "provider": "Alpha Vantage",
                    "method": "MISSING_FIELD_FALLBACK",
                    "period_end": end,
                }
                exists = Provenance.query.filter_by(
                    object_type="normalized_financial",
                    object_id=str(period.id),
                    field_name=field,
                    financial_period_id=period.id,
                    provider="Alpha Vantage",
                ).first()
                if not exists:
                    db.session.add(Provenance(
                        source_id=source.id,
                        object_type="normalized_financial",
                        object_id=str(period.id),
                        field_name=field,
                        raw_or_normalized="NORMALIZED",
                        financial_period_id=period.id,
                        provider="Alpha Vantage",
                        freshness_at=utcnow(),
                        calculation_version=CALCULATION_VERSION,
                        notes=f"{provider_field} / missing-field fallback / SEC retained when available",
                    ))
                filled += 1
                touched.add(period.id)
        if period.id in touched:
            _finish_normalized(row, source_map, period_type=period.period_type)
            quality.update({
                "provider": "SEC + Alpha Vantage fallback"
                if not quality.get("provider_only_period")
                else "Alpha Vantage historical fallback",
                "secondary_fundamental_source": "Alpha Vantage",
                "sec_preferred": True,
            })
            row.source_map = source_map
            row.quality = quality

    return {
        "configured": True,
        "filled": filled,
        "periods_created": periods_created,
        "years_backfilled": sorted(set(years_backfilled), reverse=True),
        "sources": sorted(payloads),
    }


def _reconcile_annual_history_issues(company: Company, target_years: int = 10) -> dict[str, Any]:
    """Persist visible quality issues for annual depth and holes between stored FYs."""
    rows = (
        FinancialPeriod.query
        .join(NormalizedFinancial, NormalizedFinancial.financial_period_id == FinancialPeriod.id)
        .filter(FinancialPeriod.company_id == company.id, FinancialPeriod.period_type == "FY")
        .order_by(FinancialPeriod.fiscal_year.desc(), FinancialPeriod.end_date.desc())
        .all()
    )
    years = sorted({int(row.fiscal_year) for row in rows if row.fiscal_year is not None}, reverse=True)
    if years:
        latest, oldest = years[0], years[-1]
        internal_missing = [
            year for year in range(latest, oldest - 1, -1)
            if year not in set(years)
        ][:16]
        target_window = list(range(latest, latest - target_years, -1))
        target_missing = [year for year in target_window if year not in set(years)]
    else:
        internal_missing = []
        target_window = []
        target_missing = []

    open_gap_issues = DataQualityIssue.query.filter_by(
        company_id=company.id,
        object_type="financial_history",
        code="MISSING_FISCAL_YEAR",
        status="OPEN",
    ).all()
    current_internal = {str(year) for year in internal_missing}
    for issue in open_gap_issues:
        if issue.object_id not in current_internal:
            issue.status = "RESOLVED"
            issue.resolved_at = utcnow()
    for year in internal_missing:
        if not any(issue.object_id == str(year) for issue in open_gap_issues):
            db.session.add(DataQualityIssue(
                company_id=company.id,
                object_type="financial_history",
                object_id=str(year),
                code="MISSING_FISCAL_YEAR",
                severity="REVIEW",
                message=f"FY{year} is missing between stored fiscal years. Refresh SEC data and configured secondary fundamentals before relying on trend analysis.",
            ))

    depth = DataQualityIssue.query.filter_by(
        company_id=company.id,
        object_type="financial_history",
        object_id="10Y",
        code="ANNUAL_HISTORY_DEPTH",
        status="OPEN",
    ).first()
    if len(years) < target_years:
        if depth is None:
            db.session.add(DataQualityIssue(
                company_id=company.id,
                object_type="financial_history",
                object_id="10Y",
                code="ANNUAL_HISTORY_DEPTH",
                severity="REVIEW",
                message=(
                    f"Only {len(years)} fiscal year(s) are stored versus the {target_years}Y research target. "
                    "This can reflect a shorter issuer history or incomplete provider coverage; review before relying on long-term trends."
                ),
            ))
        else:
            depth.message = (
                f"Only {len(years)} fiscal year(s) are stored versus the {target_years}Y research target. "
                "This can reflect a shorter issuer history or incomplete provider coverage; review before relying on long-term trends."
            )
    elif depth is not None:
        depth.status = "RESOLVED"
        depth.resolved_at = utcnow()

    return {
        "target_years": target_years,
        "stored_years": len(years),
        "years": years[:16],
        "target_window": target_window,
        "target_missing_years": target_missing,
        "internal_missing_years": internal_missing,
        "complete": len(years) >= target_years and not target_missing,
    }



def refresh_company_fundamentals(company: Company, security: Security, user_id: int) -> dict[str, Any]:
    user_agent = _ua(user_id)
    meta = _ticker_meta(security.ticker, user_agent)
    companyfacts = _json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json", user_agent)
    source = _source_for(company, meta, user_agent, companyfacts)

    fiscal_year_end = meta.get("fiscal_year_end") or ""
    duration = {key: _annual_duration(companyfacts, tags, fiscal_year_end) for key, tags in DURATION_TAGS.items()}
    instant = {key: _annual_instant(companyfacts, tags, fiscal_year_end=fiscal_year_end) for key, tags in INSTANT_TAGS.items()}

    # Companyfacts can expose a perfectly valid consolidated statement concept
    # under the filer's own taxonomy. Use exact statement-label matches only when
    # the canonical US-GAAP mapping did not resolve that fiscal period.
    for field in DURATION_TAGS:
        for namespace, tags in _semantic_tag_groups(companyfacts, field).items():
            fallback_rows = _annual_duration(companyfacts, tags, fiscal_year_end, namespace=namespace)
            _merge_missing(duration[field], _mark_semantic_records(fallback_rows, namespace))
    for field in INSTANT_TAGS:
        for namespace, tags in _semantic_tag_groups(companyfacts, field).items():
            fallback_rows = _annual_instant(companyfacts, tags, namespace=namespace, fiscal_year_end=fiscal_year_end)
            _merge_missing(instant[field], _mark_semantic_records(fallback_rows, namespace))

    debt_current_annual = _annual_instant(companyfacts, DEBT_CURRENT_TAGS, fiscal_year_end=fiscal_year_end)
    debt_noncurrent_annual = _annual_instant(companyfacts, DEBT_NONCURRENT_TAGS, fiscal_year_end=fiscal_year_end)
    debt_short_annual = _annual_instant(companyfacts, DEBT_SHORT_TERM_TAGS, fiscal_year_end=fiscal_year_end)

    economic_duration_annual, economic_instant_annual = _economic_maps_annual(companyfacts, fiscal_year_end)

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
                source_map[field] = {"tag": rec.get("tag"), "namespace": rec.get("_mf_namespace") or rec.get("namespace") or "us-gaap", "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id, "method": "SEMANTIC_LABEL_FALLBACK" if rec.get("_mf_semantic_fallback") else "DIRECT_FY"}
        for field, records in instant.items():
            rec = records.get(fy)
            _record_raw(period, source, rec)
            setattr(normalized, field, _as_decimal((rec or {}).get("val")))
            if rec:
                source_map[field] = {
                    "tag": rec.get("tag"), "namespace": rec.get("_mf_namespace") or rec.get("namespace") or "us-gaap",
                    "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id,
                    "method": "SEMANTIC_LABEL_FALLBACK" if rec.get("_mf_semantic_fallback") else "DIRECT_FY",
                }

        debt_value, debt_records, debt_method = _compose_debt(
            instant.get("debt", {}).get(fy), debt_current_annual.get(fy), debt_noncurrent_annual.get(fy), debt_short_annual.get(fy)
        )
        if debt_value is not None:
            normalized.debt = debt_value
            for raw in debt_records:
                _record_raw(period, source, raw)
            source_map["debt"] = {
                "tag": " + ".join(str(raw.get("tag") or "") for raw in debt_records),
                "namespace": "us-gaap", "source_id": source.id, "method": debt_method,
                "accession": next((raw.get("accn") for raw in debt_records if raw.get("accn")), None),
                "filed": next((raw.get("filed") for raw in debt_records if raw.get("filed")), None),
            }
        elif str((instant.get("debt", {}).get(fy) or {}).get("tag") or "") == "LongTermDebt":
            normalized.debt = None
            source_map.pop("debt", None)
        economic_facts, economic_sources = _economic_fact_bundle(
            economic_duration_annual, economic_instant_annual, fy
        )
        economic_snapshot = build_economic_reality(
            _economic_row_dict(normalized),
            facts=economic_facts,
            fact_sources=economic_sources,
            company_type=meta.get("sic_description") or "",
        )
        _finish_normalized(
            normalized, source_map, period_type="FY", economic_reality=economic_snapshot
        )
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
        for namespace, semantic_tags in _semantic_tag_groups(companyfacts, field).items():
            semantic_annual = _annual_duration(companyfacts, semantic_tags, fiscal_year_end, namespace=namespace)
            semantic_quarters = _quarter_duration_values(
                companyfacts, semantic_tags, semantic_annual,
                shares_metric=(field == "diluted_shares"), fiscal_year_end=fiscal_year_end, namespace=namespace,
            )
            _merge_missing(quarter_duration[field], _mark_semantic_records(semantic_quarters, namespace))

    quarter_instant = {field: _quarter_instants(companyfacts, tags, fiscal_year_end=fiscal_year_end) for field, tags in INSTANT_TAGS.items()}
    for field in INSTANT_TAGS:
        for namespace, semantic_tags in _semantic_tag_groups(companyfacts, field).items():
            semantic_quarters = _quarter_instants(companyfacts, semantic_tags, namespace=namespace, fiscal_year_end=fiscal_year_end)
            _merge_missing(quarter_instant[field], _mark_semantic_records(semantic_quarters, namespace))

    debt_current_quarter = _quarter_instants(companyfacts, DEBT_CURRENT_TAGS, fiscal_year_end=fiscal_year_end)
    debt_noncurrent_quarter = _quarter_instants(companyfacts, DEBT_NONCURRENT_TAGS, fiscal_year_end=fiscal_year_end)
    debt_short_quarter = _quarter_instants(companyfacts, DEBT_SHORT_TERM_TAGS, fiscal_year_end=fiscal_year_end)

    economic_duration_quarter, economic_instant_quarter = _economic_maps_quarter(companyfacts, fiscal_year_end)

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
                    "filed": (record or {}).get("filed"), "source_id": source.id,
                    "namespace": (record or {}).get("_mf_namespace") or (record or {}).get("namespace") or "us-gaap",
                    "method": "SEMANTIC_LABEL_FALLBACK" if (record or {}).get("_mf_semantic_fallback") else info.get("method"),
                }
        for field, records in quarter_instant.items():
            rec = records.get((fy, fp))
            _record_raw(period, source, rec)
            setattr(normalized, field, _as_decimal((rec or {}).get("val")))
            if rec:
                source_map[field] = {
                    "tag": rec.get("tag"), "namespace": rec.get("_mf_namespace") or rec.get("namespace") or "us-gaap",
                    "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": source.id,
                    "method": "SEMANTIC_LABEL_FALLBACK" if rec.get("_mf_semantic_fallback") else "DIRECT_INSTANT",
                }

        debt_value, debt_records, debt_method = _compose_debt(
            quarter_instant.get("debt", {}).get((fy, fp)),
            debt_current_quarter.get((fy, fp)), debt_noncurrent_quarter.get((fy, fp)), debt_short_quarter.get((fy, fp)),
        )
        if debt_value is not None:
            normalized.debt = debt_value
            for raw in debt_records:
                _record_raw(period, source, raw)
            source_map["debt"] = {
                "tag": " + ".join(str(raw.get("tag") or "") for raw in debt_records),
                "namespace": "us-gaap", "source_id": source.id, "method": debt_method,
                "accession": next((raw.get("accn") for raw in debt_records if raw.get("accn")), None),
                "filed": next((raw.get("filed") for raw in debt_records if raw.get("filed")), None),
            }
        elif str((quarter_instant.get("debt", {}).get((fy, fp)) or {}).get("tag") or "") == "LongTermDebt":
            normalized.debt = None
            source_map.pop("debt", None)
        economic_facts, economic_sources = _economic_fact_bundle(
            economic_duration_quarter, economic_instant_quarter, (fy, fp)
        )
        economic_snapshot = build_economic_reality(
            _economic_row_dict(normalized),
            facts=economic_facts,
            fact_sources=economic_sources,
            company_type=meta.get("sic_description") or "",
        )
        _finish_normalized(
            normalized, source_map, period_type=fp, economic_reality=economic_snapshot
        )
        quarter_saved += 1

    fallback = _alpha_vantage_fill_missing(company, security, user_id)
    annual_history = _reconcile_annual_history_issues(company, target_years=10)

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
        "fundamental_fallback": fallback,
        "annual_history": annual_history,
    }
