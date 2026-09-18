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
MIN_DOLLAR_VOLUME = 50_000_000.0
MIN_DAILY_VOLUME = 500_000.0
MAX_PER_SIDE = 12
MAJOR_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA"}
NON_OPERATING_NAME_TOKENS = (
    " WARRANT", "WARRANT ", " RIGHTS", " RIGHT", " UNIT", " UNITS",
    " ETF", "ETN", " EXCHANGE TRADED FUND", " FUND", " PORTFOLIO",
    " ACQUISITION CORP", " ACQUISITION CO", " BLANK CHECK",
    " PREFERRED", " PREFERENCE", " DEPOSITARY SHARE", " NOTES DUE ",
)


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

    Discovery never runs fundamentals/readiness/valuation engines per symbol.
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


def _asset_map(symbols: set[str], headers: dict[str, str], errors: list[str]) -> dict[str, dict[str, Any]]:
    """Validate candidate securities in one bounded Alpaca assets request.

    Failing closed is deliberate: if the scan cannot verify an active tradable
    US equity, it does not promote that symbol merely to fill the radar.
    """
    if not symbols:
        return {}
    try:
        response = requests.get(
            "https://paper-api.alpaca.markets/v2/assets",
            headers=headers,
            params={"status": "active", "asset_class": "us_equity"},
            timeout=(5, 20),
        )
        if response.status_code != 200:
            errors.append(f"Asset qualification HTTP {response.status_code}.")
            return {}
        wanted = {str(x).upper() for x in symbols}
        out: dict[str, dict[str, Any]] = {}
        for raw in response.json() or []:
            symbol = str(raw.get("symbol") or "").upper()
            if symbol not in wanted:
                continue
            out[symbol] = {
                "name": str(raw.get("name") or symbol).strip(),
                "status": str(raw.get("status") or "").lower(),
                "exchange": str(raw.get("exchange") or "").upper(),
                "tradable": bool(raw.get("tradable")),
                "marginable": bool(raw.get("marginable")),
                "shortable": bool(raw.get("shortable")),
                "easy_to_borrow": bool(raw.get("easy_to_borrow")),
                "fractionable": bool(raw.get("fractionable")),
            }
        return out
    except Exception as exc:
        errors.append(f"Asset qualification: {type(exc).__name__}")
        return {}


def _asset_is_operating_equity(asset: dict[str, Any]) -> tuple[bool, str]:
    if not asset:
        return False, "UNVERIFIED ASSET"
    if asset.get("status") != "active" or not asset.get("tradable"):
        return False, "INACTIVE / NOT TRADABLE"
    exchange = str(asset.get("exchange") or "").upper()
    if exchange not in MAJOR_EXCHANGES:
        return False, "NON-CORE EXCHANGE"
    name = " " + str(asset.get("name") or "").upper() + " "
    if any(token in name for token in NON_OPERATING_NAME_TOKENS):
        return False, "NON-OPERATING SECURITY"
    return True, ""


def _priority(
    side: str,
    base_gap: float | None,
    move: float | None,
    dollar_volume: float | None,
    context: dict[str, Any],
) -> tuple[str, int, str]:
    researched = side in {"LONG", "SHORT"}
    edge = abs(base_gap or 0.0)
    dislocation = abs(move or 0.0)
    liquid = (dollar_volume or 0.0) >= 100_000_000.0
    readiness = dict(context.get("readiness") or {})
    lenses = dict(context.get("decision_lenses") or {})
    confidence = str(lenses.get("model_confidence") or "").upper()
    research_mature = bool(readiness.get("ready_to_validate")) or confidence in {"HIGH", "MEDIUM", "VALIDATED"}

    if researched and edge >= 25.0 and research_mature:
        return "P1", 1, "RESEARCHED + MATURE VALUE GAP"
    if researched and edge >= 25.0:
        return "P2", 2, "RESEARCHED VALUE GAP · PROCESS INCOMPLETE"
    if researched and edge >= 15.0:
        return "P2", 2, "RESEARCHED EDGE"
    if "LEAD" in side and dislocation >= 12.0 and liquid:
        return "P2", 2, "LIQUID QUALIFIED DISLOCATION"
    return "P3", 3, "WATCH / DEEPER CHECK"


