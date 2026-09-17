from __future__ import annotations

from typing import Any

from .core_models import Company, Coverage, InvestmentState, Position, RiskPlan, Security
from .data_providers import latest_snapshot
from .extensions import db
from .readiness import research_readiness
from .services import valuation_result


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def portfolio_rows(user_id: int) -> tuple[list[dict[str, Any]], dict[str, float]]:
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
    raw.sort(key=lambda x: abs(x["market_value"] or 0), reverse=True)
    return raw, {
        "market_value": total_market,
        "cost": total_cost,
        "pnl": total_market - total_cost,
        "positions": len(raw),
    }


__all__ = ["portfolio_rows"]
