from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import re
from typing import Any

import requests

from .extensions import db
from .models import UserPreference

STAGE0_CACHE_KEY = "discovery_stage0_v2"
STAGE0_CURSOR_KEY = "discovery_stage0_cursor_v2"
STAGE1_SEEN_KEY = "discovery_stage1_seen_v1"
STAGE0_CACHE_HOURS = 24
STAGE1_BATCH_SIZE = 360
STAGE1_ACTIVITY_LIMIT = 160
STAGE1_COVERAGE_LIMIT = 80
SNAPSHOT_CHUNK_SIZE = 60
MAJOR_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA"}

NON_OPERATING_NAME_TOKENS = (
    " WARRANT ", " WARRANTS ", " RIGHT ", " RIGHTS ", " UNIT ", " UNITS ",
    " ETF ", " ETN ", " EXCHANGE TRADED FUND ", " EXCHANGE-TRADED FUND ",
    " FUND ", " PREFERRED ", " PREFERENCE ", " DEPOSITARY SHARE ",
    " NOTES DUE ", " SENIOR NOTE ", " SUBORDINATED NOTE ", " DEBENTURE ",
    " ACQUISITION CORP", " ACQUISITION CO", " BLANK CHECK", " SPAC ",
    " ISHARES ", " SPDR ", " VANGUARD ", " PROSHARES ", " DIREXION ",
    " GLOBAL X ", " VANECK ", " WISDOMTREE ", " INVESCO QQQ ", " ARK ETF ",
)
NON_OPERATING_SYMBOL_SUFFIXES = (
    ".WS", "-WS", ".W", "-W", ".U", "-U", ".R", "-R",
)
VALID_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds")


def _num(value: Any) -> float | None:
    try:
        out = float(value) if value is not None else None
        return out if out is None or out == out else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _preference(user_id: int, key: str) -> dict[str, Any]:
    row = UserPreference.query.filter_by(user_id=user_id, key=key).first()
    return dict(row.value or {}) if row and isinstance(row.value, dict) else {}


