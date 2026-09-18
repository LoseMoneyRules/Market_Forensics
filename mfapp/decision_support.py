from __future__ import annotations

from datetime import date, datetime, timedelta
from math import isfinite
from statistics import mean
from typing import Any

from .core_models import Event, FinancialPeriod, HistoricalPrice, Security, ValuationModel
from .current_financials import annual_rows, current_row, forecast_rows
from .finra import stored_summary as finra_stored_summary


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _pct(value: float | None) -> str:
    return f"{value:+.1f}%" if value is not None else "—"


def management_engine(company_id: int) -> dict[str, Any]:
    rows = annual_rows(company_id, 6)
    current = current_row(company_id)
    latest = current or (rows[0] if rows else {})
    metrics = latest.get("metrics") or {}
    details: list[dict[str, Any]] = []

    def component(label: str, score: float | None, detail: str, weight: float) -> None:
        details.append({"label": label, "score": round(score, 1) if score is not None else None, "detail": detail, "weight": weight})

    # Execution: growth plus operating-margin direction.
    growth = n(metrics.get("revenue_growth_pct"))
    op = n(metrics.get("operating_margin_pct"))
    prior_op = n((rows[1].get("metrics") or {}).get("operating_margin_pct")) if len(rows) > 1 else None
    execution = None
    if growth is not None or op is not None:
        execution = 55.0
        if growth is not None: execution += max(-20, min(20, growth * 1.5))
        if op is not None and prior_op is not None: execution += max(-20, min(20, (op - prior_op) * 5))
        execution = max(0, min(100, execution))
    component("Operating execution", execution, f"Revenue trend {_pct(growth)}; operating margin {op:.1f}%" if op is not None else f"Revenue trend {_pct(growth)}", .25)

    # Cash quality: FCF conversion and sign.
    fcf, ni = n(latest.get("fcf")), n(latest.get("net_income"))
    conversion = (fcf / ni) if fcf is not None and ni not in (None, 0) else None
    cash_score = None
    if fcf is not None:
        cash_score = 25.0 if fcf < 0 else 65.0
        if conversion is not None:
            cash_score = max(0, min(100, 45 + conversion * 40))
    component("Cash conversion", cash_score, f"FCF / net income {conversion:.2f}x" if conversion is not None else "Insufficient FCF / earnings evidence", .25)

    # Capital allocation: leverage and distributions relative to FCF.
    debt, cash = n(latest.get("debt")), n(latest.get("cash"))
    net_debt = (debt or 0) - (cash or 0) if debt is not None or cash is not None else None
    leverage = net_debt / fcf if net_debt is not None and fcf not in (None, 0) and fcf > 0 else None
    buybacks, dividends = n(latest.get("buybacks")), n(latest.get("dividends"))
    payout = (abs(buybacks or 0) + abs(dividends or 0)) / abs(fcf) if fcf not in (None, 0) else None
    capital = None
    if net_debt is not None:
        capital = 75.0 if net_debt <= 0 else 65.0
        if leverage is not None:
            capital -= max(0, leverage - 1.5) * 10
        if payout is not None and payout > 1.35:
            capital -= min(25, (payout - 1.35) * 25)
        capital = max(0, min(100, capital))
    cap_detail = f"Net debt/FCF {leverage:.1f}x" if leverage is not None else ("Net cash position" if net_debt is not None and net_debt <= 0 else "Leverage evidence incomplete")
    if payout is not None: cap_detail += f"; distributions {payout:.2f}x FCF"
    component("Capital allocation", capital, cap_detail, .20)

    # Shareholder alignment: dilution / shrinkage.
    shares = [n(row.get("diluted_shares")) or n(row.get("shares_outstanding")) for row in rows]
    shares = [x for x in shares if x is not None and x > 0]
    share_change = (shares[0] / shares[min(len(shares) - 1, 3)] - 1) * 100 if len(shares) >= 2 else None
    alignment = max(0, min(100, 70 - share_change * 4)) if share_change is not None else None
    component("Shareholder alignment", alignment, f"Diluted/share count change {_pct(share_change)} over available history", .15)

    # Accounting / working-capital discipline.
    inv = n(metrics.get("inventory_growth_pct")); rec = n(metrics.get("receivables_growth_pct")); rev = growth
    divergences = [x - rev for x in (inv, rec) if x is not None and rev is not None]
    discipline = max(0, min(100, 80 - max([0.0] + divergences) * 2.0)) if divergences else None
    detail = "Working-capital divergence unavailable"
    if divergences:
        detail = f"Largest inventory/receivables growth spread vs revenue {max(divergences):+.1f} pts"
    component("Accounting discipline", discipline, detail, .15)

    available = [item for item in details if item["score"] is not None]
    weight = sum(item["weight"] for item in available)
    score = sum(item["score"] * item["weight"] for item in available) / weight if weight else None
    coverage = weight / sum(item["weight"] for item in details) if details else 0.0
    if score is None:
        label = "INSUFFICIENT EVIDENCE"
    elif score >= 75 and coverage >= .75:
        label = "HIGH EXECUTION CONFIDENCE"
    elif score >= 55:
        label = "MEDIUM EXECUTION CONFIDENCE"
    else:
        label = "CAUTION / LOW CONFIDENCE"
    return {"score": round(score, 1) if score is not None else None, "label": label, "coverage_pct": round(coverage * 100), "components": details}


