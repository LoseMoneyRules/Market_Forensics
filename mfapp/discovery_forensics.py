from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
from .valuation_engine import default_cases, evaluate, infer_company_type, metrics_from_history, valuation_base_quality
from .economic_reality import DURATION_TAGS as ECONOMIC_DURATION_TAGS, INSTANT_TAGS as ECONOMIC_INSTANT_TAGS, build_economic_reality, economic_from_row, has_suppression, metric as economic_metric

FORENSIC_WATCH_EDGE_PCT = 12.0
FORENSIC_EDGE_PCT = 20.0
FORENSIC_STRONG_EDGE_PCT = 25.0
FORENSIC_CONFIRM_SCORE = 14
FORENSIC_ENRICH_LIMIT = 10
FORENSIC_ENRICH_PER_SIDE = 5  # compatibility alias; Stage 2 is capped by FORENSIC_ENRICH_LIMIT
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


def _sec_submission(cik: str, headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(
        f"{SEC_DATA}/submissions/CIK{cik}.json",
        headers=headers,
        timeout=FORENSIC_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"SEC submission HTTP {response.status_code}")
    return response.json() or {}


def _value(record: dict[str, Any] | None) -> float | None:
    d = _as_decimal((record or {}).get("val"))
    return float(d) if d is not None else None


def _economic_maps_annual(companyfacts: dict[str, Any], fiscal_year_end: str) -> tuple[dict, dict]:
    duration = {
        key: _annual_duration(companyfacts, list(tags), fiscal_year_end)
        for key, tags in ECONOMIC_DURATION_TAGS.items()
    }
    instant = {
        key: _annual_instant(companyfacts, list(tags), fiscal_year_end=fiscal_year_end)
        for key, tags in ECONOMIC_INSTANT_TAGS.items()
    }
    return duration, instant


def _economic_maps_quarter(companyfacts: dict[str, Any], fiscal_year_end: str) -> tuple[dict, dict]:
    duration: dict[str, dict] = {}
    for key, tags in ECONOMIC_DURATION_TAGS.items():
        annual = _annual_duration(companyfacts, list(tags), fiscal_year_end)
        duration[key] = _quarter_duration_values(
            companyfacts, list(tags), annual, fiscal_year_end=fiscal_year_end
        )
    instant = {
        key: _quarter_instants(companyfacts, list(tags), fiscal_year_end=fiscal_year_end)
        for key, tags in ECONOMIC_INSTANT_TAGS.items()
    }
    return duration, instant


def _economic_fact_bundle(duration: dict, instant: dict, key: Any) -> tuple[dict, dict]:
    facts: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for field, rows in duration.items():
        info = rows.get(key) or {}
        record = info.get("record") if isinstance(info, dict) and "record" in info else info
        value = info.get("value") if isinstance(info, dict) and "value" in info else _value(record)
        if value is not None:
            facts[field] = value
            sources[field] = {"tag": (record or {}).get("tag"), "filed": (record or {}).get("filed")}
    for field, rows in instant.items():
        record = rows.get(key) or {}
        value = _value(record)
        if value is not None:
            facts[field] = value
            sources[field] = {"tag": record.get("tag"), "filed": record.get("filed")}
    return facts, sources


def _ttm_economic(block: list[dict[str, Any]], statement: dict[str, Any]) -> dict[str, Any] | None:
    snapshots = [economic_from_row(row) for row in block]
    if not any(snapshots):
        return None
    facts: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for key in ECONOMIC_DURATION_TAGS:
        values = [_num((snap.get("facts") or {}).get(key)) for snap in snapshots]
        if all(value is not None for value in values):
            facts[key] = sum(values)
            sources[key] = {"method": "FOUR_QUARTERS"}
    latest = snapshots[-1]
    for key in ECONOMIC_INSTANT_TAGS:
        value = _num((latest.get("facts") or {}).get(key))
        if value is not None:
            facts[key] = value
            sources[key] = dict((latest.get("fact_sources") or {}).get(key) or {"method": "LATEST_QUARTER"})
    result = build_economic_reality(statement, facts=facts, fact_sources=sources)
    for flag in latest.get("flags") or []:
        if str(flag.get("code") or "") == "SECTOR_BALANCE_SHEET_POLICY":
            if not any(str(row.get("code") or "") == "SECTOR_BALANCE_SHEET_POLICY" for row in result["flags"]):
                result["flags"].append(dict(flag))
            result["suppressions"] = sorted(set(result["suppressions"]) | set(latest.get("suppressions") or []))
    return result


def _annual_history(companyfacts: dict[str, Any], fiscal_year_end: str = "", company_type: str = "") -> list[dict[str, Any]]:
    duration = {key: _annual_duration(companyfacts, tags, fiscal_year_end) for key, tags in DURATION_TAGS.items()}
    instant = {key: _annual_instant(companyfacts, tags, fiscal_year_end=fiscal_year_end) for key, tags in INSTANT_TAGS.items()}
    economic_duration, economic_instant = _economic_maps_annual(companyfacts, fiscal_year_end)
    dei_shares = _annual_instant(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei", fiscal_year_end=fiscal_year_end)
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
            "pretax_income": _value(duration["pretax_income"].get(fy)),
            "income_tax": _value(duration["income_tax"].get(fy)),
            "assets": _value(instant["assets"].get(fy)),
            "liabilities": _value(instant["liabilities"].get(fy)),
            "equity": _value(instant["equity"].get(fy)),
        }
        economic_facts, economic_sources = _economic_fact_bundle(economic_duration, economic_instant, fy)
        row["quality"] = {
            "economic_reality": build_economic_reality(
                row, facts=economic_facts, fact_sources=economic_sources, company_type=company_type
            )
        }
        out.append(row)
    return out


def _quarter_history(companyfacts: dict[str, Any], fiscal_year_end: str = "", company_type: str = "") -> list[dict[str, Any]]:
    annual_duration = {key: _annual_duration(companyfacts, tags, fiscal_year_end) for key, tags in DURATION_TAGS.items()}
    q_duration = {
        key: _quarter_duration_values(
            companyfacts, tags, annual_duration[key],
            shares_metric=(key == "diluted_shares"),
            fiscal_year_end=fiscal_year_end,
        )
        for key, tags in DURATION_TAGS.items()
    }
    q_instant = {key: _quarter_instants(companyfacts, tags, fiscal_year_end=fiscal_year_end) for key, tags in INSTANT_TAGS.items()}
    economic_q_duration, economic_q_instant = _economic_maps_quarter(companyfacts, fiscal_year_end)
    dei_shares = _quarter_instants(companyfacts, ["EntityCommonStockSharesOutstanding"], namespace="dei", fiscal_year_end=fiscal_year_end)
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
            "pretax_income": _num((q_duration.get("pretax_income", {}).get((fy, fp)) or {}).get("value")),
            "income_tax": _num((q_duration.get("income_tax", {}).get((fy, fp)) or {}).get("value")),
            "assets": _value(q_instant.get("assets", {}).get((fy, fp))),
            "liabilities": _value(q_instant.get("liabilities", {}).get((fy, fp))),
            "equity": _value(q_instant.get("equity", {}).get((fy, fp))),
        }
        economic_facts, economic_sources = _economic_fact_bundle(
            economic_q_duration, economic_q_instant, (fy, fp)
        )
        row["quality"] = {
            "economic_reality": build_economic_reality(
                row, facts=economic_facts, fact_sources=economic_sources, company_type=company_type
            )
        }
        out.append(row)
    return out


