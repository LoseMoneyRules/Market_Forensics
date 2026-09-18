from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import sqrt
from typing import Any

from sqlalchemy import and_

from .core_models import (
    Company, Coverage, HistoricalPrice, InvestmentState, MarketSnapshot,
    PortfolioRiskPlan, Position, PositionProfile, Security,
)
from .extensions import db
from .models import UserPreference
from .research_cache import latest_cache_map


PORTFOLIO_CACHE_KEY = "portfolio_analytics"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _f(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
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


def position_sizing(current_price, risk: PortfolioRiskPlan | None) -> dict[str, float | None]:
    """Local V3.1.12 downside math, expressed in percentage points for the web UI."""
    if risk is None:
        return {
            "loss_to_reference_pct": None,
            "adjusted_loss_pct": None,
            "suggested_position_pct": None,
        }
    price = _f(current_price)
    reference = _f(risk.sizing_reference_price)
    budget_pct = _f(risk.risk_budget_pct, 0.75)
    haircut_pct = max(0.0, _f(risk.event_liquidity_haircut_pct, 5.0))
    cap_pct = max(0.0, _f(risk.max_position_pct, 10.0))
    if price <= 0 or reference <= 0:
        return {
            "loss_to_reference_pct": None,
            "adjusted_loss_pct": None,
            "suggested_position_pct": None,
        }
    loss_pct = abs(price - reference) / price * 100.0
    adjusted_pct = loss_pct + haircut_pct
    suggested_pct = min(cap_pct, (budget_pct / adjusted_pct) * 100.0) if adjusted_pct > 0 else 0.0
    return {
        "loss_to_reference_pct": loss_pct,
        "adjusted_loss_pct": adjusted_pct,
        "suggested_position_pct": max(0.0, suggested_pct),
    }


def ensure_portfolio_profile(user_id: int, security_id: int) -> PositionProfile:
    row = PositionProfile.query.filter_by(user_id=user_id, security_id=security_id).first()
    if row is None:
        row = PositionProfile(user_id=user_id, security_id=security_id, side="LONG", tags="")
        db.session.add(row)
    return row


def ensure_portfolio_risk(user_id: int, security_id: int) -> PortfolioRiskPlan:
    row = PortfolioRiskPlan.query.filter_by(user_id=user_id, security_id=security_id).first()
    if row is None:
        row = PortfolioRiskPlan(
            user_id=user_id,
            security_id=security_id,
            risk_budget_pct=0.75,
            event_liquidity_haircut_pct=5.0,
            max_position_pct=10.0,
            updated_by=user_id,
        )
        db.session.add(row)
    return row


def _assemble_rows(user_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    positions = Position.query.filter_by(user_id=user_id).order_by(Position.updated_at.desc()).all()
    if not positions:
        return [], {
            "market_value": 0.0,
            "cost": 0.0,
            "pnl": 0.0,
            "positions": 0,
            "long_value": 0.0,
            "short_value": 0.0,
            "net_exposure": 0.0,
            "top_weight_pct": 0.0,
            "concentration_hhi": 0.0,
            "validated_positions": 0,
            "validated_pct": 0.0,
            "risk_breaches": [],
            "factor_exposure": [],
        }

    security_ids = [row.security_id for row in positions]
    securities = {row.id: row for row in Security.query.filter(Security.id.in_(security_ids)).all()}
    company_ids = [row.company_id for row in securities.values()]
    companies = {row.id: row for row in Company.query.filter(Company.id.in_(company_ids)).all()} if company_ids else {}
    coverages = Coverage.query.filter(Coverage.user_id == user_id, Coverage.security_id.in_(security_ids)).all()
    coverage_by_security = {row.security_id: row for row in coverages}
    coverage_ids = [row.id for row in coverages]
    investment_by_coverage = {
        row.coverage_id: row
        for row in InvestmentState.query.filter(InvestmentState.coverage_id.in_(coverage_ids)).all()
    } if coverage_ids else {}
    profiles = {
        row.security_id: row
        for row in PositionProfile.query.filter(
            PositionProfile.user_id == user_id,
            PositionProfile.security_id.in_(security_ids),
        ).all()
    }
    money_risks = {
        row.security_id: row
        for row in PortfolioRiskPlan.query.filter(
            PortfolioRiskPlan.user_id == user_id,
            PortfolioRiskPlan.security_id.in_(security_ids),
        ).all()
    }
    research_caches = latest_cache_map(coverage_ids)
    markets = _latest_market_map(security_ids)

    raw: list[dict[str, Any]] = []
    total_market = 0.0
    total_cost = 0.0
    long_value = 0.0
    short_value = 0.0
    factor_values: dict[str, float] = {}

    for position in positions:
        security = securities.get(position.security_id)
        if not security:
            continue
        company = companies.get(security.company_id)
        coverage = coverage_by_security.get(security.id)
        cache = dict(research_caches.get(coverage.id) or {}) if coverage else {}
        market = markets.get(security.id)
        profile = profiles.get(security.id)
        investment = investment_by_coverage.get(coverage.id) if coverage else None
        inferred_side = "SHORT" if investment and "SHORT" in str(investment.state or "").upper() else "LONG"
        side = str(profile.side if profile else inferred_side).upper()
        if side not in {"LONG", "SHORT"}:
            side = "LONG"
        tags = str(profile.tags if profile else "")
        shares = abs(_f(position.shares))
        avg_cost = _f(position.avg_cost)
        price = _f(market.price) if market else 0.0
        market_value = abs(shares * price) if market else None
        cost = abs(shares * avg_cost)
        sign = -1.0 if side == "SHORT" else 1.0
        pnl = ((price - avg_cost) * shares * sign) if market else None
        if market_value is not None:
            total_market += market_value
            if side == "SHORT":
                short_value += market_value
            else:
                long_value += market_value
            for tag in [item.strip() for item in tags.split(",") if item.strip()]:
                factor_values[tag] = factor_values.get(tag, 0.0) + market_value
        total_cost += cost
        readiness = dict(cache.get("readiness") or {"validation": {"state": "NOT RUN"}})
        risk = money_risks.get(security.id)
        sizing = position_sizing(price if market else None, risk)
        raw.append({
            "position": position,
            "profile": profile,
            "side": side,
            "tags": tags,
            "security": security,
            "company": company,
            "coverage": coverage,
            "market": market,
            "market_value": market_value,
            "cost": cost,
            "pnl": pnl,
            "pnl_pct": (((price / avg_cost) - 1.0) * sign * 100.0) if market and avg_cost > 0 else None,
            "investment": investment,
            "risk": risk,
            "sizing": sizing,
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
    factor_total = sum(factor_values.values()) or 0.0
    factor_exposure = [{
        "tag": tag,
        "market_value": value,
        "gross_pct": (value / factor_total * 100.0) if factor_total else None,
    } for tag, value in sorted(factor_values.items(), key=lambda item: -item[1])]

    return raw, {
        "market_value": total_market,
        "cost": total_cost,
        "pnl": sum(_f(row["pnl"]) for row in raw if row["pnl"] is not None),
        "positions": len(raw),
        "long_value": long_value,
        "short_value": short_value,
        "net_exposure": long_value - short_value,
        "top_weight_pct": max(weights) if weights else 0.0,
        "concentration_hhi": sum((w / 100.0) ** 2 for w in weights),
        "validated_positions": validated,
        "validated_pct": (validated / len(raw) * 100.0) if raw else 0.0,
        "risk_breaches": breaches,
        "factor_exposure": factor_exposure,
    }


def portfolio_rows(user_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fast request-path Portfolio view. Heavy correlations remain background materialized."""
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


__all__ = [
    "PORTFOLIO_CACHE_KEY",
    "ensure_portfolio_profile",
    "ensure_portfolio_risk",
    "portfolio_rows",
    "position_sizing",
    "refresh_portfolio_analytics",
]
