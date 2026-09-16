from __future__ import annotations

"""Server-side SEC ingestion adapter for the V3.1.12 web migration.

The analytical rules live in the preserved V3.1.12 engine. This module only maps official SEC
Companyfacts into the MariaDB FundamentalPeriod store. Ambiguous or missing facts remain None.
"""

from collections import defaultdict
from datetime import date, datetime, timezone
import math
from typing import Iterable

import requests

from .extensions import db
from .marketdata import get_secret
from .models import Company, FundamentalPeriod

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"

TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "flow_total_costs": ["CostsAndExpenses"],
    "flow_operating_expenses": ["OperatingExpenses", "OperatingCostsAndExpenses"],
    "flow_sga": ["SellingGeneralAndAdministrativeExpense"],
    "flow_rd": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForAdditionsToPropertyPlantAndEquipment"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "basic_shares": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet", "InventoryFinishedGoodsNetOfAllowancesCustomerAdvancesAndProgressBillings"],
    "payables": ["AccountsPayableCurrent"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "short_borrowings": ["ShortTermBorrowings", "CommercialPaper"],
    "long_debt_current": ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent", "DebtCurrent"],
    "long_debt_noncurrent": ["LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent"],
    "long_debt_total": ["LongTermDebtAndFinanceLeaseObligations", "LongTermDebt"],
    "debt_combined": ["DebtLongtermAndShorttermCombinedAmount"],
    "shares_outstanding_dei": ["EntityCommonStockSharesOutstanding"],
    "shares_outstanding_gaap": ["CommonStockSharesOutstanding"],
}


class SECRefreshError(RuntimeError):
    pass


def _ua(user_id: int) -> str:
    value = get_secret(user_id, "sec_user_agent").strip()
    if not value or "@" not in value:
        raise SECRefreshError("Configure a truthful SEC User-Agent with contact email in Settings & Data.")
    return value


def _headers(user_agent: str) -> dict:
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _json(url: str, user_agent: str) -> dict:
    response = requests.get(url, headers=_headers(user_agent), timeout=35)
    if response.status_code != 200:
        raise SECRefreshError(f"SEC HTTP {response.status_code}")
    return response.json() or {}


def _ticker_meta(ticker: str, user_agent: str) -> dict:
    raw = _json(f"{SEC_WWW}/files/company_tickers.json", user_agent)
    target = ticker.upper().strip()
    for row in raw.values():
        if str(row.get("ticker") or "").upper() == target:
            cik = str(row.get("cik_str") or "").zfill(10)
            submissions = _json(f"{SEC_DATA}/submissions/CIK{cik}.json", user_agent)
            return {
                "cik": cik,
                "name": submissions.get("name") or row.get("title") or target,
                "sic": str(submissions.get("sic") or ""),
                "sic_description": submissions.get("sicDescription") or "",
                "fiscal_year_end": submissions.get("fiscalYearEnd") or "",
            }
    raise SECRefreshError(f"{target} not found in SEC ticker mapping.")


def _fact_records(companyfacts: dict, namespace: str, tag: str) -> list[dict]:
    node = (((companyfacts.get("facts") or {}).get(namespace) or {}).get(tag) or {})
    units = node.get("units") or {}
    records: list[dict] = []
    for unit_name, values in units.items():
        for raw in values or []:
            row = dict(raw)
            row["unit"] = unit_name
            row["tag"] = tag
            records.append(row)
    return records


def _duration_days(row: dict) -> int | None:
    try:
        return (date.fromisoformat(str(row.get("end"))[:10]) - date.fromisoformat(str(row.get("start"))[:10])).days
    except Exception:
        return None


def _annual_for_tag(companyfacts: dict, tag: str) -> dict[int, dict]:
    """Conservative 10-K/FY resolver; one reported consolidated-looking fact per fiscal year."""
    candidates: dict[int, list[dict]] = defaultdict(list)
    for row in _fact_records(companyfacts, "us-gaap", tag):
        if row.get("form") not in {"10-K", "10-K/A"} or str(row.get("fp") or "") != "FY":
            continue
        days = _duration_days(row)
        if days is None or not 300 <= days <= 430:
            continue
        try:
            fy = int(row.get("fy"))
            value = float(row.get("val"))
        except Exception:
            continue
        if not math.isfinite(value):
            continue
        item = dict(row)
        item["val"] = value
        candidates[fy].append(item)

    out: dict[int, dict] = {}
    for fy, rows in candidates.items():
        # Prefer the latest filing for the same annual period. If the latest filing still contains
        # conflicting values for the same end/tag, fail closed for that tag/year.
        rows.sort(key=lambda r: (str(r.get("filed") or ""), str(r.get("end") or ""), str(r.get("accn") or "")))
        latest_filed = str(rows[-1].get("filed") or "")
        latest = [r for r in rows if str(r.get("filed") or "") == latest_filed]
        by_end: dict[str, list[dict]] = defaultdict(list)
        for row in latest:
            by_end[str(row.get("end") or "")].append(row)
        end = max(by_end) if by_end else ""
        same = by_end.get(end, [])
        values = []
        for row in same:
            if not any(abs(row["val"] - existing) <= max(1.0, abs(existing) * 1e-9) for existing in values):
                values.append(row["val"])
        if len(values) != 1:
            continue
        chosen = next(r for r in same if abs(r["val"] - values[0]) <= max(1.0, abs(values[0]) * 1e-9))
        out[fy] = chosen
    return out


