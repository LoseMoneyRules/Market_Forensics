from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from math import isfinite
from typing import Any

import requests

from .data_providers import get_secret

ALPACA_DATA = "https://data.alpaca.markets"
TRADE_LIMIT = 10_000
FLOW_SESSION_COUNT = 5
FLOW_MAX_PAGES_PER_SESSION = 8
LARGE_FLOOR = 100_000.0
VERY_LARGE_FLOOR = 250_000.0
WHALE_FLOOR = 500_000.0
FLOW_METHOD_VERSION = "0.3.4-reconciled-sample-v1"
MARKET_TZ = ZoneInfo("America/New_York")
NON_DIRECTIONAL_CONDITIONS = {"B", "C", "G", "H", "I", "M", "N", "P", "Q", "R", "T", "U", "V", "W", "Z", "4", "7", "9"}


def _headers(user_id: int) -> dict[str, str] | None:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _n(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _quantile(values: list[float], q: float) -> float | None:
    rows = sorted(float(x) for x in values if _n(x) is not None and x >= 0)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    pos = (len(rows) - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(len(rows) - 1, lo + 1)
    weight = pos - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def _session_days(count: int = FLOW_SESSION_COUNT) -> list[date]:
    out: list[date] = []
    cursor = datetime.now(MARKET_TZ).date()
    while len(out) < max(1, count):
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(out))


def _trade_window(day: date) -> tuple[str, str] | None:
    """Regular US equity session only.

    Extended-hours and overnight prints are not comparable with the regular
    daily-volume bar and can badly distort a tick-rule flow proxy.
    """
    now_market = datetime.now(MARKET_TZ)
    start_market = datetime.combine(day, time(9, 30), tzinfo=MARKET_TZ)
    close_market = datetime.combine(day, time(16, 0), tzinfo=MARKET_TZ)
    if day == now_market.date():
        end_market = min(close_market, now_market - timedelta(minutes=20))
        if end_market <= start_market:
            return None
    else:
        end_market = close_market
    start = start_market.astimezone(timezone.utc)
    end = end_market.astimezone(timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _fetch_pages(symbol: str, headers: dict[str, str], day: date, *, feed: str, sort: str, max_pages: int) -> tuple[list[dict[str, Any]], bool, list[str]]:
    window = _trade_window(day)
    if not window:
        return [], True, []
    start, end = window
    token = None
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    complete = False
    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "feed": feed,
            "limit": TRADE_LIMIT,
            "sort": sort,
        }
        if token:
            params["page_token"] = token
        response = requests.get(
            f"{ALPACA_DATA}/v2/stocks/{symbol}/trades",
            headers=headers,
            params=params,
            timeout=(5, 30),
        )
        if response.status_code in {403, 422}:
            raise PermissionError(f"{feed} feed unavailable ({response.status_code})")
        if response.status_code == 429:
            errors.append("Alpaca trade-flow rate limit reached.")
            break
        if response.status_code != 200:
            errors.append(f"Alpaca trades HTTP {response.status_code}.")
            break
        payload = response.json() or {}
        page = list(payload.get("trades") or [])
        rows.extend(row for row in page if isinstance(row, dict))
        token = payload.get("next_page_token")
        if not token or not page:
            complete = True
            break
    return rows, complete, errors


def _fetch_reference_bar(symbol: str, headers: dict[str, str], day: date, feed: str) -> dict[str, Any]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    try:
        response = requests.get(
            f"{ALPACA_DATA}/v2/stocks/{symbol}/bars",
            headers=headers,
            params={
                "timeframe": "1Day",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "feed": feed,
                "adjustment": "raw",
                "limit": 10,
            },
            timeout=(5, 20),
        )
        if response.status_code != 200:
            return {"available": False, "error": f"Alpaca daily bar HTTP {response.status_code}"}
        bars = list((response.json() or {}).get("bars") or [])
        row = next((item for item in bars if str(item.get("t") or "")[:10] == day.isoformat()), bars[0] if bars else None)
        if not row:
            return {"available": False, "error": "Daily reference bar unavailable"}
        volume = _n(row.get("v"))
        close = _n(row.get("c"))
        vwap = _n(row.get("vw"))
        return {
            "available": bool(volume and volume > 0),
            "volume": volume,
            "close": close,
            "vwap": vwap,
            "reference_price": vwap or close,
            "feed": feed,
        }
    except Exception as exc:
        return {"available": False, "error": f"Daily reference bar: {type(exc).__name__}"}


def _fetch_trade_sample(symbol: str, headers: dict[str, str], day: date) -> dict[str, Any]:
    errors: list[str] = []
    feeds = ("sip", "iex")
    for feed in feeds:
        try:
            asc_pages = max(1, FLOW_MAX_PAGES_PER_SESSION // 2)
            asc, asc_complete, first_errors = _fetch_pages(symbol, headers, day, feed=feed, sort="asc", max_pages=asc_pages)
            errors.extend(first_errors)
            rows = list(asc)
            desc_complete = False
            if not asc_complete:
                desc_pages = max(1, FLOW_MAX_PAGES_PER_SESSION - asc_pages)
                desc, desc_complete, second_errors = _fetch_pages(symbol, headers, day, feed=feed, sort="desc", max_pages=desc_pages)
                errors.extend(second_errors)
                rows.extend(desc)
            complete = bool(asc_complete or desc_complete)
            dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
            for row in rows:
                key = (
                    row.get("i") or "",
                    row.get("t") or "",
                    row.get("p"),
                    row.get("s"),
                    row.get("x") or "",
                )
                dedup[key] = row
            ordered = sorted(dedup.values(), key=lambda row: str(row.get("t") or ""))
            reference = _fetch_reference_bar(symbol, headers, day, feed)
            if reference.get("error"):
                errors.append(str(reference["error"]))
            return {
                "rows": ordered,
                "feed": feed,
                "feed_scope": "CONSOLIDATED_SIP" if feed == "sip" else "IEX_PARTIAL_MARKET",
                "complete": complete,
                "sampled": not complete,
                "reference_bar": reference,
                "errors": errors,
            }
        except PermissionError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:
            errors.append(f"Alpaca trades {feed}: {type(exc).__name__}")
            continue
    return {
        "rows": [], "feed": "", "feed_scope": "UNAVAILABLE",
        "complete": False, "sampled": False, "reference_bar": {},
        "errors": errors,
    }


def _direction_eligible(row: dict[str, Any]) -> bool:
    conditions = {str(value or "").strip().upper() for value in (row.get("c") or [])}
    return not bool(conditions & NON_DIRECTIONAL_CONDITIONS)


def _aggregate_trade_flow(day: date, sample: dict[str, Any]) -> dict[str, Any]:
    trades = list(sample.get("rows") or [])
    all_prepared: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    excluded_condition_rows = 0

    for row in trades:
        price, size = _n(row.get("p")), _n(row.get("s"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        item = {
            "price": price, "size": size, "notional": price * size,
            "t": row.get("t"), "x": row.get("x"), "c": row.get("c") or [],
        }
        all_prepared.append(item)
        if _direction_eligible(row):
            eligible.append(item)
        else:
            excluded_condition_rows += 1

    notionals = [row["notional"] for row in eligible]
    p75 = _quantile(notionals, .75) or 0.0
    p90 = _quantile(notionals, .90) or 0.0
    p99 = _quantile(notionals, .99) or 0.0
    large_threshold = max(LARGE_FLOOR, p75)
    very_large_threshold = max(VERY_LARGE_FLOOR, p90)
    whale_threshold = max(WHALE_FLOOR, p99)

    buckets = {
        "large_buy": 0.0, "large_sell": 0.0, "large_neutral": 0.0,
        "very_large_buy": 0.0, "very_large_sell": 0.0, "very_large_neutral": 0.0,
        "whale_buy": 0.0, "whale_sell": 0.0, "whale_neutral": 0.0,
    }
    directional_notional = 0.0
    total_notional = sum(notionals)
    sample_total_notional = sum(row["notional"] for row in all_prepared)
    sample_share_volume = sum(row["size"] for row in all_prepared)
    eligible_share_volume = sum(row["size"] for row in eligible)
    large_notional = very_large_notional = whale_notional = 0.0
    prior_price = None
    last_direction = 0

    for row in eligible:
        price, notional = row["price"], row["notional"]
        if prior_price is None:
            direction = 0
        elif price > prior_price:
            direction = 1
        elif price < prior_price:
            direction = -1
        else:
            direction = last_direction
        prior_price = price
        if direction:
            last_direction = direction
            directional_notional += notional

        suffix = "buy" if direction > 0 else "sell" if direction < 0 else "neutral"
        if notional >= large_threshold:
            buckets[f"large_{suffix}"] += notional
            large_notional += notional
        if notional >= very_large_threshold:
            buckets[f"very_large_{suffix}"] += notional
            very_large_notional += notional
        if notional >= whale_threshold:
            buckets[f"whale_{suffix}"] += notional
            whale_notional += notional

    large_net = buckets["large_buy"] - buckets["large_sell"]
    very_large_net = buckets["very_large_buy"] - buckets["very_large_sell"]
    whale_net = buckets["whale_buy"] - buckets["whale_sell"]
    directional_share = (directional_notional / total_notional * 100.0) if total_notional else None

    reference = dict(sample.get("reference_bar") or {})
    reference_volume = _n(reference.get("volume"))
    reference_price = _n(reference.get("reference_price"))
    reference_notional = (
        reference_volume * reference_price
        if reference_volume not in (None, 0) and reference_price not in (None, 0)
        else None
    )
    sample_volume_pct = (
        sample_share_volume / reference_volume * 100.0
        if reference_volume not in (None, 0)
        else None
    )
    eligible_volume_pct = (
        eligible_share_volume / sample_share_volume * 100.0
        if sample_share_volume > 0
        else None
    )

    # Separate factual integrity from sampling coverage. A partial SIP sample is
    # still observable evidence and should remain visible in charts, but it only
    # enters Tape scoring when its share-volume coverage is material. This avoids
    # the 0.3.3 failure mode where nearly every liquid stock disappeared because
    # an eight-page cap could not fetch the entire trading day.
    integrity_reasons: list[str] = []
    if sample.get("feed") != "sip":
        integrity_reasons.append("CONSOLIDATED_SIP_REQUIRED")
    if reference_volume in (None, 0):
        integrity_reasons.append("REFERENCE_VOLUME_MISSING")
    else:
        if sample_share_volume > reference_volume * 1.05:
            integrity_reasons.append("SAMPLE_VOLUME_EXCEEDS_REFERENCE")
    if reference_notional not in (None, 0) and sample_total_notional > reference_notional * 1.15:
        integrity_reasons.append("SAMPLE_NOTIONAL_EXCEEDS_REFERENCE")
    if not eligible:
        integrity_reasons.append("NO_DIRECTION_ELIGIBLE_TRADES")
    elif eligible_volume_pct is not None and eligible_volume_pct < 50.0:
        integrity_reasons.append("DIRECTION_ELIGIBLE_VOLUME_TOO_LOW")

    sanity_status = "PASS" if not integrity_reasons else "FAIL"
    observation_usable = sanity_status == "PASS"

    decision_reasons: list[str] = []
    if observation_usable:
        if sample.get("complete"):
            if sample_volume_pct is None or sample_volume_pct < 70.0:
                decision_reasons.append("COMPLETE_SAMPLE_COVERS_TOO_LITTLE_VOLUME")
        elif sample_volume_pct is None or sample_volume_pct < 10.0:
            decision_reasons.append("SAMPLED_VOLUME_COVERAGE_TOO_LOW")
    else:
        decision_reasons.extend(integrity_reasons)

    decision_usable = observation_usable and not decision_reasons
    coverage_status = "COMPLETE" if sample.get("complete") else "SAMPLED"
    coverage_factor = min(1.0, max(0.0, (sample_volume_pct or 0.0) / 20.0))
    flow_confidence = (directional_share or 0.0) * coverage_factor if observation_usable else 0.0
    if not sample.get("complete"):
        flow_confidence = min(flow_confidence, 60.0)

    return {
        "date": day.isoformat(),
        "method_version": FLOW_METHOD_VERSION,
        "trade_rows": len(all_prepared),
        "direction_eligible_rows": len(eligible),
        "excluded_condition_rows": excluded_condition_rows,
        "sample_share_volume": sample_share_volume,
        "eligible_share_volume": eligible_share_volume,
        "eligible_volume_pct": eligible_volume_pct,
        "reference_volume": reference_volume,
        "sample_volume_pct": sample_volume_pct,
        "sample_total_notional": sample_total_notional,
        "reference_notional": reference_notional,
        "total_notional": total_notional,
        "large_threshold": large_threshold,
        "very_large_threshold": very_large_threshold,
        "whale_threshold": whale_threshold,
        **buckets,
        "net_large": large_net,
        "net_very_large": very_large_net,
        "net_whale": whale_net,
        "net_large_ratio": (large_net / total_notional * 100.0) if total_notional else None,
        "net_whale_ratio": (whale_net / total_notional * 100.0) if total_notional else None,
        "large_share_pct": (large_notional / total_notional * 100.0) if total_notional else None,
        "very_large_share_pct": (very_large_notional / total_notional * 100.0) if total_notional else None,
        "whale_share_pct": (whale_notional / total_notional * 100.0) if total_notional else None,
        "directional_share_pct": directional_share,
        "flow_confidence_pct": min(100.0, flow_confidence),
        "feed": sample.get("feed"),
        "feed_scope": sample.get("feed_scope"),
        "classification_method": "REGULAR_SESSION_FILTERED_TICK_RULE_PROXY",
        "source_quality": "T2",
        "source_status": "COMPLETE" if sample.get("complete") else "PARTIAL_SAMPLED" if sample.get("sampled") else "NO_DATA",
        "sanity_status": sanity_status,
        "sanity_reasons": sanity_reasons,
        "decision_usable": decision_usable,
        "errors": list(sample.get("errors") or []),
    }


def refresh_institutional_flow(ticker: str, user_id: int) -> dict[str, Any]:
    headers = _headers(user_id)
    symbol = str(ticker or "").strip().upper()
    if not headers or not symbol:
        return {"configured": False, "ticker": symbol, "rows": [], "errors": ["Alpaca credentials are not configured."]}

    rows = []
    errors: list[str] = []
    for day in _session_days():
        sample = _fetch_trade_sample(symbol, headers, day)
        errors.extend(sample.get("errors") or [])
        aggregate = _aggregate_trade_flow(day, sample)
        if aggregate.get("trade_rows"):
            rows.append(aggregate)

    return {
        "configured": True,
        "ticker": symbol,
        "rows": rows,
        "errors": list(dict.fromkeys(errors)),
        "method": "REGULAR_SESSION_VOLUME_SANITY_TICK_RULE_PROXY",
        "method_version": FLOW_METHOD_VERSION,
        "threshold_policy": {
            "large": "max(P75, $100k)",
            "very_large": "max(P90, $250k)",
            "whale": "max(P99, $500k)",
        },
        "interpretation": "Large/Whale values are dollar notionals from direction-eligible trade-size proxies, not share counts or buyer/seller identity. Only complete sanity-checked consolidated SIP sessions are decision-usable.",
    }


def refresh_positioning_bundle(ticker: str, user_id: int) -> dict[str, Any]:
    headers = _headers(user_id)
    symbol = str(ticker or "").strip().upper()
    if not headers or not symbol:
        return {"configured": False, "ticker": symbol, "errors": ["Alpaca credentials are not configured."]}

    errors: list[str] = []
    out: dict[str, Any] = {
        "configured": True,
        "ticker": symbol,
        "borrow": {},
        "options": {},
        "locate": {},
        "flow": {},
        "errors": errors,
    }

    try:
        asset = requests.get(
            f"https://paper-api.alpaca.markets/v2/assets/{symbol}",
            headers=headers,
            timeout=12,
        )
        if asset.status_code == 200:
            row = asset.json() or {}
            out["borrow"] = {
                "shortable": bool(row.get("shortable")),
                "borrow_status": row.get("borrow_status") or ("easy_to_borrow" if row.get("easy_to_borrow") else "unknown"),
                "easy_to_borrow": row.get("easy_to_borrow"),
                "marginable": row.get("marginable"),
                "attributes": row.get("attributes") or [],
            }
        else:
            errors.append(f"Asset metadata HTTP {asset.status_code}.")
    except Exception as exc:
        errors.append(f"Asset metadata: {type(exc).__name__}")

    start = date.today()
    end = start + timedelta(days=120)
    contracts: list[dict[str, Any]] = []
    token = None
    pages = 0
    try:
        while pages < 5:
            params = {
                "underlying_symbols": symbol,
                "status": "active",
                "expiration_date_gte": start.isoformat(),
                "expiration_date_lte": end.isoformat(),
                "limit": 1000,
            }
            if token:
                params["page_token"] = token
            response = requests.get(
                "https://paper-api.alpaca.markets/v2/options/contracts",
                headers=headers,
                params=params,
                timeout=15,
            )
            if response.status_code != 200:
                errors.append(f"Options contracts HTTP {response.status_code}.")
                break
            payload = response.json() or {}
            rows = list(payload.get("option_contracts") or [])
            contracts.extend(rows)
            token = payload.get("next_page_token") or payload.get("page_token")
            pages += 1
            if not token or not rows:
                break
    except Exception as exc:
        errors.append(f"Options contracts: {type(exc).__name__}")

    if contracts:
        call_oi = 0.0
        put_oi = 0.0
        call_contracts = put_contracts = 0
        expirations: set[str] = set()
        for row in contracts:
            oi = _n(row.get("open_interest")) or 0.0
            typ = str(row.get("type") or "").lower()
            if typ == "put":
                put_oi += oi
                put_contracts += 1
            elif typ == "call":
                call_oi += oi
                call_contracts += 1
            if row.get("expiration_date"):
                expirations.add(str(row["expiration_date"]))
        out["options"] = {
            "put_open_interest": put_oi,
            "call_open_interest": call_oi,
            "put_call_oi": (put_oi / call_oi) if call_oi > 0 else None,
            "contracts": len(contracts),
            "put_contracts": put_contracts,
            "call_contracts": call_contracts,
            "expirations": sorted(expirations),
            "horizon_days": 120,
        }

    borrow_status = str((out.get("borrow") or {}).get("borrow_status") or "").lower()
    if "hard" in borrow_status:
        for base in ("https://api.alpaca.markets", "https://paper-api.alpaca.markets"):
            try:
                response = requests.get(
                    f"{base}/v1/locates/quotes",
                    headers=headers,
                    params={"symbols": symbol},
                    timeout=10,
                )
                if response.status_code == 200:
                    payload = response.json() or {}
                    quotes = payload.get("quotes") or []
                    quote = next((q for q in quotes if str(q.get("symbol") or "").upper() == symbol), None)
                    if quote:
                        out["locate"] = {
                            "available_qty": quote.get("available_qty"),
                            "price": quote.get("price"),
                            "quoted_at": quote.get("quoted_at"),
                        }
                        break
            except Exception:
                continue

    try:
        out["flow"] = refresh_institutional_flow(symbol, user_id)
        errors.extend((out["flow"] or {}).get("errors") or [])
    except Exception as exc:
        errors.append(f"Institutional flow: {type(exc).__name__}")

    out["errors"] = list(dict.fromkeys(errors))
    return out


__all__ = ["refresh_positioning_bundle", "refresh_institutional_flow"]