def management_accountability(company_id: int) -> list[dict[str, Any]]:
    """Historical execution ledger from filed results, not personality scoring."""
    rows = list(reversed(annual_rows(company_id, 7)))
    out: list[dict[str, Any]] = []
    for row in rows:
        metrics = row.get("metrics") or {}
        ni, fcf = n(row.get("net_income")), n(row.get("fcf"))
        conversion = (fcf / ni) if fcf is not None and ni not in (None, 0) else None
        shares = n(row.get("diluted_shares")) or n(row.get("shares_outstanding"))
        out.append({
            "fiscal_year": row.get("fiscal_year"),
            "revenue_growth_pct": n(metrics.get("revenue_growth_pct")),
            "operating_margin_pct": n(metrics.get("operating_margin_pct")),
            "fcf_margin_pct": n(metrics.get("fcf_margin_pct")),
            "fcf_conversion": conversion,
            "shares": shares,
            "buybacks": n(row.get("buybacks")),
            "dividends": n(row.get("dividends")),
        })
    return out


def _next_filing_window(company_id: int) -> dict[str, Any]:
    latest = FinancialPeriod.query.filter(FinancialPeriod.company_id == company_id, FinancialPeriod.filed_at.is_not(None)).order_by(FinancialPeriod.filed_at.desc(), FinancialPeriod.id.desc()).first()
    if not latest or not latest.filed_at:
        return {"date": None, "label": "Next SEC filing", "confidence": "LOW"}
    estimate = latest.filed_at + timedelta(days=91)
    today = date.today()
    while estimate < today:
        estimate += timedelta(days=91)
    return {"date": estimate.isoformat(), "label": "Estimated next filing window", "confidence": "ESTIMATE"}


