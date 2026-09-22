from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any, Iterable

import requests
from sqlalchemy import or_

from .extensions import db
from .data_providers import get_secret
from .core_models import Company, DataQualityIssue, FinancialPeriod, NormalizedFinancial, Provenance, RawFinancialFact, Security, Source
from .economic_reality import DURATION_TAGS as ECONOMIC_DURATION_TAGS, INSTANT_TAGS as ECONOMIC_INSTANT_TAGS, build_economic_reality
from .sec_inline_facts import extract_extension_concepts

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
CALCULATION_VERSION = "0.2.0"
SEC_NORMALIZER_VERSION = "0.3.6-fiscal-instant-integrity-r3"

DURATION_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfProductsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "gross_profit": ["GrossProfit"],
    "operating_expenses": ["OperatingExpenses"],
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
    "operating_expenses": {"operating expenses", "total operating expenses", "operating costs and expenses", "total operating costs and expenses"},
    "operating_income": {"operating income", "income from operations", "operating income loss"},
    "pretax_income": {"income before income taxes", "income before taxes", "earnings before income taxes"},
    "income_tax": {"income tax expense", "provision for income taxes", "income taxes"},
    "net_income": {"net income", "net income loss"},
    "cfo": {"net cash provided by operating activities", "cash provided by operations", "net cash provided by used in operating activities"},
    "capex": {"capital expenditures", "additions to property plant and equipment", "purchases of property plant and equipment"},
    "buybacks": {"repurchases of common stock", "payments for repurchase of common stock", "share repurchases"},
    "dividends": {"dividends paid", "payments of dividends", "common stock dividends paid"},
    "diluted_shares": {"weighted average diluted shares outstanding", "weighted average number of diluted shares outstanding"},
    "cash": {"cash and cash equivalents", "cash and equivalents"},
    "receivables": {"accounts receivable net", "accounts receivable"},
    "inventory": {"inventories", "inventory", "inventories net", "total inventories"},
    "payables": {"accounts payable", "accounts payable current"},
    "assets": {"total assets"},
    "liabilities": {"total liabilities"},
    "equity": {"total shareholders equity", "total stockholders equity", "shareholders equity", "stockholders equity"},
    "shares_outstanding": {"common shares outstanding", "common stock shares outstanding", "shares outstanding"},
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

SGA_CANDIDATE_TAGS = ("SellingGeneralAndAdministrativeExpense",)
SGA_LABEL_ALIASES = {
    "selling general and administrative expense",
    "selling general administrative expense",
    "total selling and administrative expense",
    "total selling general and administrative expense",
}
NONOPERATING_TOTAL_TAGS = ("NonoperatingIncomeExpense",)
NONOPERATING_COMPONENT_TAGS = (
    "InterestIncomeExpenseNonoperatingNet",
    "InterestExpenseNonOperating",
    "OtherNonoperatingIncomeExpense",
)



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


def _semantic_tag_groups_for_aliases(companyfacts: dict, aliases: Iterable[str]) -> dict[str, list[str]]:
    normalized = {_normalize_label(x) for x in aliases}
    out: dict[str, list[str]] = defaultdict(list)
    if not normalized:
        return out
    for namespace, concepts in (companyfacts.get("facts") or {}).items():
        for tag, node in (concepts or {}).items():
            if _normalize_label((node or {}).get("label")) in normalized:
                out[str(namespace)].append(str(tag))
    return out


def _semantic_tag_groups(companyfacts: dict, field: str) -> dict[str, list[str]]:
    """Find exact statement-label concepts across standard and filer taxonomies.

    This runs only as a missing-field fallback. Matching is deliberately exact
    after punctuation/whitespace normalization so a segment or similarly named
    disclosure is not silently treated as a consolidated statement fact.
    """
    return _semantic_tag_groups_for_aliases(companyfacts, SEMANTIC_LABEL_ALIASES.get(field, set()))


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


def _filing_extension_source(
    company: Company,
    *,
    cik: str,
    accession: str,
    form: str,
    filed: str,
    primary_document: str,
) -> Source:
    base = f"https://www.sec.gov/Archives/edgar/data/{int(str(cik))}/{str(accession).replace('-', '')}"
    url = f"{base}/{primary_document}" if primary_document else base
    source = Source.query.filter_by(
        company_id=company.id,
        provider="SEC",
        source_type="FILING_XBRL_EXTENSION",
        accession_no=str(accession or ""),
    ).first()
    if source is None:
        source = Source(
            company_id=company.id,
            provider="SEC",
            source_type="FILING_XBRL_EXTENSION",
            title=f"{form} filing-level XBRL extension fallback",
            url=url,
            accession_no=str(accession or ""),
            published_at=datetime.fromisoformat(str(filed)[:10]) if filed else None,
            retrieved_at=utcnow(),
            meta={"form": form, "cik": cik, "fallback_only": True},
        )
        db.session.add(source)
        db.session.flush()
    else:
        source.url = source.url or url
        source.retrieved_at = utcnow()
    return source


def _extension_fallback_needed(
    duration: dict[str, dict],
    instant: dict[str, dict],
    companyfacts: dict,
    fiscal_year_end: str,
) -> bool:
    years = sorted(set().union(*(set(rows) for rows in duration.values()), *(set(rows) for rows in instant.values())))
    if not years:
        return True
    latest = years[-1]
    operating_company_evidence = (
        duration.get("gross_profit", {}).get(latest) is not None
        or duration.get("cogs", {}).get(latest) is not None
    )
    if duration.get("revenue", {}).get(latest) is None:
        return True
    if operating_company_evidence and duration.get("operating_income", {}).get(latest) is None:
        return True
    if operating_company_evidence and instant.get("inventory", {}).get(latest) is None:
        return True

    # If Inventory is applicable annually but absent from the most recent stored
    # quarter, a custom filing extension may be the missing current fact.
    if any(instant.get("inventory", {}).values()):
        quarter_inventory = _quarter_instants(
            companyfacts, INSTANT_TAGS["inventory"], fiscal_year_end=fiscal_year_end
        )
        quarter_revenue_direct, quarter_revenue_ytd = _quarter_duration_sources(
            companyfacts, DURATION_TAGS["revenue"], fiscal_year_end
        )
        if (quarter_revenue_direct or quarter_revenue_ytd) and not quarter_inventory:
            return True
    return False


