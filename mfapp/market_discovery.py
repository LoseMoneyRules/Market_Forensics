from __future__ import annotations

from collections import Counter
from typing import Any

import requests

from .core_models import Coverage, Security
from .data_providers import get_secret
from .extensions import db
from .research_cache import latest_cache_map

MIN_LONG_PRICE = 5.0
MIN_SHORT_PRICE = 10.0
MIN_DOLLAR_VOLUME = 25_000_000.0


def _headers(user_id: int) -> dict[str, str] | None:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _n(value: Any) -> float | None:
    try:
        out = float(value) if value is not None else None
        return out if out is None or out == out else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _coverage_context_map(user_id: int, symbols: set[str]) -> dict[str, dict[str, Any]]:
    """Batch-read already materialized Research context.

    Discovery must never run fundamentals/readiness/valuation engines per symbol.
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

    caches = latest_cache_map([coverage.id for coverage, _ in rows])
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


def _snapshot_map(symbols: set[str], headers: dict[str, str], errors: list[str]) -> dict[str, dict[str, Any]]:
    """One bounded market-data call to qualify screener results.

    This prevents penny/illiquid names from becoming high-priority short ideas
    merely because they printed a large percentage move.
    """
    if not symbols:
        return {}
    try:
        response = requests.get(
            "https://data.alpaca.markets/v2/stocks/snapshots",
            headers=headers,
            params={"symbols": ",".join(sorted(symbols)), "feed": "iex"},
            timeout=(5, 15),
        )
        if response.status_code != 200:
            errors.append(f"Snapshot qualification HTTP {response.status_code}.")
            return {}
        payload = response.json() or {}
        out: dict[str, dict[str, Any]] = {}
        for symbol, node in payload.items():
            node = node or {}
            trade = node.get("latestTrade") or {}
            day = node.get("dailyBar") or {}
            price = _n(trade.get("p")) or _n(day.get("c"))
            volume = _n(day.get("v"))
            out[str(symbol).upper()] = {
                "price": price,
                "daily_volume": volume,
                "dollar_volume": (price * volume) if price is not None and volume is not None else None,
            }
        return out
    except Exception as exc:
        errors.append(f"Snapshot qualification: {type(exc).__name__}")
        return {}


def _priority(side: str, base_gap: float | None, move: float | None, dollar_volume: float | None) -> tuple[str, int, str]:
    researched = side in {"LONG", "SHORT"}
    edge = abs(base_gap or 0.0)
    dislocation = abs(move or 0.0)
    liquid = (dollar_volume or 0.0) >= 50_000_000.0
    if researched and edge >= 25.0:
        return "P1", 1, "RESEARCHED VALUE GAP"
    if researched and edge >= 15.0:
        return "P2", 2, "RESEARCHED EDGE"
    if "LEAD" in side and dislocation >= 12.0 and liquid:
        return "P2", 2, "QUALIFIED DISLOCATION"
    return "P3", 3, "WATCH / DEEPER CHECK"


def market_scan(user_id: int) -> dict[str, Any]:
    """Two-sided market radar with quality guardrails and explicit priorities.

    Provider screeners create leads. A single snapshot call qualifies price and
    liquidity. Stored Research Base gaps outrank raw price moves whenever they
    exist. No unknown name receives an intrinsic-value conclusion.
    """
    headers = _headers(user_id)
    if not headers:
        return {
            "configured": False, "candidates": [], "long_candidates": [], "short_candidates": [],
            "errors": ["Alpaca credentials are not configured."],
        }

    errors: list[str] = []
    by_symbol: dict[str, dict[str, Any]] = {}

    def touch(symbol: str) -> dict[str, Any]:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return {}
        return by_symbol.setdefault(symbol, {
            "ticker": symbol,
            "activity_rank": None,
            "activity_volume": None,
            "trades": None,
            "move_pct": None,
            "move_side": "",
            "price": None,
            "daily_volume": None,
            "dollar_volume": None,
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
                if not item:
                    continue
                item["activity_rank"] = idx
                item["activity_volume"] = _n(row.get("volume"))
                item["trades"] = _n(row.get("trade_count") or row.get("trades"))
                item["scan_score"] += max(0.0, 35.0 - (idx - 1) * 0.35)
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
                    if not item:
                        continue
                    change = _n(row.get("percent_change") or row.get("change_pct") or row.get("percentChange"))
                    if change is not None and sign < 0 and change > 0:
                        change = -change
                    item["move_pct"] = change
                    item["move_side"] = side_label
                    item["price"] = _n(row.get("price")) or item.get("price")
                    item["scan_score"] += min(45.0, abs(change or 0.0) * 2.5) + max(0.0, 10.0 - idx * 0.15)
                    item["lenses"].append("PRICE DISLOCATION")
        else:
            errors.append(f"Market-movers screener HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Market-movers screener: {type(exc).__name__}")

    snapshots = _snapshot_map(set(by_symbol), headers, errors)
    for symbol, item in by_symbol.items():
        snap = snapshots.get(symbol) or {}
        item["price"] = _n(snap.get("price")) or _n(item.get("price"))
        item["daily_volume"] = _n(snap.get("daily_volume"))
        item["dollar_volume"] = _n(snap.get("dollar_volume"))
        if item["dollar_volume"] is None and item["price"] is not None and item.get("activity_volume") is not None:
            item["dollar_volume"] = item["price"] * item["activity_volume"]

    local_context = _coverage_context_map(user_id, set(by_symbol))
    excluded = Counter()
    candidates: list[dict[str, Any]] = []

    for symbol, item in by_symbol.items():
        context = dict(local_context.get(symbol) or {})
        local = list(context.get("discovery_labels") or [])
        move = _n(item.get("move_pct"))
        base_gap = _n(context.get("base_gap_pct"))
        price = _n(item.get("price")) or _n((context.get("valuation") or {}).get("current_price"))
        dollar_volume = _n(item.get("dollar_volume"))
        item["price"] = price
        reasons: list[str] = []
        if item.get("activity_rank"):
            reasons.append(f"Most active #{item['activity_rank']}")
        if move is not None:
            reasons.append(f"Market move {move:+.1f}%")
        if price is not None:
            reasons.append(f"Price ${price:,.2f}")

        side = ""
        target_status = "TARGET UNKNOWN"
        radar_label = ""

        if context.get("cache_ready") and base_gap is not None:
            reasons.append(f"Stored Base gap {base_gap:+.1f}%")
            if abs(base_gap) <= 7.5:
                excluded["AT / NEAR BASE"] += 1
                continue
            if base_gap >= 15.0:
                side = "LONG"
                target_status = "ROOM TO BASE"
                radar_label = "VALUE GAP · RESEARCHED"
                item["scan_score"] += min(25.0, base_gap / 2.5)
                local.append("LONG VALUE GAP")
            elif base_gap <= -15.0:
                side = "SHORT"
                target_status = "ABOVE BASE"
                radar_label = "OVERVALUED VS BASE"
                item["scan_score"] += min(25.0, abs(base_gap) / 2.5)
                local.append("SHORT VALUE GAP")
            elif base_gap > 0:
                side = "LONG WATCH"
                target_status = "LIMITED ROOM"
                radar_label = "WATCH · LIMITED ROOM"
            else:
                side = "SHORT WATCH"
                target_status = "LIMITED ROOM"
                radar_label = "WATCH · LIMITED ROOM"
        else:
            if price is None:
                excluded["PRICE UNKNOWN"] += 1
                continue
            if price < MIN_LONG_PRICE:
                excluded["LOW PRICE"] += 1
                continue
            if dollar_volume is not None and dollar_volume < MIN_DOLLAR_VOLUME:
                excluded["LOW LIQUIDITY"] += 1
                continue
            if move is not None and move <= -8.0:
                side = "LONG LEAD"
                radar_label = "DISLOCATION · TARGET UNKNOWN"
                local.append("DOWNSIDE DISLOCATION")
                reasons.append("Intrinsic value not yet known")
            elif move is not None and move >= 10.0 and price >= MIN_SHORT_PRICE:
                side = "SHORT LEAD"
                radar_label = "OVEREXTENSION · TARGET UNKNOWN"
                local.append("UPSIDE DISLOCATION")
                reasons.append("Intrinsic value not yet known")
            elif move is not None and move >= 10.0 and price < MIN_SHORT_PRICE:
                excluded["LOW-PRICE SHORT GUARDRAIL"] += 1
                continue
            else:
                excluded["NO QUALIFIED EDGE"] += 1
                continue

        priority, priority_rank, priority_reason = _priority(side, base_gap, move, dollar_volume)
        if priority == "P1":
            item["scan_score"] += 30.0
        elif priority == "P2":
            item["scan_score"] += 15.0

        item["known_context"] = context
        item["in_coverage"] = bool(context)
        item["base_gap_pct"] = base_gap
        item["research_side"] = side
        item["target_status"] = target_status
        item["radar_label"] = radar_label
        item["priority"] = priority
        item["priority_rank"] = priority_rank
        item["priority_reason"] = priority_reason
        item["why_found"] = reasons
        item["lenses"] = list(dict.fromkeys(local + item["lenses"]))
        if not context:
            item["lenses"].append("DEEP RESEARCH REQUIRED")
        elif not context.get("cache_ready"):
            item["lenses"].append("RESEARCH CACHE UPDATING")
        item["scan_score"] = round(max(0.0, item["scan_score"]), 2)
        candidates.append(item)

    candidates.sort(key=lambda row: (row.get("priority_rank", 9), -row.get("scan_score", 0.0), row["ticker"]))
    long_candidates = [row for row in candidates if str(row.get("research_side") or "").startswith("LONG")][:24]
    short_candidates = [row for row in candidates if str(row.get("research_side") or "").startswith("SHORT")][:24]
    final = long_candidates + short_candidates

    return {
        "configured": True,
        "candidates": final,
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "errors": errors,
        "universe_source": "Alpaca most-active + movers + one IEX snapshot qualification call",
        "candidate_count": len(final),
        "known_enriched": sum(1 for row in final if row.get("known_context")),
        "long_count": len(long_candidates),
        "short_count": len(short_candidates),
        "p1_count": sum(1 for row in final if row.get("priority") == "P1"),
        "p2_count": sum(1 for row in final if row.get("priority") == "P2"),
        "excluded_count": sum(excluded.values()),
        "excluded_breakdown": dict(excluded),
        "guardrails": {
            "min_long_price": MIN_LONG_PRICE,
            "min_short_price": MIN_SHORT_PRICE,
            "min_dollar_volume": MIN_DOLLAR_VOLUME,
            "unknown_targets": "never treated as intrinsic conclusions",
        },
        "enrichment_mode": "BATCH_CACHE_PLUS_SINGLE_SNAPSHOT_CALL",
    }


__all__ = ["market_scan"]
