from __future__ import annotations

from collections import Counter
from typing import Any

import requests

from .core_models import Coverage, Security, ValuationModel
from .data_providers import get_secret
from .discovery_engine import classify_coverage
from .discovery_forensics import enrich_forensic_candidates, forensic_side
from .extensions import db
from .research_cache import cache_is_stale, latest_cache_map
from .services import valuation_result

MIN_LONG_PRICE = 5.0
MIN_SHORT_PRICE = 10.0
MIN_DOLLAR_VOLUME = 50_000_000.0
MIN_DAILY_VOLUME = 500_000.0
MAX_PER_SIDE = 10
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
    """Batch-read already materialized Research context for screened symbols."""
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
    model_rows = ValuationModel.query.filter(
        ValuationModel.coverage_id.in_(coverage_ids),
        ValuationModel.is_active.is_(True),
    ).order_by(ValuationModel.id.desc()).all()
    models: dict[int, ValuationModel] = {}
    for model in model_rows:
        models.setdefault(model.coverage_id, model)

    out: dict[str, dict[str, Any]] = {}
    for coverage, security in rows:
        cache = dict(caches.get(coverage.id) or {})
        valuation = dict(cache.get("valuation") or {})
        if cache and cache_is_stale(cache, coverage, models.get(coverage.id)):
            valuation["base_quality"] = "DATA_WARNING"
            valuation["quality"] = "DATA_WARNING"
            valuation["decision_grade"] = False
        elif not valuation.get("base_quality"):
            live_valuation = valuation_result(coverage)
            for key in ("quality", "base_quality", "decision_grade"):
                valuation[key] = live_valuation.get(key)

        intelligence = dict(cache.get("intelligence") or {})
        intelligence["valuation_base_quality"] = valuation.get("base_quality") or "DATA_WARNING"
        intelligence["valuation_decision_grade"] = bool(valuation.get("decision_grade"))
        discovery_labels = classify_coverage(intelligence, dict(cache.get("readiness") or {}))
        out[security.ticker.upper()] = {
            "known": True,
            "coverage_id": coverage.id,
            "company_id": security.company_id,
            "security_id": security.id,
            "base_gap_pct": (cache.get("intelligence") or {}).get("base_gap_pct"),
            "readiness": dict(cache.get("readiness") or {}),
            "decision_lenses": dict(cache.get("decision_lenses") or {}),
            "discovery_labels": discovery_labels,
            "valuation": {
                "current_price": valuation.get("current_price"),
                "base": valuation.get("base"),
                "quality": valuation.get("quality"),
                "base_quality": valuation.get("base_quality"),
                "decision_grade": valuation.get("decision_grade"),
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
            prior = node.get("prevDailyBar") or {}
            price = _n(trade.get("p")) or _n(day.get("c"))
            volume = _n(day.get("v"))
            prior_close = _n(prior.get("c"))
            move = ((price / prior_close - 1.0) * 100.0) if price is not None and prior_close not in (None, 0) else None
            out[str(symbol).upper()] = {
                "price": price,
                "daily_volume": volume,
                "dollar_volume": (price * volume) if price is not None and volume is not None else None,
                "move_pct": move,
            }
        return out
    except Exception as exc:
        errors.append(f"Snapshot qualification: {type(exc).__name__}")
        return {}


def _asset_map(symbols: set[str], headers: dict[str, str], errors: list[str]) -> dict[str, dict[str, Any]]:
    """Validate the screened pool against active Alpaca US-equity metadata."""
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


def _screen_pool(user_id: int, headers: dict[str, str], errors: list[str]) -> dict[str, dict[str, Any]]:
    """Create a broad but cheap investigation pool. This never decides Long/Short."""
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
            "screen_score": 0.0,
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
                item["screen_score"] += max(0.0, 30.0 - (idx - 1) * 0.30)
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
                    item["screen_score"] += min(35.0, abs(change or 0.0) * 1.8) + max(0.0, 6.0 - idx * 0.10)
        else:
            errors.append(f"Market-movers screener HTTP {response.status_code}.")
    except Exception as exc:
        errors.append(f"Market-movers screener: {type(exc).__name__}")

    if not by_symbol:
        return {}

    symbols = set(by_symbol)
    snapshots = _snapshot_map(symbols, headers, errors)
    assets = _asset_map(symbols, headers, errors)
    local_context = _coverage_context_map(user_id, symbols)

    for symbol, item in by_symbol.items():
        snap = snapshots.get(symbol) or {}
        item["price"] = _n(snap.get("price")) or _n(item.get("price"))
        item["move_pct"] = _n(item.get("move_pct"))
        if item["move_pct"] is None:
            item["move_pct"] = _n(snap.get("move_pct"))
        item["daily_volume"] = _n(snap.get("daily_volume"))
        item["dollar_volume"] = _n(snap.get("dollar_volume"))
        if item["dollar_volume"] is None and item["price"] is not None and item.get("activity_volume") is not None:
            item["dollar_volume"] = item["price"] * item["activity_volume"]
        item["asset"] = dict(assets.get(symbol) or {})
        item["known_context"] = dict(local_context.get(symbol) or {})
    return by_symbol


def market_scan(user_id: int) -> dict[str, Any]:
    """Market Forensics Discovery.

    Stage 1 is only a cheap investigation funnel. Stage 2 requires a calculable
    Base fair value and confirming operating evidence. A raw price move can never
    appear in the final Long/Short list by itself.
    """
    headers = _headers(user_id)
    if not headers:
        return {
            "configured": False, "candidates": [], "long_candidates": [], "short_candidates": [],
            "errors": ["Alpaca credentials are not configured."],
        }

    errors: list[str] = []
    screened = _screen_pool(user_id, headers, errors)
    excluded = Counter()
    qualified_pool: list[dict[str, Any]] = []
    local_context: dict[str, dict[str, Any]] = {}

    for symbol, item in screened.items():
        asset = dict(item.get("asset") or {})
        valid_asset, reason = _asset_is_operating_equity(asset)
        if not valid_asset:
            excluded[reason] += 1
            continue

        context = dict(item.get("known_context") or {})
        if context:
            local_context[symbol] = context

        price = _n(item.get("price")) or _n((context.get("valuation") or {}).get("current_price"))
        volume = _n(item.get("daily_volume"))
        dollar_volume = _n(item.get("dollar_volume"))
        if price is None:
            excluded["PRICE UNKNOWN"] += 1
            continue
        if price < MIN_LONG_PRICE:
            excluded["LOW PRICE"] += 1
            continue
        if volume is not None and volume < MIN_DAILY_VOLUME and not context:
            excluded["LOW VOLUME"] += 1
            continue
        if (dollar_volume is None or dollar_volume < MIN_DOLLAR_VOLUME) and not context:
            excluded["LOW / UNKNOWN LIQUIDITY"] += 1
            continue

        item["price"] = price
        item["name"] = asset.get("name") or symbol
        item["exchange"] = asset.get("exchange") or ""
        item["in_coverage"] = bool(context)
        qualified_pool.append(item)

    forensic = enrich_forensic_candidates(user_id, qualified_pool, local_context, errors)
    candidates: list[dict[str, Any]] = []

    for item in qualified_pool:
        symbol = str(item.get("ticker") or "").upper()
        evidence = dict(forensic.get(symbol) or {})
        if not evidence:
            excluded["NO FORENSIC FAIR VALUE / DATA"] += 1
            continue

        decision = forensic_side(evidence)
        if decision is None:
            excluded["FAIR VALUE / OPERATIONS NOT ALIGNED"] += 1
            continue
        side, priority, forensic_score = decision

        asset = dict(item.get("asset") or {})
        price = _n(item.get("price"))
        gap = _n(evidence.get("gap_pct"))
        fair = _n(evidence.get("base"))
        move = _n(item.get("move_pct"))

        if side == "SHORT" and (price is None or price < MIN_SHORT_PRICE or not asset.get("shortable")):
            excluded["SHORT NOT ACTIONABLE"] += 1
            continue

        operating_signals = [
            row for row in list(evidence.get("signals") or [])
            if str(row.get("side") or "").upper() == side
        ][:4]
        if not operating_signals:
            excluded["NO CONFIRMING OPERATING SIGNAL"] += 1
            continue

        context = dict(item.get("known_context") or {})
        reasons = [
            f"Base fair value ${fair:,.2f}" if fair is not None else "Base unavailable",
            f"Fair-value gap {gap:+.1f}%" if gap is not None else "Gap unavailable",
        ]
        reasons.extend(str(row.get("detail") or "") for row in operating_signals[:2])
        if move is not None:
            reasons.append(f"Price move {move:+.1f}%")

        priority_rank = 1 if priority == "P1" else 2
        scan_score = abs(gap or 0.0) + forensic_score + min(15.0, abs(move or 0.0) * 0.5)
        item.update({
            "research_side": side,
            "priority": priority,
            "priority_rank": priority_rank,
            "priority_reason": "FAIR VALUE + OPERATING CONFIRMATION",
            "scan_score": round(scan_score, 2),
            "base_gap_pct": gap,
            "fair_value": fair,
            "fair_value_quality": evidence.get("quality"),
            "forensic_source": evidence.get("source"),
            "forensic_score": forensic_score,
            "forensic_signals": operating_signals,
            "operating_snapshot": dict(evidence.get("snapshot") or {}),
            "target_status": "ROOM TO BASE" if side == "LONG" else "ABOVE BASE",
            "radar_label": "UNDERVALUED + OPERATING INFLECTION" if side == "LONG" else "OVERVALUED + OPERATING DETERIORATION",
            "why_found": reasons,
            "known_context": context,
            "in_coverage": bool(context),
            "lenses": [str(row.get("label") or "") for row in operating_signals],
        })
        candidates.append(item)

    candidates.sort(key=lambda row: (row["priority_rank"], -row["scan_score"], row["ticker"]))
    long_candidates = [row for row in candidates if row["research_side"] == "LONG"][:MAX_PER_SIDE]
    short_candidates = [row for row in candidates if row["research_side"] == "SHORT"][:MAX_PER_SIDE]
    final = long_candidates + short_candidates

    return {
        "configured": True,
        "candidates": final,
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "errors": errors,
        "universe_source": "Alpaca investigation funnel → SEC operating forensics → Market Forensics Base fair value",
        "candidate_count": len(final),
        "screened_count": len(screened),
        "qualified_pool_count": len(qualified_pool),
        "forensic_enriched_count": len(forensic),
        "known_enriched": sum(1 for row in final if row.get("in_coverage")),
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
            "fair_value_edge_pct": 20.0,
            "major_exchanges": sorted(MAJOR_EXCHANGES),
            "short_requires_shortable": True,
            "final_requires_fair_value": True,
            "final_requires_operating_confirmation": True,
            "fill_quota": "none",
        },
        "contract_version": "FORENSIC_FAIR_VALUE_V1",
        "enrichment_mode": "FAIR_VALUE_FORENSIC_STAGE",
    }


__all__ = ["market_scan"]
