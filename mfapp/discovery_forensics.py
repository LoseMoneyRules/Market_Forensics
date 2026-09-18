from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

import requests

from .current_financials import annual_rows, quarterly_rows
from .data_providers import get_secret
from .secdata import (
    DURATION_TAGS, INSTANT_TAGS, SEC_DATA, SEC_WWW,
    _annual_duration, _annual_instant, _as_decimal,
    _quarter_duration_values, _quarter_instants,
)
from .valuation_engine import default_cases, evaluate, metrics_from_history, n

FORENSIC_EDGE_PCT = 20.0
FORENSIC_ENRICH_PER_SIDE = 8
FORENSIC_TIMEOUT = (4, 10)


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


def _sec_headers(user_id: int) -> dict[str, str] | None:
    user_agent = get_secret(user_id, "sec_user_agent").strip()
    if not user_agent or "@" not in user_agent:
        return None
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _sec_ticker_map(headers: dict[str, str]) -> dict[str, dict[str, str]]:
    response = requests.get(f"{SEC_WWW}/files/company_tickers.json", headers=headers, timeout=FORENSIC_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"SEC ticker map HTTP {response.status_code}")
    out: dict[str, dict[str, str]] = {}
    for row in (response.json() or {}).values():
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        out[ticker] = {
            "cik": str(row.get("cik_str") or "").zfill(10),
            "name": str(row.get("title") or ticker).strip(),
        }
    return out