def _ttm(rows: list[dict[str, Any]], offset: int = 0) -> dict[str, Any] | None:
    """Build only a filing-coherent four-quarter TTM block."""
    block = rows[-(4 + offset):len(rows) - offset if offset else None]
    if len(block) != 4:
        return None
    ends: list[date] = []
    for row in block:
        try:
            ends.append(date.fromisoformat(str(row.get("period_end") or "")[:10]))
        except Exception:
            return None
    if len(set(ends)) != 4 or ends != sorted(ends):
        return None
    span = (ends[-1] - ends[0]).days
    if span < 240 or span > 370:
        return None

    flow_fields = ("revenue", "gross_profit", "operating_income", "net_income", "cfo", "capex", "fcf")
    out: dict[str, Any] = {}
    for field in flow_fields:
        values = [_num(row.get(field)) for row in block]
        out[field] = sum(values) if all(value is not None for value in values) else None
    if out.get("revenue") is None:
        return None

    latest = block[-1]
    for field in ("cash", "debt", "inventory", "receivables", "payables", "shares_outstanding"):
        out[field] = _num(latest.get(field))
    shares = [_num(row.get("diluted_shares")) for row in block if _num(row.get("diluted_shares")) is not None]
    out["diluted_shares"] = mean(shares) if shares else _num(latest.get("shares_outstanding"))
    out["period_end"] = latest.get("period_end")
    out["quarter_ends"] = [item.isoformat() for item in ends]
    economic = _ttm_economic(block, out)
    if economic is not None:
        out["quality"] = {"economic_reality": economic}
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
        "economic_reality": economic_from_row(current),
    }