def _augment_companyfacts_with_recent_filing_extensions(
    company: Company,
    companyfacts: dict,
    meta: dict[str, str],
    user_agent: str,
    *,
    max_filings: int = 5,
) -> dict[str, Any]:
    """Add exact-label custom filing facts only when standard Companyfacts is thin."""
    try:
        submissions = _json(f"{SEC_DATA}/submissions/CIK{meta['cik']}.json", user_agent)
    except Exception:
        return {"attempted": True, "filings_scanned": 0, "concepts_added": 0, "facts_added": 0}

    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    filed = recent.get("filingDate") or []
    docs = recent.get("primaryDocument") or []

    selected: list[int] = []
    k_count = 0
    q_count = 0
    for idx, form in enumerate(forms):
        upper = str(form or "").upper()
        if upper in {"10-K", "10-K/A"} and k_count < 1:
            selected.append(idx); k_count += 1
        elif upper in {"10-Q", "10-Q/A"} and q_count < 4:
            selected.append(idx); q_count += 1
        if len(selected) >= max_filings:
            break

    label_aliases = {field: set(aliases) for field, aliases in SEMANTIC_LABEL_ALIASES.items()}
    label_aliases["_sga_candidate"] = set(SGA_LABEL_ALIASES)
    extension_namespace = (companyfacts.setdefault("facts", {})).setdefault("filing-extension", {})
    filings_scanned = concepts_added = facts_added = 0

    for idx in selected:
        form = str(forms[idx] if idx < len(forms) else "")
        accession = str(accns[idx] if idx < len(accns) else "")
        primary = str(docs[idx] if idx < len(docs) else "")
        filed_at = str(filed[idx] if idx < len(filed) else "")
        if not accession:
            continue
        concepts = extract_extension_concepts(
            cik=str(meta["cik"]),
            accession=accession,
            primary_document=primary,
            form=form,
            filed=filed_at,
            user_agent=user_agent,
            label_aliases=label_aliases,
        )
        filings_scanned += 1
        if not concepts:
            continue
        filing_source = _filing_extension_source(
            company,
            cik=str(meta["cik"]),
            accession=accession,
            form=form,
            filed=filed_at,
            primary_document=primary,
        )
        for tag, node in concepts.items():
            target = extension_namespace.setdefault(
                tag,
                {"label": node.get("label") or tag, "units": defaultdict(list)},
            )
            if not isinstance(target.get("units"), defaultdict):
                target["units"] = defaultdict(list, target.get("units") or {})
            before = sum(len(rows) for rows in target["units"].values())
            for unit, rows in (node.get("units") or {}).items():
                for raw in rows:
                    record = dict(raw)
                    record["_mf_source_id"] = filing_source.id
                    record["_mf_derived_method"] = "FILING_EXTENSION_LABEL_FALLBACK"
                    target["units"][unit].append(record)
            after = sum(len(rows) for rows in target["units"].values())
            if before == 0 and after > 0:
                concepts_added += 1
            facts_added += max(0, after - before)

    for node in extension_namespace.values():
        if isinstance(node.get("units"), defaultdict):
            node["units"] = dict(node["units"])
    return {
        "attempted": True,
        "filings_scanned": filings_scanned,
        "concepts_added": concepts_added,
        "facts_added": facts_added,
    }


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
    """Resolve represented fiscal year from the fact period end.

    The SEC fiscalYearEnd value is a current MMDD convention. 52/53-week issuers
    can close a historical fiscal year a few days before or after that MMDD.
    Treating the MMDD as an exact cutover silently shifts those annual facts into
    the next fiscal year and creates fake history holes. Comparative Companyfacts
    rows also make row['fy'] unsafe as the primary key, so the represented end
    date remains canonical.

    A 21-day year-end tolerance is deliberately much smaller than a fiscal
    quarter: dates near nominal FYE stay in the same represented year, while
    genuine post-year-end quarter dates still map to the following fiscal year.
    """
    try:
        end = date.fromisoformat(str(row.get("end") or "")[:10])
    except Exception:
        return None
    fye = str(fiscal_year_end or "").strip()
    if len(fye) == 4 and fye.isdigit():
        month, day = int(fye[:2]), int(fye[2:])
        if 1 <= month <= 12 and 1 <= day <= 31:
            candidate_day = day
            nominal = None
            while candidate_day >= 28:
                try:
                    nominal = date(end.year, month, candidate_day)
                    break
                except ValueError:
                    candidate_day -= 1
            if nominal is not None:
                delta_days = (end - nominal).days
                if abs(delta_days) <= 21:
                    return end.year
                return end.year + (1 if delta_days > 0 else 0)
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
    raw = source_map.get(field)
    prior = dict(raw) if isinstance(raw, dict) else ({"prior_source": str(raw)} if raw else {})
    prior["refresh_state"] = "LAST_GOOD_RETAINED"
    prior["refresh_note"] = "Current provider refresh missed this same-period fact; prior sourced value retained."
    source_map[field] = prior


def _fact_value(record: dict[str, Any] | None) -> Decimal | None:
    return _as_decimal((record or {}).get("val"))


def _synthetic_record(
    anchor: dict[str, Any] | None,
    value: Decimal,
    method: str,
    derived_from: Iterable[dict[str, Any] | None] = (),
) -> dict[str, Any]:
    record = dict(anchor or {})
    record["val"] = value
    record["tag"] = "DERIVED"
    record["namespace"] = "derived"
    record["_mf_derived_method"] = method
    record["_mf_derived_from_tags"] = [
        str(item.get("tag") or "") for item in derived_from if item and item.get("tag")
    ]
    return record