def _sec_companyfacts(cik: str, headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(
        f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json",
        headers=headers,
        timeout=FORENSIC_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"SEC companyfacts HTTP {response.status_code}")
    return response.json() or {}


def _value(record: dict[str, Any] | None) -> float | None:
    d = _as_decimal((record or {}).get("val"))
    return float(d) if d is not None else None


def _annual_history(companyfacts: dict[str, Any]) -> list[dict[str, Any]]:
    duration = {key: _annual_duration(companyfacts, tags) for key, tags in DURATION_TAGS.items()}
    instant = {key: _annual_instant(companyfacts, tags) for key, tags in INSTANT_TAGS.items()}
    dei_shares = _annual_instant(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei")
    if dei_shares:
        instant["shares_outstanding"] = dei_shares

    years = sorted(set().union(*(set(rows) for rows in duration.values()), *(set(rows) for rows in instant.values())))
    out: list[dict[str, Any]] = []
    for fy in years[-8:]:
        revenue = _value(duration["revenue"].get(fy))
        if revenue is None:
            continue
        cfo = _value(duration["cfo"].get(fy))
        capex = _value(duration["capex"].get(fy))
        row = {
            "fiscal_year": fy,
            "filed_at": (duration["revenue"].get(fy) or {}).get("filed"),
            "revenue": revenue,
            "gross_profit": _value(duration["gross_profit"].get(fy)),
            "operating_income": _value(duration["operating_income"].get(fy)),
            "net_income": _value(duration["net_income"].get(fy)),
            "cfo": cfo,
            "capex": capex,
            "fcf": (cfo - capex) if cfo is not None and capex is not None else None,
            "cash": _value(instant["cash"].get(fy)),
            "debt": _value(instant["debt"].get(fy)),
            "inventory": _value(instant["inventory"].get(fy)),
            "receivables": _value(instant["receivables"].get(fy)),
            "payables": _value(instant["payables"].get(fy)),
            "shares_outstanding": _value(instant["shares_outstanding"].get(fy)),
            "diluted_shares": _value(duration["diluted_shares"].get(fy)),
        }
        out.append(row)
    return out


def _quarter_history(companyfacts: dict[str, Any]) -> list[dict[str, Any]]:
    annual_duration = {key: _annual_duration(companyfacts, tags) for key, tags in DURATION_TAGS.items()}
    q_duration = {
        key: _quarter_duration_values(
            companyfacts, tags, annual_duration[key],
            shares_metric=(key == "diluted_shares"),
        )
        for key, tags in DURATION_TAGS.items()
    }
    q_instant = {key: _quarter_instants(companyfacts, tags) for key, tags in INSTANT_TAGS.items()}
    dei_shares = _quarter_instants(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei")
    if dei_shares:
        q_instant["shares_outstanding"] = dei_shares

    order = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    keys = sorted(
        set().union(*(set(rows) for rows in q_duration.values()), *(set(rows) for rows in q_instant.values())),
        key=lambda key: (key[0], order.get(key[1], 0)),
    )
    out: list[dict[str, Any]] = []
    for fy, fp in keys[-12:]:
        rev_info = q_duration.get("revenue", {}).get((fy, fp)) or {}
        revenue = _num(rev_info.get("value"))
        if revenue is None:
            continue
        cfo = _num((q_duration.get("cfo", {}).get((fy, fp)) or {}).get("value"))
        capex = _num((q_duration.get("capex", {}).get((fy, fp)) or {}).get("value"))
        record = rev_info.get("record") or {}
        row = {
            "fiscal_year": fy,
            "period_type": fp,
            "period_end": str(record.get("end") or ""),
            "revenue": revenue,
            "gross_profit": _num((q_duration.get("gross_profit", {}).get((fy, fp)) or {}).get("value")),
            "operating_income": _num((q_duration.get("operating_income", {}).get((fy, fp)) or {}).get("value")),
            "net_income": _num((q_duration.get("net_income", {}).get((fy, fp)) or {}).get("value")),
            "cfo": cfo,
            "capex": capex,
            "fcf": (cfo - capex) if cfo is not None and capex is not None else None,
            "cash": _value(q_instant.get("cash", {}).get((fy, fp))),
            "debt": _value(q_instant.get("debt", {}).get((fy, fp))),
            "inventory": _value(q_instant.get("inventory", {}).get((fy, fp))),
            "receivables": _value(q_instant.get("receivables", {}).get((fy, fp))),
            "payables": _value(q_instant.get("payables", {}).get((fy, fp))),
            "shares_outstanding": _value(q_instant.get("shares_outstanding", {}).get((fy, fp))),
            "diluted_shares": _num((q_duration.get("diluted_shares", {}).get((fy, fp)) or {}).get("value")),
        }
        out.append(row)
    return out


def _ttm(rows: list[dict[str, Any]], offset: int = 0) -> dict[str, Any] | None:
    block = rows[-(4 + offset):len(rows) - offset if offset else None]
    if len(block) != 4:
        return None
    try:
        first = date.fromisoformat(str(block[0].get("period_end") or "")[:10])
        last = date.fromisoformat(str(block[-1].get("period_end") or "")[:10])
        if (last - first).days > 370:
            return None
    except Exception:
        pass
    flow_fields = ("revenue", "gross_profit", "operating_income", "net_income", "cfo", "capex", "fcf")
    out: dict[str, Any] = {}
    for field in flow_fields:
        values = [_num(row.get(field)) for row in block]
        out[field] = sum(values) if all(value is not None for value in values) else None
    latest = block[-1]
    for field in ("cash", "debt", "inventory", "receivables", "payables", "shares_outstanding"):
        out[field] = _num(latest.get(field))
    shares = [_num(row.get("diluted_shares")) for row in block if _num(row.get("diluted_shares")) is not None]
    out["diluted_shares"] = mean(shares) if shares else _num(latest.get("shares_outstanding"))
    out["period_end"] = latest.get("period_end")
    return out


def _operating_snapshot(current: dict[str, Any] | None, prior: dict[str, Any] | None) -> dict[str, Any]:
    current = current or {}
    prior = prior or {}
    revenue = _num(current.get("revenue"))
    prior_revenue = _num(prior.get("revenue"))
    op_margin = _ratio(current.get("operating_income"), revenue, 100.0)
    prior_op_margin = _ratio(prior.get("operating_income"), prior_revenue, 100.0)
    fcf_margin = _ratio(current.get("fcf"), revenue, 100.0)
    prior_fcf_margin = _ratio(prior.get("fcf"), prior_revenue, 100.0)
    revenue_growth = _pct_change(revenue, prior_revenue)
    inventory_growth = _pct_change(current.get("inventory"), prior.get("inventory"))
    receivables_growth = _pct_change(current.get("receivables"), prior.get("receivables"))
    return {
        "revenue_growth_pct": revenue_growth,
        "operating_margin_pct": op_margin,
        "operating_margin_delta_bps": ((op_margin - prior_op_margin) * 100.0) if op_margin is not None and prior_op_margin is not None else None,
        "fcf_margin_pct": fcf_margin,
        "fcf_margin_delta_bps": ((fcf_margin - prior_fcf_margin) * 100.0) if fcf_margin is not None and prior_fcf_margin is not None else None,
        "inventory_growth_pct": inventory_growth,
        "receivables_growth_pct": receivables_growth,
        "inventory_vs_revenue_pp": (inventory_growth - revenue_growth) if inventory_growth is not None and revenue_growth is not None else None,
        "receivables_vs_revenue_pp": (receivables_growth - revenue_growth) if receivables_growth is not None and revenue_growth is not None else None,
        "fcf_conversion": _ratio(current.get("fcf"), current.get("net_income"), 1.0),
    }


def _signals(snapshot: dict[str, Any], day_move: float | None) -> tuple[list[dict[str, Any]], int, int]:
    signals: list[dict[str, Any]] = []
    long_score = short_score = 0

    def add(side: str, label: str, detail: str, points: int):
        nonlocal long_score, short_score
        signals.append({"side": side, "label": label, "detail": detail, "points": points})
        if side == "LONG":
            long_score += points
        else:
            short_score += points

    rg = _num(snapshot.get("revenue_growth_pct"))
    op = _num(snapshot.get("operating_margin_delta_bps"))
    fm = _num(snapshot.get("fcf_margin_delta_bps"))
    inv = _num(snapshot.get("inventory_vs_revenue_pp"))
    rec = _num(snapshot.get("receivables_vs_revenue_pp"))
    conv = _num(snapshot.get("fcf_conversion"))

    if rg is not None:
        if rg >= 8:
            add("LONG", "REVENUE ACCELERATION", f"TTM revenue {rg:+.1f}% YoY", 14)
        elif rg <= -5:
            add("SHORT", "REVENUE DETERIORATION", f"TTM revenue {rg:+.1f}% YoY", 16)

    if op is not None:
        if op >= 100:
            add("LONG", "OPERATING LEVERAGE", f"Operating margin {op:+.0f} bps YoY", 16)
        elif op <= -100:
            add("SHORT", "OPERATING DELEVERAGE", f"Operating margin {op:+.0f} bps YoY", 18)

    if fm is not None:
        if fm >= 150:
            add("LONG", "CASH MARGIN INFLECTION", f"FCF margin {fm:+.0f} bps YoY", 14)
        elif fm <= -150:
            add("SHORT", "CASH MARGIN EROSION", f"FCF margin {fm:+.0f} bps YoY", 16)

    if inv is not None:
        if inv <= -8:
            add("LONG", "INVENTORY DISCIPLINE", f"Inventory growth trails revenue by {abs(inv):.1f} pp", 9)
        elif inv >= 12:
            add("SHORT", "INVENTORY BUILD", f"Inventory growth exceeds revenue by {inv:.1f} pp", 14)

    if rec is not None:
        if rec <= -8:
            add("LONG", "COLLECTION QUALITY", f"Receivables growth trails revenue by {abs(rec):.1f} pp", 8)
        elif rec >= 12:
            add("SHORT", "RECEIVABLES BUILD", f"Receivables growth exceeds revenue by {rec:.1f} pp", 13)

    if conv is not None:
        if conv >= 1.0:
            add("LONG", "EARNINGS → CASH", f"FCF / net income {conv:.2f}x", 8)
        elif conv < 0.55:
            add("SHORT", "WEAK CASH CONVERSION", f"FCF / net income {conv:.2f}x", 10)

    move = _num(day_move)
    if move is not None and move <= -8 and long_score >= 14:
        add("LONG", "PRICE / FUNDAMENTALS DISCONNECT", f"Price {move:+.1f}% while operating evidence improves", 14)
    elif move is not None and move >= 8 and short_score >= 14:
        add("SHORT", "PRICE / FUNDAMENTALS DISCONNECT", f"Price {move:+.1f}% while operating evidence deteriorates", 14)

    signals.sort(key=lambda row: (-row["points"], row["label"]))
    return signals, long_score, short_score


def _valuation_from_history(annual: list[dict[str, Any]], current_ttm: dict[str, Any] | None, prior_ttm: dict[str, Any] | None, price: float) -> dict[str, Any]:
    metrics = metrics_from_history(annual)
    if current_ttm and _num(current_ttm.get("revenue")) is not None:
        revenue = _num(current_ttm.get("revenue"))
        net_income = _num(current_ttm.get("net_income"))
        fcf = _num(current_ttm.get("fcf"))
        cash = _num(current_ttm.get("cash")) or 0.0
        debt = _num(current_ttm.get("debt")) or 0.0
        shares = _num(current_ttm.get("shares_outstanding")) or _num(current_ttm.get("diluted_shares")) or _num(metrics.get("shares"))
        metrics.update({
            "revenue": revenue,
            "net_income": net_income,
            "fcf": fcf,
            "net_debt": debt - cash,
            "shares": shares,
            "basis_usable": shares not in (None, 0),
            "basis_issue": "" if shares not in (None, 0) else "No usable current share denominator.",
            "revenue_growth": (_pct_change(revenue, (prior_ttm or {}).get("revenue")) or 0.0) / 100.0 if prior_ttm else metrics.get("revenue_growth"),
            "net_margin": _ratio(net_income, revenue, 1.0),
            "fcf_margin": _ratio(fcf, revenue, 1.0),
            "operating_margin": _ratio(current_ttm.get("operating_income"), revenue, 1.0),
        })
    defaults = default_cases(metrics, "Generic")
    cases = {name: defaults[name] for name in ("BEAR", "BASE", "BULL")}
    result = evaluate(metrics, cases, defaults["weights"], defaults["horizon_years"], current_price=price, allow_reference_fallback=False)
    base = ((result.get("scenarios") or {}).get("BASE") or {}).get("fair_value")
    gap = ((float(base) / price - 1.0) * 100.0) if base is not None and price else None
    return {
        "base": base,
        "gap_pct": gap,
        "quality": result.get("quality"),
        "metrics": metrics,
    }


def _local_forensics(context: dict[str, Any], price: float, day_move: float | None) -> dict[str, Any] | None:
    company_id = int(context.get("company_id") or 0)
    if not company_id:
        return None
    quarters = list(reversed(quarterly_rows(company_id, 8)))
    current = _ttm(quarters, 0)
    prior = _ttm(quarters, 4)
    snapshot = _operating_snapshot(current, prior)
    signals, long_score, short_score = _signals(snapshot, day_move)
    stored_base = _num((context.get("valuation") or {}).get("base"))
    base_gap = _num(context.get("base_gap_pct"))
    return {
        "base": stored_base,
        "gap_pct": base_gap,
        "quality": "RESEARCH BASE",
        "snapshot": snapshot,
        "signals": signals,
        "long_score": long_score,
        "short_score": short_score,
        "source": "STORED RESEARCH + SEC OPERATING DATA",
    }


def _external_forensics(symbol: str, price: float, day_move: float | None, meta: dict[str, str], headers: dict[str, str]) -> dict[str, Any] | None:
    facts = _sec_companyfacts(meta["cik"], headers)
    annual = _annual_history(facts)
    quarters = _quarter_history(facts)
    if len(annual) < 2:
        return None
    current = _ttm(quarters, 0)
    prior = _ttm(quarters, 4)
    valuation = _valuation_from_history(annual, current, prior, price)
    if valuation.get("base") is None or valuation.get("gap_pct") is None:
        return None
    snapshot = _operating_snapshot(current, prior)
    signals, long_score, short_score = _signals(snapshot, day_move)
    return {
        "base": valuation["base"],
        "gap_pct": valuation["gap_pct"],
        "quality": "FORENSIC BASE · SAME VALUATION ENGINE",
        "snapshot": snapshot,
        "signals": signals,
        "long_score": long_score,
        "short_score": short_score,
        "source": "SEC COMPANYFACTS + MARKET FORENSICS VALUATION",
    }


def enrich_forensic_candidates(
    user_id: int,
    candidates: list[dict[str, Any]],
    local_context: dict[str, dict[str, Any]],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    """Return only candidates with a calculable fair value and confirming operating evidence.

    This stage is intentionally bounded. Screeners choose where to investigate;
    they never decide the final Long/Short list.
    """
    headers = _sec_headers(user_id)
    known = [row for row in candidates if local_context.get(str(row.get("ticker") or "").upper())]
    unknown = [row for row in candidates if not local_context.get(str(row.get("ticker") or "").upper())]

    # Limit unknown SEC work symmetrically so Discovery remains a bounded background job.
    long_unknown = sorted([r for r in unknown if (_num(r.get("move_pct")) or 0) < 0], key=lambda r: (_num(r.get("move_pct")) or 0))[:FORENSIC_ENRICH_PER_SIDE]
    short_unknown = sorted([r for r in unknown if (_num(r.get("move_pct")) or 0) > 0], key=lambda r: -(_num(r.get("move_pct")) or 0))[:FORENSIC_ENRICH_PER_SIDE]
    selected_unknown = long_unknown + short_unknown

    ticker_map: dict[str, dict[str, str]] = {}
    if selected_unknown and headers:
        try:
            ticker_map = _sec_ticker_map(headers)
        except Exception as exc:
            errors.append(f"Forensic SEC ticker map: {type(exc).__name__}")
    elif selected_unknown and not headers:
        errors.append("Forensic SEC enrichment unavailable: configure SEC User-Agent in Settings.")

    out: dict[str, dict[str, Any]] = {}
    for row in known:
        symbol = str(row.get("ticker") or "").upper()
        price = _num(row.get("price"))
        if price is None:
            continue
        result = _local_forensics(local_context.get(symbol) or {}, price, _num(row.get("move_pct")))
        if result:
            out[symbol] = result

    for row in selected_unknown:
        symbol = str(row.get("ticker") or "").upper()
        price = _num(row.get("price"))
        meta = ticker_map.get(symbol)
        if price is None or not meta or not headers:
            continue
        try:
            result = _external_forensics(symbol, price, _num(row.get("move_pct")), meta, headers)
        except Exception as exc:
            errors.append(f"{symbol} forensic enrichment: {type(exc).__name__}")
            continue
        if result:
            result["sec_name"] = meta.get("name") or symbol
            out[symbol] = result
    return out


def forensic_side(result: dict[str, Any]) -> tuple[str, str, int] | None:
    gap = _num(result.get("gap_pct"))
    long_score = int(result.get("long_score") or 0)
    short_score = int(result.get("short_score") or 0)
    if gap is None:
        return None
    if gap >= FORENSIC_EDGE_PCT and long_score >= 14 and long_score > short_score:
        priority = "P1" if gap >= 30 and long_score >= 35 else "P2"
        return "LONG", priority, long_score
    if gap <= -FORENSIC_EDGE_PCT and short_score >= 14 and short_score > long_score:
        priority = "P1" if gap <= -30 and short_score >= 35 else "P2"
        return "SHORT", priority, short_score
    return None


__all__ = [
    "FORENSIC_EDGE_PCT", "FORENSIC_ENRICH_PER_SIDE",
    "enrich_forensic_candidates", "forensic_side",
]