def _basis_review_flags(
    current: dict[str, Any] | None,
    prior: dict[str, Any] | None,
    annual: list[dict[str, Any]] | None = None,
    submission: dict[str, Any] | None = None,
) -> list[str]:
    """Conservative pre-candidate guard for split/listing/share-basis discontinuities."""
    current = current or {}
    prior = prior or {}
    flags: list[str] = []
    current_shares = _num(current.get("shares_outstanding")) or _num(current.get("diluted_shares"))
    prior_shares = _num(prior.get("shares_outstanding")) or _num(prior.get("diluted_shares"))
    if current_shares not in (None, 0) and prior_shares not in (None, 0):
        ratio = current_shares / prior_shares
        if ratio >= 1.5 or ratio <= (2.0 / 3.0):
            flags.append(f"Share-count basis changed {(ratio - 1.0) * 100.0:+.0f}% YoY; split/issuance/buyback basis needs review.")
    if annual is not None and len(annual) < 3:
        flags.append("Short filed history (<3 annual periods); recent listing/reorganization basis needs review.")

    recent = dict(((submission or {}).get("filings") or {}).get("recent") or {})
    forms = list(recent.get("form") or [])
    dates = list(recent.get("filingDate") or [])
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=550)
    for form, filing_date in zip(forms, dates):
        if str(form or "").upper() not in {"S-1", "S-1/A", "F-1", "F-1/A", "10-12B", "10-12G"}:
            continue
        try:
            if date.fromisoformat(str(filing_date)[:10]) >= cutoff:
                flags.append(f"Recent registration/listing filing {str(form).upper()} ({str(filing_date)[:10]}); price/share basis needs review.")
                break
        except Exception:
            continue
    return flags


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
        elif op <= -100 and not has_suppression(snapshot.get("economic_reality"), "REPORTED_MARGIN_DETERIORATION_AUTOMATIC"):
            add("SHORT", "OPERATING DELEVERAGE", f"Operating margin {op:+.0f} bps YoY", 18)

    if fm is not None:
        if fm >= 150:
            add("LONG", "CASH MARGIN INFLECTION", f"FCF margin {fm:+.0f} bps YoY", 14)
        elif fm <= -150 and not has_suppression(snapshot.get("economic_reality"), "FCF_MARGIN_EROSION_AUTOMATIC"):
            add("SHORT", "CASH MARGIN EROSION", f"FCF margin {fm:+.0f} bps YoY", 16)

    generic_wc_disabled = has_suppression(snapshot.get("economic_reality"), "GENERIC_WORKING_CAPITAL_SCORE")
    if inv is not None:
        if inv <= -8:
            add("LONG", "INVENTORY DISCIPLINE", f"Inventory growth trails revenue by {abs(inv):.1f} pp", 9)
        elif inv >= 12 and not generic_wc_disabled:
            add("SHORT", "INVENTORY BUILD", f"Inventory growth exceeds revenue by {inv:.1f} pp", 14)

    if rec is not None:
        if rec <= -8:
            add("LONG", "COLLECTION QUALITY", f"Receivables growth trails revenue by {abs(rec):.1f} pp", 8)
        elif rec >= 12 and not generic_wc_disabled:
            add("SHORT", "RECEIVABLES BUILD", f"Receivables growth exceeds revenue by {rec:.1f} pp", 13)

    if conv is not None:
        if conv >= 1.0:
            add("LONG", "EARNINGS → CASH", f"FCF / net income {conv:.2f}x", 8)
        elif conv < 0.55 and not has_suppression(snapshot.get("economic_reality"), "NEGATIVE_FCF_AUTOMATIC"):
            add("SHORT", "WEAK CASH CONVERSION", f"FCF / net income {conv:.2f}x", 10)

    move = _num(day_move)
    if move is not None and move <= -8 and long_score >= 14:
        add("LONG", "PRICE / FUNDAMENTALS DISCONNECT", f"Price {move:+.1f}% while operating evidence improves", 14)
    elif move is not None and move >= 8 and short_score >= 14:
        add("SHORT", "PRICE / FUNDAMENTALS DISCONNECT", f"Price {move:+.1f}% while operating evidence deteriorates", 14)

    signals.sort(key=lambda row: (-row["points"], row["label"]))
    return signals, long_score, short_score