def _reconciles(a: Decimal | None, b: Decimal | None, tolerance: Decimal = Decimal("0.015")) -> bool:
    if a is None or b is None:
        return False
    scale = max(Decimal("1"), abs(a), abs(b))
    return abs(a - b) <= scale * tolerance


def _nonoperating_magnitudes(
    total: dict[str, Any] | None,
    components: Iterable[dict[str, Any] | None],
) -> list[Decimal]:
    candidates: set[Decimal] = set()
    total_value = _fact_value(total)
    if total_value is not None:
        candidates.add(abs(total_value))
    values = [_fact_value(row) for row in components]
    values = [value for value in values if value is not None]
    if values:
        candidates.add(abs(sum(values, Decimal("0"))))
        candidates.add(sum((abs(value) for value in values), Decimal("0")))
    return [value for value in candidates if value >= 0]


def _validated_sga_operating_bridge(
    gross_profit: dict[str, Any] | None,
    sga: dict[str, Any] | None,
    pretax: dict[str, Any] | None,
    nonoperating_total: dict[str, Any] | None,
    nonoperating_components: Iterable[dict[str, Any] | None],
) -> tuple[Decimal | None, Decimal | None]:
    """Resolve Nike-like statements without assuming SGA is always total OpEx.

    SellingGeneralAndAdministrativeExpense is accepted as the complete operating
    expense layer only when the resulting operating income reconciles to pre-tax
    income through independently reported non-operating evidence (or the gap is
    immaterial). This prevents a software company with separate R&D from having
    SGA silently misclassified as total operating expenses.
    """
    gp, sga_value, pretax_value = _fact_value(gross_profit), _fact_value(sga), _fact_value(pretax)
    if gp is None or sga_value is None or pretax_value is None:
        return None, None
    candidate = gp - sga_value
    gap = abs(pretax_value - candidate)
    scale = max(Decimal("1"), abs(pretax_value), abs(candidate), abs(gp))
    if gap <= scale * Decimal("0.015"):
        return sga_value, candidate
    # SGA is a candidate component, not a license to manufacture a subtotal.
    # If the missing bridge is large relative to the operating statement, leave
    # it unresolved even when an unrelated non-operating amount happens to match.
    if gap > max(Decimal("1"), abs(gp), abs(candidate)) * Decimal("0.12"):
        return None, None
    for magnitude in _nonoperating_magnitudes(nonoperating_total, nonoperating_components):
        if _reconciles(gap, magnitude):
            return sga_value, candidate
    return None, None


def _apply_annual_statement_bridges(
    duration: dict[str, dict[int, dict[str, Any]]],
    *,
    sga: dict[int, dict[str, Any]],
    nonoperating_total: dict[int, dict[str, Any]],
    nonoperating_components: dict[str, dict[int, dict[str, Any]]],
) -> None:
    years = set().union(*(set(rows) for rows in duration.values()), set(sga), set(nonoperating_total))
    for fy in years:
        gp = duration.get("gross_profit", {}).get(fy)
        op_exp = duration.get("operating_expenses", {}).get(fy)
        op_income = duration.get("operating_income", {}).get(fy)
        if op_income is None and gp is not None and op_exp is not None:
            gp_value, op_exp_value = _fact_value(gp), _fact_value(op_exp)
            if gp_value is not None and op_exp_value is not None:
                duration["operating_income"][fy] = _synthetic_record(
                    gp, gp_value - op_exp_value, "GROSS_PROFIT_MINUS_OPERATING_EXPENSES", (gp, op_exp)
                )
                op_income = duration["operating_income"][fy]
        if op_exp is None and gp is not None and op_income is not None:
            gp_value, op_income_value = _fact_value(gp), _fact_value(op_income)
            if gp_value is not None and op_income_value is not None:
                duration["operating_expenses"][fy] = _synthetic_record(
                    gp, gp_value - op_income_value, "GROSS_PROFIT_MINUS_OPERATING_INCOME", (gp, op_income)
                )
                op_exp = duration["operating_expenses"][fy]
        if op_income is not None or op_exp is not None:
            continue
        sga_row = sga.get(fy)
        pretax = duration.get("pretax_income", {}).get(fy)
        validated_expense, validated_income = _validated_sga_operating_bridge(
            gp, sga_row, pretax, nonoperating_total.get(fy),
            [rows.get(fy) for rows in nonoperating_components.values()],
        )
        if validated_expense is None or validated_income is None:
            continue
        expense_record = dict(sga_row or {})
        expense_record["_mf_derived_method"] = "VALIDATED_SGA_AS_OPERATING_EXPENSES"
        expense_record["_mf_derived_from_tags"] = [str((sga_row or {}).get("tag") or "")]
        duration["operating_expenses"][fy] = expense_record
        duration["operating_income"][fy] = _synthetic_record(
            gp, validated_income, "VALIDATED_GROSS_PROFIT_MINUS_SGA",
            [gp, sga_row, pretax, nonoperating_total.get(fy), *[rows.get(fy) for rows in nonoperating_components.values()]],
        )


def _quarter_info_value(info: dict[str, Any] | None) -> Decimal | None:
    return _as_decimal((info or {}).get("value"))


