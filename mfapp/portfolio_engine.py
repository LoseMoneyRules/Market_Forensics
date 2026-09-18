from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import sqrt
from typing import Any

from sqlalchemy import and_

from .core_models import (
    Company, Coverage, HistoricalPrice, InvestmentState, MarketSnapshot, Position,
    RiskPlan, Security,
)
from .extensions import db
from .models import UserPreference
from .research_cache import latest_cache_map


PORTFOLIO_CACHE_KEY = "portfolio_analytics"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _return_series(security_id: int, days: int = 320) -> dict[date, float]:
    cutoff = date.today() - timedelta(days=max(120, days))
    rows = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security_id,
        HistoricalPrice.trade_date >= cutoff,
    ).order_by(HistoricalPrice.trade_date.asc(), HistoricalPrice.id.asc()).all()
    closes: list[tuple[date, float]] = []
    for row in rows:
        price = row.close_split_adjusted if row.close_split_adjusted is not None else row.close_raw
        p = _f(price)
        if p > 0:
            closes.append((row.trade_date, p))
    out: dict[date, float] = {}
    prev = None
    for d, p in closes:
        if prev and prev > 0:
            out[d] = p / prev - 1.0
        prev = p
    return out


def _corr(a: dict[date, float], b: dict[date, float]) -> tuple[float | None, int]:
    common = sorted(set(a) & set(b))
    if len(common) < 30:
        return None, len(common)
    xs = [a[d] for d in common]
    ys = [b[d] for d in common]
    mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None, len(common)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / sqrt(vx * vy), len(common)


def _latest_market_map(security_ids: list[int]) -> dict[int, MarketSnapshot]:
    if not security_ids:
        return {}
    latest_times = db.session.query(
        MarketSnapshot.security_id.label("security_id"),
        db.func.max(MarketSnapshot.as_of).label("max_as_of"),
    ).filter(MarketSnapshot.security_id.in_(security_ids)).group_by(MarketSnapshot.security_id).subquery()
    rows = MarketSnapshot.query.join(
        latest_times,
        and_(
            MarketSnapshot.security_id == latest_times.c.security_id,
            MarketSnapshot.as_of == latest_times.c.max_as_of,
        ),
    ).order_by(MarketSnapshot.id.desc()).all()
    out: dict[int, MarketSnapshot] = {}
    for row in rows:
        out.setdefault(row.security_id, row)
    return out


def _stored_portfolio_analytics(user_id: int) -> dict[str, Any]:
    row = UserPreference.query.filter_by(user_id=user_id, key=PORTFOLIO_CACHE_KEY).first()
    return dict((row.value or {}) if row else {})


