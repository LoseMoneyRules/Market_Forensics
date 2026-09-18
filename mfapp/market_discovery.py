from __future__ import annotations

from typing import Any

import requests

from .core_models import Coverage, Security
from .data_providers import get_secret
from .extensions import db
from .research_cache import latest_cache_map


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


def _coverage_context_map(user_id: int, symbols: set[str]) -> dict[str, dict[str, Any]]:
    """One batched DB read for all locally known candidates.

    Discovery must never run fundamentals/readiness/valuation engines per symbol.
    Those are materialized by RECALCULATE jobs into the research cache.
    """
    if not symbols:
        return {}

    rows = (
        db.session.query(Coverage, Security)
        .join(Security, Coverage.security_id == Security.id)
        .filter(
            Coverage.user_id == user_id,
            Coverage.status != "ARCHIVED",
            Security.active.is_(True),
            db.func.upper(Security.ticker).in_(sorted(symbols)),
        )
        .all()
    )
    if not rows:
        return {}

    coverage_ids = [coverage.id for coverage, _ in rows]
    caches = latest_cache_map(coverage_ids)
    out: dict[str, dict[str, Any]] = {}
    for coverage, security in rows:
        cache = dict(caches.get(coverage.id) or {})
        valuation = dict(cache.get("valuation") or {})
        readiness = dict(cache.get("readiness") or {})
        lenses = dict(cache.get("decision_lenses") or {})
        out[security.ticker.upper()] = {
            "known": True,
            "coverage_id": coverage.id,
            "base_gap_pct": (cache.get("intelligence") or {}).get("base_gap_pct"),
            "readiness": readiness,
            "decision_lenses": lenses,
            "discovery_labels": list(cache.get("discovery_labels") or []),
            "valuation": {
                "current_price": valuation.get("current_price"),
                "base": valuation.get("base"),
            },
            "cache_ready": bool(cache),
        }
    return out


def market_scan(user_id: int) -> dict[str, Any]:
    """Fast market-wide discovery for shared hosting.

    Two bounded provider calls build the broad market radar. Local enrichment is
    a single batch cache read; no per-symbol fundamentals or valuation work is
    allowed inside this job.
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
            timeout=(5, 12),
        )
        if response.status_code == 200:
            for idx, row in enumerate((response.json() or {}).get("most_actives") or [], start=1):
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
            timeout=(5, 12),
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

    local_context = _coverage_context_map(user_id, set(by_symbol))
    for symbol, item in by_symbol.items():
        context = dict(local_context.get(symbol) or {})
        local = list(context.get("discovery_labels") or [])
        move = _n(item.get("move_pct"))
        base_gap = _n(context.get("base_gap_pct"))
        reasons: list[str] = []
        if item.get("activity_rank"):
            reasons.append(f"Most active #{item['activity_rank']}")
        if move is not None:
            reasons.append(f"Market mover {move:+.1f}%")

        side = "RESEARCH"
        target_status = "TARGET UNKNOWN"
        if context.get("cache_ready") and base_gap is not None:
            reasons.append(f"Stored Base gap {base_gap:+.1f}%")
            if abs(base_gap) <= 7.5:
                side = "NO EDGE"
                target_status = "AT / NEAR BASE"
                item["scan_score"] = max(0.0, item["scan_score"] - 30.0)
                local.append("AT / NEAR BASE")
            elif base_gap >= 15.0:
                side = "LONG"
                target_status = "ROOM TO BASE"
                item["scan_score"] += min(20.0, base_gap / 3.0)
                local.append("LONG VALUE GAP")
            elif base_gap <= -15.0:
                side = "SHORT"
                target_status = "ABOVE BASE"
                item["scan_score"] += min(20.0, abs(base_gap) / 3.0)
                local.append("SHORT VALUE GAP")
            elif base_gap > 0:
                side = "LONG WATCH"
                target_status = "LIMITED ROOM"
            else:
                side = "SHORT WATCH"
                target_status = "LIMITED ROOM"
        elif move is not None and move <= -8.0:
            side = "LONG LEAD"
            local.append("DOWNSIDE DISLOCATION")
            reasons.append("Needs intrinsic-value check")
        elif move is not None and move >= 8.0:
            side = "SHORT LEAD"
            local.append("UPSIDE DISLOCATION")
            reasons.append("Needs overvaluation check")

        item["known_context"] = context
        item["in_coverage"] = bool(context)
        item["base_gap_pct"] = base_gap
        item["research_side"] = side
        item["target_status"] = target_status
        item["why_found"] = reasons
        item["lenses"] = list(dict.fromkeys(local + item["lenses"]))
        if local:
            item["scan_score"] += min(18.0, len(local) * 4.0)
        if not context:
            item["lenses"].append("DEEP RESEARCH REQUIRED")
        elif not context.get("cache_ready"):
            item["lenses"].append("RESEARCH CACHE UPDATING")
        item["scan_score"] = round(max(0.0, item["scan_score"]), 2)

    side_order = {
        "LONG": 0, "SHORT": 0, "LONG LEAD": 1, "SHORT LEAD": 1,
        "LONG WATCH": 2, "SHORT WATCH": 2, "RESEARCH": 3, "NO EDGE": 4,
    }
    candidates = sorted(
        by_symbol.values(),
        key=lambda row: (side_order.get(row.get("research_side"), 3), -row["scan_score"], row["ticker"]),
    )[:120]
    return {
        "configured": True,
        "candidates": candidates,
        "errors": errors,
        "universe_source": "Alpaca most-active + market-movers screeners",
        "candidate_count": len(candidates),
        "known_enriched": sum(1 for row in candidates if row.get("known_context")),
        "long_count": sum(1 for row in candidates if str(row.get("research_side") or "").startswith("LONG")),
        "short_count": sum(1 for row in candidates if str(row.get("research_side") or "").startswith("SHORT")),
        "no_edge_count": sum(1 for row in candidates if row.get("research_side") == "NO EDGE"),
        "enrichment_mode": "BATCH_CACHE_ONLY",
    }


__all__ = ["market_scan"]
