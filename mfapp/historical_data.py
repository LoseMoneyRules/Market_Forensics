from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .core_models import HistoricalPrice, Security
from .data_providers import get_secret
from .extensions import db

USER_AGENT = "MarketForensics/0.2.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _iso_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _alpaca_pages(ticker: str, user_id: int, start: date, end: date, adjustment: str) -> list[dict[str, Any]]:
    key, secret = get_secret(user_id, "alpaca_key"), get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return []
    rows: list[dict[str, Any]] = []
    token = None
    for _ in range(30):
        params = {
            "start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat(),
            "timeframe": "1Day", "adjustment": adjustment, "feed": "iex", "limit": 10000,
        }
        if token:
            params["page_token"] = token
        response = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/bars",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params=params, timeout=35,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Alpaca historical HTTP {response.status_code}")
        payload = response.json() or {}
        rows.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
    return rows


def _alpaca_history(ticker: str, user_id: int, start: date, end: date) -> list[dict[str, Any]]:
    raw = _alpaca_pages(ticker, user_id, start, end, "raw")
    if not raw:
        return []
    split = _alpaca_pages(ticker, user_id, start, end, "split")
    split_map = {str(row.get("t") or "")[:10]: row for row in split}
    out = []
    for row in raw:
        day = _iso_day(row.get("t"))
        close = _num(row.get("c"))
        adjusted = _num((split_map.get(str(row.get("t") or "")[:10]) or {}).get("c"))
        if day is None or close is None or close <= 0:
            continue
        if adjusted is None or adjusted <= 0:
            adjusted = close
        out.append({
            "trade_date": day, "provider": "Alpaca IEX historical", "close_raw": close,
            "close_split_adjusted": adjusted, "split_basis_factor": adjusted / close,
            "volume": _num(row.get("v")), "quality": "OBSERVED",
            "payload": {"source": "alpaca", "raw": True, "split_adjusted_pair": bool(split_map)},
        })
    return out


def _tiingo_history(ticker: str, user_id: int, start: date, end: date) -> list[dict[str, Any]]:
    token = get_secret(user_id, "tiingo_token")
    if not token:
        return []
    response = requests.get(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
        params={"startDate": start.isoformat(), "endDate": end.isoformat(), "resampleFreq": "daily", "token": token},
        headers={"User-Agent": USER_AGENT}, timeout=35,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Tiingo historical HTTP {response.status_code}")
    rows = response.json() or []
    parsed = []
    for row in rows:
        day, close = _iso_day(row.get("date")), _num(row.get("close"))
        if day is not None and close is not None and close > 0:
            parsed.append((day, close, _num(row.get("splitFactor")) or 1.0, row))
    parsed.sort(key=lambda x: x[0])
    future_product = 1.0
    factors: dict[date, float] = {}
    for day, _, split_factor, _ in reversed(parsed):
        factors[day] = 1.0 / future_product if future_product else 1.0
        if split_factor and split_factor > 0:
            future_product *= split_factor
    return [{
        "trade_date": day, "provider": "Tiingo historical", "close_raw": close,
        "close_split_adjusted": close * factors[day], "split_basis_factor": factors[day],
        "volume": _num(row.get("volume")), "quality": "OBSERVED",
        "payload": {"source": "tiingo", "split_factor": split_factor},
    } for day, close, split_factor, row in parsed]


def _public_history(ticker: str, start: date, end: date) -> list[dict[str, Any]]:
    symbol = ticker.replace(".", "-")
    p1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"period1": p1, "period2": p2, "interval": "1d", "events": "splits", "includeAdjustedClose": "true"},
        headers={"User-Agent": f"Mozilla/5.0 {USER_AGENT}"}, timeout=35,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Public historical chart HTTP {response.status_code}")
    result = (((response.json() or {}).get("chart") or {}).get("result") or [])
    if not result:
        return []
    node = result[0]
    timestamps = node.get("timestamp") or []
    quote = (((node.get("indicators") or {}).get("quote") or [{}])[0])
    closes, volumes = quote.get("close") or [], quote.get("volume") or []
    split_events = ((node.get("events") or {}).get("splits") or {})
    split_by_day: dict[date, float] = {}
    for raw in split_events.values():
        day = datetime.fromtimestamp(int(raw.get("date") or 0), tz=timezone.utc).date()
        numerator, denominator = _num(raw.get("numerator")), _num(raw.get("denominator"))
        ratio = (numerator / denominator) if numerator not in (None, 0) and denominator not in (None, 0) else _num(raw.get("splitRatio"))
        if ratio and ratio > 0:
            split_by_day[day] = ratio
    future_product = 1.0
    factors: dict[date, float] = {}
    days = [datetime.fromtimestamp(int(ts), tz=timezone.utc).date() for ts in timestamps]
    for day in reversed(days):
        factors[day] = 1.0 / future_product if future_product else 1.0
        if day in split_by_day:
            future_product *= split_by_day[day]
    out = []
    for idx, day in enumerate(days):
        close = _num(closes[idx] if idx < len(closes) else None)
        if close is None or close <= 0:
            continue
        factor = factors.get(day, 1.0)
        out.append({
            "trade_date": day, "provider": "Public historical chart", "close_raw": close,
            "close_split_adjusted": close * factor, "split_basis_factor": factor,
            "volume": _num(volumes[idx] if idx < len(volumes) else None), "quality": "PUBLIC_FALLBACK",
            "payload": {"source": "public_chart", "split_event": split_by_day.get(day)},
        })
    return out