def market_scan(user_id: int) -> dict[str, Any]:
    """High-conviction two-sided research radar.

    The scan intentionally prefers an empty list over a low-quality list.
    Screeners generate a small candidate pool; asset metadata, price, liquidity,
    shortability and stored Research context decide whether a name is allowed
    into the visible Long/Short radar.
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
                item["scan_score"] += max(0.0, 30.0 - (idx - 1) * 0.30)
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
                    item["scan_score"] += min(42.0, abs(change or 0.0) * 2.25) + max(0.0, 8.0 - idx * 0.12)
                    item["lenses"].append("PRICE DISLOCATION")
        else:
            errors.append(f"Market-movers screener HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Market-movers screener: {type(exc).__name__}")

    symbols = set(by_symbol)
    snapshots = _snapshot_map(symbols, headers, errors)
    assets = _asset_map(symbols, headers, errors)
    local_context = _coverage_context_map(user_id, symbols)

    for symbol, item in by_symbol.items():
        snap = snapshots.get(symbol) or {}
        item["price"] = _n(snap.get("price")) or _n(item.get("price"))
        item["daily_volume"] = _n(snap.get("daily_volume"))
        item["dollar_volume"] = _n(snap.get("dollar_volume"))
        if item["dollar_volume"] is None and item["price"] is not None and item.get("activity_volume") is not None:
            item["dollar_volume"] = item["price"] * item["activity_volume"]

    excluded = Counter()
    candidates: list[dict[str, Any]] = []

    for symbol, item in by_symbol.items():
        asset = dict(assets.get(symbol) or {})
        valid_asset, asset_reason = _asset_is_operating_equity(asset)
        if not valid_asset:
            excluded[asset_reason] += 1
            continue

        context = dict(local_context.get(symbol) or {})
        local = list(context.get("discovery_labels") or [])
        move = _n(item.get("move_pct"))
        base_gap = _n(context.get("base_gap_pct"))
        price = _n(item.get("price")) or _n((context.get("valuation") or {}).get("current_price"))
        volume = _n(item.get("daily_volume"))
        dollar_volume = _n(item.get("dollar_volume"))
        item["price"] = price
        item["asset"] = asset
        item["name"] = asset.get("name") or symbol
        item["exchange"] = asset.get("exchange") or ""

        reasons: list[str] = [f"{item['exchange']} · active / tradable"]
        if item.get("activity_rank"):
            reasons.append(f"Most active #{item['activity_rank']}")
        if move is not None:
            reasons.append(f"Market move {move:+.1f}%")
        if dollar_volume is not None:
            reasons.append(f"Day $ volume {dollar_volume/1_000_000:.0f}M")

        if price is None:
            excluded["PRICE UNKNOWN"] += 1
            continue

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
                radar_label = "VALUE GAP"
                item["scan_score"] += min(26.0, base_gap / 2.4)
                local.append("LONG VALUE GAP")
            elif base_gap <= -15.0:
                if price < MIN_SHORT_PRICE or not asset.get("shortable"):
                    excluded["SHORT NOT ACTIONABLE"] += 1
                    continue
                side = "SHORT"
                target_status = "ABOVE BASE"
                radar_label = "OVERVALUED VS BASE"
                item["scan_score"] += min(26.0, abs(base_gap) / 2.4)
                local.append("SHORT VALUE GAP")
            elif base_gap > 0:
                side = "LONG WATCH"
                target_status = "LIMITED ROOM"
                radar_label = "WATCH"
            else:
                if price < MIN_SHORT_PRICE or not asset.get("shortable"):
                    excluded["SHORT NOT ACTIONABLE"] += 1
                    continue
                side = "SHORT WATCH"
                target_status = "LIMITED ROOM"
                radar_label = "WATCH"
        else:
            if price < MIN_LONG_PRICE:
                excluded["LOW PRICE"] += 1
                continue
            if volume is not None and volume < MIN_DAILY_VOLUME:
                excluded["LOW VOLUME"] += 1
                continue
            if dollar_volume is None or dollar_volume < MIN_DOLLAR_VOLUME:
                excluded["LOW / UNKNOWN LIQUIDITY"] += 1
                continue

            if move is not None and move <= -10.0:
                side = "LONG LEAD"
                radar_label = "DOWNSIDE DISLOCATION"
                local.append("DOWNSIDE DISLOCATION")
                reasons.append("Intrinsic target not yet established")
            elif move is not None and move >= 10.0:
                if price < MIN_SHORT_PRICE:
                    excluded["LOW-PRICE SHORT"] += 1
                    continue
                if not asset.get("shortable"):
                    excluded["NOT SHORTABLE"] += 1
                    continue
                side = "SHORT LEAD"
                radar_label = "UPSIDE OVEREXTENSION"
                local.append("UPSIDE DISLOCATION")
                reasons.append("Intrinsic target not yet established")
            else:
                excluded["NO STRONG EDGE"] += 1
                continue

        priority, priority_rank, priority_reason = _priority(side, base_gap, move, dollar_volume, context)

        # Unknown raw-move leads are shown only when they clear the stronger P2 bar.
        if not context and priority_rank > 2:
            excluded["INSUFFICIENT CONVICTION"] += 1
            continue

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
    long_candidates = [row for row in candidates if str(row.get("research_side") or "").startswith("LONG")][:MAX_PER_SIDE]
    short_candidates = [row for row in candidates if str(row.get("research_side") or "").startswith("SHORT")][:MAX_PER_SIDE]
    final = long_candidates + short_candidates

    return {
        "configured": True,
        "candidates": final,
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "errors": errors,
        "universe_source": "Alpaca active/tradable assets + most-active + movers + IEX snapshots + stored Research cache",
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
            "min_daily_volume": MIN_DAILY_VOLUME,
            "major_exchanges": sorted(MAJOR_EXCHANGES),
            "short_requires_shortable": True,
            "unknown_targets": "never treated as intrinsic conclusions",
            "fill_quota": "none",
        },
        "enrichment_mode": "FAIL_CLOSED_ASSET_QUALIFICATION",
    }


__all__ = ["market_scan"]