def monitoring_plan(company_id: int, valuation: dict[str, Any], intelligence: dict[str, Any], model: ValuationModel | None) -> list[dict[str, Any]]:
    current = current_row(company_id) or {}
    metrics = current.get("metrics") or {}
    filing = _next_filing_window(company_id)
    rules: list[dict[str, Any]] = []

    def add(name: str, metric: str, cadence: str, next_review: str, trigger: str, why: str, severity: str = "WATCH") -> None:
        rules.append({"name": name, "metric": metric, "cadence": cadence, "next_review": next_review, "trigger": trigger, "why": why, "severity": severity})

    add("Market price vs intrinsic range", "price", "Every 5 minutes while CONTROL is open", "Live", f"Review if price crosses Base/Bull or falls below Bear; Base currently {valuation.get('base') or '—'}", "Price is the decision interface, not the thesis itself.")
    filing_date = filing.get("date") or "Next filing"
    rev_growth = n(metrics.get("revenue_growth_pct"))
    op_margin = n(metrics.get("operating_margin_pct"))
    fcf_margin = n(metrics.get("fcf_margin_pct"))
    inv_growth = n(metrics.get("inventory_growth_pct"))
    rec_growth = n(metrics.get("receivables_growth_pct"))
    add("Revenue trend", "revenue_growth_pct", "Each 10-Q / 10-K", filing_date, f"Flag if growth falls below {(rev_growth - 5):.1f}%" if rev_growth is not None else "Establish threshold at next filing", "Detect demand deterioration before changing the thesis.")
    add("Operating margin", "operating_margin_pct", "Each 10-Q / 10-K", filing_date, f"Flag if margin falls below {(op_margin - 2):.1f}%" if op_margin is not None else "Establish threshold at next filing", "Tests operating leverage and execution.")
    add("Free-cash-flow margin", "fcf_margin_pct", "Each 10-Q / 10-K", filing_date, f"Flag if FCF margin falls below {(fcf_margin - 3):.1f}%" if fcf_margin is not None else "Establish threshold at next filing", "Earnings without cash should not support the same valuation.")
    if inv_growth is not None:
        add("Inventory vs revenue", "inventory_growth_pct", "Each filing", filing_date, "Flag if inventory growth exceeds revenue growth by 10 pts", "Potential demand / markdown warning.")
    if rec_growth is not None:
        add("Receivables vs revenue", "receivables_growth_pct", "Each filing", filing_date, "Flag if receivables growth exceeds revenue growth by 10 pts", "Potential collection or channel-quality warning.")
    add("Valuation assumptions", "base_case", "Quarterly or after material evidence", filing_date, "Rebuild only when evidence changes", "Prevents price-driven thesis drift.")
    if any(signal.get("section") == "tape" for signal in intelligence.get("signals", [])):
        add("Positioning / short interest", "short_interest", "Twice monthly + daily flow check", "Next FINRA release", "Flag sharp SI change or persistent flow divergence", "Flow is context, never intrinsic value.", "INFO")
    return rules


def journal_prefill(intelligence: dict[str, Any], valuation: dict[str, Any], model: ValuationModel | None) -> dict[str, str]:
    positives = [x for x in intelligence.get("signals", []) if x.get("tone") == "positive"]
    negatives = [x for x in intelligence.get("signals", []) if x.get("tone") in {"negative", "watch"}]
    evidence_for = "\n".join(f"• {x['label']}: {x['detail']}" for x in positives[:6]) or "No automatic positive evidence clears the threshold yet."
    evidence_against = "\n".join(f"• {x['label']}: {x['detail']}" for x in negatives[:6])
    if intelligence.get("warnings"):
        evidence_against += ("\n" if evidence_against else "") + "\n".join(f"• {x}" for x in intelligence["warnings"][:5])
    evidence_against = evidence_against or "No automatic numeric counter-signal clears the threshold; qualitative disconfirmation still required."
    horizon = int((model.assumptions or {}).get("horizon_years") or 5) if model else 5
    bias = (
        f"Do not change the thesis because of price alone. Current model: Bear {valuation.get('bear')}, Base {valuation.get('base')}, Bull {valuation.get('bull')} over ~{horizon}Y. "
        "Explicitly check confirmation bias, thesis drift, narrative fitting, and what evidence would falsify the current view."
    )
    return {"decision": intelligence.get("action") or "WAIT", "evidence_for": evidence_for, "evidence_against": evidence_against, "bias_notes": bias}


