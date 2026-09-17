from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .core_models import Event
from .data_providers import get_secret

FINRA_API = "https://api.finra.org"
FINRA_TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"
FINRA_DAILY_CDN = "https://cdn.finra.org/equity/regsho/daily"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def api_configured(user_id: int) -> bool:
    return bool(get_secret(user_id, "finra_client_id") and get_secret(user_id, "finra_client_secret"))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


def _oauth_token(user_id: int) -> str:
    client_id = get_secret(user_id, "finra_client_id")
    client_secret = get_secret(user_id, "finra_client_secret")
    if not client_id or not client_secret:
        raise RuntimeError("FINRA Public API credential is not configured")
    response = requests.post(
        FINRA_TOKEN_URL,
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"FINRA OAuth HTTP {response.status_code}")
    token = str((response.json() or {}).get("access_token") or "").strip()
    if not token:
        raise RuntimeError("FINRA OAuth response did not contain access_token")
    return token


def _query_dataset(user_id: int, dataset: str, *, filters: list[dict[str, str]], fields: list[str], limit: int = 5000) -> list[dict[str, Any]]:
    token = _oauth_token(user_id)
    response = requests.post(
        f"{FINRA_API}/data/group/otcMarket/name/{dataset}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Data-API-Version": "1",
            "User-Agent": "MarketForensics/0.2.0",
        },
        json={"fields": fields, "compareFilters": filters, "limit": max(1, min(int(limit), 5000))},
        timeout=25,
    )
    if response.status_code == 204:
        return []
    if response.status_code != 200:
        raise RuntimeError(f"FINRA {dataset} HTTP {response.status_code}")
    payload = response.json()
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("results") or payload.get("records") or []
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
    return []


def daily_short_volume(ticker: str, lookback_days: int = 35) -> list[dict[str, Any]]:
    """Read FINRA's public Reg SHO daily files. No API credential is required."""
    rows: list[dict[str, Any]] = []
    today = date.today()
    lookback = max(1, min(int(lookback_days), 90))
    for offset in range(lookback, -1, -1):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        try:
            response = requests.get(f"{FINRA_DAILY_CDN}/CNMSshvol{day:%Y%m%d}.txt", timeout=15)
            if response.status_code != 200:
                continue
            for raw in csv.DictReader(io.StringIO(response.text), delimiter="|"):
                if str(raw.get("Symbol") or "").upper() != ticker.upper():
                    continue
                short = _to_float(raw.get("ShortVolume")) or 0.0
                exempt = _to_float(raw.get("ShortExemptVolume")) or 0.0
                total = _to_float(raw.get("TotalVolume")) or 0.0
                rows.append({
                    "trade_date": day.isoformat(),
                    "short_volume": short,
                    "short_exempt_volume": exempt,
                    "total_reported_volume": total,
                    "short_pct": ((short + exempt) / total) if total else None,
                    "market": str(raw.get("Market") or ""),
                })
        except requests.RequestException:
            continue
    return rows