def _annual_metric(companyfacts: dict, tags: Iterable[str]) -> dict[int, dict]:
    """Use the first taxonomy tag that provides a year, preserving V3.1.12 tag priority."""
    merged: dict[int, dict] = {}
    for tag in tags:
        rows = _annual_for_tag(companyfacts, tag)
        for fy, row in rows.items():
            merged.setdefault(fy, row)
    return merged


def _annual_instant_for_tag(companyfacts: dict, namespace: str, tag: str) -> dict[int, dict]:
    candidates: dict[int, list[dict]] = defaultdict(list)
    for row in _fact_records(companyfacts, namespace, tag):
        if row.get("form") not in {"10-K", "10-K/A"}:
            continue
        if row.get("start"):
            continue
        try:
            fy = int(row.get("fy"))
            value = float(row.get("val"))
        except Exception:
            continue
        if not math.isfinite(value):
            continue
        item = dict(row)
        item["val"] = value
        candidates[fy].append(item)
    out = {}
    for fy, rows in candidates.items():
        rows.sort(key=lambda r: (str(r.get("filed") or ""), str(r.get("end") or ""), str(r.get("accn") or "")))
        latest = rows[-1]
        out[fy] = latest
    return out


def _annual_instant(companyfacts: dict, tags: Iterable[str], namespace: str = "us-gaap") -> dict[int, dict]:
    merged = {}
    for tag in tags:
        for fy, row in _annual_instant_for_tag(companyfacts, namespace, tag).items():
            merged.setdefault(fy, row)
    return merged


def _debt(short_rec, current_rec, noncurrent_rec, total_rec, combined_rec):
    short = short_rec.get("val") if short_rec else None
    current = current_rec.get("val") if current_rec else None
    noncurrent = noncurrent_rec.get("val") if noncurrent_rec else None
    total = total_rec.get("val") if total_rec else None
    combined = combined_rec.get("val") if combined_rec else None
    current_tag = str((current_rec or {}).get("tag") or "")
    if noncurrent is not None or current is not None:
        short_component = 0 if current_tag == "DebtCurrent" else (short or 0)
        return (noncurrent or 0) + (current or 0) + short_component
    if total is not None:
        return total + (short or 0)
    return combined


def _flow_operating_income(row: dict, maps: dict[str, dict[int, dict]], fy: int) -> tuple[float | None, str | None]:
    direct = (maps["operating_income"].get(fy) or {}).get("val")
    if direct is not None:
        return float(direct), "REPORTED_OPERATING_INCOME"
    revenue = row.get("revenue")
    gross_profit = row.get("gross_profit")
    total_costs = (maps["flow_total_costs"].get(fy) or {}).get("val")
    operating_expenses = (maps["flow_operating_expenses"].get(fy) or {}).get("val")
    sga_rec = maps["flow_sga"].get(fy) or {}
    rd_rec = maps["flow_rd"].get(fy) or {}
    value = None
    method = None
    if revenue is not None and total_costs is not None:
        value = float(revenue) - float(total_costs)
        method = "DERIVED_REVENUE_MINUS_TOTAL_COSTS"
    elif gross_profit is not None and operating_expenses is not None:
        value = float(gross_profit) - float(operating_expenses)
        method = "DERIVED_GROSS_PROFIT_MINUS_OPERATING_EXPENSES"
    elif gross_profit is not None and sga_rec.get("val") is not None:
        value = float(gross_profit) - float(sga_rec["val"]) - float(rd_rec.get("val") or 0.0)
        method = "DERIVED_GROSS_PROFIT_MINUS_SG&A" + ("_AND_R&D" if rd_rec.get("val") is not None else "")
        pretax = row.get("pretax")
        if pretax is not None:
            rev_abs = abs(float(revenue or 0.0))
            gp_abs = abs(float(gross_profit or 0.0))
            if abs(value - float(pretax)) > max(1.0, rev_abs * 0.10, gp_abs * 0.25):
                return None, "DATA_REVIEW_SG&A_BRIDGE_NOT_RECONCILABLE"
    if value is not None:
        rev_abs = abs(float(revenue or 0.0))
        if not math.isfinite(value) or (gross_profit is not None and value > float(gross_profit) + max(1.0, rev_abs * 1e-8)):
            return None, "DATA_REVIEW_IMPLAUSIBLE_OPERATING_BRIDGE"
    return value, method