def _assemble_rows(user_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    positions = Position.query.filter_by(user_id=user_id).order_by(Position.updated_at.desc()).all()
    if not positions:
        return [], {
            "market_value": 0.0, "cost": 0.0, "pnl": 0.0, "positions": 0,
            "top_weight_pct": 0.0, "concentration_hhi": 0.0,
            "validated_positions": 0, "validated_pct": 0.0, "risk_breaches": [],
        }

    security_ids = [row.security_id for row in positions]
    securities = {row.id: row for row in Security.query.filter(Security.id.in_(security_ids)).all()}
    company_ids = [row.company_id for row in securities.values()]
    companies = {row.id: row for row in Company.query.filter(Company.id.in_(company_ids)).all()} if company_ids else {}
    coverages = Coverage.query.filter(Coverage.user_id == user_id, Coverage.security_id.in_(security_ids)).all()
    coverage_by_security = {row.security_id: row for row in coverages}
    coverage_ids = [row.id for row in coverages]
    investment_by_coverage = {
        row.coverage_id: row for row in InvestmentState.query.filter(InvestmentState.coverage_id.in_(coverage_ids)).all()
    } if coverage_ids else {}
    risk_by_coverage = {
        row.coverage_id: row for row in RiskPlan.query.filter(RiskPlan.coverage_id.in_(coverage_ids)).all()
    } if coverage_ids else {}
    research_caches = latest_cache_map(coverage_ids)
    markets = _latest_market_map(security_ids)

    raw: list[dict[str, Any]] = []
    total_market = 0.0
    total_cost = 0.0
    for position in positions:
        security = securities.get(position.security_id)
        if not security:
            continue
        company = companies.get(security.company_id)
        coverage = coverage_by_security.get(security.id)
        cache = dict(research_caches.get(coverage.id) or {}) if coverage else {}
        market = markets.get(security.id)
        shares = _f(position.shares)
        avg_cost = _f(position.avg_cost)
        price = _f(market.price) if market else 0.0
        market_value = shares * price if market else None
        cost = shares * avg_cost
        if market_value is not None:
            total_market += market_value
        total_cost += cost
        readiness = dict(cache.get("readiness") or {"validation": {"state": "NOT RUN"}})
        raw.append({
            "position": position,
            "security": security,
            "company": company,
            "coverage": coverage,
            "market": market,
            "market_value": market_value,
            "cost": cost,
            "pnl": (market_value - cost) if market_value is not None else None,
            "investment": investment_by_coverage.get(coverage.id) if coverage else None,
            "risk": risk_by_coverage.get(coverage.id) if coverage else None,
            "valuation": dict(cache.get("valuation") or {}),
            "readiness": readiness,
            "decision_lenses": dict(cache.get("decision_lenses") or {}),
        })

    for row in raw:
        value = row["market_value"]
        row["weight_pct"] = (value / total_market * 100.0) if value is not None and total_market else None
        limit = _f(row["risk"].max_position_pct) if row.get("risk") and row["risk"].max_position_pct is not None else None
        row["position_limit_pct"] = limit
        row["risk_breach"] = bool(limit is not None and row["weight_pct"] is not None and row["weight_pct"] > limit)

    raw.sort(key=lambda x: abs(x["market_value"] or 0), reverse=True)
    weights = [float(row["weight_pct"]) for row in raw if row["weight_pct"] is not None]
    validated = sum(1 for row in raw if (row.get("readiness") or {}).get("validation", {}).get("state") == "VALIDATED")
    breaches = [{
        "ticker": row["security"].ticker,
        "weight_pct": row["weight_pct"],
        "max_position_pct": row["position_limit_pct"],
    } for row in raw if row.get("risk_breach")]

    return raw, {
        "market_value": total_market,
        "cost": total_cost,
        "pnl": total_market - total_cost,
        "positions": len(raw),
        "top_weight_pct": max(weights) if weights else 0.0,
        "concentration_hhi": sum((w / 100.0) ** 2 for w in weights),
        "validated_positions": validated,
        "validated_pct": (validated / len(raw) * 100.0) if raw else 0.0,
        "risk_breaches": breaches,
    }


def portfolio_rows(user_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fast request-path portfolio view.

    Correlations are read from the last background analytics cache; historical
    price series are never loaded while a page is opening.
    """
    rows, totals = _assemble_rows(user_id)
    analytics = _stored_portfolio_analytics(user_id)
    totals["correlations"] = list(analytics.get("correlations") or [])[:20]
    totals["analytics_generated_at"] = analytics.get("generated_at")
    totals["analytics_ready"] = bool(analytics)
    return rows, totals


def refresh_portfolio_analytics(user_id: int) -> dict[str, Any]:
    rows, _ = _assemble_rows(user_id)
    top = [row for row in rows if row.get("market_value") is not None][:8]
    series = {row["security"].id: _return_series(row["security"].id) for row in top}
    correlation_rows: list[dict[str, Any]] = []
    for i, left in enumerate(top):
        for right in top[i + 1:]:
            corr, samples = _corr(series[left["security"].id], series[right["security"].id])
            if corr is not None:
                correlation_rows.append({
                    "left": left["security"].ticker,
                    "right": right["security"].ticker,
                    "correlation": corr,
                    "samples": samples,
                })
    correlation_rows.sort(key=lambda row: abs(row["correlation"]), reverse=True)
    value = {
        "generated_at": utcnow().isoformat(),
        "correlations": correlation_rows[:20],
        "top_security_ids": [row["security"].id for row in top],
    }
    pref = UserPreference.query.filter_by(user_id=user_id, key=PORTFOLIO_CACHE_KEY).first()
    if pref is None:
        pref = UserPreference(user_id=user_id, key=PORTFOLIO_CACHE_KEY, value=value)
        db.session.add(pref)
    else:
        pref.value = value
    db.session.commit()
    return value


__all__ = ["portfolio_rows", "refresh_portfolio_analytics", "PORTFOLIO_CACHE_KEY"]