def consolidated_short_interest(ticker: str, user_id: int) -> list[dict[str, Any]]:
    fields = [
        "accountingYearMonthNumber", "symbolCode", "issueName", "issuerServicesGroupExchangeCode",
        "marketClassCode", "currentShortPositionQuantity", "previousShortPositionQuantity",
        "averageDailyVolumeQuantity", "daysToCoverQuantity", "revisionFlag", "changePercent",
        "changePreviousNumber", "settlementDate",
    ]
    rows = _query_dataset(
        user_id,
        "consolidatedShortInterest",
        filters=[{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": ticker.upper()}],
        fields=fields,
        limit=250,
    )
    normalized = []
    for row in rows:
        normalized.append({
            "settlement_date": str(row.get("settlementDate") or "")[:10],
            "symbol": str(row.get("symbolCode") or ticker).upper(),
            "issue_name": row.get("issueName"),
            "exchange": row.get("issuerServicesGroupExchangeCode"),
            "market_class": row.get("marketClassCode"),
            "current_short": _to_float(row.get("currentShortPositionQuantity")),
            "previous_short": _to_float(row.get("previousShortPositionQuantity")),
            "change_percent": _to_float(row.get("changePercent")),
            "change_previous": _to_float(row.get("changePreviousNumber")),
            "average_daily_volume": _to_float(row.get("averageDailyVolumeQuantity")),
            "days_to_cover": _to_float(row.get("daysToCoverQuantity")),
            "revision": row.get("revisionFlag"),
            "accounting_month": row.get("accountingYearMonthNumber"),
        })
    normalized.sort(key=lambda x: x.get("settlement_date") or "")
    return normalized


def threshold_history(ticker: str, user_id: int) -> list[dict[str, Any]]:
    fields = [
        "tradeDate", "issueSymbolIdentifier", "issueName", "marketClassCode",
        "thresholdListFlag", "marketCategoryDescription", "regShoThresholdFlag", "rule4320Flag",
    ]
    rows = _query_dataset(
        user_id,
        "thresholdList",
        filters=[{"compareType": "EQUAL", "fieldName": "issueSymbolIdentifier", "fieldValue": ticker.upper()}],
        fields=fields,
        limit=500,
    )
    normalized = [{
        "trade_date": str(row.get("tradeDate") or "")[:10],
        "symbol": str(row.get("issueSymbolIdentifier") or ticker).upper(),
        "threshold_flag": row.get("thresholdListFlag"),
        "reg_sho_flag": row.get("regShoThresholdFlag"),
        "rule_4320_flag": row.get("rule4320Flag"),
        "market_category": row.get("marketCategoryDescription"),
    } for row in rows]
    normalized.sort(key=lambda x: x.get("trade_date") or "")
    return normalized


def refresh_bundle(ticker: str, user_id: int, lookback_days: int = 35) -> dict[str, Any]:
    daily = daily_short_volume(ticker, lookback_days)
    result: dict[str, Any] = {
        "ticker": ticker.upper(),
        "retrieved_at": utcnow().isoformat(),
        "daily_short_volume": daily,
        "short_interest": [],
        "threshold_history": [],
        "api_configured": api_configured(user_id),
        "errors": [],
    }
    if result["api_configured"]:
        try:
            result["short_interest"] = consolidated_short_interest(ticker, user_id)
        except Exception as exc:
            result["errors"].append(f"short_interest: {type(exc).__name__}: {exc}")
        try:
            result["threshold_history"] = threshold_history(ticker, user_id)
        except Exception as exc:
            result["errors"].append(f"threshold_list: {type(exc).__name__}: {exc}")
    return result


def _average_short_pct(rows: list[dict[str, Any]], count: int) -> float | None:
    values = [float(x["short_pct"]) for x in rows[-count:] if x.get("short_pct") is not None]
    return sum(values) / len(values) if values else None


def stored_summary(company_id: int) -> dict[str, Any]:
    daily_event = Event.query.filter_by(company_id=company_id, event_type="FINRA_SHORT_VOLUME_SERIES").order_by(Event.event_date.desc(), Event.id.desc()).first()
    interest_event = Event.query.filter_by(company_id=company_id, event_type="FINRA_SHORT_INTEREST_SERIES").order_by(Event.event_date.desc(), Event.id.desc()).first()
    threshold_event = Event.query.filter_by(company_id=company_id, event_type="FINRA_THRESHOLD_HISTORY").order_by(Event.event_date.desc(), Event.id.desc()).first()
    daily_rows = list((daily_event.payload or {}).get("rows") or []) if daily_event else []
    interest_rows = list((interest_event.payload or {}).get("rows") or []) if interest_event else []
    threshold_rows = list((threshold_event.payload or {}).get("rows") or []) if threshold_event else []
    latest_interest = interest_rows[-1] if interest_rows else None
    previous_interest = interest_rows[-2] if len(interest_rows) > 1 else None
    latest_threshold = threshold_rows[-1] if threshold_rows else None
    return {
        "daily_rows": daily_rows,
        "short_interest_rows": interest_rows,
        "threshold_rows": threshold_rows,
        "daily_5d_short_pct": _average_short_pct(daily_rows, 5),
        "daily_20d_short_pct": _average_short_pct(daily_rows, 20),
        "latest_short_interest": latest_interest,
        "previous_short_interest": previous_interest,
        "latest_threshold": latest_threshold,
        "has_daily": bool(daily_rows),
        "has_short_interest": bool(interest_rows),
    }


__all__ = [
    "api_configured", "daily_short_volume", "consolidated_short_interest", "threshold_history",
    "refresh_bundle", "stored_summary",
]
