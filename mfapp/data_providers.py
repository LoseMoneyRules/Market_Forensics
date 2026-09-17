from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .extensions import db
from .models import AppSecret
from .core_models import MarketSnapshot, Security
from .security import decrypt_secret, encrypt_secret


@dataclass(frozen=True)
class QuoteResult:
    ok: bool
    provider: str
    price: float | None = None
    currency: str = "USD"
    as_of: datetime | None = None
    quality: str = "UNAVAILABLE"
    payload: dict[str, Any] | None = None
    message: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    return utcnow()


def get_secret(user_id: int, name: str) -> str:
    row = AppSecret.query.filter_by(user_id=user_id, name=name).first()
    if not row:
        return ""
    try:
        return decrypt_secret(row.value_enc)
    except Exception:
        return ""


def set_secret(user_id: int, name: str, value: str) -> None:
    value = str(value or "").strip()
    row = AppSecret.query.filter_by(user_id=user_id, name=name).first()
    if not value:
        if row:
            db.session.delete(row)
        return
    if row is None:
        db.session.add(AppSecret(user_id=user_id, name=name, value_enc=encrypt_secret(value)))
    else:
        row.value_enc = encrypt_secret(value)


def provider_status(user_id: int) -> dict[str, bool]:
    finra_api = bool(get_secret(user_id, "finra_client_id") and get_secret(user_id, "finra_client_secret"))
    return {
        "alpaca": bool(get_secret(user_id, "alpaca_key") and get_secret(user_id, "alpaca_secret")),
        "tiingo": bool(get_secret(user_id, "tiingo_token")),
        "alpha_vantage": bool(get_secret(user_id, "alpha_vantage_key")),
        "massive": bool(get_secret(user_id, "massive_key")),
        "sec": bool(get_secret(user_id, "sec_user_agent")),
        "finra": True,
        "finra_api": finra_api,
    }


def provider_overview(user_id: int) -> list[dict[str, Any]]:
    status = provider_status(user_id)
    return [
        {"key": "sec", "name": "SEC EDGAR", "category": "Fundamentals / filings", "state": "READY" if status["sec"] else "NEEDS USER-AGENT", "required": True,
         "capabilities": "10-K, 10-Q, 8-K, XBRL facts, normalized financials, provenance"},
        {"key": "alpaca", "name": "Alpaca", "category": "Market data", "state": "READY" if status["alpaca"] else "OPTIONAL", "required": False,
         "capabilities": "Primary quote source; public chart remains last-resort fallback"},
        {"key": "finra", "name": "FINRA public files", "category": "Positioning / flows", "state": "PUBLIC", "required": False,
         "capabilities": "Reg SHO daily short-sale volume; no credential required"},
        {"key": "finra_api", "name": "FINRA Query API", "category": "Positioning / flows", "state": "READY" if status["finra_api"] else "OPTIONAL", "required": False,
         "capabilities": "Consolidated short interest, days-to-cover, changes, threshold history"},
        {"key": "tiingo", "name": "Tiingo", "category": "Market redundancy", "state": "READY" if status["tiingo"] else "OPTIONAL", "required": False,
         "capabilities": "Secondary quote source"},
        {"key": "alpha_vantage", "name": "Alpha Vantage", "category": "Market redundancy", "state": "READY" if status["alpha_vantage"] else "OPTIONAL", "required": False,
         "capabilities": "Secondary delayed quote source"},
        {"key": "massive", "name": "Massive", "category": "Future market depth", "state": "READY" if status["massive"] else "OPTIONAL", "required": False,
         "capabilities": "Credential retained for future options / reference / market-depth modules"},
    ]


def _alpaca(ticker: str, user_id: int) -> QuoteResult:
    key, secret = get_secret(user_id, "alpaca_key"), get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return QuoteResult(False, "Alpaca", message="not configured")
    try:
        r = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"feed": "iex"}, timeout=8,
        )
        if r.status_code != 200:
            return QuoteResult(False, "Alpaca", message=f"HTTP {r.status_code}")
        raw = r.json() or {}
        trade, bar = raw.get("latestTrade") or {}, raw.get("dailyBar") or {}
        price = trade.get("p") or bar.get("c")
        if price is None:
            return QuoteResult(False, "Alpaca", message="no usable quote")
        return QuoteResult(True, "Alpaca IEX", float(price), "USD", _dt(trade.get("t") or bar.get("t")), "OBSERVED", {"feed": "iex"})
    except Exception as exc:
        return QuoteResult(False, "Alpaca", message=type(exc).__name__)