def _valuation_from_history(
    annual: list[dict[str, Any]],
    current_ttm: dict[str, Any] | None,
    prior_ttm: dict[str, Any] | None,
    price: float,
    company_type: str = "Generic",
) -> dict[str, Any]:
    """Reuse the canonical valuation engine; Discovery owns no duplicate valuation model."""
    metrics = metrics_from_history(annual)
    if current_ttm and _num(current_ttm.get("revenue")) is not None:
        revenue = _num(current_ttm.get("revenue"))
        net_income = _num(current_ttm.get("net_income"))
        fcf = _num(current_ttm.get("fcf"))
        cash = _num(current_ttm.get("cash")) or 0.0
        debt = _num(current_ttm.get("debt")) or 0.0
        economic = economic_from_row(current_ttm)
        economic_net_debt = economic_metric(economic, "economic_net_debt")
        if economic.get("material_unresolved"):
            current_net_debt = None
        elif economic_net_debt is not None:
            current_net_debt = economic_net_debt
        else:
            current_net_debt = debt - cash
        shares = _num(current_ttm.get("shares_outstanding")) or _num(current_ttm.get("diluted_shares")) or _num(metrics.get("shares"))
        metrics.update({
            "revenue": revenue,
            "net_income": net_income,
            "fcf": fcf,
            "net_debt": current_net_debt,
            "net_debt_basis": str(economic.get("debt_basis") or "REPORTED_DEBT_MINUS_CASH_FALLBACK"),
            "economic_reality": economic,
            "economic_reality_unresolved": bool(economic.get("material_unresolved")),
            "shares": shares,
            "basis_usable": shares not in (None, 0),
            "basis_issue": "" if shares not in (None, 0) else "No usable current share denominator.",
            "revenue_growth": (
                (_pct_change(revenue, (prior_ttm or {}).get("revenue")) / 100.0)
                if prior_ttm and _pct_change(revenue, (prior_ttm or {}).get("revenue")) is not None
                else metrics.get("revenue_growth")
            ),
            "net_margin": _ratio(net_income, revenue, 1.0),
            "fcf_margin": _ratio(fcf, revenue, 1.0),
            "operating_margin": _ratio(current_ttm.get("operating_income"), revenue, 1.0),
        })
    defaults = default_cases(metrics, company_type)
    cases = {name: defaults[name] for name in ("BEAR", "BASE", "BULL")}
    result = evaluate(
        metrics, cases, defaults["weights"], defaults["horizon_years"],
        current_price=price, allow_reference_fallback=False,
    )
    scenarios = result.get("scenarios") or {}
    base_row = scenarios.get("BASE") or {}
    methods = sum(1 for key in ("pe", "ev_sales", "fcf_yield") if _num(base_row.get(key)) is not None)
    base = base_row.get("fair_value")
    gap = ((float(base) / price - 1.0) * 100.0) if base is not None and price else None
    return {
        "bear": (scenarios.get("BEAR") or {}).get("fair_value"),
        "base": base,
        "bull": (scenarios.get("BULL") or {}).get("fair_value"),
        "gap_pct": gap,
        "quality": result.get("quality"),
        "valuation_methods": methods,
        "metrics": metrics,
        "warnings": list(result.get("warnings") or []),
    }