def _apply_quarter_statement_bridges(
    quarter_duration: dict[str, dict[tuple[int, str], dict[str, Any]]],
    *,
    annual_duration: dict[str, dict[int, dict[str, Any]]],
    sga: dict[tuple[int, str], dict[str, Any]],
    nonoperating_total: dict[tuple[int, str], dict[str, Any]],
    nonoperating_components: dict[str, dict[tuple[int, str], dict[str, Any]]],
) -> None:
    keys = set().union(*(set(rows) for rows in quarter_duration.values()), set(sga), set(nonoperating_total))
    for key in sorted(keys):
        gp_info = quarter_duration.get("gross_profit", {}).get(key)
        op_exp_info = quarter_duration.get("operating_expenses", {}).get(key)
        op_income_info = quarter_duration.get("operating_income", {}).get(key)
        gp_value = _quarter_info_value(gp_info)
        if op_income_info is None and gp_value is not None and _quarter_info_value(op_exp_info) is not None:
            op_exp_value = _quarter_info_value(op_exp_info)
            quarter_duration["operating_income"][key] = {
                "value": gp_value - op_exp_value,
                "record": (gp_info or {}).get("record"),
                "derived_from": [(gp_info or {}).get("record"), (op_exp_info or {}).get("record")],
                "method": "GROSS_PROFIT_MINUS_OPERATING_EXPENSES",
            }
            op_income_info = quarter_duration["operating_income"][key]
        if op_exp_info is None and gp_value is not None and _quarter_info_value(op_income_info) is not None:
            op_income_value = _quarter_info_value(op_income_info)
            quarter_duration["operating_expenses"][key] = {
                "value": gp_value - op_income_value,
                "record": (gp_info or {}).get("record"),
                "derived_from": [(gp_info or {}).get("record"), (op_income_info or {}).get("record")],
                "method": "GROSS_PROFIT_MINUS_OPERATING_INCOME",
            }
            op_exp_info = quarter_duration["operating_expenses"][key]
        if op_income_info is not None or op_exp_info is not None:
            continue

        sga_info = sga.get(key) or {}
        pretax_info = quarter_duration.get("pretax_income", {}).get(key) or {}
        gp_record = (gp_info or {}).get("record")
        sga_record = sga_info.get("record")
        pretax_record = pretax_info.get("record")
        total_info = nonoperating_total.get(key) or {}
        component_infos = [rows.get(key) or {} for rows in nonoperating_components.values()]
        # Validation operates on fact-shaped records; use quarter-only values so
        # YTD source records do not distort the accounting bridge.
        gp_fact = _synthetic_record(gp_record, gp_value, "QUARTER_VALUE") if gp_value is not None else None
        sga_value = _quarter_info_value(sga_info)
        pretax_value = _quarter_info_value(pretax_info)
        total_value = _quarter_info_value(total_info)
        sga_fact = _synthetic_record(sga_record, sga_value, "QUARTER_VALUE") if sga_value is not None else None
        pretax_fact = _synthetic_record(pretax_record, pretax_value, "QUARTER_VALUE") if pretax_value is not None else None
        total_fact = _synthetic_record(total_info.get("record"), total_value, "QUARTER_VALUE") if total_value is not None else None
        component_facts = [
            _synthetic_record(info.get("record"), value, "QUARTER_VALUE")
            for info in component_infos
            if (value := _quarter_info_value(info)) is not None
        ]
        validated_expense, validated_income = _validated_sga_operating_bridge(
            gp_fact, sga_fact, pretax_fact, total_fact, component_facts
        )
        if validated_expense is None or validated_income is None:
            continue
        quarter_duration["operating_expenses"][key] = {
            "value": validated_expense,
            "record": sga_record,
            "derived_from": [sga_record, gp_record, pretax_record],
            "method": "VALIDATED_SGA_AS_OPERATING_EXPENSES",
        }
        quarter_duration["operating_income"][key] = {
            "value": validated_income,
            "record": gp_record,
            "derived_from": [gp_record, sga_record, pretax_record, total_info.get("record"), *[info.get("record") for info in component_infos]],
            "method": "VALIDATED_GROSS_PROFIT_MINUS_SGA",
        }

    # Once Q1-Q3 have been repaired, derive missing Q4 operating rows from
    # the compatible annual total. This is the same exact FY - Q1 - Q2 - Q3
    # bridge used for other duration facts.
    fiscal_years = sorted({fy for fy, _ in keys} | set(annual_duration.get("operating_income", {})) | set(annual_duration.get("operating_expenses", {})))
    for fy in fiscal_years:
        for field in ("operating_expenses", "operating_income"):
            if (fy, "Q4") in quarter_duration.get(field, {}):
                continue
            annual_record = annual_duration.get(field, {}).get(fy)
            annual_value = _fact_value(annual_record)
            quarter_values = [
                _quarter_info_value(quarter_duration.get(field, {}).get((fy, fp)))
                for fp in ("Q1", "Q2", "Q3")
            ]
            if annual_value is None or any(value is None for value in quarter_values):
                continue
            quarter_duration[field][(fy, "Q4")] = {
                "value": annual_value - sum(quarter_values, Decimal("0")),
                "record": annual_record,
                "derived_from": [
                    *((quarter_duration.get(field, {}).get((fy, fp)) or {}).get("record") for fp in ("Q1", "Q2", "Q3")),
                    annual_record,
                ],
                "method": "FY_MINUS_Q1_Q2_Q3",
            }


def _weighted_average_increment(current: dict[str, Any] | None, previous: dict[str, Any] | None) -> Decimal | None:
    current_value, previous_value = _fact_value(current), _fact_value(previous)
    current_days, previous_days = _duration_days(current or {}), _duration_days(previous or {})
    if current_value is None or previous_value is None or current_days is None or previous_days is None:
        return None
    increment_days = current_days - previous_days
    if current_days <= previous_days or increment_days <= 0:
        return None
    return (current_value * Decimal(current_days) - previous_value * Decimal(previous_days)) / Decimal(increment_days)


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


def _annual_instant_matches_fye(row: dict[str, Any], fiscal_year_end: str = "", *, tolerance_days: int = 21) -> bool:
    """Return True only when a 10-K instant represents the fiscal-year balance sheet.

    10-K Companyfacts also contains cover-page instants such as
    EntityCommonStockSharesOutstanding measured days or weeks after fiscal
    year-end. Those are valid facts, but they are not a new fiscal year. Treating
    their as-of date as an FY end can create a phantom next-year period containing
    little more than a share count.
    """
    fye = str(fiscal_year_end or "").strip()
    if len(fye) != 4 or not fye.isdigit():
        return True
    try:
        end = date.fromisoformat(str(row.get("end") or "")[:10])
        month, day = int(fye[:2]), int(fye[2:])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return True
        candidate_day = day
        nominal = None
        while candidate_day >= 28:
            try:
                nominal = date(end.year, month, candidate_day)
                break
            except ValueError:
                candidate_day -= 1
        return nominal is not None and abs((end - nominal).days) <= int(tolerance_days)
    except Exception:
        return False


