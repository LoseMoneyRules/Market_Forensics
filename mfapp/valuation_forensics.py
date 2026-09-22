from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite
from statistics import median
from typing import Any

from .core_models import (
    Catalyst, Company, Coverage, HistoricalPrice, MonitoringHistory, MonitoringRule,
    Security, Source, ValuationModel,
)
from .current_financials import annual_rows, current_row
from .data_providers import latest_snapshot
from .extensions import db
from .historical_data import preferred_provider, price_on_or_after
from .macro_context import macro_context
from .research_basis import latest_financial_basis
from .valuation_engine import detect_operating_regime

ENGINE_VERSION = "0.3.2-integrity-v2"

# These coefficients deliberately create a bounded explanatory bridge, not a
# fitted valuation model. They are visible in the output so the analyst can
# distinguish measured facts from directional analytical translation.
BRIDGE_POLICY = {
    "growth_x_per_ppt": 0.20,
    "operating_margin_x_per_ppt": 0.15,
    "fcf_margin_x_per_ppt": 0.12,
    "roic_x_per_ppt": 0.10,
    "cash_conversion_x_per_turn": 1.25,
    "leverage_x_per_turn": -0.65,
    "dilution_x_per_ppt": -0.10,
    "working_capital_x_per_ppt": -0.08,
    "rate_x_per_100bps": -1.25,
}


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _pct_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    return (current / prior - 1.0) * 100.0


