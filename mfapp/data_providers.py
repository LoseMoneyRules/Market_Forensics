from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
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
        {"key": "sec", "name": "SEC EDGAR", "category": "Fundamentals / filings", "state": "READY" if status["sec"] else "NEEDS USER-AGENT", "required": True, "capabilities": "10-K, 10-Q, 8-K, XBRL facts, normalized financials, provenance"},
        {"key": "alpaca", "name": "Alpaca", "category": "Market data", "state": "READY" if status["alpaca"] else "OPTIONAL", "required": False, "capabilities": "Authenticated quote + historical price source"},
        {"key": "finra", "name": "FINRA public files", "category": "Positioning / flows", "state": "PUBLIC", "required": False, "capabilities": "Reg SHO daily short-sale volume; no credential required"},
        {"key": "finra_api", "name": "FINRA Query API", "category": "Positioning / flows", "state": "READY" if status["finra_api"] else "OPTIONAL", "required": False, "capabilities": "Consolidated short interest, days-to-cover, changes, threshold history"},
        {"key": "tiingo", "name": "Tiingo", "category": "Market redundancy", "state": "READY" if status["tiingo"] else "OPTIONAL", "required": False, "capabilities": "Independent quote and historical cross-check"},
        {"key": "alpha_vantage", "name": "Alpha Vantage", "category": "Market redundancy", "state": "READY" if status["alpha_vantage"] else "OPTIONAL", "required": False, "capabilities": "Secondary delayed quote source"},
        {"key": "massive", "name": "Massive", "category": "Future market depth", "state": "READY" if status["massive"] else "OPTIONAL", "required": False, "capabilities": "Credential retained for future options / reference / market-depth modules"},
    ]


