from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .extensions import db
from .models import AppSecret, MarketSnapshot
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


def _utc_naive(value: str | None = None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        row = AppSecret(user_id=user_id, name=name, value_enc=encrypt_secret(value))
        db.session.add(row)
    else:
        row.value_enc = encrypt_secret(value)


def provider_status(user_id: int) -> dict[str, bool]:
    return {
        "alpaca": bool(get_secret(user_id, "alpaca_key") and get_secret(user_id, "alpaca_secret")),
        "tiingo": bool(get_secret(user_id, "tiingo_token")),
        "alpha_vantage": bool(get_secret(user_id, "alpha_vantage_key")),
        "massive": bool(get_secret(user_id, "massive_key")),
    }


def _alpaca_quote(ticker: str, user_id: int) -> QuoteResult:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return QuoteResult(False, "Alpaca", message="Not configured")
    try:
        r = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"feed": "iex"},
            timeout=8,
        )
        if r.status_code != 200:
            return QuoteResult(False, "Alpaca", message=f"HTTP {r.status_code}")
        data = r.json() or {}
        trade = data.get("latestTrade") or {}
        daily = data.get("dailyBar") or {}
        price = trade.get("p") or daily.get("c")
        if price is None:
            return QuoteResult(False, "Alpaca", message="No usable quote")
        return QuoteResult(
            True,
            "Alpaca IEX",
            float(price),
            "USD",
            _utc_naive(trade.get("t") or daily.get("t")),
            "LIVE/OBSERVED",
            {"source": "snapshot", "feed": "iex"},
        )
    except Exception as exc:
        return QuoteResult(False, "Alpaca", message=type(exc).__name__)


def _yahoo_quote(ticker: str) -> QuoteResult:
    symbol = ticker.replace(".", "-")
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d", "events": "div,splits"},
            headers={"User-Agent": "Mozilla/5.0 MarketForensics/0.0.2"},
            timeout=8,
        )
        if r.status_code != 200:
            return QuoteResult(False, "Yahoo market chart", message=f"HTTP {r.status_code}")
        chart = (r.json() or {}).get("chart") or {}
        results = chart.get("result") or []
        if not results:
            return QuoteResult(False, "Yahoo market chart", message="No result")
        result = results[0]
        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        ts = meta.get("regularMarketTime")
        if price is None:
            closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            usable = [x for x in closes if x is not None]
            price = usable[-1] if usable else None
        if price is None:
            return QuoteResult(False, "Yahoo market chart", message="No usable quote")
        as_of = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else _utc_naive()
        return QuoteResult(
            True,
            "Yahoo market chart",
            float(price),
            str(meta.get("currency") or "USD"),
            as_of,
            "OBSERVED",
            {
                "exchange": meta.get("exchangeName") or "",
                "instrument_type": meta.get("instrumentType") or "",
                "previous_close": meta.get("chartPreviousClose"),
            },
        )
    except Exception as exc:
        return QuoteResult(False, "Yahoo market chart", message=type(exc).__name__)


def refresh_quote(company_id: int, ticker: str, user_id: int) -> QuoteResult:
    """Alpaca first, Yahoo fallback. A failed refresh never destroys last-good data."""
    attempts = [_alpaca_quote(ticker, user_id), _yahoo_quote(ticker)]
    result = next((x for x in attempts if x.ok and x.price is not None and x.price > 0), None)
    if result is None:
        messages = "; ".join(f"{x.provider}: {x.message}" for x in attempts)
        return QuoteResult(False, "none", message=messages)
    snapshot = MarketSnapshot.query.filter_by(company_id=company_id).first()
    if snapshot is None:
        snapshot = MarketSnapshot(company_id=company_id, provider=result.provider, price=result.price)
        db.session.add(snapshot)
    snapshot.provider = result.provider
    snapshot.price = float(result.price)
    snapshot.currency = result.currency or "USD"
    snapshot.as_of = result.as_of
    snapshot.quality = result.quality
    snapshot.payload = result.payload or {}
    db.session.commit()
    return result