def _quantile(values: list[float], q: float) -> float | None:
    rows = sorted(x for x in (_n(v) for v in values) if x is not None)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    pos = (len(rows) - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(len(rows) - 1, lo + 1)
    if lo == hi:
        return rows[lo]
    weight = pos - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def _primary_security(company_id: int) -> Security | None:
    return (
        Security.query.filter_by(company_id=company_id, active=True)
        .order_by(Security.is_primary.desc(), Security.id.asc())
        .first()
    )


def _effective_fcf(row: dict[str, Any]) -> float | None:
    # 0.3.2 integrity rule: SBC is handled through observed diluted-share
    # growth in the canonical valuation denominator. Do not subtract it again
    # from reported FCF in relative-value evidence.
    return _n(row.get("fcf"))


def _economics_from_row(row: dict[str, Any], *, price: float | None = None) -> dict[str, Any]:
    metrics = dict(row.get("metrics") or {})
    revenue = _n(row.get("revenue"))
    gross_profit = _n(row.get("gross_profit"))
    operating_income = _n(row.get("operating_income"))
    net_income = _n(row.get("net_income"))
    fcf = _effective_fcf(row)
    economic = dict((row.get("quality") or {}).get("economic_reality") or {})
    depreciation_amortization = _n(economic.get("depreciation_amortization"))
    ebitda = (operating_income + depreciation_amortization) if operating_income is not None and depreciation_amortization is not None else None
    shares = _n(row.get("diluted_shares")) or _n(row.get("shares_outstanding"))
    net_debt = _n(metrics.get("economic_net_debt"))
    if net_debt is None and not bool(metrics.get("economic_reality_unresolved")):
        debt = _n(row.get("debt"))
        cash = _n(row.get("cash"))
        if debt is not None and cash is not None:
            net_debt = debt - cash
    market_cap = price * shares if price not in (None, 0) and shares not in (None, 0) else None
    ev = market_cap + net_debt if market_cap is not None and net_debt is not None else None

    def margin(numerator):
        return (numerator / revenue * 100.0) if numerator is not None and revenue not in (None, 0) else None

    return {
        "period": row.get("period_label") or row.get("period_type"),
        "period_type": row.get("period_type"),
        "fiscal_year": row.get("fiscal_year"),
        "period_end": row.get("period_end"),
        "filed_at": row.get("filed_at"),
        "price": price,
        "shares": shares,
        "market_cap": market_cap,
        "enterprise_value": ev,
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "ebitda": ebitda,
        "net_income": net_income,
        "fcf": fcf,
        "revenue_growth_pct": _n(metrics.get("revenue_growth_pct")),
        "gross_margin_pct": _n(metrics.get("gross_margin_pct")) if _n(metrics.get("gross_margin_pct")) is not None else margin(gross_profit),
        "operating_margin_pct": _n(metrics.get("operating_margin_pct")) if _n(metrics.get("operating_margin_pct")) is not None else margin(operating_income),
        "fcf_margin_pct": _n(metrics.get("fcf_margin_pct")) if _n(metrics.get("fcf_margin_pct")) is not None else margin(fcf),
        "roic_pct": _n(metrics.get("roic_pct")),
        "cash_conversion": _n(metrics.get("cfo_to_net_income")),
        "net_debt_to_fcf": _n(metrics.get("net_debt_to_fcf")),
        "inventory_to_revenue_pct": _n(metrics.get("inventory_to_revenue_pct")),
        "receivables_to_revenue_pct": _n(metrics.get("receivables_to_revenue_pct")),
        "share_count_growth_pct": _n(metrics.get("share_count_growth_pct")),
        "capex_to_revenue_pct": (
            abs(_n(row.get("capex"))) / revenue * 100.0
            if _n(row.get("capex")) is not None and revenue not in (None, 0) else None
        ),
        "net_debt": net_debt,
        "pe": market_cap / net_income if market_cap not in (None, 0) and net_income is not None and net_income > 0 else None,
        "ev_ebit": ev / operating_income if ev not in (None, 0) and operating_income is not None and operating_income > 0 else None,
        "ev_ebitda": ev / ebitda if ev not in (None, 0) and ebitda is not None and ebitda > 0 else None,
        "ev_sales": ev / revenue if ev not in (None, 0) and revenue not in (None, 0) else None,
        "p_fcf": market_cap / fcf if market_cap not in (None, 0) and fcf is not None and fcf > 0 else None,
        "fcf_yield_pct": fcf / market_cap * 100.0 if market_cap not in (None, 0) and fcf is not None else None,
    }


def _historical_observations(company_id: int, security_id: int, years: int = 10) -> list[dict[str, Any]]:
    provider = preferred_provider(security_id)
    if not provider:
        return []
    cutoff = date.today().replace(year=max(1900, date.today().year - max(1, years)))
    rows = list(reversed(annual_rows(company_id, max(12, years + 2))))
    out: list[dict[str, Any]] = []
    for row in rows:
        filed = row.get("filed_at")
        try:
            filed_day = date.fromisoformat(str(filed)[:10]) if filed else None
        except Exception:
            filed_day = None
        if filed_day is None or filed_day < cutoff:
            continue
        market = price_on_or_after(security_id, filed_day, 14, provider=provider)
        if market is None:
            continue
        raw_price = _n(market.close_raw)
        if raw_price in (None, 0):
            continue
        economics = _economics_from_row(row, price=raw_price)
        economics.update({
            "anchor_date": market.trade_date.isoformat(),
            "filing_date": filed_day.isoformat(),
            "price_provider": provider,
            "point_in_time": True,
        })
        out.append(economics)
    return out


def _multiple_stats(observations: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "pe": "P/E",
        "ev_ebit": "EV / EBIT",
        "ev_ebitda": "EV / EBITDA",
        "ev_sales": "EV / Sales",
        "p_fcf": "P / FCF",
        "fcf_yield_pct": "FCF Yield",
    }
    result = {}
    today = date.today()
    for key, label in labels.items():
        current_value = _n(current.get(key))
        horizons = {}
        for years in (3, 5, 10):
            cutoff_year = today.year - years
            vals = [
                _n(row.get(key)) for row in observations
                if _n(row.get(key)) is not None
                and int(str(row.get("filing_date") or "0000")[:4] or 0) >= cutoff_year
            ]
            vals = [v for v in vals if v is not None]
            horizons[f"{years}y"] = {
                "sample_size": len(vals),
                "median": median(vals) if vals else None,
                "low": min(vals) if vals else None,
                "high": max(vals) if vals else None,
                "p10": _quantile(vals, .10),
                "p25": _quantile(vals, .25),
                "p75": _quantile(vals, .75),
                "p90": _quantile(vals, .90),
            }
        reference = horizons["5y"] if horizons["5y"]["sample_size"] >= 3 else horizons["10y"]
        vals = [
            _n(row.get(key)) for row in observations
            if _n(row.get(key)) is not None
        ]
        vals = [v for v in vals if v is not None]
        percentile = None
        if current_value is not None and vals:
            percentile = sum(1 for v in vals if v <= current_value) / len(vals) * 100.0
        ref_med = _n(reference.get("median"))
        if current_value is None or ref_med in (None, 0):
            regime = "UNAVAILABLE"
        else:
            delta = current_value / ref_med - 1.0
            regime = "COMPRESSED" if delta <= -.15 else "EXPANDED" if delta >= .15 else "IN LINE"
        result[key] = {
            "key": key,
            "label": label,
            "current": current_value,
            "horizons": horizons,
            "reference": "5Y" if horizons["5y"]["sample_size"] >= 3 else "10Y",
            "reference_median": ref_med,
            "percentile": percentile,
            "regime": regime,
            "basis": "POINT_IN_TIME_POST_FILING_ANCHORS",
        }
    return result


def _historical_driver_medians(observations: list[dict[str, Any]]) -> dict[str, float | None]:
    keys = (
        "revenue_growth_pct", "operating_margin_pct", "fcf_margin_pct", "roic_pct",
        "cash_conversion", "net_debt_to_fcf", "share_count_growth_pct",
        "inventory_to_revenue_pct", "receivables_to_revenue_pct",
    )
    return {
        key: median(vals) if (vals := [_n(r.get(key)) for r in observations if _n(r.get(key)) is not None]) else None
        for key in keys
    }


def _bounded_effect(delta: float | None, coefficient: float, cap: float) -> float | None:
    return max(-cap, min(cap, delta * coefficient)) if delta is not None else None


def _multiple_bridge(
    current: dict[str, Any],
    multiple_stats: dict[str, Any],
    observations: list[dict[str, Any]],
    macro: dict[str, Any],
) -> dict[str, Any]:
    # Thin-margin businesses are not anchored to sales multiples. Prefer
    # EV/EBITDA / cash-flow evidence when EBITDA is available; otherwise use
    # the first company-specific multiple with sufficient point-in-time history.
    thin_margin = (
        _n(current.get("operating_margin_pct")) is not None
        and abs(_n(current.get("operating_margin_pct")) or 0.0) <= 4.0
    )
    order = (
        ("ev_ebitda", "p_fcf", "pe", "ev_ebit", "ev_sales")
        if thin_margin
        else ("pe", "ev_ebitda", "ev_ebit", "ev_sales", "p_fcf")
    )
    preferred = next((
        key for key in order
        if _n((multiple_stats.get(key) or {}).get("current")) is not None
        and _n((multiple_stats.get(key) or {}).get("reference_median")) is not None
    ), None)
    if not preferred:
        return {
            "available": False,
            "reason": "No current multiple has enough point-in-time history for a bridge.",
            "items": [],
            "policy": BRIDGE_POLICY,
        }

    base = _n(multiple_stats[preferred].get("reference_median"))
    historical = _historical_driver_medians(observations)
    items: list[dict[str, Any]] = []

    def add(label, key, coeff, cap, direction="higher"):
        cur = _n(current.get(key)); hist = _n(historical.get(key))
        delta = cur - hist if cur is not None and hist is not None else None
        effect = _bounded_effect(delta, coeff, cap)
        items.append({
            "label": label, "driver": key, "current": cur, "historical": hist,
            "delta": delta, "estimated_multiple_effect": effect,
            "status": "QUANTIFIED" if effect is not None else "MISSING",
            "basis": "BOUNDED_DIRECTIONAL_BRIDGE",
            "direction": direction,
        })

    add("Revenue growth", "revenue_growth_pct", BRIDGE_POLICY["growth_x_per_ppt"], 5.0)
    add("Operating margin", "operating_margin_pct", BRIDGE_POLICY["operating_margin_x_per_ppt"], 4.0)
    add("FCF margin", "fcf_margin_pct", BRIDGE_POLICY["fcf_margin_x_per_ppt"], 3.5)
    add("ROIC", "roic_pct", BRIDGE_POLICY["roic_x_per_ppt"], 3.0)
    add("Cash conversion", "cash_conversion", BRIDGE_POLICY["cash_conversion_x_per_turn"], 2.0)
    add("Leverage", "net_debt_to_fcf", BRIDGE_POLICY["leverage_x_per_turn"], 3.0, "lower")
    add("Share dilution", "share_count_growth_pct", BRIDGE_POLICY["dilution_x_per_ppt"], 2.0, "lower")

    wc_current = None
    wc_hist = None
    cur_inv, cur_ar = _n(current.get("inventory_to_revenue_pct")), _n(current.get("receivables_to_revenue_pct"))
    hist_inv, hist_ar = _n(historical.get("inventory_to_revenue_pct")), _n(historical.get("receivables_to_revenue_pct"))
    if cur_inv is not None or cur_ar is not None:
        wc_current = sum(v for v in (cur_inv, cur_ar) if v is not None)
    if hist_inv is not None or hist_ar is not None:
        wc_hist = sum(v for v in (hist_inv, hist_ar) if v is not None)
    wc_delta = wc_current - wc_hist if wc_current is not None and wc_hist is not None else None
    items.append({
        "label": "Working-capital drag", "driver": "inventory_plus_receivables_to_revenue",
        "current": wc_current, "historical": wc_hist, "delta": wc_delta,
        "estimated_multiple_effect": _bounded_effect(wc_delta, BRIDGE_POLICY["working_capital_x_per_ppt"], 2.5),
        "status": "QUANTIFIED" if wc_delta is not None else "MISSING",
        "basis": "BOUNDED_DIRECTIONAL_BRIDGE", "direction": "lower",
    })

    rate = next((row for row in list(macro.get("factors") or []) if row.get("key") == "rates"), None)
    if rate:
        change_12m = _n(rate.get("change_12m"))
        # macro_context currently exposes 3m in its normalized factor payload.
        # If 12m is absent we deliberately keep the bridge qualitative.
        items.append({
            "label": "Risk-free rate regime", "driver": "rates",
            "current": _n(rate.get("value")), "historical": None, "delta": change_12m,
            "estimated_multiple_effect": (
                _bounded_effect(change_12m / 1.0, BRIDGE_POLICY["rate_x_per_100bps"], 3.5)
                if change_12m is not None else None
            ),
            "status": "QUANTIFIED" if change_12m is not None else "QUALITATIVE",
            "basis": "FRED_STORED_MACRO_CONTEXT",
            "detail": f"{rate.get('label') or 'Rates'} {rate.get('trend') or 'unknown'} as of {rate.get('as_of') or 'unknown'}.",
        })

    quantified = [row["estimated_multiple_effect"] for row in items if row.get("estimated_multiple_effect") is not None]
    justified = base + sum(quantified) if base is not None else None
    if justified is not None:
        justified = max(.1, justified)
    uncertainty = max(1.5, abs(justified or 0) * .10)
    current_multiple = _n(multiple_stats[preferred].get("current"))
    return {
        "available": True,
        "multiple_key": preferred,
        "multiple_label": multiple_stats[preferred].get("label"),
        "historical_reference": base,
        "current_multiple": current_multiple,
        "items": items,
        "quantified_item_count": len(quantified),
        "justified_current": justified,
        "justified_low": max(.1, justified - uncertainty) if justified is not None else None,
        "justified_high": justified + uncertainty if justified is not None else None,
        "unexplained_gap": current_multiple - justified if current_multiple is not None and justified is not None else None,
        "policy": BRIDGE_POLICY,
        "precision_warning": "Bridge effects are bounded directional estimates, not a regression or a claim of exact causal x-points.",
    }


def _status_against_target(current: float | None, target: float | None, prior: float | None, higher_is_better: bool = True) -> str:
    if current is None or target is None:
        return "UNAVAILABLE"
    tolerance = max(abs(target) * .08, .5)
    met = current >= target - tolerance if higher_is_better else current <= target + tolerance
    if met:
        return "MET"
    gap = (target - current) if higher_is_better else (current - target)
    partial = gap <= max(abs(target) * .20, 1.5)
    if prior is not None:
        improving = current > prior if higher_is_better else current < prior
        worsening = current < prior if higher_is_better else current > prior
        if worsening and not partial:
            return "DETERIORATING"
        if improving:
            return "PARTIALLY MET"
    return "PARTIALLY MET" if partial else "NOT MET"


def _rerating_conditions(current: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    hist = _historical_driver_medians(observations)
    prior = observations[-1] if observations else {}
    specs = [
        ("Revenue growth", "revenue_growth_pct", True),
        ("Operating margin", "operating_margin_pct", True),
        ("FCF margin", "fcf_margin_pct", True),
        ("ROIC", "roic_pct", True),
        ("Cash conversion", "cash_conversion", True),
        ("Leverage", "net_debt_to_fcf", False),
        ("Share dilution", "share_count_growth_pct", False),
    ]
    rows = []
    for label, key, higher in specs:
        cur = _n(current.get(key)); target = _n(hist.get(key)); previous = _n(prior.get(key))
        rows.append({
            "label": label, "metric": key, "current": cur, "target": target,
            "historical_reference": target, "prior_anchor": previous,
            "status": _status_against_target(cur, target, previous, higher),
            "direction": "AT OR ABOVE" if higher else "AT OR BELOW",
            "basis": "HISTORICAL_POINT_IN_TIME_MEDIAN",
        })
    usable = [row for row in rows if row["status"] != "UNAVAILABLE"]
    met = sum(1 for row in usable if row["status"] == "MET")
    deteriorating = sum(1 for row in usable if row["status"] == "DETERIORATING")
    return {
        "conditions": rows,
        "met": met,
        "usable": len(usable),
        "deteriorating": deteriorating,
        "completion_pct": (met / len(usable) * 100.0) if usable else None,
    }


def _old_multiple_defensibility(bridge: dict[str, Any], rerating: dict[str, Any]) -> dict[str, Any]:
    if not bridge.get("available"):
        return {"state": "UNRESOLVED", "reason": bridge.get("reason") or "Historical bridge unavailable."}
    completion = _n(rerating.get("completion_pct"))
    hist = _n(bridge.get("historical_reference"))
    justified = _n(bridge.get("justified_current"))
    if completion is None or hist in (None, 0) or justified is None:
        state = "UNRESOLVED"
    elif completion >= 75 and justified >= hist * .90:
        state = "OLD MULTIPLE STILL ECONOMICALLY DEFENSIBLE"
    elif completion >= 45 or justified >= hist * .75:
        state = "PARTIAL RE-RATING DEFENSIBLE"
    elif justified < hist * .65:
        state = "HISTORICAL MULTIPLE NO LONGER A VALID ANCHOR WITHOUT STRUCTURAL IMPROVEMENT"
    else:
        state = "OLD MULTIPLE UNLIKELY WITHOUT STRUCTURAL IMPROVEMENT"
    return {
        "state": state,
        "condition_completion_pct": completion,
        "historical_multiple": hist,
        "justified_current_multiple": justified,
        "deteriorating_conditions": int(rerating.get("deteriorating") or 0),
    }


def _market_implied_expectations(current: dict[str, Any], stats: dict[str, Any], model: ValuationModel | None) -> dict[str, Any]:
    drivers = []
    market_cap = _n(current.get("market_cap"))
    ev = _n(current.get("enterprise_value"))
    shares = _n(current.get("shares"))
    base_inputs = {}
    if model:
        try:
            base_row = next((row for row in model.scenarios if str(row.name or "").upper() == "BASE"), None)
            base_inputs = dict(base_row.inputs or {}) if base_row else {}
        except Exception:
            base_inputs = {}

    pe_ref = _n((stats.get("pe") or {}).get("reference_median"))
    earnings = _n(current.get("net_income"))
    if market_cap not in (None, 0) and earnings not in (None, 0) and pe_ref not in (None, 0):
        required_earnings = market_cap / pe_ref
        drivers.append({
            "metric": "Earnings vs historical P/E",
            "market_implied_change_pct": _pct_change(required_earnings, earnings),
            "our_base": _n(base_inputs.get("growth")) * 100.0 if _n(base_inputs.get("growth")) is not None else None,
            "basis": f"Current market cap at historical {pe_ref:.1f}x P/E",
        })

    evs_ref = _n((stats.get("ev_sales") or {}).get("reference_median"))
    revenue = _n(current.get("revenue"))
    if ev not in (None, 0) and revenue not in (None, 0) and evs_ref not in (None, 0):
        required_revenue = ev / evs_ref
        drivers.append({
            "metric": "Revenue vs historical EV/Sales",
            "market_implied_change_pct": _pct_change(required_revenue, revenue),
            "our_base": _n(base_inputs.get("growth")) * 100.0 if _n(base_inputs.get("growth")) is not None else None,
            "basis": f"Current EV at historical {evs_ref:.2f}x EV/Sales",
        })

    pfcf_ref = _n((stats.get("p_fcf") or {}).get("reference_median"))
    fcf = _n(current.get("fcf"))
    if market_cap not in (None, 0) and fcf not in (None, 0) and pfcf_ref not in (None, 0):
        required_fcf = market_cap / pfcf_ref
        drivers.append({
            "metric": "FCF vs historical P/FCF",
            "market_implied_change_pct": _pct_change(required_fcf, fcf),
            "our_base": _n(base_inputs.get("fcf_margin")) * 100.0 if _n(base_inputs.get("fcf_margin")) is not None else None,
            "basis": f"Current market cap at historical {pfcf_ref:.1f}x P/FCF",
        })

    return {
        "available": bool(drivers),
        "drivers": drivers,
        "interpretation": "These are reverse-engineered operating requirements at historical reference multiples; they are not Street consensus.",
    }


def _peer_metric_row(company: Company, user_id: int | None = None) -> dict[str, Any] | None:
    current = current_row(company.id)
    security = _primary_security(company.id)
    if not current or not security:
        return None
    market = latest_snapshot(security.id)
    price = _n(market.price) if market else None
    row = _economics_from_row(current, price=price)
    row.update({
        "company_id": company.id,
        "ticker": security.ticker,
        "name": company.display_name,
        "sector": company.sector or "",
        "industry": company.industry or "",
        "country": company.country or "",
        "market_as_of": market.as_of.isoformat() if market and market.as_of else None,
    })
    history = annual_rows(company.id, 3)
    if len(history) >= 2:
        latest, prior = history[0], history[1]
        latest_eps = (
            _n(latest.get("net_income")) / _n(latest.get("diluted_shares"))
            if _n(latest.get("net_income")) is not None and _n(latest.get("diluted_shares")) not in (None, 0) else None
        )
        prior_eps = (
            _n(prior.get("net_income")) / _n(prior.get("diluted_shares"))
            if _n(prior.get("net_income")) is not None and _n(prior.get("diluted_shares")) not in (None, 0) else None
        )
        row["eps_growth_pct"] = _pct_change(latest_eps, prior_eps)
    else:
        row["eps_growth_pct"] = None
    return row


def _sic_meta(company_id: int) -> tuple[str, str]:
    source = (
        Source.query.filter_by(company_id=company_id, provider="SEC", source_type="COMPANYFACTS")
        .order_by(Source.retrieved_at.desc(), Source.id.desc()).first()
    )
    meta = dict((source.meta or {}) if source else {})
    sic = str(meta.get("sic") or "").zfill(4) if meta.get("sic") else ""
    return sic, str(meta.get("sic_description") or "")


def _similarity(target: dict[str, Any], peer: dict[str, Any], target_company: Company, peer_company: Company, target_sic: str, peer_sic: str) -> dict[str, Any]:
    score = 0.0
    evidence = []
    max_score = 0.0

    same_sic = bool(target_sic and peer_sic and target_sic == peer_sic)
    same_sic_division = bool(target_sic and peer_sic and target_sic[:2] == peer_sic[:2])
    same_industry = bool(target_company.industry and target_company.industry == peer_company.industry)
    same_sector = bool(target_company.sector and target_company.sector == peer_company.sector)
    structural_match = same_sic or same_sic_division or same_industry

    def categorical(points, matched, label):
        nonlocal score, max_score
        max_score += points
        if matched:
            score += points
            evidence.append(label)

    if target_sic and peer_sic:
        categorical(25, same_sic, "same SIC")
        categorical(8, same_sic_division, "same SIC division")
    categorical(15, same_industry, "same industry")
    categorical(8, same_sector, "same sector")
    categorical(4, bool(target_company.country and target_company.country == peer_company.country), "same country")

    target_cap, peer_cap = _n(target.get("market_cap")), _n(peer.get("market_cap"))
    if target_cap not in (None, 0) and peer_cap not in (None, 0):
        max_score += 10
        ratio = max(target_cap, peer_cap) / min(target_cap, peer_cap)
        if ratio <= 2: score += 10; evidence.append("similar size")
        elif ratio <= 5: score += 6
        elif ratio <= 10: score += 3

    for key, label, points, tolerance in (
        ("revenue_growth_pct", "growth", 8, 7.5),
        ("operating_margin_pct", "operating margin", 8, 6.0),
        ("fcf_margin_pct", "FCF margin", 6, 6.0),
        ("roic_pct", "ROIC", 6, 8.0),
        ("net_debt_to_fcf", "leverage", 4, 1.5),
        ("capex_to_revenue_pct", "capital intensity", 4, 4.0),
    ):
        tv, pv = _n(target.get(key)), _n(peer.get(key))
        if tv is None or pv is None:
            continue
        max_score += points
        diff = abs(tv - pv)
        if diff <= tolerance:
            score += points
            evidence.append(f"similar {label}")
        elif diff <= tolerance * 2:
            score += points * .5

    normalized = score / max_score * 100.0 if max_score else 0.0

    # Economic resemblance cannot by itself manufacture a peer relationship.
    # At least one structural industry/SIC relation is mandatory for any peer
    # that can enter the peer median or peer-adjusted valuation.
    if not structural_match:
        tier = "REFERENCE ONLY" if normalized >= 30 else "NOT COMPARABLE"
    elif same_sic and normalized >= 60:
        tier = "CLOSE PEER"
    elif normalized >= 55:
        tier = "CLOSE PEER"
    elif normalized >= 40:
        tier = "PARTIAL PEER"
    else:
        tier = "REFERENCE ONLY"

    return {
        "score": round(normalized, 1),
        "tier": tier,
        "evidence": evidence,
        "structural_match": structural_match,
        "same_sic": same_sic,
        "same_sic_division": same_sic_division,
        "same_industry": same_industry,
    }


def _peer_adjustment(target: dict[str, Any], peers: list[dict[str, Any]], multiple_key: str) -> dict[str, Any]:
    eligible = [p for p in peers if p.get("comparability") in {"CLOSE PEER", "PARTIAL PEER"} and _n(p.get(multiple_key)) is not None]
    vals = [_n(p.get(multiple_key)) for p in eligible if _n(p.get(multiple_key)) is not None]
    if len(vals) < 3:
        return {
            "available": False, "multiple_key": multiple_key, "peer_count": len(eligible),
            "reason": "Fewer than three structurally comparable close/partial peers have a usable comparable multiple.",
            "adjustments": [],
        }
    peer_med = median(vals)
    adjustments = []
    specs = [
        ("Revenue growth", "revenue_growth_pct", .15, 3.0, True),
        ("Operating margin", "operating_margin_pct", .12, 2.5, True),
        ("FCF margin", "fcf_margin_pct", .10, 2.0, True),
        ("ROIC", "roic_pct", .08, 2.0, True),
        ("Leverage", "net_debt_to_fcf", -.50, 2.0, False),
        ("Cash conversion", "cash_conversion", .75, 1.5, True),
    ]
    for label, key, coeff, cap, higher in specs:
        tv = _n(target.get(key))
        peer_vals = [_n(p.get(key)) for p in eligible if _n(p.get(key)) is not None]
        if tv is None or not peer_vals:
            adjustments.append({"label": label, "status": "MISSING", "effect": None})
            continue
        med = median(peer_vals)
        delta = tv - med
        effect = _bounded_effect(delta, coeff, cap)
        adjustments.append({
            "label": label, "metric": key, "target": tv, "peer_median": med,
            "delta": delta, "effect": effect, "status": "QUANTIFIED",
            "direction": "higher is better" if higher else "lower is better",
        })
    effects = [row["effect"] for row in adjustments if row.get("effect") is not None]
    justified = max(.1, peer_med + sum(effects))
    uncertainty = max(1.0, justified * .08)
    current_multiple = _n(target.get(multiple_key))
    return {
        "available": True,
        "multiple_key": multiple_key,
        "peer_count": len(eligible),
        "peer_median": peer_med,
        "adjustments": adjustments,
        "justified_multiple": justified,
        "justified_low": max(.1, justified - uncertainty),
        "justified_high": justified + uncertainty,
        "current_multiple": current_multiple,
        "relative_gap_pct": ((justified / current_multiple - 1.0) * 100.0) if current_multiple not in (None, 0) else None,
        "precision_warning": "Peer adjustments are bounded directional estimates and are not a fitted regression.",
    }


def build_peer_analysis(company_id: int, user_id: int | None = None, limit: int = 10) -> dict[str, Any]:
    company = db.session.get(Company, company_id)
    if not company:
        return {"available": False, "reason": "Company not found.", "peers": [], "comparisons": []}
    target = _peer_metric_row(company, user_id)
    if not target:
        return {"available": False, "reason": "Target fundamentals/market data unavailable.", "peers": [], "comparisons": []}
    target_sic, target_sic_description = _sic_meta(company.id)
    candidates = []
    for peer_company in Company.query.filter(Company.id != company.id).all():
        peer = _peer_metric_row(peer_company, user_id)
        if not peer:
            continue
        peer_sic, _ = _sic_meta(peer_company.id)
        similarity = _similarity(target, peer, company, peer_company, target_sic, peer_sic)
        peer.update({
            "sic": peer_sic,
            "comparability": similarity["tier"],
            "similarity_score": similarity["score"],
            "similarity_evidence": similarity["evidence"],
        })
        candidates.append(peer)
    candidates.sort(key=lambda r: (-float(r.get("similarity_score") or 0), abs((_n(r.get("market_cap")) or 0) - (_n(target.get("market_cap")) or 0))))
    eligible_candidates = [
        row for row in candidates
        if row.get("comparability") in {"CLOSE PEER", "PARTIAL PEER"}
    ]
    peers = eligible_candidates[:max(3, limit)]
    reference_candidates = [
        row for row in candidates
        if row.get("comparability") == "REFERENCE ONLY"
    ][:max(0, min(limit, 5))]

    comparisons = []
    for key, label in (
        ("pe", "P/E"), ("ev_ebit", "EV / EBIT"), ("ev_ebitda", "EV / EBITDA"),
        ("ev_sales", "EV / Sales"), ("p_fcf", "P / FCF"), ("fcf_yield_pct", "FCF Yield"),
        ("revenue_growth_pct", "Revenue growth"), ("eps_growth_pct", "EPS growth"),
        ("operating_margin_pct", "Operating margin"), ("fcf_margin_pct", "FCF margin"),
        ("roic_pct", "ROIC"), ("net_debt_to_fcf", "Leverage"),
    ):
        vals = [_n(p.get(key)) for p in peers if p.get("comparability") in {"CLOSE PEER", "PARTIAL PEER"} and _n(p.get(key)) is not None]
        comparisons.append({
            "key": key, "label": label, "target": _n(target.get(key)),
            "peer_median": median(vals) if vals else None, "sample_size": len(vals),
        })

    primary = next((
        key for key in ("pe", "ev_ebitda", "ev_ebit", "ev_sales", "p_fcf")
        if _n(target.get(key)) is not None
        and sum(
            1 for p in peers
            if p.get("comparability") in {"CLOSE PEER", "PARTIAL PEER"}
            and _n(p.get(key)) is not None
        ) >= 3
    ), "pe")
    adjusted = _peer_adjustment(target, peers, primary)
    eligible_count = sum(1 for p in peers if p.get("comparability") in {"CLOSE PEER", "PARTIAL PEER"})
    return {
        "available": bool(peers),
        "reason": "" if peers else "No structurally comparable stored peers are available; peer valuation is withheld rather than using unrelated researched names.",
        "method": "STRUCTURAL_AND_ECONOMIC_STORED_PEER_SIMILARITY",
        "peer_universe_scope": "STORED_NORMALIZED_COMPANIES_ONLY",
        "peer_selection_rule": "SIC division or same industry required; sector/economic resemblance alone cannot qualify a peer.",
        "sic": target_sic,
        "sic_description": target_sic_description,
        "target": target,
        "peers": peers,
        "reference_candidates": reference_candidates,
        "eligible_peer_count": eligible_count,
        "candidate_pool_count": len(candidates),
        "comparisons": comparisons,
        "peer_adjusted": adjusted,
        "limitations": [
            "The current peer universe is still limited to companies with stored normalized fundamentals and a stored market snapshot; unrelated researched names are never promoted into the peer median merely because they are present in the database.",
            "Recurring-revenue mix, customer concentration and competitive position are not scored unless structured evidence exists.",
            "EV/EBITDA is available only when filed D&A and the economic debt bridge are both usable.",
        ],
    }


def _multiple_to_equity_value(current: dict[str, Any], multiple_key: str, multiple: float | None) -> float | None:
    multiple = _n(multiple)
    shares = _n(current.get("shares"))
    if multiple is None or shares in (None, 0):
        return None
    if multiple_key == "pe":
        ni = _n(current.get("net_income"))
        return (ni * multiple / shares) if ni is not None and ni > 0 else None
    if multiple_key == "ev_ebit":
        op = _n(current.get("operating_income")); net_debt = _n(current.get("net_debt"))
        return ((op * multiple - net_debt) / shares) if op is not None and op > 0 and net_debt is not None else None
    if multiple_key == "ev_ebitda":
        ebitda = _n(current.get("ebitda")); net_debt = _n(current.get("net_debt"))
        return ((ebitda * multiple - net_debt) / shares) if ebitda is not None and ebitda > 0 and net_debt is not None else None
    if multiple_key == "ev_sales":
        revenue = _n(current.get("revenue")); net_debt = _n(current.get("net_debt"))
        return ((revenue * multiple - net_debt) / shares) if revenue is not None and revenue > 0 and net_debt is not None else None
    if multiple_key == "p_fcf":
        fcf = _n(current.get("fcf"))
        return (fcf * multiple / shares) if fcf is not None and fcf > 0 else None
    return None


def _triangulation(valuation: dict[str, Any], current: dict[str, Any], bridge: dict[str, Any], peers: dict[str, Any]) -> dict[str, Any]:
    intrinsic = _n(valuation.get("base"))
    hist_value = _multiple_to_equity_value(current, str(bridge.get("multiple_key") or ""), _n(bridge.get("justified_current")))
    peer_adj = dict(peers.get("peer_adjusted") or {})
    peer_value = _multiple_to_equity_value(current, str(peer_adj.get("multiple_key") or ""), _n(peer_adj.get("justified_multiple")))
    methods = [
        {"method": "INTRINSIC", "value": intrinsic, "applicable": intrinsic is not None, "basis": valuation.get("base_quality") or valuation.get("quality")},
        {"method": "HISTORICAL JUSTIFIED MULTIPLE", "value": hist_value, "applicable": hist_value is not None, "basis": bridge.get("multiple_label")},
        {"method": "PEER-ADJUSTED MULTIPLE", "value": peer_value, "applicable": peer_value is not None, "basis": peer_adj.get("multiple_key")},
    ]
    vals = [row["value"] for row in methods if row.get("value") not in (None, 0)]
    spread_pct = ((max(vals) / min(vals) - 1.0) * 100.0) if len(vals) >= 2 and min(vals) > 0 else None
    if spread_pct is None:
        state = "INSUFFICIENT METHODS"
    elif spread_pct <= 15:
        state = "CONVERGENT"
    elif spread_pct <= 35:
        state = "MIXED"
    else:
        state = "DIVERGENT"
    return {
        "methods": methods,
        "state": state,
        "spread_pct": spread_pct,
        "low": min(vals) if vals else None,
        "high": max(vals) if vals else None,
        "rule": "No arithmetic average is used. Each method remains independent and its evidence basis is shown.",
    }


def _market_read(current_price: float | None, valuation: dict[str, Any], bridge: dict[str, Any], peers: dict[str, Any], rerating: dict[str, Any], triangulation: dict[str, Any]) -> dict[str, Any]:
    base = _n(valuation.get("base"))
    intrinsic_gap = ((base / current_price - 1.0) * 100.0) if base is not None and current_price not in (None, 0) else None
    current_multiple = _n(bridge.get("current_multiple"))
    hist_low, hist_high = _n(bridge.get("justified_low")), _n(bridge.get("justified_high"))
    peer_adj = dict(peers.get("peer_adjusted") or {})
    peer_low, peer_high = _n(peer_adj.get("justified_low")), _n(peer_adj.get("justified_high"))
    peer_current = _n(peer_adj.get("current_multiple"))

    wrong = []
    right = []
    if current_multiple is not None and hist_low is not None and current_multiple < hist_low:
        wrong.append("Current multiple is below the historically justified range after the explicit driver bridge.")
    elif current_multiple is not None and hist_low is not None and hist_high is not None and hist_low <= current_multiple <= hist_high:
        right.append("Current multiple sits inside the historical driver-adjusted range.")
    if peer_current is not None and peer_low is not None and peer_current < peer_low:
        wrong.append("Current multiple is below the peer-adjusted justified range.")
    elif peer_current is not None and peer_low is not None and peer_high is not None and peer_low <= peer_current <= peer_high:
        right.append("Current multiple is consistent with the peer-adjusted range.")
    if int(rerating.get("deteriorating") or 0) > 0:
        right.append(f"{int(rerating.get('deteriorating') or 0)} re-rating condition(s) are still deteriorating.")
    if _n(rerating.get("completion_pct")) is not None and _n(rerating.get("completion_pct")) < 50:
        right.append("Fewer than half of the measurable historical re-rating conditions are currently met.")
    if intrinsic_gap is not None and intrinsic_gap >= 20:
        wrong.append(f"Intrinsic Base remains {intrinsic_gap:+.1f}% above the current price.")
    elif intrinsic_gap is not None and intrinsic_gap <= 10:
        right.append("Intrinsic Base does not show a large positive gap versus current price.")

    if len(wrong) >= 2 and not right:
        conclusion = "Market appears to price more persistent deterioration than the current stored evidence requires."
    elif right and not wrong:
        conclusion = "The lower multiple is broadly consistent with the deterioration captured by current evidence; the historical multiple is not a sufficient anchor."
    elif wrong:
        conclusion = "Relative and intrinsic evidence indicate a possible gap, but re-rating still requires operating evidence that is not fully present."
    else:
        conclusion = "The available evidence does not yet establish that current market pricing is materially wrong."
    return {
        "conclusion": conclusion,
        "market_may_be_getting_wrong": wrong,
        "market_may_be_getting_right": right,
        "intrinsic_base_gap_pct": intrinsic_gap,
        "triangulation_state": triangulation.get("state"),
    }


def _catalyst_timeline(coverage_id: int, rerating: dict[str, Any], market_read: dict[str, Any], bridge: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    windows = [
        ("0–3 months", 0, 92),
        ("3–6 months", 92, 184),
        ("6–12 months", 184, 366),
        ("12–24 months", 366, 731),
    ]
    buckets = [{"window": label, "events": [], "evidence_to_watch": []} for label, _, _ in windows]
    catalysts = Catalyst.query.filter_by(coverage_id=coverage_id).order_by(Catalyst.expected_date.asc(), Catalyst.id.asc()).all()
    for row in catalysts:
        if row.expected_date is None:
            continue
        days = (row.expected_date - today).days
        if days < 0 or days > 730:
            continue
        for idx, (_, start, end) in enumerate(windows):
            if start <= days < end:
                buckets[idx]["events"].append({
                    "title": row.title,
                    "date": row.expected_date.isoformat(),
                    "type": row.catalyst_type,
                    "direction": row.direction,
                    "status": row.status,
                    "evidence": row.evidence,
                    "source_id": row.source_id,
                })
                break
    watch = [
        f"{row['label']}: {row['status']}"
        for row in rerating.get("conditions") or []
        if row.get("status") in {"NOT MET", "PARTIALLY MET", "DETERIORATING"}
    ]
    for bucket in buckets:
        bucket["evidence_to_watch"] = watch[:5]

    near_event = bool(buckets[0]["events"])
    gap_open = bool(market_read.get("market_may_be_getting_wrong"))
    deterioration = int(rerating.get("deteriorating") or 0)
    completion = _n(rerating.get("completion_pct"))
    current_multiple = _n(bridge.get("current_multiple")); justified = _n(bridge.get("justified_current"))
    narrow_gap = current_multiple is not None and justified not in (None, 0) and abs(current_multiple / justified - 1.0) < .07

    triggered = False
    rules = MonitoringRule.query.filter_by(coverage_id=coverage_id, is_active=True).all()
    for rule in rules:
        hist = MonitoringHistory.query.filter_by(rule_id=rule.id).order_by(MonitoringHistory.observed_at.desc(), MonitoringHistory.id.desc()).first()
        if hist and str(hist.status or "").upper() in {"TRIGGERED", "FAIL", "BREACH"}:
            triggered = True
            break

    if triggered or (deterioration >= 2 and not gap_open):
        state = "THESIS BROKEN"
        reason = "Monitoring/invalidation evidence or multiple deteriorating conditions moved against the thesis."
    elif narrow_gap and gap_open:
        state = "CLOSING WINDOW"
        reason = "The valuation/multiple gap is already close to the justified range; urgency is not based on price movement alone."
    elif near_event and gap_open:
        state = "ACTIVE WINDOW"
        reason = "A material sourced catalyst is within 0–3 months while the evidence-based valuation gap remains open."
    elif gap_open and (completion is None or completion < 75):
        state = "BUILDING WINDOW"
        reason = "A possible gap exists, but more re-rating conditions need evidence before the thesis is fully confirmed."
    else:
        state = "NO URGENCY"
        reason = "No immediate evidence-driven catalyst closes the research window; additional evidence can be awaited."
    return {
        "windows": buckets,
        "decision_window": {
            "state": state,
            "reason": reason,
            "discipline": "ADD ON EVIDENCE, NOT ON PRICE.",
        },
    }


def build_valuation_forensics(
    *,
    coverage: Coverage,
    company: Company,
    security: Security,
    model: ValuationModel | None,
    valuation: dict[str, Any],
) -> dict[str, Any]:
    """Canonical stored-data re-rating / peer / timing engine.

    It is intended to run inside RECALCULATE and be materialized in Research
    cache.  No network/provider call occurs here.
    """
    market = latest_snapshot(security.id)
    price = _n(market.price) if market else _n(valuation.get("current_price"))
    current_financial = current_row(company.id) or {}
    current = _economics_from_row(current_financial, price=price)
    regime_history = list(reversed(annual_rows(company.id, 12)))
    operating_regime = detect_operating_regime(regime_history)
    regime_start_fy = operating_regime.get("start_fiscal_year")
    observations = _historical_observations(company.id, security.id, 10)
    if regime_start_fy is not None:
        observations = [
            row for row in observations
            if int(row.get("fiscal_year") or 0) >= int(regime_start_fy)
        ]
    stats = _multiple_stats(observations, current)
    macro = macro_context(company.id)
    bridge = _multiple_bridge(current, stats, observations, macro)
    rerating = _rerating_conditions(current, observations)
    defensibility = _old_multiple_defensibility(bridge, rerating)
    implied = _market_implied_expectations(current, stats, model)
    peers = build_peer_analysis(company.id, coverage.user_id)
    triangulation = _triangulation(valuation, current, bridge, peers)
    market_read = _market_read(price, valuation, bridge, peers, rerating, triangulation)
    timeline = _catalyst_timeline(coverage.id, rerating, market_read, bridge)
    basis = latest_financial_basis(company.id)

    return {
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "financial_basis": basis,
        "market_pricing": {
            "price": price,
            "market_as_of": market.as_of.isoformat() if market and market.as_of else None,
            "market_provider": market.provider if market else "",
            "current_multiples": {
                key: current.get(key) for key in ("pe", "ev_ebit", "ev_ebitda", "ev_sales", "p_fcf", "fcf_yield_pct")
            },
        },
        "current_economics": current,
        "historical_multiples": stats,
        "historical_observations": observations,
        "operating_regime": operating_regime,
        "multiple_bridge": bridge,
        "rerating_conditions": rerating,
        "old_multiple_defensibility": defensibility,
        "market_implied_expectations": implied,
        "peer_analysis": peers,
        "triangulation": triangulation,
        "market_read": market_read,
        "catalyst_timeline": timeline,
        "audit": {
            "historical_basis": "POINT_IN_TIME_POST_FILING_ANCHORS",
            "historical_price_provider": preferred_provider(security.id),
            "historical_observation_count": len(observations),
            "regime_start_fiscal_year": regime_start_fy,
            "pre_regime_history_excluded": bool(operating_regime.get("detected")),
            "peer_basis": "STORED_NORMALIZED_FINANCIALS_AND_STORED_MARKET_SNAPSHOTS",
            "network_calls": False,
            "blind_average_used": False,
            "missing_data_policy": "MISSING_STAYS_MISSING",
            "bridge_policy": BRIDGE_POLICY,
        },
        "limitations": [
            "Historical multiple samples use post-filing point-in-time anchors, not a daily reconstructed fundamental series.",
            "Peer coverage is limited to companies with stored normalized fundamentals and stored quotes.",
            "Multiple-bridge and peer adjustments are bounded explanatory estimates, not causal regressions.",
            "EV/EBITDA remains unavailable until EBITDA is a canonical normalized fact or deterministic derivation.",
            "Catalyst timing uses only stored dated catalysts; no event date is invented.",
        ],
    }


__all__ = [
    "ENGINE_VERSION", "BRIDGE_POLICY", "build_valuation_forensics", "build_peer_analysis",
]