def _local_forensics(
    context: dict[str, Any],
    price: float,
    day_move: float | None,
) -> tuple[dict[str, Any] | None, str]:
    company_id = int(context.get("company_id") or 0)
    if not company_id:
        return None, "LOCAL CONTEXT MISSING"
    annual = list(reversed(annual_rows(company_id, 8)))
    quarters = list(reversed(quarterly_rows(company_id, 12)))
    current = _ttm(quarters, 0)
    prior = _ttm(quarters, 4)
    if current is None or prior is None:
        return None, "TTM INVALID / INCOMPLETE"

    snapshot = _operating_snapshot(current, prior)
    signals, long_score, short_score = _signals(snapshot, day_move)
    stored_valuation = dict(context.get("valuation") or {})
    stored_base = _num(stored_valuation.get("base"))
    base_gap = _num(context.get("base_gap_pct"))
    methods = int(context.get("valuation_methods") or 0)
    quality = valuation_base_quality(stored_valuation)
    if stored_base is None:
        return None, "BASE UNAVAILABLE"
    if base_gap is None:
        comparison_price = _num(stored_valuation.get("current_price")) or price
        if comparison_price not in (None, 0):
            base_gap = (stored_base / comparison_price - 1.0) * 100.0
    if base_gap is None:
        return None, "BASE GAP UNAVAILABLE"
    return {
        "bear": _num(stored_valuation.get("bear")),
        "base": stored_base,
        "bull": _num(stored_valuation.get("bull")),
        "gap_pct": base_gap,
        "quality": quality,
        "valuation_methods": methods,
        "snapshot": snapshot,
        "economic_reality": dict(snapshot.get("economic_reality") or {}),
        "accounting_context": list((snapshot.get("economic_reality") or {}).get("flags") or []),
        "signals": signals,
        "long_score": long_score,
        "short_score": short_score,
        "source": "STORED RESEARCH + NORMALIZED SEC OPERATING DATA",
        "data_freshness": current.get("period_end"),
        "materialized_at": context.get("cache_generated_at"),
        "warnings": list(stored_valuation.get("warnings") or []),
        "corporate_action_review": _basis_review_flags(current, prior, annual),
    }, ""

