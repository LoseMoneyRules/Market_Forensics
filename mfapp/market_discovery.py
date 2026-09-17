from __future__ import annotations

from typing import Any

import requests

from .core_models import Company, Coverage, Security, ValuationModel
from .current_financials import current_row
from .data_providers import get_secret, latest_snapshot
from .extensions import db
from .readiness import research_readiness
from .services import valuation_result


def _headers(user_id: int) -> dict[str, str] | None:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _n(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _known_context(symbol: str, user_id: int) -> dict[str, Any]:
    security = Security.query.filter(db.func.upper(Security.ticker) == symbol.upper(), Security.active.is_(True)).order_by(Security.is_primary.desc()).first()
    if not security:
        return {}
    company = db.session.get(Company, security.company_id)
    row = current_row(company.id) if company else None
    metrics = (row or {}).get("metrics") or {}
    coverage = Coverage.query.filter_by(user_id=user_id, security_id=security.id).first()
    valuation = valuation_result(coverage) if coverage else {}
    market = latest_snapshot(security.id)
    price = _n(market.price) if market else None
    base = _n(valuation.get("base"))
    gap = ((base / price - 1.0) * 100.0) if base is not None and price not in (None, 0) else None
    return {
        "known": True,
        "company_id": company.id if company else None,
        "coverage_id": coverage.id if coverage else None,
        "revenue_growth_pct": _n(metrics.get("revenue_growth_pct")),
        "operating_margin_pct": _n(metrics.get("operating_margin_pct")),
        "fcf_margin_pct": _n(metrics.get("fcf_margin_pct")),
        "inventory_growth_pct": _n(metrics.get("inventory_growth_pct")),
        "receivables_growth_pct": _n(metrics.get("receivables_growth_pct")),
        "base_gap_pct": gap,
        "readiness": research_readiness(coverage) if coverage else None,
    }


def _local_lenses(context: dict[str, Any]) -> list[str]:
    if not context:
        return []
    labels: list[str] = []
    rev = context.get("revenue_growth_pct")
    op = context.get("operating_margin_pct")
    fcf = context.get("fcf_margin_pct")
    inv = context.get("inventory_growth_pct")
    rec = context.get("receivables_growth_pct")
    gap = context.get("base_gap_pct")

    quality = (op is not None and op > 8) and (fcf is not None and fcf > 5)
    if quality and gap is not None and gap >= 20:
        labels.append("QUALITY AT DISCOUNT")
    if rev is not None and rev >= 8 and fcf is not None and fcf > 0:
        labels.append("FUNDAMENTAL INFLECTION")
    if gap is not None and gap >= 25:
        labels.append("LONG DISLOCATION")
    if gap is not None and gap <= -15:
        labels.append("SHORT DISLOCATION")
    if gap is not None and gap >= 25 and not quality:
        labels.append("POTENTIAL VALUE TRAP")
    if rev is not None and ((inv is not None and inv - rev >= 12) or (rec is not None and rec - rev >= 12)):
        labels.append("FORENSIC DIVERGENCE")
    return labels


def market_scan(user_id: int) -> dict[str, Any]:
    """Hosting-friendly market-wide discovery.

    Alpaca's screeners reduce the full tradable universe to the day's most active
    and largest movers. We then enrich known names with stored fundamental/valuation
    evidence. Unknown names are deliberately not assigned fair values.
    """
    headers = _headers(user_id)
    if not headers:
        return {"configured": False, "candidates": [], "errors": ["Alpaca credentials are not configured."]}

    errors: list[str] = []
    by_symbol: dict[str, dict[str, Any]] = {}

    def touch(symbol: str) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        return by_symbol.setdefault(symbol, {
            "ticker": symbol,
            "activity_rank": None,
            "activity_volume": None,
            "trades": None,
            "move_pct": None,
            "move_side": "",
            "scan_score": 0.0,
            "lenses": [],
        })

    try:
        response = requests.get(
            "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives",
            headers=headers,
            params={"by": "volume", "top": 100},
            timeout=15,
        )
        if response.status_code == 200:
            rows = (response.json() or {}).get("most_actives") or []
            for idx, row in enumerate(rows, start=1):
                item = touch(row.get("symbol"))
                item["activity_rank"] = idx
                item["activity_volume"] = _n(row.get("volume"))
                item["trades"] = _n(row.get("trade_count") or row.get("trades"))
                item["scan_score"] += max(0.0, 35.0 - (idx - 1) * .35)
                item["lenses"].append("HIGH ACTIVITY")
        else:
            errors.append(f"Most-active screener HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Most-active screener: {type(exc).__name__}")

    try:
        response = requests.get(
            "https://data.alpaca.markets/v1beta1/screener/stocks/movers",
            headers=headers,
            params={"top": 50},
            timeout=15,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            for side_key, side_label, sign in (("gainers", "GAINER", 1), ("losers", "LOSER", -1)):
                for idx, row in enumerate(payload.get(side_key) or [], start=1):
                    item = touch(row.get("symbol"))
                    change = _n(row.get("percent_change") or row.get("change_pct") or row.get("percentChange"))
                    if change is not None and sign < 0 and change > 0:
                        change = -change
                    item["move_pct"] = change
                    item["move_side"] = side_label
                    item["scan_score"] += min(45.0, abs(change or 0) * 2.5) + max(0.0, 10.0 - idx * .15)
                    item["lenses"].append("PRICE DISLOCATION")
        else:
            errors.append(f"Market-movers screener HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Market-movers screener: {type(exc).__name__}")

    covered = {
        row[0].upper()
        for row in db.session.query(Security.ticker)
        .join(Coverage, Coverage.security_id == Security.id)
        .filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED")
        .all()
    }

    for symbol, item in by_symbol.items():
        context = _known_context(symbol, user_id)
        local = _local_lenses(context)
        item["known_context"] = context
        item["lenses"] = list(dict.fromkeys(local + item["lenses"]))
        item["in_coverage"] = symbol in covered
        if local:
            item["scan_score"] += min(25.0, len(local) * 6.0)
        if not local:
            item["lenses"].append("DEEP RESEARCH REQUIRED")
        item["scan_score"] = round(item["scan_score"], 2)

    candidates = sorted(by_symbol.values(), key=lambda row: row["scan_score"], reverse=True)[:120]
    return {
        "configured": True,
        "candidates": candidates,
        "errors": errors,
        "universe_source": "Alpaca most-active + market-movers screeners",
        "candidate_count": len(candidates),
        "known_enriched": sum(1 for row in candidates if row.get("known_context")),
    }


__all__ = ["market_scan"]