def _coverage_stats(rows: list[dict[str, Any]], requested_start: date, requested_end: date) -> dict[str, Any]:
    dates = sorted(row["trade_date"] for row in rows if isinstance(row.get("trade_date"), date))
    if not dates:
        return {"first": None, "last": None, "span_days": 0, "complete": False}
    first, last = dates[0], dates[-1]
    requested_days = max(1, (requested_end - requested_start).days)
    span_days = max(0, (last - first).days)
    # Exchanges can start later for newer issuers. For established names, require roughly
    # the requested history rather than accepting any provider with >200 observations.
    complete = first <= requested_start + timedelta(days=75) and last >= requested_end - timedelta(days=10)
    return {"first": first, "last": last, "span_days": span_days, "requested_days": requested_days, "complete": complete}


def fetch_history(ticker: str, user_id: int, lookback_years: int = 10) -> tuple[list[dict[str, Any]], list[str]]:
    years = max(1, min(int(lookback_years), 40))
    end = date.today()
    start = end - timedelta(days=366 * years + 45)
    errors: list[str] = []
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any], str]] = []
    loaders = (
        ("Alpaca", lambda: _alpaca_history(ticker, user_id, start, end)),
        ("Tiingo", lambda: _tiingo_history(ticker, user_id, start, end)),
        ("Public", lambda: _public_history(ticker, start, end)),
    )
    for name, loader in loaders:
        try:
            rows = loader()
            stats = _coverage_stats(rows, start, end)
            if rows:
                candidates.append((rows, stats, name))
            if len(rows) >= 200 and stats["complete"]:
                return rows, errors
            if rows:
                first = stats["first"].isoformat() if stats["first"] else "?"
                last = stats["last"].isoformat() if stats["last"] else "?"
                errors.append(f"{name}: partial historical span {first} to {last} for requested {years}Y")
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    if candidates:
        candidates.sort(key=lambda item: (item[1].get("span_days") or 0, len(item[0])), reverse=True)
        rows, stats, name = candidates[0]
        errors.append(f"Using widest available provider {name}; requested {years}Y span is not fully covered.")
        return rows, errors
    return [], errors


def refresh_historical_prices(security: Security, user_id: int, lookback_years: int = 10) -> dict[str, Any]:
    rows, errors = fetch_history(security.ticker, user_id, lookback_years)
    if not rows:
        raise RuntimeError("Historical market data unavailable. " + "; ".join(errors))
    provider = rows[0]["provider"]
    saved = 0
    for item in rows:
        row = HistoricalPrice.query.filter_by(security_id=security.id, trade_date=item["trade_date"], provider=item["provider"]).first()
        if row is None:
            row = HistoricalPrice(security_id=security.id, trade_date=item["trade_date"], provider=item["provider"])
            db.session.add(row)
        row.close_raw = item["close_raw"]
        row.close_split_adjusted = item["close_split_adjusted"]
        row.split_basis_factor = item["split_basis_factor"]
        row.volume = item.get("volume")
        row.quality = item["quality"]
        row.payload = item.get("payload") or {}
        row.retrieved_at = _utcnow()
        saved += 1
    db.session.commit()
    dates = sorted(item["trade_date"] for item in rows)
    return {
        "provider": provider, "rows": saved, "errors": errors, "lookback_years": lookback_years,
        "first_date": dates[0].isoformat() if dates else None,
        "last_date": dates[-1].isoformat() if dates else None,
        "span_years": round((dates[-1] - dates[0]).days / 365.25, 2) if len(dates) >= 2 else 0,
    }


def preferred_provider(security_id: int) -> str | None:
    rows = db.session.query(
        HistoricalPrice.provider,
        db.func.count(HistoricalPrice.id),
        db.func.min(HistoricalPrice.trade_date),
        db.func.max(HistoricalPrice.trade_date),
    ).filter(HistoricalPrice.security_id == security_id).group_by(HistoricalPrice.provider).all()
    if not rows:
        return None
    rows = sorted(rows, key=lambda row: (((row[3] - row[2]).days if row[2] and row[3] else 0), row[1]), reverse=True)
    return rows[0][0]


def price_on_or_after(security_id: int, target: date, max_days: int = 14, *, adjusted: bool = True, provider: str | None = None) -> HistoricalPrice | None:
    provider = provider or preferred_provider(security_id)
    query = HistoricalPrice.query.filter(HistoricalPrice.security_id == security_id, HistoricalPrice.trade_date >= target, HistoricalPrice.trade_date <= target + timedelta(days=max_days))
    if provider:
        query = query.filter(HistoricalPrice.provider == provider)
    return query.order_by(HistoricalPrice.trade_date.asc()).first()


def price_on_or_before(security_id: int, target: date, max_days: int = 14, *, provider: str | None = None) -> HistoricalPrice | None:
    provider = provider or preferred_provider(security_id)
    query = HistoricalPrice.query.filter(HistoricalPrice.security_id == security_id, HistoricalPrice.trade_date <= target, HistoricalPrice.trade_date >= target - timedelta(days=max_days))
    if provider:
        query = query.filter(HistoricalPrice.provider == provider)
    return query.order_by(HistoricalPrice.trade_date.desc()).first()


__all__ = ["refresh_historical_prices", "fetch_history", "preferred_provider", "price_on_or_after", "price_on_or_before"]