def _annual_instant(companyfacts: dict, tags: Iterable[str], namespace: str = "us-gaap", fiscal_year_end: str = "") -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for tag in tags:
        candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _facts(companyfacts, namespace, tag):
            if row.get("form") not in {"10-K", "10-K/A"} or row.get("start"):
                continue
            if not _annual_instant_matches_fye(row, fiscal_year_end):
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
                previous_ytd = ytd.get((fy, prev_fp)) or (direct.get((fy, "Q1")) if prev_fp == "Q1" else None)
                increment = _weighted_average_increment(current_ytd, previous_ytd)
                if current_ytd and previous_ytd and increment is not None:
                    out[(fy, fp)] = {
                        "value": increment,
                        "record": current_ytd,
                        "derived_from": [previous_ytd, current_ytd],
                        "method": "YTD_WEIGHTED_AVERAGE_DIFFERENCE",
                    }
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
                q3_ytd = ytd.get((fy, "Q3"))
                increment = _weighted_average_increment(annual_record, q3_ytd)
                if q3_ytd and increment is not None:
                    out[(fy, "Q4")] = {
                        "value": increment,
                        "record": annual_record,
                        "derived_from": [q3_ytd, annual_record],
                        "method": "FY_MINUS_9M_WEIGHTED_AVERAGE",
                    }
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
            "normalizer_version": SEC_NORMALIZER_VERSION,
        },
    )
    db.session.add(source)
    db.session.flush()
    return source


def _record_raw(period: FinancialPeriod, source: Source, record: dict | None) -> None:
    if not record:
        return
    if str(record.get("tag") or "") == "DERIVED" or str(record.get("namespace") or "") == "derived":
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
        source_id=int(record.get("_mf_source_id") or source.id),
        taxonomy=str(record.get("namespace") or "us-gaap"),
        tag=str(record.get("tag") or ""),
        unit=str(record.get("unit") or ""),
        value=value,
        context_hash=context_hash,
        filed_at=date.fromisoformat(str(record.get("filed"))[:10]) if record.get("filed") else None,
        raw_payload={k: record.get(k) for k in ("fy", "fp", "form", "start", "end", "filed", "accn", "frame")},
    ))


def _period_family_query(company_id: int, end_date: date, period_type: str):
    query = FinancialPeriod.query.filter_by(company_id=company_id, end_date=end_date)
    if period_type == "FY":
        family_types = ("FY",)
    else:
        family_types = ("Q1", "Q2", "Q3", "Q4")
    clauses = []
    for family_type in family_types:
        clauses.extend((
            FinancialPeriod.period_type == family_type,
            FinancialPeriod.period_type == f"SUPERSEDED_{family_type}",
            FinancialPeriod.period_type.like(f"SUP_{family_type}_%"),
        ))
    return query.filter(or_(*clauses))


_PERIOD_RECOVERY_FIELDS = (
    "revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
    "pretax_income", "income_tax", "net_income", "cfo", "capex", "fcf",
    "cash", "debt", "receivables", "inventory", "payables", "assets",
    "liabilities", "equity", "shares_outstanding", "diluted_shares",
    "buybacks", "dividends",
)


def _period_evidence_score(period: FinancialPeriod, *, period_type: str, fiscal_year: int) -> tuple:
    normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
    populated = 0
    sourced = 0
    if normalized is not None:
        populated = sum(1 for field in _PERIOD_RECOVERY_FIELDS if getattr(normalized, field, None) is not None)
        source_map = dict(normalized.source_map or {})
        sourced = sum(1 for field in _PERIOD_RECOVERY_FIELDS if source_map.get(field))
    exact_identity = int(str(period.period_type or "") == period_type and int(period.fiscal_year or 0) == int(fiscal_year))
    active = int(not str(period.period_type or "").startswith(("SUPERSEDED_", "SUP_")))
    filed = period.filed_at.toordinal() if period.filed_at else 0
    return (populated, sourced, exact_identity, active, filed, int(period.id or 0))


def _supersede_period_identity(period: FinancialPeriod) -> None:
    original = str(period.period_type or "")
    if original.startswith("SUPERSEDED_") or original.startswith("SUP_"):
        return
    target = f"SUPERSEDED_{original}"[:16]
    collision = FinancialPeriod.query.filter_by(
        company_id=period.company_id,
        period_type=target,
        fiscal_year=period.fiscal_year,
        end_date=period.end_date,
    ).filter(FinancialPeriod.id != period.id).first()
    if collision is not None:
        target = f"SUP_{original}_{period.id}"[:16]
    period.period_type = target
    normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
    if normalized is not None:
        quality = dict(normalized.quality or {})
        quality["period_identity_state"] = "SUPERSEDED"
        quality["superseded_period_type"] = original
        normalized.quality = quality