def tape_series(security: Security, months: int = 12) -> dict[str, Any]:
    months = 6 if int(months) <= 6 else 12
    cutoff = date.today() - timedelta(days=31 * months)
    prices = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security.id,
        HistoricalPrice.trade_date >= cutoff,
    ).order_by(HistoricalPrice.trade_date.asc()).all()

    weekly: list[dict[str, Any]] = []
    last_week = None
    daily_prices: list[dict[str, Any]] = []
    for row in prices:
        price = n(row.close_split_adjusted) or n(row.close_raw)
        volume = n(row.volume)
        if price is None:
            continue
        daily_prices.append({
            "date": row.trade_date.isoformat(),
            "price": price,
            "volume": volume,
            "turnover": (price * volume) if volume is not None else None,
        })
        key = row.trade_date.isocalendar()[:2]
        item = {"date": row.trade_date.isoformat(), "price": price, "volume": volume}
        if key == last_week and weekly:
            weekly[-1] = item
        else:
            weekly.append(item)
            last_week = key

    finra = finra_stored_summary(security.company_id)
    short_interest = [
        {"date": row.get("settlement_date"), "short": n(row.get("current_short")), "days_to_cover": n(row.get("days_to_cover"))}
        for row in finra.get("short_interest_rows", [])
        if row.get("settlement_date") and row.get("settlement_date") >= cutoff.isoformat()
    ]
    short_volume = [
        {"date": row.get("trade_date"), "short_pct": (n(row.get("short_pct")) * 100 if n(row.get("short_pct")) is not None else None)}
        for row in finra.get("daily_rows", [])
        if row.get("trade_date") and row.get("trade_date") >= cutoff.isoformat()
    ]

    positioning_event = Event.query.filter_by(
        company_id=security.company_id,
        event_type="ALPACA_POSITIONING",
    ).order_by(Event.event_date.desc(), Event.id.desc()).first()
    borrow_fee_event = Event.query.filter_by(
        company_id=security.company_id,
        event_type="BORROW_FEE_OBSERVATION",
    ).order_by(Event.event_date.desc(), Event.id.desc()).first()
    positioning = dict((positioning_event.payload or {}) if positioning_event else {})
    borrow = dict(positioning.get("borrow") or {})
    options = dict(positioning.get("options") or {})
    locate = dict(positioning.get("locate") or {})
    put_call = n(options.get("put_call_oi"))
    borrow_fee_payload = dict((borrow_fee_event.payload or {}) if borrow_fee_event else {})
    borrow_fee_pct = n(borrow_fee_payload.get("annualized_fee_pct"))

    def ret(days: int) -> float | None:
        if len(daily_prices) < 2:
            return None
        end_price = daily_prices[-1]["price"]
        idx = max(0, len(daily_prices) - 1 - days)
        start_price = daily_prices[idx]["price"]
        return (end_price / start_price - 1.0) * 100.0 if start_price not in (None, 0) else None

    vols = [x["volume"] for x in daily_prices if x.get("volume") not in (None, 0)]
    recent_vol = mean(vols[-20:]) if vols[-20:] else None
    prior_vol = mean(vols[-60:-20]) if len(vols) > 20 and vols[-60:-20] else None
    volume_ratio = recent_vol / prior_vol if recent_vol is not None and prior_vol not in (None, 0) else None

    turnovers = [x["turnover"] for x in daily_prices if x.get("turnover") not in (None, 0)]
    recent_turnover = mean(turnovers[-20:]) if turnovers[-20:] else None
    prior_turnover = mean(turnovers[-60:-20]) if len(turnovers) > 20 and turnovers[-60:-20] else None
    turnover_ratio = recent_turnover / prior_turnover if recent_turnover is not None and prior_turnover not in (None, 0) else None

    sv = [x["short_pct"] for x in short_volume if x.get("short_pct") is not None]
    short_5 = mean(sv[-5:]) if sv else None
    short_20 = mean(sv[-20:]) if sv else None
    r20 = ret(20)
    r60 = ret(60)

    absorption = None
    if short_20 is not None and r20 is not None:
        absorption = max(0.0, min(100.0, 50.0 + (short_20 - 50.0) * 1.2 + max(-15.0, min(15.0, r20)) * 1.3))

    price_resilience = None
    if r20 is not None:
        short_pressure = max(0.0, (short_20 or 50.0) - 50.0)
        price_resilience = max(0.0, min(100.0, 50.0 + r20 * 2.2 + short_pressure * .55))

    long_demand = None
    if r20 is not None:
        long_demand = 50.0 + r20 * 2.0 + ((volume_ratio or 1.0) - 1.0) * 25.0 + ((turnover_ratio or 1.0) - 1.0) * 12.0
        if put_call is not None and put_call < .70:
            long_demand += 5.0
        long_demand = max(0.0, min(100.0, long_demand))

    bear_pressure = None
    if r20 is not None:
        bear_pressure = 50.0 - r20 * 2.0 + max(0.0, (short_20 or 50.0) - 50.0) * 1.4
        if put_call is not None and put_call > 1.20:
            bear_pressure += min(12.0, (put_call - 1.20) * 20.0)
        if "hard" in str(borrow.get("borrow_status") or "").lower():
            bear_pressure += 5.0
        bear_pressure = max(0.0, min(100.0, bear_pressure))

    battle = None
    if volume_ratio is not None or short_20 is not None or turnover_ratio is not None:
        battle = 35.0
        battle += max(0.0, (volume_ratio or 1.0) - 1.0) * 25.0
        battle += max(0.0, (turnover_ratio or 1.0) - 1.0) * 20.0
        battle += abs((short_20 or 50.0) - 50.0)
        if put_call is not None:
            battle += min(10.0, abs(put_call - 1.0) * 8.0)
        battle = max(0.0, min(100.0, battle))

    data_points = sum(x is not None for x in (absorption, long_demand, bear_pressure, battle, put_call, turnover_ratio))
    confidence = "HIGH" if len(daily_prices) >= 120 and len(sv) >= 20 and data_points >= 5 else "MEDIUM" if len(daily_prices) >= 40 and data_points >= 3 else "LOW"
    net_tape = ((long_demand or 50.0) + (absorption or 50.0) + (price_resilience or 50.0) - (bear_pressure or 50.0)) / 3.0
    if net_tape >= 38:
        regime = "SUPPORTIVE"
    elif net_tape <= 12:
        regime = "HOSTILE"
    else:
        regime = "MIXED"

    rank_score = max(0.0, min(100.0, 50.0 + net_tape - 25.0))
    return {
        "months": months,
        "market": weekly,
        "short_interest": short_interest,
        "short_volume": short_volume,
        "positioning": positioning,
        "metrics": {
            "return_1m_pct": r20,
            "return_3m_pct": r60,
            "volume_ratio_20d": volume_ratio,
            "turnover_ratio_20d": turnover_ratio,
            "short_5d_pct": short_5,
            "short_20d_pct": short_20,
            "put_call_oi": put_call,
            "put_open_interest": n(options.get("put_open_interest")),
            "call_open_interest": n(options.get("call_open_interest")),
            "borrow_status": borrow.get("borrow_status") or "unknown",
            "borrow_fee_pct": borrow_fee_pct,
            "borrow_fee_source": borrow_fee_payload.get("source") or "",
            "borrow_fee_as_of": borrow_fee_event.event_date.isoformat() if borrow_fee_event and borrow_fee_event.event_date else None,
            "shortable": borrow.get("shortable"),
            "locate_price": n(locate.get("price")),
            "locate_available_qty": n(locate.get("available_qty")),
            "absorption": absorption,
            "price_resilience": price_resilience,
            "long_demand": long_demand,
            "bear_pressure": bear_pressure,
            "battle_intensity": battle,
            "net_tape": net_tape,
            "rank_score": rank_score,
            "regime": regime,
            "confidence": confidence,
        },
    }

def company_brief(company_id: int, valuation: dict[str, Any], intelligence: dict[str, Any], model: ValuationModel | None) -> dict[str, Any]:
    price = n(valuation.get("current_price")); base = n(valuation.get("base")); bear = n(valuation.get("bear")); bull = n(valuation.get("bull"))
    horizon = int((model.assumptions or {}).get("horizon_years") or 5) if model else 5
    target_year = date.today().year + horizon
    reasons = [f"{x['label']}: {x['detail']}" for x in intelligence.get("top_signals", [])[:4]]
    return {
        "price": price, "bear": bear, "base": base, "bull": bull,
        "base_gap_pct": ((base / price - 1) * 100) if base is not None and price not in (None, 0) else None,
        "action": intelligence.get("action") or "WAIT", "confidence": intelligence.get("confidence") or "LOW",
        "horizon_years": horizon, "target_year": target_year, "reasons": reasons,
        "current_financial": current_row(company_id), "forecasts": forecast_rows(company_id, model, 3),
    }


__all__ = ["management_engine", "management_accountability", "monitoring_plan", "journal_prefill", "tape_series", "company_brief"]