def _external_forensics(
    symbol: str,
    price: float,
    day_move: float | None,
    meta: dict[str, str],
    headers: dict[str, str],
    provider_calls: dict[str, int] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    provider_calls = provider_calls if provider_calls is not None else {}
    provider_calls["sec_submissions"] = int(provider_calls.get("sec_submissions") or 0) + 1
    submission = _sec_submission(meta["cik"], headers)
    fiscal_year_end = str(submission.get("fiscalYearEnd") or "")
    sic_description = str(submission.get("sicDescription") or "")
    company_type = infer_company_type("", sic_description)
    provider_calls["sec_companyfacts"] = int(provider_calls.get("sec_companyfacts") or 0) + 1
    facts = _sec_companyfacts(meta["cik"], headers)
    annual = _annual_history(facts, fiscal_year_end, sic_description)
    quarters = _quarter_history(facts, fiscal_year_end, sic_description)
    if len(annual) < 2:
        return None, "FILED HISTORY INSUFFICIENT"
    current = _ttm(quarters, 0)
    prior = _ttm(quarters, 4)
    if current is None or prior is None:
        return None, "TTM INVALID / INCOMPLETE"

    valuation = _valuation_from_history(annual, current, prior, price, company_type)
    if valuation.get("base") is None or valuation.get("gap_pct") is None:
        return None, "BASE / GAP UNAVAILABLE"

    snapshot = _operating_snapshot(current, prior)
    signals, long_score, short_score = _signals(snapshot, day_move)
    return {
        "bear": valuation.get("bear"),
        "base": valuation["base"],
        "bull": valuation.get("bull"),
        "gap_pct": valuation["gap_pct"],
        "quality": str(valuation.get("quality") or "DATA_WARNING").upper(),
        "valuation_methods": int(valuation.get("valuation_methods") or 0),
        "company_type": company_type,
        "sic_description": sic_description,
        "snapshot": snapshot,
        "economic_reality": dict(snapshot.get("economic_reality") or {}),
        "accounting_context": list((snapshot.get("economic_reality") or {}).get("flags") or []),
        "signals": signals,
        "long_score": long_score,
        "short_score": short_score,
        "source": "SEC COMPANYFACTS + CANONICAL MARKET FORENSICS VALUATION",
        "data_freshness": current.get("period_end"),
        "materialized_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        "warnings": list(valuation.get("warnings") or []),
        "corporate_action_review": _basis_review_flags(current, prior, annual, submission),
    }, ""

def enrich_forensic_candidates(
    user_id: int,
    candidates: list[dict[str, Any]],
    local_context: dict[str, dict[str, Any]],
    errors: list[str],
    provider_calls: dict[str, int] | None = None,
    *,
    limit: int = FORENSIC_ENRICH_LIMIT,
    rejection_log: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Stage 2: enrich exactly the bounded Stage-1 finalists.

    No mover/activity re-ranking happens here. SEC work is sequential, bounded,
    and only performed for unknown finalists. Companyfacts is never called for
    the Stage-0 universe or the full Stage-1 batch.
    """
    provider_calls = provider_calls if provider_calls is not None else {}
    selected = list(candidates or [])[:max(0, int(limit))]
    known = [row for row in selected if local_context.get(str(row.get("ticker") or "").upper())]
    unknown = [row for row in selected if not local_context.get(str(row.get("ticker") or "").upper())]
    diagnostics: dict[str, int] = {}
    rejection_log = rejection_log if rejection_log is not None else []

    def reject(symbol: str, reason: str, detail: str = "") -> None:
        diagnostics[reason] = diagnostics.get(reason, 0) + 1
        if len(rejection_log) < 24:
            rejection_log.append({
                "ticker": str(symbol or "").upper(),
                "stage": "STAGE2 ENRICHMENT",
                "reason": reason,
                "detail": detail,
            })

    headers = _sec_headers(user_id)
    ticker_map: dict[str, dict[str, str]] = {}
    if unknown and headers:
        try:
            provider_calls["sec_ticker_map"] = int(provider_calls.get("sec_ticker_map") or 0) + 1
            ticker_map = _sec_ticker_map(headers)
        except Exception as exc:
            errors.append(f"Forensic SEC ticker map: {type(exc).__name__}")
    elif unknown and not headers:
        errors.append("Forensic SEC enrichment unavailable: configure SEC User-Agent in Settings.")

    out: dict[str, dict[str, Any]] = {}
    for row in known:
        symbol = str(row.get("ticker") or "").upper()
        price = _num(row.get("price"))
        if price is None:
            reject(symbol, "PRICE UNKNOWN")
            continue
        result, reason = _local_forensics(local_context.get(symbol) or {}, price, _num(row.get("move_pct")))
        if result:
            out[symbol] = result
        else:
            reject(symbol, reason or "LOCAL FORENSIC DATA INSUFFICIENT")

    for row in unknown:
        symbol = str(row.get("ticker") or "").upper()
        price = _num(row.get("price"))
        meta = ticker_map.get(symbol)
        if price is None:
            reject(symbol, "PRICE UNKNOWN")
            continue
        if not headers:
            reject(symbol, "SEC NOT CONFIGURED")
            continue
        if not meta:
            reject(symbol, "SEC TICKER UNRESOLVED")
            continue
        try:
            result, reason = _external_forensics(
                symbol, price, _num(row.get("move_pct")), meta, headers, provider_calls,
            )
        except Exception as exc:
            errors.append(f"{symbol} forensic enrichment: {type(exc).__name__}")
            reject(symbol, "SEC ENRICHMENT ERROR")
            continue
        if result:
            result["sec_name"] = meta.get("name") or symbol
            out[symbol] = result
        else:
            reject(symbol, reason or "FORENSIC DATA INSUFFICIENT")
    return out, diagnostics

def discovery_opportunity(result: dict[str, Any]) -> dict[str, Any] | None:
    """Classify research leads without pretending Discovery is final validation.

    P1 requires a large intrinsic gap plus aligned operating confirmation.
    P2 accepts a decision-grade valuation gap when operations are stable or aligned.
    WATCH keeps emerging or verification-needed dislocations visible for Research.
    """
    gap = _num(result.get("gap_pct"))
    if gap is None or abs(gap) < FORENSIC_WATCH_EDGE_PCT:
        return None

    side = "LONG" if gap > 0 else "SHORT"
    long_score = int(result.get("long_score") or 0)
    short_score = int(result.get("short_score") or 0)
    aligned_score = long_score if side == "LONG" else short_score
    opposing_score = short_score if side == "LONG" else long_score
    aligned_confirmation = aligned_score >= FORENSIC_CONFIRM_SCORE and aligned_score > opposing_score
    material_contradiction = opposing_score >= FORENSIC_CONFIRM_SCORE and opposing_score > aligned_score
    quality = str(result.get("quality") or "DATA_WARNING").upper()
    methods = int(result.get("valuation_methods") or 0)
    decision_grade = quality == "INTRINSIC" and methods >= 2
    edge = abs(gap)
    economic = dict(result.get("economic_reality") or {})
    if economic.get("material_unresolved"):
        return {
            "side": side, "priority": "WATCH", "priority_rank": 3, "score": aligned_score,
            "reason": "Valuation dislocation exists, but material accounting/debt classification remains unresolved; Research must verify Economic Reality before P1/P2.",
            "operating_state": "ACCOUNTING REVIEW",
        }

    if decision_grade and edge >= FORENSIC_STRONG_EDGE_PCT and aligned_confirmation:
        return {
            "side": side, "priority": "P1", "priority_rank": 1, "score": aligned_score,
            "reason": "Strong intrinsic valuation edge with aligned filed operating confirmation.",
            "operating_state": "CONFIRMING",
        }

    if decision_grade and edge >= FORENSIC_EDGE_PCT and not material_contradiction:
        return {
            "side": side, "priority": "P2", "priority_rank": 2, "score": aligned_score,
            "reason": (
                "Intrinsic valuation edge with confirming operations."
                if aligned_confirmation
                else "Intrinsic valuation edge with no material operating contradiction."
            ),
            "operating_state": "CONFIRMING" if aligned_confirmation else "STABLE / NOT CONTRADICTED",
        }

    if edge >= FORENSIC_EDGE_PCT or aligned_confirmation:
        if edge < FORENSIC_EDGE_PCT:
            reason = "Emerging 12–20% valuation edge with aligned operating confirmation."
        elif not decision_grade:
            reason = "Large valuation dislocation; valuation evidence still needs verification."
        elif material_contradiction:
            reason = "Large valuation dislocation, but current operating evidence conflicts."
        else:
            reason = "Emerging valuation setup that merits Research verification."
        return {
            "side": side, "priority": "WATCH", "priority_rank": 3, "score": aligned_score,
            "reason": reason,
            "operating_state": "CONFLICTING" if material_contradiction else ("CONFIRMING" if aligned_confirmation else "UNCONFIRMED"),
        }
    return None


def forensic_side(result: dict[str, Any]) -> tuple[str, str, int] | None:
    """Compatibility wrapper for callers that only accept qualified P1/P2 leads."""
    opportunity = discovery_opportunity(result)
    if not opportunity or opportunity["priority"] == "WATCH":
        return None
    return opportunity["side"], opportunity["priority"], int(opportunity.get("score") or 0)


__all__ = [
    "FORENSIC_WATCH_EDGE_PCT", "FORENSIC_EDGE_PCT", "FORENSIC_STRONG_EDGE_PCT",
    "FORENSIC_CONFIRM_SCORE", "FORENSIC_ENRICH_LIMIT", "FORENSIC_ENRICH_PER_SIDE",
    "enrich_forensic_candidates", "discovery_opportunity", "forensic_side", "_basis_review_flags",
]