def _upsert_period(company: Company, source: Source, *, period_type: str, fiscal_year: int, end_date: date, anchor: dict[str, Any] | None) -> FinancialPeriod:
    family = _period_family_query(company.id, end_date, period_type).order_by(FinancialPeriod.id.desc()).all()

    # A parser revision must never promote a sparse shell merely because it is the
    # newest/active row. Pick the richest factual identity across the full same-end
    # family (including audit-preserved superseded rows), then make that row the
    # single active canonical identity for the represented period.
    period = max(
        family,
        key=lambda row: _period_evidence_score(row, period_type=period_type, fiscal_year=fiscal_year),
        default=None,
    )
    if period is None:
        period = FinancialPeriod(company_id=company.id, source_id=source.id, period_type=period_type, fiscal_year=fiscal_year, end_date=end_date, currency="USD")
        db.session.add(period)
        db.session.flush()
    else:
        period.period_type = period_type
        period.fiscal_year = fiscal_year

    for sibling in family:
        if sibling.id != period.id and not str(sibling.period_type or "").startswith(("SUPERSEDED_", "SUP_")):
            _supersede_period_identity(sibling)

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
    if row.operating_income is None and row.gross_profit is not None and row.operating_expenses is not None:
        row.operating_income = row.gross_profit - row.operating_expenses
        source_map.setdefault("operating_income", {"tag": "DERIVED", "method": "GROSS_PROFIT_MINUS_OPERATING_EXPENSES"})
    if row.cfo is not None and row.capex is not None:
        row.fcf = row.cfo - row.capex
    row.source_map = source_map
    quality = dict(row.quality or {})
    retained = sorted(
        field for field, ref in source_map.items()
        if isinstance(ref, dict) and ref.get("refresh_state") == "LAST_GOOD_RETAINED"
    )
    quality.update({
        "provider": "SEC", "filing_aware": True, "raw_facts_persisted": True,
        "period_type": period_type, "ttm_eligible": period_type in {"Q1", "Q2", "Q3", "Q4"},
        "retained_last_good_fields": retained,
        "provider_refresh_complete": not retained,
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

    from .current_financials import canonical_annual_pairs, canonical_quarter_pairs

    def load_pairs() -> list[tuple[FinancialPeriod, NormalizedFinancial]]:
        pairs = canonical_annual_pairs(company.id) + canonical_quarter_pairs(company.id)
        return sorted(
            pairs,
            key=lambda pair: (pair[0].end_date, pair[0].filed_at or date.min, pair[0].id),
            reverse=True,
        )[:80]

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

    existing_periods = [period for period, _ in canonical_annual_pairs(company.id)]
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


def _bridge_fy_end_instants_to_q4(company: Company) -> int:
    """Copy exact balance-sheet facts from FY to same-date Q4 when Q4 is synthetic.

    A Q4 reconstructed from FY minus Q1-Q3 is a duration construct. Balance-sheet
    facts are instants, so the issuer's FY-end Inventory/Cash/Receivables/etc. are
    exactly the Q4-end values. This bridge restores those fields without guessing.
    """
    bridged = 0
    q4_periods = FinancialPeriod.query.filter_by(company_id=company.id, period_type="Q4").all()
    for q4_period in q4_periods:
        fy_period = FinancialPeriod.query.filter_by(
            company_id=company.id,
            period_type="FY",
            fiscal_year=q4_period.fiscal_year,
            end_date=q4_period.end_date,
        ).first()
        if fy_period is None:
            continue
        q4_row = NormalizedFinancial.query.filter_by(financial_period_id=q4_period.id).first()
        fy_row = NormalizedFinancial.query.filter_by(financial_period_id=fy_period.id).first()
        if q4_row is None or fy_row is None:
            continue
        source_map = dict(q4_row.source_map or {})
        changed = False
        for field in INSTANT_TAGS:
            if getattr(q4_row, field, None) is not None:
                continue
            value = getattr(fy_row, field, None)
            if value is None:
                continue
            setattr(q4_row, field, value)
            fy_ref = (fy_row.source_map or {}).get(field)
            ref = dict(fy_ref) if isinstance(fy_ref, dict) else ({"prior_source": str(fy_ref)} if fy_ref else {})
            ref["method"] = "FY_END_INSTANT_BRIDGE"
            ref["bridge_from_period_id"] = fy_period.id
            source_map[field] = ref
            source_id = int(ref.get("source_id") or fy_period.source_id or q4_period.source_id or 0)
            if source_id:
                exists = Provenance.query.filter_by(
                    object_type="normalized_financial",
                    object_id=str(q4_period.id),
                    field_name=field,
                    financial_period_id=q4_period.id,
                ).filter(Provenance.notes.like("%FY_END_INSTANT_BRIDGE%")).first()
                if not exists:
                    db.session.add(Provenance(
                        source_id=source_id,
                        object_type="normalized_financial",
                        object_id=str(q4_period.id),
                        field_name=field,
                        raw_or_normalized="NORMALIZED",
                        financial_period_id=q4_period.id,
                        provider=str(ref.get("provider") or "SEC"),
                        freshness_at=utcnow(),
                        calculation_version=CALCULATION_VERSION,
                        notes=f"{ref.get('tag','')} / FY_END_INSTANT_BRIDGE / FY period {fy_period.id}",
                    ))
            bridged += 1
            changed = True
        if changed:
            _finish_normalized(q4_row, source_map, period_type="Q4")
    return bridged


def _reconcile_financial_field_issues(company: Company) -> dict[str, Any]:
    """Turn silent current-basis holes into explicit issues on the coalesced basis."""
    from .current_financials import canonical_annual_rows, current_row

    current_view = current_row(company.id)
    if not current_view or not current_view.get("period_id"):
        return {"basis_period_id": None, "missing_expected_fields": [], "issue_count": 0}

    current_period = db.session.get(FinancialPeriod, int(current_view["period_id"]))
    if current_period is None:
        return {"basis_period_id": None, "missing_expected_fields": [], "issue_count": 0}

    annual = canonical_annual_rows(company.id)[:3]
    historical_operating_income = any(row.get("operating_income") is not None for row in annual)
    historical_presence = {
        field: any(row.get(field) is not None for row in annual)
        for field in ("cash", "receivables", "inventory", "payables", "assets", "liabilities", "equity", "shares_outstanding")
    }

    expected_missing: list[str] = []
    if (
        current_view.get("operating_income") is None
        and (
            current_view.get("gross_profit") is not None
            or current_view.get("operating_expenses") is not None
            or historical_operating_income
        )
    ):
        expected_missing.append("operating_income")
    for field, expected in historical_presence.items():
        if expected and current_view.get(field) is None:
            expected_missing.append(field)

    active_codes = {f"MISSING_EXPECTED_{field.upper()}" for field in expected_missing}
    existing = DataQualityIssue.query.filter(
        DataQualityIssue.company_id == company.id,
        DataQualityIssue.object_type == "financial_basis",
        DataQualityIssue.status == "OPEN",
        DataQualityIssue.code.like("MISSING_EXPECTED_%"),
    ).all()
    for issue in existing:
        if issue.code not in active_codes or issue.object_id != str(current_period.id):
            issue.status = "RESOLVED"
            issue.resolved_at = utcnow()

    existing_keys = {(issue.code, issue.object_id) for issue in existing if issue.status == "OPEN"}
    for field in expected_missing:
        code = f"MISSING_EXPECTED_{field.upper()}"
        key = (code, str(current_period.id))
        if key in existing_keys:
            continue
        db.session.add(DataQualityIssue(
            company_id=company.id,
            object_type="financial_basis",
            object_id=str(current_period.id),
            code=code,
            severity="REVIEW",
            message=(
                f"{current_period.period_type} FY{current_period.fiscal_year}: {field.replace('_', ' ')} "
                "is missing even though issuer evidence indicates this field is applicable. "
                "The current basis remains in data review until provider/normalization evidence resolves it."
            ),
        ))
    return {
        "basis_period_id": current_period.id,
        "basis": f"{current_period.period_type} FY{current_period.fiscal_year}",
        "missing_expected_fields": sorted(expected_missing),
        "issue_count": len(expected_missing),
    }



def _reconcile_annual_history_issues(company: Company, target_years: int = 10) -> dict[str, Any]:
    """Persist quality issues from canonical annual history, never legacy duplicates."""
    from .current_financials import canonical_annual_pairs

    rows = [period for period, _ in canonical_annual_pairs(company.id)]
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

    extension_fallback = {
        "attempted": False, "filings_scanned": 0, "concepts_added": 0, "facts_added": 0,
    }

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

    # Generic statement-algebra inputs. SGA is only promoted to total operating
    # expense when it independently reconciles to pretax/non-operating evidence.
    sga_annual = _annual_duration(companyfacts, SGA_CANDIDATE_TAGS, fiscal_year_end)
    for namespace, tags in _semantic_tag_groups_for_aliases(companyfacts, SGA_LABEL_ALIASES).items():
        _merge_missing(
            sga_annual,
            _mark_semantic_records(_annual_duration(companyfacts, tags, fiscal_year_end, namespace=namespace), namespace),
        )
    nonoperating_total_annual = _annual_duration(companyfacts, NONOPERATING_TOTAL_TAGS, fiscal_year_end)
    nonoperating_components_annual = {
        tag: _annual_duration(companyfacts, [tag], fiscal_year_end)
        for tag in NONOPERATING_COMPONENT_TAGS
    }
    _apply_annual_statement_bridges(
        duration,
        sga=sga_annual,
        nonoperating_total=nonoperating_total_annual,
        nonoperating_components=nonoperating_components_annual,
    )

    # Only leave Companyfacts when the standard + exact-label + accounting-algebra
    # ladder is still incomplete. SEC filing-level extensions are bounded and are
    # then fed back through the same resolver rather than handled per ticker.
    if _extension_fallback_needed(duration, instant, companyfacts, fiscal_year_end):
        extension_fallback = _augment_companyfacts_with_recent_filing_extensions(
            company, companyfacts, meta, user_agent
        )
        if int(extension_fallback.get("facts_added") or 0) > 0:
            for field in DURATION_TAGS:
                for namespace, tags in _semantic_tag_groups(companyfacts, field).items():
                    fallback_rows = _annual_duration(companyfacts, tags, fiscal_year_end, namespace=namespace)
                    _merge_missing(duration[field], _mark_semantic_records(fallback_rows, namespace))
            for field in INSTANT_TAGS:
                for namespace, tags in _semantic_tag_groups(companyfacts, field).items():
                    fallback_rows = _annual_instant(companyfacts, tags, namespace=namespace, fiscal_year_end=fiscal_year_end)
                    _merge_missing(instant[field], _mark_semantic_records(fallback_rows, namespace))

            # Rebuild the reconciliation-gated SGA bridge with any custom filing
            # concepts that now have exact human-readable labels.
            for namespace, tags in _semantic_tag_groups_for_aliases(companyfacts, SGA_LABEL_ALIASES).items():
                _merge_missing(
                    sga_annual,
                    _mark_semantic_records(_annual_duration(companyfacts, tags, fiscal_year_end, namespace=namespace), namespace),
                )
            _apply_annual_statement_bridges(
                duration,
                sga=sga_annual,
                nonoperating_total=nonoperating_total_annual,
                nonoperating_components=nonoperating_components_annual,
            )

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
        source_map: dict[str, Any] = dict(normalized.source_map or {})
        for field, records in duration.items():
            rec = records.get(fy)
            _record_raw(period, source, rec)
            value = _as_decimal((rec or {}).get("val"))
            if rec and value is not None:
                setattr(normalized, field, value)
                source_map[field] = {"tag": rec.get("tag"), "namespace": rec.get("_mf_namespace") or rec.get("namespace") or "us-gaap", "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": int(rec.get("_mf_source_id") or source.id), "method": rec.get("_mf_derived_method") or ("SEMANTIC_LABEL_FALLBACK" if rec.get("_mf_semantic_fallback") else "DIRECT_FY")}
            elif getattr(normalized, field, None) is not None:
                _mark_last_good_retained(source_map, field)
        for field, records in instant.items():
            rec = records.get(fy)
            _record_raw(period, source, rec)
            value = _as_decimal((rec or {}).get("val"))
            if rec and value is not None:
                setattr(normalized, field, value)
                source_map[field] = {
                    "tag": rec.get("tag"), "namespace": rec.get("_mf_namespace") or rec.get("namespace") or "us-gaap",
                    "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": int(rec.get("_mf_source_id") or source.id),
                    "method": rec.get("_mf_derived_method") or ("SEMANTIC_LABEL_FALLBACK" if rec.get("_mf_semantic_fallback") else "DIRECT_FY"),
                }
            elif getattr(normalized, field, None) is not None:
                _mark_last_good_retained(source_map, field)

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
            if normalized.debt is not None:
                _mark_last_good_retained(source_map, "debt")
            else:
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
            ref = ref if isinstance(ref, dict) else {"prior_source": str(ref)}
            provenance_source_id = int(ref.get("source_id") or source.id)
            provenance_provider = str(ref.get("provider") or "SEC")
            provenance_note = " / ".join(str(part) for part in (
                ref.get("tag", ""), ref.get("accession", ""), ref.get("method", ""), ref.get("refresh_state", "")
            ) if part)
            db.session.add(Provenance(
                source_id=provenance_source_id,
                object_type="normalized_financial",
                object_id=str(period.id),
                field_name=field,
                raw_or_normalized="NORMALIZED",
                financial_period_id=period.id,
                provider=provenance_provider,
                freshness_at=utcnow(),
                calculation_version=CALCULATION_VERSION,
                notes=provenance_note,
            ))
        missing_revenue_issue = DataQualityIssue.query.filter_by(
            company_id=company.id, object_type="financial_period", object_id=str(period.id),
            code="MISSING_REVENUE", status="OPEN",
        ).first()
        if normalized.revenue is None:
            if not missing_revenue_issue:
                db.session.add(DataQualityIssue(company_id=company.id, object_type="financial_period", object_id=str(period.id), code="MISSING_REVENUE", severity="REVIEW", message=f"FY{fy}: revenue was not resolved from SEC Companyfacts."))
        elif missing_revenue_issue:
            missing_revenue_issue.status = "RESOLVED"
            missing_revenue_issue.resolved_at = utcnow()
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

    # Repair operating statement fields before creating quarter periods. This uses
    # the same generic, reconciliation-gated ladder for every issuer.
    sga_quarter = _quarter_duration_values(
        companyfacts, SGA_CANDIDATE_TAGS, sga_annual, fiscal_year_end=fiscal_year_end
    )
    for namespace, tags in _semantic_tag_groups_for_aliases(companyfacts, SGA_LABEL_ALIASES).items():
        semantic_sga_annual = _annual_duration(companyfacts, tags, fiscal_year_end, namespace=namespace)
        semantic_sga_quarter = _quarter_duration_values(
            companyfacts, tags, semantic_sga_annual,
            fiscal_year_end=fiscal_year_end, namespace=namespace,
        )
        _merge_missing(sga_quarter, _mark_semantic_records(semantic_sga_quarter, namespace))

    nonoperating_total_quarter = _quarter_duration_values(
        companyfacts, NONOPERATING_TOTAL_TAGS, nonoperating_total_annual,
        fiscal_year_end=fiscal_year_end,
    )
    nonoperating_components_quarter = {
        tag: _quarter_duration_values(
            companyfacts, [tag], nonoperating_components_annual[tag],
            fiscal_year_end=fiscal_year_end,
        )
        for tag in NONOPERATING_COMPONENT_TAGS
    }
    _apply_quarter_statement_bridges(
        quarter_duration,
        annual_duration=duration,
        sga=sga_quarter,
        nonoperating_total=nonoperating_total_quarter,
        nonoperating_components=nonoperating_components_quarter,
    )

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
        source_map: dict[str, Any] = dict(normalized.source_map or {})
        for field, records in quarter_duration.items():
            info = records.get((fy, fp)) or {}
            record = info.get("record")
            for raw in info.get("derived_from") or []:
                _record_raw(period, source, raw)
            _record_raw(period, source, record)
            value = info.get("value")
            if info and value is not None:
                setattr(normalized, field, value)
                source_map[field] = {
                    "tag": (record or {}).get("tag"), "accession": (record or {}).get("accn"),
                    "filed": (record or {}).get("filed"), "source_id": int((record or {}).get("_mf_source_id") or source.id),
                    "namespace": (record or {}).get("_mf_namespace") or (record or {}).get("namespace") or "us-gaap",
                    "method": (record or {}).get("_mf_derived_method") or ("SEMANTIC_LABEL_FALLBACK" if (record or {}).get("_mf_semantic_fallback") else info.get("method")),
                }
            elif getattr(normalized, field, None) is not None:
                _mark_last_good_retained(source_map, field)
        for field, records in quarter_instant.items():
            rec = records.get((fy, fp))
            _record_raw(period, source, rec)
            value = _as_decimal((rec or {}).get("val"))
            if rec and value is not None:
                setattr(normalized, field, value)
                source_map[field] = {
                    "tag": rec.get("tag"), "namespace": rec.get("_mf_namespace") or rec.get("namespace") or "us-gaap",
                    "accession": rec.get("accn"), "filed": rec.get("filed"), "source_id": int(rec.get("_mf_source_id") or source.id),
                    "method": rec.get("_mf_derived_method") or ("SEMANTIC_LABEL_FALLBACK" if rec.get("_mf_semantic_fallback") else "DIRECT_INSTANT"),
                }
            elif getattr(normalized, field, None) is not None:
                _mark_last_good_retained(source_map, field)

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
            if normalized.debt is not None:
                _mark_last_good_retained(source_map, "debt")
            else:
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
    fy_end_instant_bridges = _bridge_fy_end_instants_to_q4(company)
    field_integrity = _reconcile_financial_field_issues(company)
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
        "normalizer_version": SEC_NORMALIZER_VERSION,
        "fy_end_instant_bridges": fy_end_instant_bridges,
        "field_integrity": field_integrity,
        "filing_extension_fallback": extension_fallback,
        "fundamental_fallback": fallback,
        "annual_history": annual_history,
    }