def _tiingo(ticker: str, user_id: int) -> QuoteResult:
    token = get_secret(user_id, "tiingo_token")
    if not token:
        return QuoteResult(False, "Tiingo", message="not configured")
    try:
        r = requests.get(f"https://api.tiingo.com/iex/{ticker}", params={"token": token}, headers={"User-Agent": "MarketForensics/0.1.2"}, timeout=8)
        if r.status_code != 200:
            return QuoteResult(False, "Tiingo", message=f"HTTP {r.status_code}")
        raw = r.json() or []
        row = raw[0] if isinstance(raw, list) and raw else raw if isinstance(raw, dict) else {}
        price = row.get("last") or row.get("tngoLast") or row.get("prevClose")
        if price is None:
            return QuoteResult(False, "Tiingo", message="no usable quote")
        return QuoteResult(True, "Tiingo", float(price), "USD", _dt(row.get("timestamp") or row.get("quoteTimestamp")), "OBSERVED", {"source": "iex"})
    except Exception as exc:
        return QuoteResult(False, "Tiingo", message=type(exc).__name__)


def _alpha_vantage(ticker: str, user_id: int) -> QuoteResult:
    key = get_secret(user_id, "alpha_vantage_key")
    if not key:
        return QuoteResult(False, "Alpha Vantage", message="not configured")
    try:
        r = requests.get("https://www.alphavantage.co/query", params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": key}, timeout=10)
        if r.status_code != 200:
            return QuoteResult(False, "Alpha Vantage", message=f"HTTP {r.status_code}")
        row = (r.json() or {}).get("Global Quote") or {}
        price = row.get("05. price")
        if not price:
            return QuoteResult(False, "Alpha Vantage", message="no usable quote")
        return QuoteResult(True, "Alpha Vantage", float(price), "USD", utcnow(), "DELAYED_OBSERVED", {"source": "global_quote"})
    except Exception as exc:
        return QuoteResult(False, "Alpha Vantage", message=type(exc).__name__)


def _public_chart(ticker: str) -> QuoteResult:
    symbol = ticker.replace(".", "-")
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d", "events": "div,splits"},
            headers={"User-Agent": "Mozilla/5.0 MarketForensics/0.1.2"}, timeout=8,
        )
        if r.status_code != 200:
            return QuoteResult(False, "Public market chart", message=f"HTTP {r.status_code}")
        result = (((r.json() or {}).get("chart") or {}).get("result") or [])
        if not result:
            return QuoteResult(False, "Public market chart", message="no result")
        node, meta = result[0], (result[0].get("meta") or {})
        price = meta.get("regularMarketPrice")
        if price is None:
            closes = (((node.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            usable = [x for x in closes if x is not None]
            price = usable[-1] if usable else None
        if price is None:
            return QuoteResult(False, "Public market chart", message="no usable quote")
        ts = meta.get("regularMarketTime")
        as_of = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else utcnow()
        return QuoteResult(True, "Public market chart", float(price), str(meta.get("currency") or "USD"), as_of, "PUBLIC_FALLBACK", {"exchange": meta.get("exchangeName"), "instrument_type": meta.get("instrumentType")})
    except Exception as exc:
        return QuoteResult(False, "Public market chart", message=type(exc).__name__)


def latest_snapshot(security_id: int) -> MarketSnapshot | None:
    return MarketSnapshot.query.filter_by(security_id=security_id).order_by(MarketSnapshot.as_of.desc(), MarketSnapshot.id.desc()).first()


def fetch_quote(ticker: str, user_id: int) -> QuoteResult:
    attempts = [_alpaca(ticker, user_id), _tiingo(ticker, user_id), _alpha_vantage(ticker, user_id), _public_chart(ticker)]
    result = next((x for x in attempts if x.ok and x.price is not None and x.price > 0), None)
    if result:
        return result
    return QuoteResult(False, "last-good cache", message="; ".join(f"{x.provider}: {x.message}" for x in attempts))


def refresh_security_quote(security: Security, user_id: int) -> QuoteResult:
    result = fetch_quote(security.ticker, user_id)
    if not result.ok or result.price is None:
        return result
    db.session.add(MarketSnapshot(
        security_id=security.id,
        provider=result.provider,
        price=result.price,
        currency=result.currency or security.currency or "USD",
        as_of=result.as_of or utcnow(),
        quality=result.quality,
        payload=result.payload or {},
    ))
    db.session.commit()
    return result


__all__ = ["QuoteResult", "get_secret", "set_secret", "provider_status", "provider_overview", "latest_snapshot", "fetch_quote", "refresh_security_quote"]