def _save_preference(user_id: int, key: str, value: dict[str, Any]) -> None:
    row = UserPreference.query.filter_by(user_id=user_id, key=key).first()
    if row is None:
        row = UserPreference(user_id=user_id, key=key, value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.commit()


def _asset_is_operating_equity(raw: dict[str, Any]) -> tuple[bool, str]:
    symbol = str(raw.get("symbol") or "").upper().strip()
    name = str(raw.get("name") or "").upper().strip()
    exchange = str(raw.get("exchange") or "").upper().strip()
    status = str(raw.get("status") or "").lower().strip()
    asset_class = str(raw.get("class") or raw.get("asset_class") or "us_equity").lower().strip()

    if not symbol or not VALID_SYMBOL.fullmatch(symbol):
        return False, "INVALID TICKER"
    if status != "active" or not bool(raw.get("tradable")):
        return False, "INACTIVE / NOT TRADABLE"
    if asset_class not in {"us_equity", ""}:
        return False, "NOT US EQUITY"
    if exchange not in MAJOR_EXCHANGES:
        return False, "NON-CORE EXCHANGE"
    padded_name = f" {name} "
    if any(token in padded_name for token in NON_OPERATING_NAME_TOKENS):
        return False, "NON-OPERATING SECURITY"
    if any(symbol.endswith(suffix) for suffix in NON_OPERATING_SYMBOL_SUFFIXES):
        return False, "NON-OPERATING SECURITY"
    return True, ""


def _compact_asset(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": str(raw.get("symbol") or "").upper().strip(),
        "name": str(raw.get("name") or raw.get("symbol") or "").strip(),
        "exchange": str(raw.get("exchange") or "").upper().strip(),
        "shortable": bool(raw.get("shortable")),
        "easy_to_borrow": bool(raw.get("easy_to_borrow")),
        "marginable": bool(raw.get("marginable")),
        "fractionable": bool(raw.get("fractionable")),
    }


def stage0_universe(
    user_id: int,
    headers: dict[str, str],
    errors: list[str],
    provider_calls: Counter,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return a cached fail-closed operating-equity universe.

    One Alpaca asset-catalog call refreshes the universe at most once per day.
    The cache is CONTROL-private and does not create Coverage, Research or Portfolio rows.
    """
    cached = _preference(user_id, STAGE0_CACHE_KEY)
    generated_at = _parse_dt(cached.get("generated_at"))
    fresh = bool(
        cached.get("members")
        and generated_at
        and (_utcnow() - generated_at) <= timedelta(hours=STAGE0_CACHE_HOURS)
    )
    if fresh and not force_refresh:
        out = dict(cached)
        out["cache_hit"] = True
        return out

    try:
        provider_calls["alpaca_assets"] += 1
        response = requests.get(
            "https://paper-api.alpaca.markets/v2/assets",
            headers=headers,
            params={"status": "active", "asset_class": "us_equity"},
            timeout=(5, 25),
        )
        if response.status_code != 200:
            raise RuntimeError(f"Asset universe HTTP {response.status_code}")
        excluded = Counter()
        members: list[dict[str, Any]] = []
        raw_count = 0
        for raw in response.json() or []:
            raw_count += 1
            valid, reason = _asset_is_operating_equity(raw or {})
            if not valid:
                excluded[reason] += 1
                continue
            members.append(_compact_asset(raw or {}))
        members.sort(key=lambda row: row["ticker"])
        previous_member_count = int(cached.get("member_count") or 0)
        payload = {
            "contract": "BROAD_US_OPERATING_EQUITY_V1",
            "generated_at": _iso(),
            "source": "Alpaca active US-equity asset catalog",
            "raw_count": raw_count,
            "member_count": len(members),
            "members": members,
            "excluded_count": sum(excluded.values()),
            "excluded_breakdown": dict(excluded),
            "cache_hours": STAGE0_CACHE_HOURS,
            "cache_hit": False,
            "previous_member_count": previous_member_count or None,
            "previous_generated_at": cached.get("generated_at"),
            "stale_cache": False,
        }
        _save_preference(user_id, STAGE0_CACHE_KEY, payload)
        return payload
    except Exception as exc:
        errors.append(f"Stage 0 universe refresh: {type(exc).__name__}: {exc}")
        if cached.get("members"):
            out = dict(cached)
            out["cache_hit"] = True
            out["stale_cache"] = True
            return out
        return {
            "contract": "BROAD_US_OPERATING_EQUITY_V1",
            "generated_at": None,
            "source": "Unavailable",
            "raw_count": 0,
            "member_count": 0,
            "members": [],
            "excluded_count": 0,
            "excluded_breakdown": {},
            "cache_hours": STAGE0_CACHE_HOURS,
            "cache_hit": False,
            "previous_member_count": int(cached.get("member_count") or 0) or None,
            "previous_generated_at": cached.get("generated_at"),
            "stale_cache": False,
        }


def _activity_pool(headers: dict[str, str], errors: list[str], provider_calls: Counter) -> dict[str, dict[str, Any]]:
    activity: dict[str, dict[str, Any]] = {}

    def touch(symbol: Any) -> dict[str, Any]:
        ticker = str(symbol or "").upper().strip()
        if not ticker:
            return {}
        return activity.setdefault(ticker, {"ticker": ticker, "activity_rank": None, "move_pct": None, "activity_sources": []})

    try:
        provider_calls["alpaca_most_active"] += 1
        response = requests.get(
            "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives",
            headers=headers,
            params={"by": "volume", "top": 100},
            timeout=(5, 12),
        )
        if response.status_code == 200:
            for idx, row in enumerate((response.json() or {}).get("most_actives") or [], start=1):
                item = touch(row.get("symbol"))
                if item:
                    item["activity_rank"] = idx
                    item["activity_sources"].append("MOST_ACTIVE")
        else:
            errors.append(f"Stage 1 Most Active HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Stage 1 Most Active: {type(exc).__name__}")

    try:
        provider_calls["alpaca_movers"] += 1
        response = requests.get(
            "https://data.alpaca.markets/v1beta1/screener/stocks/movers",
            headers=headers,
            params={"top": 50},
            timeout=(5, 12),
        )
        if response.status_code == 200:
            payload = response.json() or {}
            for side_key, sign, label in (("gainers", 1, "GAINER"), ("losers", -1, "LOSER")):
                for row in payload.get(side_key) or []:
                    item = touch(row.get("symbol"))
                    if not item:
                        continue
                    move = _num(row.get("percent_change") or row.get("change_pct") or row.get("percentChange"))
                    if move is not None and sign < 0 and move > 0:
                        move = -move
                    item["move_pct"] = move
                    item["activity_sources"].append(label)
        else:
            errors.append(f"Stage 1 Movers HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Stage 1 Movers: {type(exc).__name__}")
    return activity


def _snapshot_map(
    symbols: list[str],
    headers: dict[str, str],
    errors: list[str],
    provider_calls: Counter,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(symbols), SNAPSHOT_CHUNK_SIZE):
        chunk = symbols[start:start + SNAPSHOT_CHUNK_SIZE]
        if not chunk:
            continue
        try:
            provider_calls["alpaca_snapshot_batches"] += 1
            response = requests.get(
                "https://data.alpaca.markets/v2/stocks/snapshots",
                headers=headers,
                params={"symbols": ",".join(chunk), "feed": "iex"},
                timeout=(5, 18),
            )
            if response.status_code != 200:
                errors.append(f"Stage 1 snapshot batch HTTP {response.status_code}.")
                continue
            for symbol, node in (response.json() or {}).items():
                node = node or {}
                trade = node.get("latestTrade") or {}
                day = node.get("dailyBar") or {}
                prior = node.get("prevDailyBar") or {}
                price = _num(trade.get("p")) or _num(day.get("c"))
                current_volume = _num(day.get("v"))
                prior_close = _num(prior.get("c"))
                prior_volume = _num(prior.get("v"))
                move = ((price / prior_close - 1.0) * 100.0) if price is not None and prior_close not in (None, 0) else None
                # Use the completed prior bar for the cheap liquidity gate when
                # available so an early-session scan does not become activity-biased.
                liquidity_volume = prior_volume if prior_volume is not None else current_volume
                liquidity_price = prior_close if prior_close is not None else price
                out[str(symbol).upper()] = {
                    "price": price,
                    "daily_volume": liquidity_volume,
                    "current_session_volume": current_volume,
                    "dollar_volume": (
                        liquidity_price * liquidity_volume
                        if liquidity_price is not None and liquidity_volume is not None
                        else None
                    ),
                    "move_pct": move,
                    "as_of": trade.get("t") or day.get("t"),
                    "liquidity_basis": "PREVIOUS_COMPLETED_DAILY_BAR" if prior_volume is not None else "CURRENT_DAILY_BAR",
                }
        except Exception as exc:
            errors.append(f"Stage 1 snapshot batch: {type(exc).__name__}")
    return out


def _stage1_coverage_progress(
    user_id: int,
    universe: dict[str, Any],
    reviewed_symbols: set[str],
) -> dict[str, Any]:
    """Track how much of the broad universe was actually touched recently."""
    members = {
        str(row.get("ticker") or "").upper()
        for row in list(universe.get("members") or [])
        if row.get("ticker")
    }
    state = _preference(user_id, STAGE1_SEEN_KEY)
    seen = dict(state.get("seen") or {}) if isinstance(state.get("seen") or {}, dict) else {}
    now = _utcnow()
    now_iso = _iso(now)

    # Keep the persisted map bounded to the current eligible universe and only
    # stamp names for which Stage 1 actually received market data.
    seen = {ticker: stamp for ticker, stamp in seen.items() if ticker in members}
    for ticker in reviewed_symbols:
        if ticker in members:
            seen[ticker] = now_iso
    _save_preference(user_id, STAGE1_SEEN_KEY, {
        "seen": seen,
        "updated_at": now_iso,
        "universe_generated_at": universe.get("generated_at"),
    })

    def count_recent(days: int) -> int:
        cutoff = now - timedelta(days=days)
        total = 0
        for stamp in seen.values():
            parsed = _parse_dt(stamp)
            if parsed and parsed >= cutoff:
                total += 1
        return total

    universe_size = len(members)
    seen_7d = count_recent(7)
    seen_30d = count_recent(30)
    return {
        "universe_size": universe_size,
        "seen_7d": seen_7d,
        "seen_30d": seen_30d,
        "pct_7d": round((seen_7d / universe_size) * 100.0, 1) if universe_size else 0.0,
        "pct_30d": round((seen_30d / universe_size) * 100.0, 1) if universe_size else 0.0,
        "estimated_full_rotation_runs": ((universe_size + STAGE1_BATCH_SIZE - 1) // STAGE1_BATCH_SIZE) if universe_size else 0,
        "tracked_at": now_iso,
    }


def stage1_screen(
    user_id: int,
    headers: dict[str, str],
    universe: dict[str, Any],
    errors: list[str],
    provider_calls: Counter,
    *,
    known_tickers: set[str] | None = None,
    min_price: float = 5.0,
    min_daily_volume: float = 200_000.0,
    min_dollar_volume: float = 15_000_000.0,
) -> dict[str, Any]:
    """Cheap, rotating screen over the cached broad universe.

    Stage 1 never calls SEC or Companyfacts. It scans one bounded slice per run,
    adds current activity as a secondary lane, and checkpoints the next slice.
    """
    members = list(universe.get("members") or [])
    if not members:
        return {
            "rows": [], "scanned_count": 0, "qualified_count": 0,
            "cursor_start": 0, "cursor_end": 0, "broad_rotation_count": 0,
            "activity_count": 0, "excluded_breakdown": {},
            "snapshot_requested_count": 0, "snapshot_received_count": 0,
            "coverage_progress": {"universe_size": 0, "seen_7d": 0, "seen_30d": 0, "pct_7d": 0.0, "pct_30d": 0.0, "estimated_full_rotation_runs": 0},
        }

    by_symbol = {str(row.get("ticker") or "").upper(): dict(row) for row in members if row.get("ticker")}
    n_members = len(members)
    cursor_state = _preference(user_id, STAGE0_CURSOR_KEY)
    cursor_start = int(cursor_state.get("cursor") or 0) % max(1, n_members)
    rotation: list[dict[str, Any]] = []
    for offset in range(min(STAGE1_BATCH_SIZE, n_members)):
        rotation.append(members[(cursor_start + offset) % n_members])
    cursor_end = (cursor_start + len(rotation)) % max(1, n_members)

    activity = _activity_pool(headers, errors, provider_calls)
    selected: dict[str, dict[str, Any]] = {}
    for row in rotation:
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            selected[ticker] = {"broad_rotation": True}
    activity_order = sorted(
        activity,
        key=lambda ticker: (
            int((activity.get(ticker) or {}).get("activity_rank") or 9999),
            -abs(_num((activity.get(ticker) or {}).get("move_pct")) or 0.0),
            ticker,
        ),
    )
    for ticker in activity_order[:STAGE1_ACTIVITY_LIMIT]:
        if ticker in by_symbol:
            selected.setdefault(ticker, {})["activity"] = True
    for ticker in sorted({str(x or "").upper() for x in (known_tickers or set())})[:STAGE1_COVERAGE_LIMIT]:
        if ticker in by_symbol:
            selected.setdefault(ticker, {})["coverage"] = True

    symbols = sorted(selected)
    snapshots = _snapshot_map(symbols, headers, errors, provider_calls)
    excluded = Counter()
    rows: list[dict[str, Any]] = []
    for ticker in symbols:
        asset = by_symbol.get(ticker) or {}
        snap = snapshots.get(ticker) or {}
        meta = selected.get(ticker) or {}
        activity_meta = activity.get(ticker) or {}
        price = _num(snap.get("price"))
        volume = _num(snap.get("daily_volume"))
        dollar_volume = _num(snap.get("dollar_volume"))
        is_known = bool(meta.get("coverage"))
        if price is None:
            excluded["PRICE UNKNOWN"] += 1
            continue
        if price < min_price:
            excluded["LOW PRICE"] += 1
            continue
        if not is_known and (volume is None or volume < min_daily_volume):
            excluded["LOW / UNKNOWN VOLUME"] += 1
            continue
        if not is_known and (dollar_volume is None or dollar_volume < min_dollar_volume):
            excluded["LOW / UNKNOWN LIQUIDITY"] += 1
            continue

        lanes = []
        if meta.get("broad_rotation"):
            lanes.append("BROAD_ROTATION")
        if meta.get("activity"):
            lanes.append("MARKET_ACTIVITY")
        if is_known:
            lanes.append("COVERAGE_CONTEXT")
        move = _num(activity_meta.get("move_pct"))
        if move is None:
            move = _num(snap.get("move_pct"))
        reasons = []
        if "BROAD_ROTATION" in lanes and "MARKET_ACTIVITY" not in lanes:
            reasons.append("Quiet liquid name from the broad-universe rotation.")
        if activity_meta.get("activity_rank"):
            reasons.append(f"Most Active rank {int(activity_meta['activity_rank'])}.")
        if move is not None and "MARKET_ACTIVITY" in lanes:
            reasons.append(f"Current daily move {move:+.1f}%.")
        if is_known:
            reasons.append("Stored Coverage evidence is available for cheap triage.")

        rows.append({
            "ticker": ticker,
            "name": asset.get("name") or ticker,
            "exchange": asset.get("exchange") or "",
            "shortable": bool(asset.get("shortable")),
            "easy_to_borrow": bool(asset.get("easy_to_borrow")),
            "price": price,
            "daily_volume": volume,
            "dollar_volume": dollar_volume,
            "move_pct": move,
            "snapshot_as_of": snap.get("as_of"),
            "liquidity_basis": snap.get("liquidity_basis"),
            "stage1_lanes": lanes,
            "stage1_reasons": reasons,
            "activity_rank": activity_meta.get("activity_rank"),
        })

    # Do not advance the broad-universe checkpoint when market snapshots failed
    # completely; a later retry must see the same slice rather than silently skip it.
    if snapshots:
        _save_preference(user_id, STAGE0_CURSOR_KEY, {
            "cursor": cursor_end,
            "updated_at": _iso(),
            "universe_generated_at": universe.get("generated_at"),
            "universe_size": n_members,
        })
    else:
        cursor_end = cursor_start

    reviewed_rotation = {
        str(row.get("ticker") or "").upper()
        for row in rotation
        if str(row.get("ticker") or "").upper() in snapshots
    }
    # A failed snapshot run must not erase previously accumulated breadth history.
    # Passing an empty reviewed set preserves history while still leaving the
    # broad-universe cursor unchanged.
    coverage_progress = _stage1_coverage_progress(user_id, universe, reviewed_rotation if snapshots else set())

    return {
        "rows": rows,
        "scanned_count": len(symbols),
        "qualified_count": len(rows),
        "cursor_start": cursor_start,
        "cursor_end": cursor_end,
        "broad_rotation_count": sum(1 for row in rows if "BROAD_ROTATION" in row.get("stage1_lanes", [])),
        "quiet_broad_count": sum(1 for row in rows if "BROAD_ROTATION" in row.get("stage1_lanes", []) and "MARKET_ACTIVITY" not in row.get("stage1_lanes", [])),
        "activity_count": sum(1 for row in rows if "MARKET_ACTIVITY" in row.get("stage1_lanes", [])),
        "excluded_breakdown": dict(excluded),
        "snapshot_requested_count": len(symbols),
        "snapshot_received_count": len(snapshots),
        "coverage_progress": coverage_progress,
        "batch_size": STAGE1_BATCH_SIZE,
        "activity_limit": STAGE1_ACTIVITY_LIMIT,
        "coverage_limit": STAGE1_COVERAGE_LIMIT,
        "snapshot_chunk_size": SNAPSHOT_CHUNK_SIZE,
    }


__all__ = [
    "MAJOR_EXCHANGES", "STAGE0_CACHE_HOURS", "STAGE1_BATCH_SIZE",
    "STAGE1_ACTIVITY_LIMIT", "STAGE1_COVERAGE_LIMIT", "SNAPSHOT_CHUNK_SIZE", "STAGE1_SEEN_KEY",
    "stage0_universe", "stage1_screen", "_asset_is_operating_equity",
]
