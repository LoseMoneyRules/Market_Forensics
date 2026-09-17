from __future__ import annotations

from datetime import date, timedelta
from math import sqrt
from typing import Any

from .core_models import Company, Coverage, HistoricalPrice, InvestmentState, Position, RiskPlan, Security
from .data_providers import latest_snapshot
from .extensions import db
from .readiness import research_readiness
from .services import valuation_result


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


def portfolio_rows(user_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    positions = Position.query.filter_by(user_id=user_id).order_by(Position.updated_at.desc()).all()
    raw: list[dict[str, Any]] = []
    total_market = 0.0
    total_cost = 0.0
    for position in positions:
        security = db.session.get(Security, position.security_id)
        if not security:
            continue
        company = db.session.get(Company, security.company_id)
        coverage = Coverage.query.filter_by(user_id=user_id, security_id=security.id).first()
        market = latest_snapshot(security.id)
        shares = _f(position.shares)
        avg_cost = _f(position.avg_cost)
        price = _f(market.price) if market else 0.0
        market_value = shares * price if market else None
        cost = shares * avg_cost
        if market_value is not None:
            total_market += market_value
        total_cost += cost
        readiness = research_readiness(coverage) if coverage else {"validation": {"state": "NOT RUN"}}
        raw.append({
            "position": position,
            "security": security,
            "company": company,
            "coverage": coverage,
            "market": market,
            "market_value": market_value,
            "cost": cost,
            "pnl": (market_value - cost) if market_value is not None else None,
            "investment": InvestmentState.query.filter_by(coverage_id=coverage.id).first() if coverage else None,
            "risk": RiskPlan.query.filter_by(coverage_id=coverage.id).first() if coverage else None,
            "valuation": valuation_result(coverage) if coverage else {},
            "readiness": readiness,
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
    breaches = [
        {
            "ticker": row["security"].ticker,
            "weight_pct": row["weight_pct"],
            "max_position_pct": row["position_limit_pct"],
        }
        for row in raw if row.get("risk_breach")
    ]

    correlation_rows: list[dict[str, Any]] = []
    top = [row for row in raw if row.get("market_value") is not None][:8]
    series = {row["security"].id: _return_series(row["security"].id) for row in top}
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
        "correlations": correlation_rows[:20],
    }


__all__ = ["portfolio_rows"]