def refresh_company_fundamentals(company: Company, user_id: int) -> dict:
    user_agent = _ua(user_id)
    meta = _ticker_meta(company.ticker, user_agent)
    facts = _json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json", user_agent)

    duration_keys = [
        "revenue", "gross_profit", "operating_income", "flow_total_costs", "flow_operating_expenses",
        "flow_sga", "flow_rd", "pretax", "tax", "net_income", "cfo", "capex", "buybacks", "dividends",
        "diluted_shares", "basic_shares",
    ]
    maps = {key: _annual_metric(facts, TAGS[key]) for key in duration_keys}
    instant_keys = ["cash", "receivables", "inventory", "payables", "assets", "liabilities", "equity", "short_borrowings", "long_debt_current", "long_debt_noncurrent", "long_debt_total", "debt_combined"]
    instant = {key: _annual_instant(facts, TAGS[key]) for key in instant_keys}
    outstanding = _annual_instant(facts, TAGS["shares_outstanding_dei"], namespace="dei")
    if not outstanding:
        outstanding = _annual_instant(facts, TAGS["shares_outstanding_gaap"])

    years = sorted(set().union(*(set(m.keys()) for m in maps.values()), *(set(m.keys()) for m in instant.values()), set(outstanding.keys())))
    saved = 0
    for fy in years[-10:]:
        revenue_rec = maps["revenue"].get(fy) or {}
        anchor = revenue_rec or next((m.get(fy) for m in maps.values() if m.get(fy)), {})
        row = {
            "revenue": (maps["revenue"].get(fy) or {}).get("val"),
            "gross_profit": (maps["gross_profit"].get(fy) or {}).get("val"),
            "operating_income": (maps["operating_income"].get(fy) or {}).get("val"),
            "pretax": (maps["pretax"].get(fy) or {}).get("val"),
            "tax": (maps["tax"].get(fy) or {}).get("val"),
            "net_income": (maps["net_income"].get(fy) or {}).get("val"),
            "cfo": (maps["cfo"].get(fy) or {}).get("val"),
            "capex": (maps["capex"].get(fy) or {}).get("val"),
            "buybacks": (maps["buybacks"].get(fy) or {}).get("val"),
            "dividends": (maps["dividends"].get(fy) or {}).get("val"),
        }
        row["fcf"] = row["cfo"] - row["capex"] if row["cfo"] is not None and row["capex"] is not None else None
        flow_oi, flow_method = _flow_operating_income(row, maps, fy)
        row["flow_operating_income"] = flow_oi
        row["flow_operating_income_method"] = flow_method
        diluted = maps["diluted_shares"].get(fy) or maps["basic_shares"].get(fy) or {}
        row["diluted_shares"] = diluted.get("val")
        row["shares_outstanding"] = (outstanding.get(fy) or {}).get("val")
        for key in ["cash", "receivables", "inventory", "payables", "assets", "liabilities", "equity"]:
            row[key] = (instant[key].get(fy) or {}).get("val")
        row["debt"] = _debt(
            instant["short_borrowings"].get(fy), instant["long_debt_current"].get(fy),
            instant["long_debt_noncurrent"].get(fy), instant["long_debt_total"].get(fy), instant["debt_combined"].get(fy),
        )

        period = FundamentalPeriod.query.filter_by(company_id=company.id, period_key=f"FY{fy}").first()
        if period is None:
            period = FundamentalPeriod(company_id=company.id, period_key=f"FY{fy}", period_type="FY", fiscal_year=fy)
            db.session.add(period)
        period.period_end = str(anchor.get("end") or "")[:10] or None
        period.period_filed = str(anchor.get("filed") or "")[:10] or None
        for key, value in row.items():
            setattr(period, key, value)
        period.source = "SEC Companyfacts"
        period.provenance = {
            "cik": meta["cik"],
            "company_name": meta["name"],
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "engine_basis": "Market Forensics V3.1.12 FULL",
        }
        saved += 1

    company.name = meta["name"] or company.name
    db.session.commit()
    return {"saved": saved, "cik": meta["cik"], "name": company.name, "years": years[-10:]}