def _alpaca(ticker: str, user_id: int) -> QuoteResult:
    key, secret = get_secret(user_id, "alpaca_key"), get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return QuoteResult(False, "Alpaca", message="not configured")
    try:
        response = requests.get(f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot", headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, params={"feed": "iex"}, timeout=8)
        if response.status_code != 200:
            return QuoteResult(False, "Alpaca", message=f"HTTP {response.status_code}")
        raw = response.json() or {}; trade, bar = raw.get("latestTrade") or {}, raw.get("dailyBar") or {}
        trade_price, bar_price = trade.get("p"), bar.get("c")
        price = trade_price or bar_price
        if price is None:
            return QuoteResult(False, "Alpaca", message="no usable quote")
        if trade_price and bar_price and abs(float(trade_price) / float(bar_price) - 1) > .08:
            price = bar_price
        return QuoteResult(True, "Alpaca IEX", float(price), "USD", _dt(trade.get("t") or bar.get("t")), "OBSERVED", {"feed": "iex", "latest_trade": trade_price, "daily_close": bar_price})
    except Exception as exc:
        return QuoteResult(False, "Alpaca", message=type(exc).__name__)


def _tiingo(ticker: str, user_id: int) -> QuoteResult:
    token = get_secret(user_id, "tiingo_token")
    if not token:
        return QuoteResult(False, "Tiingo", message="not configured")
    try:
        response = requests.get(f"https://api.tiingo.com/iex/{ticker}", params={"token": token}, headers={"User-Agent": "MarketForensics/0.1.3"}, timeout=8)
        if response.status_code != 200:
            return QuoteResult(False, "Tiingo", message=f"HTTP {response.status_code}")
        raw = response.json() or []
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
        response = requests.get("https://www.alphavantage.co/query", params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": key}, timeout=10)
        if response.status_code != 200:
            return QuoteResult(False, "Alpha Vantage", message=f"HTTP {response.status_code}")
        row = (response.json() or {}).get("Global Quote") or {}; price = row.get("05. price")
        if not price:
            return QuoteResult(False, "Alpha Vantage", message="no usable quote")
        return QuoteResult(True, "Alpha Vantage", float(price), "USD", utcnow(), "DELAYED_OBSERVED", {"source": "global_quote"})
    except Exception as exc:
        return QuoteResult(False, "Alpha Vantage", message=type(exc).__name__)


def _public_chart(ticker: str) -> QuoteResult:
    symbol = ticker.replace(".", "-")
    try:
        response = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range": "5d", "interval": "1d", "events": "div,splits"}, headers={"User-Agent": "Mozilla/5.0 MarketForensics/0.1.3"}, timeout=8)
        if response.status_code != 200:
            return QuoteResult(False, "Public market chart", message=f"HTTP {response.status_code}")
        result = (((response.json() or {}).get("chart") or {}).get("result") or [])
        if not result:
            return QuoteResult(False, "Public market chart", message="no result")
        node, meta = result[0], result[0].get("meta") or {}; price = meta.get("regularMarketPrice")
        if price is None:
            closes = (((node.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []); usable = [x for x in closes if x is not None]; price = usable[-1] if usable else None
        if price is None:
            return QuoteResult(False, "Public market chart", message="no usable quote")
        ts = meta.get("regularMarketTime"); as_of = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else utcnow()
        return QuoteResult(True, "Public market chart", float(price), str(meta.get("currency") or "USD"), as_of, "PUBLIC_FALLBACK", {"exchange": meta.get("exchangeName"), "instrument_type": meta.get("instrumentType")})
    except Exception as exc:
        return QuoteResult(False, "Public market chart", message=type(exc).__name__)


def latest_snapshot(security_id: int) -> MarketSnapshot | None:
    return MarketSnapshot.query.filter_by(security_id=security_id).order_by(MarketSnapshot.as_of.desc(), MarketSnapshot.id.desc()).first()


def quote_candidates(ticker: str, user_id: int) -> list[QuoteResult]:
    return [_alpaca(ticker, user_id), _tiingo(ticker, user_id), _alpha_vantage(ticker, user_id), _public_chart(ticker)]


def _candidate_payload(rows: list[QuoteResult]) -> list[dict[str, Any]]:
    return [{"provider": row.provider, "ok": row.ok, "price": row.price, "quality": row.quality, "as_of": row.as_of.isoformat() if row.as_of else None, "message": row.message} for row in rows]


def fetch_quote(ticker: str, user_id: int) -> QuoteResult:
    attempts = quote_candidates(ticker, user_id)
    authenticated = [row for row in attempts[:3] if row.ok and row.price is not None and row.price > 0]
    public = attempts[-1] if attempts[-1].ok and attempts[-1].price is not None and attempts[-1].price > 0 else None
    evidence = _candidate_payload(attempts)
    if len(authenticated) >= 2:
        prices = [float(row.price) for row in authenticated]; center = median(prices); dispersion = max(abs(price / center - 1.0) for price in prices)
        if public:
            public_gap = abs(float(public.price) / center - 1.0)
        else:
            public_gap = None
        if dispersion > .035:
            return QuoteResult(False, "Quote consensus", message=f"Authenticated quote disagreement {dispersion:.1%}; last-good quote preserved.", payload={"candidates": evidence, "dispersion": dispersion})
        latest = max((row.as_of for row in authenticated if row.as_of), default=utcnow())
        return QuoteResult(True, "Verified quote consensus", center, authenticated[0].currency, latest, "CONSENSUS_OBSERVED", {"candidates": evidence, "dispersion": dispersion, "public_gap": public_gap})
    if len(authenticated) == 1:
        row = authenticated[0]
        if public:
            gap = abs(float(public.price) / float(row.price) - 1.0)
            if gap > .05:
                return QuoteResult(False, "Quote verification", message=f"Authenticated/public quote disagreement {gap:.1%}; last-good quote preserved.", payload={"candidates": evidence, "dispersion": gap})
            return QuoteResult(True, row.provider + " · cross-checked", row.price, row.currency, row.as_of, "CROSS_CHECKED_OBSERVED", {"candidates": evidence, "dispersion": gap})
        return QuoteResult(True, row.provider, row.price, row.currency, row.as_of, row.quality, {"candidates": evidence, **(row.payload or {})})
    if public:
        return QuoteResult(True, public.provider, public.price, public.currency, public.as_of, "PUBLIC_FALLBACK", {"candidates": evidence, **(public.payload or {})})
    return QuoteResult(False, "last-good cache", message="; ".join(f"{row.provider}: {row.message}" for row in attempts), payload={"candidates": evidence})


def refresh_security_quote(security: Security, user_id: int) -> QuoteResult:
    result = fetch_quote(security.ticker, user_id)
    if not result.ok or result.price is None:
        return result
    db.session.add(MarketSnapshot(security_id=security.id, provider=result.provider, price=result.price, currency=result.currency or security.currency or "USD", as_of=result.as_of or utcnow(), quality=result.quality, payload=result.payload or {}))
    db.session.commit()
    return result


__all__ = ["QuoteResult", "get_secret", "set_secret", "provider_status", "provider_overview", "latest_snapshot", "quote_candidates", "fetch_quote", "refresh_security_quote"]
