from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .data_providers import get_secret
from .extensions import db
from .models import UserPreference

CACHE_KEY = "discovery_full_market_fundamentals_v1"
CACHE_HOURS = 24
FRAME_TIMEOUT = (5, 30)
SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"

DURATION_SPECS: dict[str, list[tuple[str, str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
        ("us-gaap", "Revenues", "USD"),
        ("us-gaap", "SalesRevenueNet", "USD"),
    ],
    "operating_income": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "net_income": [
        ("us-gaap", "NetIncomeLoss", "USD"),
        ("us-gaap", "ProfitLoss", "USD"),
    ],
    "cfo": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD")],
}

INSTANT_SPECS: dict[str, list[tuple[str, str, str]]] = {
    "inventory": [("us-gaap", "InventoryNet", "USD")],
    "receivables": [
        ("us-gaap", "AccountsReceivableNetCurrent", "USD"),
        ("us-gaap", "AccountsNotesAndLoansReceivableNetCurrent", "USD"),
    ],
    "shares": [
        ("us-gaap", "CommonStockSharesOutstanding", "shares"),
        ("dei", "EntityCommonStockSharesOutstanding", "shares"),
    ],
}

ANNUAL_METRICS = ("revenue", "net_income", "cfo", "capex")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _num(value: Any) -> float | None:
    try:
        out = float(value) if value is not None else None
        return out if out is None or out == out else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _pct_change(current: Any, previous: Any) -> float | None:
    c, p = _num(current), _num(previous)
    if c is None or p in (None, 0):
        return None
    return (c / p - 1.0) * 100.0


def _ratio(a: Any, b: Any, scale: float = 1.0) -> float | None:
    x, y = _num(a), _num(b)
    if x is None or y in (None, 0):
        return None
    return x / y * scale


def _preference(user_id: int) -> dict[str, Any]:
    row = UserPreference.query.filter_by(user_id=user_id, key=CACHE_KEY).first()
    return dict(row.value or {}) if row and isinstance(row.value, dict) else {}


def _save_preference(user_id: int, value: dict[str, Any]) -> None:
    row = UserPreference.query.filter_by(user_id=user_id, key=CACHE_KEY).first()
    if row is None:
        row = UserPreference(user_id=user_id, key=CACHE_KEY, value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.commit()


def _sec_headers(user_id: int) -> dict[str, str] | None:
    user_agent = str(get_secret(user_id, "sec_user_agent") or "").strip()
    if not user_agent or "@" not in user_agent:
        return None
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


def _www_headers(user_id: int) -> dict[str, str] | None:
    headers = _sec_headers(user_id)
    if not headers:
        return None
    return {
        "User-Agent": headers["User-Agent"],
        "Accept-Encoding": "gzip, deflate",
    }


def _last_completed_quarter(today: date | None = None) -> tuple[int, int]:
    """Use a filing-lagged calendar quarter for a broad, comparable SEC frame."""
    current = today or _utcnow().date()
    cutoff = current - timedelta(days=75)
    q = ((cutoff.month - 1) // 3) + 1
    q_start = date(cutoff.year, ((q - 1) * 3) + 1, 1)
    prior_end = q_start - timedelta(days=1)
    return prior_end.year, ((prior_end.month - 1) // 3) + 1


def _frame_periods(today: date | None = None) -> dict[str, str]:
    current = today or _utcnow().date()
    year, quarter = _last_completed_quarter(current)
    prior_year = year - 1
    if quarter == 4:
        current_duration = f"CY{year}"
        prior_duration = f"CY{prior_year}"
    else:
        current_duration = f"CY{year}Q{quarter}"
        prior_duration = f"CY{prior_year}Q{quarter}"
    current_instant = f"CY{year}Q{quarter}I"
    prior_instant = f"CY{prior_year}Q{quarter}I"
    annual_year = current.year - 1 if current.month >= 5 else current.year - 2
    return {
        "current_duration": current_duration,
        "prior_duration": prior_duration,
        "current_instant": current_instant,
        "prior_instant": prior_instant,
        "annual": f"CY{annual_year}",
        "annual_year": str(annual_year),
    }


def _ticker_map(user_id: int, provider_calls: Counter) -> dict[str, str]:
    headers = _www_headers(user_id)
    if not headers:
        return {}
    provider_calls["sec_ticker_map"] += 1
    response = requests.get(
        f"{SEC_WWW}/files/company_tickers.json",
        headers=headers,
        timeout=FRAME_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"SEC ticker map HTTP {response.status_code}")
    out: dict[str, str] = {}
    for row in (response.json() or {}).values():
        ticker = str(row.get("ticker") or "").upper().strip()
        cik = str(row.get("cik_str") or "").zfill(10)
        if ticker and cik.strip("0"):
            out[ticker] = cik
    return out


def _fetch_frame_metric(
    specs: list[tuple[str, str, str]],
    period: str,
    user_id: int,
    provider_calls: Counter,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    headers = _sec_headers(user_id)
    if not headers:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for taxonomy, tag, unit in specs:
        provider_calls["sec_frame_calls"] += 1
        try:
            response = requests.get(
                f"{SEC_DATA}/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json",
                headers=headers,
                timeout=FRAME_TIMEOUT,
            )
        except Exception as exc:
            errors.append(f"SEC frame {tag} {period}: {type(exc).__name__}")
            continue
        if response.status_code in {400, 404}:
            continue
        if response.status_code != 200:
            errors.append(f"SEC frame {tag} {period}: HTTP {response.status_code}")
            continue
        for row in (response.json() or {}).get("data") or []:
            cik = str(row.get("cik") or "").zfill(10)
            value = _num(row.get("val"))
            if not cik.strip("0") or value is None or cik in out:
                continue
            out[cik] = {
                "value": value,
                "filed": row.get("filed"),
                "form": row.get("form"),
                "tag": tag,
                "taxonomy": taxonomy,
                "frame": row.get("frame") or period,
            }
    return out


def _build_baseline(
    user_id: int,
    errors: list[str],
    provider_calls: Counter,
) -> dict[str, Any]:
    if not _sec_headers(user_id):
        return {
            "configured": False,
            "generated_at": None,
            "by_ticker": {},
            "periods": _frame_periods(),
            "cache_hit": False,
            "stale_cache": False,
            "error": "SEC User-Agent is not configured.",
        }

    periods = _frame_periods()
    ticker_map = _ticker_map(user_id, provider_calls)

    current_duration = {
        metric: _fetch_frame_metric(specs, periods["current_duration"], user_id, provider_calls, errors)
        for metric, specs in DURATION_SPECS.items()
    }
    prior_duration = {
        metric: _fetch_frame_metric(specs, periods["prior_duration"], user_id, provider_calls, errors)
        for metric, specs in DURATION_SPECS.items()
    }
    current_instant = {
        metric: _fetch_frame_metric(specs, periods["current_instant"], user_id, provider_calls, errors)
        for metric, specs in INSTANT_SPECS.items()
    }
    prior_instant = {
        metric: _fetch_frame_metric(specs, periods["prior_instant"], user_id, provider_calls, errors)
        for metric, specs in INSTANT_SPECS.items()
    }
    annual = {
        metric: _fetch_frame_metric(DURATION_SPECS[metric], periods["annual"], user_id, provider_calls, errors)
        for metric in ANNUAL_METRICS
    }

    by_ticker: dict[str, dict[str, Any]] = {}
    for ticker, cik in ticker_map.items():
        current_values = {
            metric: (rows.get(cik) or {}).get("value")
            for metric, rows in current_duration.items()
        }
        current_values.update({
            metric: (rows.get(cik) or {}).get("value")
            for metric, rows in current_instant.items()
        })
        prior_values = {
            metric: (rows.get(cik) or {}).get("value")
            for metric, rows in prior_duration.items()
        }
        prior_values.update({
            metric: (rows.get(cik) or {}).get("value")
            for metric, rows in prior_instant.items()
        })
        annual_values = {
            metric: (rows.get(cik) or {}).get("value")
            for metric, rows in annual.items()
        }
        if not any(value is not None for value in current_values.values()) and not any(
            value is not None for value in annual_values.values()
        ):
            continue
        filed_dates = [
            str((rows.get(cik) or {}).get("filed") or "")
            for rows in list(current_duration.values()) + list(current_instant.values()) + list(annual.values())
            if rows.get(cik)
        ]
        by_ticker[ticker] = {
            "cik": cik,
            "current": current_values,
            "prior": prior_values,
            "annual": annual_values,
            "latest_filed": max(filed_dates) if filed_dates else None,
        }

    return {
        "configured": True,
        "generated_at": _iso(),
        "source": "SEC XBRL Frames full-market pre-screen",
        "periods": periods,
        "by_ticker": by_ticker,
        "ticker_count": len(by_ticker),
        "cache_hours": CACHE_HOURS,
        "cache_hit": False,
        "stale_cache": False,
        "error": "",
    }


def _baseline(
    user_id: int,
    errors: list[str],
    provider_calls: Counter,
) -> dict[str, Any]:
    cached = _preference(user_id)
    generated_at = _parse_dt(cached.get("generated_at"))
    fresh = bool(
        cached.get("by_ticker")
        and generated_at
        and (_utcnow() - generated_at) <= timedelta(hours=CACHE_HOURS)
    )
    if fresh:
        out = dict(cached)
        out["cache_hit"] = True
        out["stale_cache"] = False
        return out
    try:
        payload = _build_baseline(user_id, errors, provider_calls)
        if payload.get("configured") and payload.get("by_ticker"):
            _save_preference(user_id, payload)
        return payload
    except Exception as exc:
        errors.append(f"Full-market SEC fundamental pre-screen: {type(exc).__name__}: {exc}")
        if cached.get("by_ticker"):
            out = dict(cached)
            out["cache_hit"] = True
            out["stale_cache"] = True
            return out
        return {
            "configured": bool(_sec_headers(user_id)),
            "generated_at": None,
            "by_ticker": {},
            "periods": _frame_periods(),
            "cache_hit": False,
            "stale_cache": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _screen_one(row: dict[str, Any], facts: dict[str, Any] | None) -> dict[str, Any]:
    if not facts:
        return {
            "status": "MISSING",
            "eligible": False,
            "side": "NEUTRAL",
            "strength": 0,
            "long_points": 0,
            "short_points": 0,
            "signals": [],
            "metrics": {},
            "reason": "No comparable SEC frame fundamentals for this ticker.",
        }

    current = dict(facts.get("current") or {})
    prior = dict(facts.get("prior") or {})
    annual = dict(facts.get("annual") or {})
    price = _num(row.get("price"))

    revenue = _num(current.get("revenue"))
    prior_revenue = _num(prior.get("revenue"))
    operating_income = _num(current.get("operating_income"))
    prior_operating_income = _num(prior.get("operating_income"))
    cfo = _num(current.get("cfo"))
    capex = _num(current.get("capex"))
    inventory = _num(current.get("inventory"))
    prior_inventory = _num(prior.get("inventory"))
    receivables = _num(current.get("receivables"))
    prior_receivables = _num(prior.get("receivables"))
    current_shares = _num(current.get("shares"))
    prior_shares = _num(prior.get("shares"))
    shares = current_shares if current_shares not in (None, 0) else prior_shares
    share_basis = "CURRENT_COMPARABLE_FRAME" if current_shares not in (None, 0) else ("PRIOR_COMPARABLE_FRAME" if prior_shares not in (None, 0) else "UNAVAILABLE")

    revenue_yoy = _pct_change(revenue, prior_revenue)
    operating_margin = _ratio(operating_income, revenue, 100.0)
    prior_operating_margin = _ratio(prior_operating_income, prior_revenue, 100.0)
    operating_margin_change = (
        operating_margin - prior_operating_margin
        if operating_margin is not None and prior_operating_margin is not None
        else None
    )
    fcf = (cfo - capex) if cfo is not None and capex is not None else None
    fcf_margin = _ratio(fcf, revenue, 100.0)
    inventory_growth = _pct_change(inventory, prior_inventory)
    receivables_growth = _pct_change(receivables, prior_receivables)

    annual_revenue = _num(annual.get("revenue"))
    annual_net_income = _num(annual.get("net_income"))
    annual_cfo = _num(annual.get("cfo"))
    annual_capex = _num(annual.get("capex"))
    annual_fcf = (
        annual_cfo - annual_capex
        if annual_cfo is not None and annual_capex is not None
        else None
    )
    market_cap = price * shares if price is not None and shares not in (None, 0) else None
    pe = _ratio(market_cap, annual_net_income) if market_cap is not None and annual_net_income and annual_net_income > 0 else None
    ps = _ratio(market_cap, annual_revenue) if market_cap is not None and annual_revenue and annual_revenue > 0 else None
    fcf_yield = _ratio(annual_fcf, market_cap, 100.0) if market_cap not in (None, 0) else None

    signals: list[dict[str, Any]] = []

    def add(side: str, label: str, detail: str, points: int) -> None:
        signals.append({"side": side, "label": label, "detail": detail, "points": int(points)})

    if revenue_yoy is not None:
        if revenue_yoy >= 10:
            add("LONG", "REVENUE ACCELERATION", f"Comparable SEC-frame revenue {revenue_yoy:+.1f}% YoY", 2)
        elif revenue_yoy >= 3:
            add("LONG", "REVENUE GROWTH", f"Comparable SEC-frame revenue {revenue_yoy:+.1f}% YoY", 1)
        elif revenue_yoy <= -8:
            add("SHORT", "REVENUE DETERIORATION", f"Comparable SEC-frame revenue {revenue_yoy:+.1f}% YoY", 2)
        elif revenue_yoy <= -3:
            add("SHORT", "REVENUE PRESSURE", f"Comparable SEC-frame revenue {revenue_yoy:+.1f}% YoY", 1)

    if operating_margin_change is not None:
        if operating_margin_change >= 2:
            add("LONG", "OPERATING LEVERAGE", f"Operating margin improved {operating_margin_change:+.1f} pp YoY", 3)
        elif operating_margin_change >= 1:
            add("LONG", "MARGIN IMPROVEMENT", f"Operating margin improved {operating_margin_change:+.1f} pp YoY", 1)
        elif operating_margin_change <= -2:
            add("SHORT", "OPERATING DELEVERAGE", f"Operating margin deteriorated {operating_margin_change:+.1f} pp YoY", 3)
        elif operating_margin_change <= -1:
            add("SHORT", "MARGIN PRESSURE", f"Operating margin deteriorated {operating_margin_change:+.1f} pp YoY", 1)

    if fcf_margin is not None:
        if fcf_margin >= 10:
            add("LONG", "CASH MARGIN", f"Comparable-period FCF margin {fcf_margin:.1f}%", 2)
        elif fcf_margin >= 5:
            add("LONG", "POSITIVE CASH MARGIN", f"Comparable-period FCF margin {fcf_margin:.1f}%", 1)
        elif fcf_margin < 0:
            add("SHORT", "NEGATIVE CASH MARGIN", f"Comparable-period FCF margin {fcf_margin:.1f}%", 2)

    if revenue_yoy is not None and inventory_growth is not None:
        spread = inventory_growth - revenue_yoy
        if spread >= 12:
            add("SHORT", "INVENTORY BUILD", f"Inventory growth exceeds revenue growth by {spread:.1f} pp", 2)
        elif spread <= -8:
            add("LONG", "INVENTORY DISCIPLINE", f"Inventory growth trails revenue growth by {abs(spread):.1f} pp", 1)

    if revenue_yoy is not None and receivables_growth is not None:
        spread = receivables_growth - revenue_yoy
        if spread >= 12:
            add("SHORT", "RECEIVABLES BUILD", f"Receivables growth exceeds revenue growth by {spread:.1f} pp", 2)
        elif spread <= -8:
            add("LONG", "COLLECTION QUALITY", f"Receivables growth trails revenue growth by {abs(spread):.1f} pp", 1)

    if fcf_yield is not None:
        if fcf_yield >= 8:
            add("LONG", "FCF YIELD", f"Current-price FCF yield proxy {fcf_yield:.1f}%", 3)
        elif fcf_yield >= 5:
            add("LONG", "FCF YIELD", f"Current-price FCF yield proxy {fcf_yield:.1f}%", 1)
        elif fcf_yield <= 0:
            add("SHORT", "FCF YIELD", f"Current-price FCF yield proxy {fcf_yield:.1f}%", 2)

    if pe is not None:
        if pe <= 15:
            add("LONG", "EARNINGS MULTIPLE", f"Current-price trailing P/E proxy {pe:.1f}x", 2)
        elif pe <= 20:
            add("LONG", "EARNINGS MULTIPLE", f"Current-price trailing P/E proxy {pe:.1f}x", 1)
        elif pe >= 45 and (revenue_yoy is None or revenue_yoy <= 0):
            add("SHORT", "EARNINGS MULTIPLE", f"Trailing P/E proxy {pe:.1f}x without positive comparable revenue growth", 1)

    long_points = sum(int(item["points"]) for item in signals if item["side"] == "LONG")
    short_points = sum(int(item["points"]) for item in signals if item["side"] == "SHORT")
    evidence_fields = sum(
        value is not None
        for value in (
            revenue_yoy, operating_margin, operating_margin_change, fcf_margin,
            inventory_growth, receivables_growth, pe, ps, fcf_yield,
        )
    )
    status = "READY" if evidence_fields >= 4 and revenue_yoy is not None else ("PARTIAL" if evidence_fields >= 2 else "MISSING")
    side = "NEUTRAL"
    if max(long_points, short_points) >= 3 and abs(long_points - short_points) >= 2:
        side = "LONG" if long_points > short_points else "SHORT"
    strength = max(long_points, short_points)
    eligible = status in {"READY", "PARTIAL"} and side in {"LONG", "SHORT"} and strength >= 3

    return {
        "status": status,
        "eligible": eligible,
        "side": side,
        "strength": strength,
        "long_points": long_points,
        "short_points": short_points,
        "signals": sorted(signals, key=lambda item: (-int(item["points"]), item["label"])),
        "metrics": {
            "revenue_yoy_pct": revenue_yoy,
            "operating_margin_pct": operating_margin,
            "operating_margin_change_pp": operating_margin_change,
            "fcf_margin_pct": fcf_margin,
            "inventory_growth_pct": inventory_growth,
            "receivables_growth_pct": receivables_growth,
            "pe_proxy": pe,
            "ps_proxy": ps,
            "fcf_yield_pct": fcf_yield,
            "market_cap_proxy": market_cap,
            "share_basis": share_basis,
        },
        "latest_filed": facts.get("latest_filed"),
        "cik": facts.get("cik"),
        "reason": "Comparable full-market SEC-frame fundamentals screened before deep forensics.",
    }


def screen_full_universe(
    user_id: int,
    rows: list[dict[str, Any]],
    errors: list[str],
    provider_calls: Counter,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the same cheap fundamental screen to every liquid Stage-1 name.

    This is the anti-blind-selection layer. Deep Companyfacts work remains bounded,
    but Stage 2 may only select unknown names after this market-wide fundamental pass.
    """
    baseline = _baseline(user_id, errors, provider_calls)
    by_ticker = dict(baseline.get("by_ticker") or {})
    augmented: list[dict[str, Any]] = []
    ready = partial = missing = eligible = long_count = short_count = valuation_usable = 0

    for raw in rows:
        row = dict(raw)
        ticker = str(row.get("ticker") or "").upper()
        screen = _screen_one(row, by_ticker.get(ticker))
        row["fundamental_screen"] = screen
        augmented.append(row)
        if screen["status"] == "READY":
            ready += 1
        elif screen["status"] == "PARTIAL":
            partial += 1
        else:
            missing += 1
        valuation_metrics = dict(screen.get("metrics") or {})
        if any(valuation_metrics.get(key) is not None for key in ("pe_proxy", "ps_proxy", "fcf_yield_pct")):
            valuation_usable += 1
        if screen.get("eligible"):
            eligible += 1
            if screen.get("side") == "LONG":
                long_count += 1
            elif screen.get("side") == "SHORT":
                short_count += 1

    total = len(augmented)
    usable = ready + partial
    stats = {
        "configured": bool(baseline.get("configured")),
        "generated_at": baseline.get("generated_at"),
        "cache_hit": bool(baseline.get("cache_hit")),
        "stale_cache": bool(baseline.get("stale_cache")),
        "periods": dict(baseline.get("periods") or {}),
        "total_liquid_names": total,
        "ready_count": ready,
        "partial_count": partial,
        "missing_count": missing,
        "usable_count": usable,
        "usable_pct": round((usable / total) * 100.0, 1) if total else 0.0,
        "valuation_usable_count": valuation_usable,
        "valuation_usable_pct": round((valuation_usable / total) * 100.0, 1) if total else 0.0,
        "eligible_count": eligible,
        "long_screen_count": long_count,
        "short_screen_count": short_count,
        "source": baseline.get("source") or "SEC XBRL Frames",
        "error": baseline.get("error") or "",
        "rule": "Every liquid Stage-1 name is screened on comparable SEC fundamentals before unknown names can consume deep Stage-2 budget.",
    }
    return augmented, stats


__all__ = [
    "CACHE_HOURS", "screen_full_universe", "_frame_periods", "_screen_one",
]
