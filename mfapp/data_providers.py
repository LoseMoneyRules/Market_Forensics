from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _dt(value: Any) -> datetime | None:
    """Preserve actual provider observation time; missing is never 'now'."""
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


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
        {"key": "alpha_vantage", "name": "Alpha Vantage", "category": "Market + fundamental redundancy", "state": "READY" if status["alpha_vantage"] else "OPTIONAL", "required": False, "capabilities": "Secondary delayed quote plus missing-field income statement / balance sheet / cash-flow fallback; SEC remains primary"},
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
        observed_at = trade.get("t") if trade_price else bar.get("t")
        if price is None:
            return QuoteResult(False, "Alpaca", message="no usable quote")
        if trade_price and bar_price and abs(float(trade_price) / float(bar_price) - 1) > .08:
            price, observed_at = bar_price, bar.get("t")
        return QuoteResult(True, "Alpaca IEX", float(price), "USD", _dt(observed_at), "OBSERVED", {"feed": "iex", "latest_trade": trade_price, "daily_close": bar_price})
    except Exception as exc:
        return QuoteResult(False, "Alpaca", message=type(exc).__name__)


def _tiingo(ticker: str, user_id: int) -> QuoteResult:
    token = get_secret(user_id, "tiingo_token")
    if not token:
        return QuoteResult(False, "Tiingo", message="not configured")
    try:
        response = requests.get(f"https://api.tiingo.com/iex/{ticker}", params={"token": token}, headers={"User-Agent": "MarketForensics/0.2.0"}, timeout=8)
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
        return QuoteResult(True, "Alpha Vantage", float(price), "USD", _dt(row.get("07. latest trading day")), "DELAYED_OBSERVED", {"source": "global_quote", "latest_trading_day": row.get("07. latest trading day")})
    except Exception as exc:
        return QuoteResult(False, "Alpha Vantage", message=type(exc).__name__)


def _public_chart(ticker: str) -> QuoteResult:
    symbol = ticker.replace(".", "-")
    try:
        response = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range": "5d", "interval": "1d", "events": "div,splits"}, headers={"User-Agent": "Mozilla/5.0 MarketForensics/0.2.0"}, timeout=8)
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
        ts = meta.get("regularMarketTime") or ((node.get("timestamp") or [None])[-1]); as_of = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else None
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
    """Resolve current market evidence by freshness, then source agreement.

    A stale or delayed authenticated feed must never veto a current one.
    Disputed fresh observations can be displayed with an explicit non-decision
    grade quality marker; that is safer than silently serving a months-old quote.
    """
    attempts = quote_candidates(ticker, user_id)
    evidence = _candidate_payload(attempts)
    now = utcnow()
    valid = [
        row for row in attempts
        if row.ok and row.price is not None and 0 < float(row.price)
        and row.as_of is not None
        and timedelta(minutes=-15) <= (now - row.as_of) <= timedelta(days=5)
    ]
    # If every provider is stale, retain the last-good cache only as historical
    # reference. Never relabel it as a fresh or decision-grade current quote.
    if not valid:
        return QuoteResult(
            False, "Quote freshness", message="No recently dated quote available; existing quote is stale and must not be treated as current.",
            payload={"candidates": evidence, "freshness_error": True},
        )

    # Only compare the same recent trading window: previous closes are not
    # contemporaneous with a newer trade after a gap or strong price move.
    latest = max(row.as_of for row in valid)
    comparable = [row for row in valid if latest - row.as_of <= timedelta(hours=36)]
    # A provider that reports missing time must never override this group.
    freshest_auth = next(
        (row for row in sorted(comparable, key=lambda x: x.as_of, reverse=True) if row in attempts[:3]), None
    )
    preferred_currency = freshest_auth.currency if freshest_auth else comparable[0].currency
    comparable = [row for row in comparable if row.currency == preferred_currency]
    if not comparable:
        return QuoteResult(False, "Quote currency", message="Recent providers disagree on quote currency.",
                           payload={"candidates": evidence, "currency_conflict": True})

    # Find a corroborated price cluster. An older outlier must not invalidate
    # two newer matching sources (the former global-median 3.5% rejection).
    clusters = []
    for anchor in comparable:
        pair = [row for row in comparable if abs(float(row.price) / float(anchor.price) - 1) <= .035]
        if len(pair) >= 2:
            auth_count = sum(row in attempts[:3] for row in pair)
            if auth_count:
                clusters.append((len(pair), auth_count, max(row.as_of for row in pair), pair))
    if clusters:
        best = max(clusters, key=lambda x: (x[0], x[1], x[2]))[3]
        observed = max(row.as_of for row in best)
        prices = [float(row.price) for row in best]
        price = median(prices)
        dispersion = max(abs(x / price - 1) for x in prices)
        auth = sum(row in attempts[:3] for row in best)
        return QuoteResult(
            True, "Verified quote consensus" if auth >= 2 else best[0].provider + " · cross-checked",
            price, preferred_currency, observed,
            "CONSENSUS_OBSERVED" if auth >= 2 else "CROSS_CHECKED_OBSERVED",
            {"candidates": evidence, "selected_providers": [row.provider for row in best],
             "dispersion": dispersion, "discarded_outliers": [row.provider for row in comparable if row not in best]},
        )

    # With no agreement, prefer the most recent authenticated trade over the
    # oldest stored quote; explicitly mark it disputed so Research fails closed.
    chosen = freshest_auth or max(comparable, key=lambda row: row.as_of)
    disputed = len(comparable) > 1
    return QuoteResult(
        True, chosen.provider, float(chosen.price), chosen.currency, chosen.as_of,
        "DISPUTED_OBSERVED" if disputed else ("SINGLE_SOURCE_OBSERVED" if chosen in attempts[:3] else "PUBLIC_FALLBACK"),
        {"candidates": evidence, "selected_providers": [chosen.provider],
         "disagreement": disputed, "warning": "Contemporaneous providers disagree; displayed price is not decision-grade." if disputed else ""},
    )


def refresh_security_quote(security: Security, user_id: int) -> QuoteResult:
    result = fetch_quote(security.ticker, user_id)
    if not result.ok or result.price is None:
        return result
    db.session.add(MarketSnapshot(security_id=security.id, provider=result.provider, price=result.price, currency=result.currency or security.currency or "USD", as_of=result.as_of or utcnow(), quality=result.quality, payload=result.payload or {}))
    db.session.commit()
    return result


__all__ = ["QuoteResult", "get_secret", "set_secret", "provider_status", "provider_overview", "latest_snapshot", "quote_candidates", "fetch_quote", "refresh_security_quote"]
