from __future__ import annotations

from datetime import datetime, timezone

from .extensions import db
from .core_models import MarketSnapshot, Security
from .marketdata import (
    QuoteResult,
    _alpaca_quote,
    _alpha_vantage_quote,
    _tiingo_quote,
    _yahoo_quote,
    get_secret,
    provider_status,
    set_secret,
)


def latest_snapshot(security_id: int) -> MarketSnapshot | None:
    return MarketSnapshot.query.filter_by(security_id=security_id).order_by(MarketSnapshot.as_of.desc(), MarketSnapshot.id.desc()).first()


def refresh_security_quote(security: Security, user_id: int) -> QuoteResult:
    attempts = [
        _alpaca_quote(security.ticker, user_id),
        _tiingo_quote(security.ticker, user_id),
        _alpha_vantage_quote(security.ticker, user_id),
        _yahoo_quote(security.ticker),
    ]
    result = next((item for item in attempts if item.ok and item.price is not None and item.price > 0), None)
    if result is None:
        return QuoteResult(False, "last-good cache", message="; ".join(f"{item.provider}: {item.message}" for item in attempts))
    as_of = result.as_of or datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.add(MarketSnapshot(
        security_id=security.id,
        provider=result.provider,
        price=float(result.price),
        currency=result.currency or security.currency or "USD",
        as_of=as_of,
        quality=result.quality,
        payload=result.payload or {},
    ))
    db.session.commit()
    return result


__all__ = ["QuoteResult", "get_secret", "provider_status", "set_secret", "latest_snapshot", "refresh_security_quote"]
