from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import re
from typing import Any

import requests

from .extensions import db
from .models import UserPreference

STAGE0_CACHE_KEY = "discovery_stage0_v2"
STAGE1_SEEN_KEY = "discovery_stage1_seen_v1"
STAGE0_CACHE_HOURS = 24
STAGE1_BATCH_SIZE = 0  # compatibility surface: Stage 1 now scans the full universe every run
STAGE1_ACTIVITY_LIMIT = 0
STAGE1_COVERAGE_LIMIT = 0
SNAPSHOT_CHUNK_SIZE = 100
SNAPSHOT_MAX_WORKERS = 4
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


def _snapshot_map(
    symbols: list[str],
    headers: dict[str, str],
    errors: list[str],
    provider_calls: Counter,
) -> dict[str, dict[str, Any]]:
    """Fetch current market state for the entire eligible universe.

    Requests are chunked and modestly parallelized. Selection happens only after
    every symbol has had the same opportunity to return a snapshot.
    """
    out: dict[str, dict[str, Any]] = {}
    chunks = [
        symbols[start:start + SNAPSHOT_CHUNK_SIZE]
        for start in range(0, len(symbols), SNAPSHOT_CHUNK_SIZE)
        if symbols[start:start + SNAPSHOT_CHUNK_SIZE]
    ]
    if not chunks:
        return out
    provider_calls["alpaca_snapshot_batches"] += len(chunks)

    def fetch_chunk(chunk: list[str]) -> tuple[list[str], int | None, dict[str, Any], str]:
        try:
            response = requests.get(
                "https://data.alpaca.markets/v2/stocks/snapshots",
                headers=headers,
                params={"symbols": ",".join(chunk), "feed": "iex"},
                timeout=(5, 18),
            )
            if response.status_code != 200:
                return chunk, response.status_code, {}, ""
            return chunk, response.status_code, response.json() or {}, ""
        except Exception as exc:
            return chunk, None, {}, type(exc).__name__

    with ThreadPoolExecutor(max_workers=SNAPSHOT_MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_chunk, chunk) for chunk in chunks]
        for future in as_completed(futures):
            chunk, status, payload, error = future.result()
            if error:
                errors.append(f"Stage 1 snapshot batch: {error}")
                continue
            if status != 200:
                errors.append(f"Stage 1 snapshot batch HTTP {status}.")
                continue
            for symbol, node in payload.items():
                node = node or {}
                trade = node.get("latestTrade") or {}
                day = node.get("dailyBar") or {}
                prior = node.get("prevDailyBar") or {}
                price = _num(trade.get("p")) or _num(day.get("c"))
                current_volume = _num(day.get("v"))
                prior_close = _num(prior.get("c"))
                prior_volume = _num(prior.get("v"))
                move = ((price / prior_close - 1.0) * 100.0) if price is not None and prior_close not in (None, 0) else None
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
        "estimated_full_rotation_runs": 1 if universe_size else 0,
        "scan_mode": "FULL_UNIVERSE",
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
    """Cheap full-market screen over every eligible Stage-0 equity.

    There is no rotating cursor. Every run requests market data for every Stage-0
    member, applies the same price/liquidity rules, and passes the resulting liquid
    universe to the market-wide fundamental pre-screen.
    """
    members = list(universe.get("members") or [])
    if not members:
        return {
            "rows": [], "scanned_count": 0, "qualified_count": 0,
            "cursor_start": 0, "cursor_end": 0, "broad_rotation_count": 0,
            "quiet_broad_count": 0, "activity_count": 0, "full_universe_count": 0,
            "excluded_breakdown": {},
            "snapshot_requested_count": 0, "snapshot_received_count": 0,
            "coverage_progress": {
                "universe_size": 0, "seen_7d": 0, "seen_30d": 0,
                "pct_7d": 0.0, "pct_30d": 0.0,
                "estimated_full_rotation_runs": 0, "scan_mode": "FULL_UNIVERSE",
            },
            "scan_mode": "FULL_UNIVERSE",
        }

    by_symbol = {
        str(row.get("ticker") or "").upper(): dict(row)
        for row in members if row.get("ticker")
    }
    known = {str(x or "").upper() for x in (known_tickers or set())}
    symbols = sorted(by_symbol)
    snapshots = _snapshot_map(symbols, headers, errors, provider_calls)

    excluded = Counter()
    rows: list[dict[str, Any]] = []
    for ticker in symbols:
        asset = by_symbol.get(ticker) or {}
        snap = snapshots.get(ticker) or {}
        price = _num(snap.get("price"))
        volume = _num(snap.get("daily_volume"))
        dollar_volume = _num(snap.get("dollar_volume"))
        is_known = ticker in known

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

        lanes = ["FULL_UNIVERSE"]
        reasons = ["Eligible operating equity passed the same full-market price/liquidity screen as every other Stage-0 name."]
        if is_known:
            lanes.append("COVERAGE_CONTEXT")
            reasons.append("Stored Coverage evidence is available for additional triage.")

        rows.append({
            "ticker": ticker,
            "name": asset.get("name") or ticker,
            "exchange": asset.get("exchange") or "",
            "shortable": bool(asset.get("shortable")),
            "easy_to_borrow": bool(asset.get("easy_to_borrow")),
            "price": price,
            "daily_volume": volume,
            "dollar_volume": dollar_volume,
            "move_pct": _num(snap.get("move_pct")),
            "snapshot_as_of": snap.get("as_of"),
            "liquidity_basis": snap.get("liquidity_basis"),
            "stage1_lanes": lanes,
            "stage1_reasons": reasons,
            "activity_rank": None,
        })

    reviewed_symbols = set(snapshots)
    coverage_progress = _stage1_coverage_progress(user_id, universe, reviewed_symbols)

    return {
        "rows": rows,
        "scanned_count": len(symbols),
        "qualified_count": len(rows),
        "cursor_start": 0,
        "cursor_end": 0,
        "broad_rotation_count": 0,
        "quiet_broad_count": 0,
        "activity_count": 0,
        "full_universe_count": len(rows),
        "excluded_breakdown": dict(excluded),
        "snapshot_requested_count": len(symbols),
        "snapshot_received_count": len(snapshots),
        "coverage_progress": coverage_progress,
        "batch_size": len(symbols),
        "activity_limit": 0,
        "coverage_limit": 0,
        "snapshot_chunk_size": SNAPSHOT_CHUNK_SIZE,
        "snapshot_max_workers": SNAPSHOT_MAX_WORKERS,
        "scan_mode": "FULL_UNIVERSE",
    }


__all__ = [
    "MAJOR_EXCHANGES", "STAGE0_CACHE_HOURS", "STAGE1_BATCH_SIZE",
    "STAGE1_ACTIVITY_LIMIT", "STAGE1_COVERAGE_LIMIT", "SNAPSHOT_CHUNK_SIZE", "SNAPSHOT_MAX_WORKERS", "STAGE1_SEEN_KEY",
    "stage0_universe", "stage1_screen", "_asset_is_operating_equity",
]
