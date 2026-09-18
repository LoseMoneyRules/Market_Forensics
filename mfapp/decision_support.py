from __future__ import annotations

from datetime import date, datetime, timedelta
from math import isfinite
from statistics import mean
from typing import Any

from .core_models import Event, FinancialPeriod, HistoricalPrice, Security, ValuationModel
from .current_financials import annual_rows, current_row, forecast_rows
from .finra import stored_summary as finra_stored_summary
from .tape_engine import score_tape_day, what_changed, what_would_change_regime


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


def tape_context_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Translate stored tape metrics into a compact context read.

    This accepts an already-materialized metric payload so the UI can upgrade old
    cache rows without forcing a provider refresh.
    """
    long_demand = n(metrics.get("long_demand"))
    bear_pressure = n(metrics.get("bear_pressure"))
    resilience = n(metrics.get("price_resilience"))
    battle = n(metrics.get("battle_intensity"))
    confidence = str(metrics.get("confidence") or "LOW").upper()
    regime = str(metrics.get("regime") or "MIXED").upper()

    pressure_delta = long_demand - bear_pressure if long_demand is not None and bear_pressure is not None else None
    if pressure_delta is None:
        pressure_direction = "LOW DATA"; pressure_tone = "watch"
        pressure_detail = "Direction unresolved."
    elif pressure_delta >= 12:
        pressure_direction = "LONG"; pressure_tone = "positive"
        pressure_detail = f"Long demand leads by {pressure_delta:.0f} pts."
    elif pressure_delta <= -12:
        pressure_direction = "SHORT"; pressure_tone = "negative"
        pressure_detail = f"Bear pressure leads by {abs(pressure_delta):.0f} pts."
    else:
        pressure_direction = "LATERAL"; pressure_tone = "watch"
        pressure_detail = f"Pressure spread {pressure_delta:+.0f} pts."

    if confidence == "LOW":
        posture = "WAIT FOR DATA"; posture_tone = "watch"; confirmation_state = "REFRESH"
        next_confirmation = "Refresh price + FINRA / positioning."
    elif regime == "MIXED" or pressure_direction in {"LATERAL", "LOW DATA"} or (battle is not None and battle >= 70):
        posture = "WAIT FOR CONFIRMATION"; posture_tone = "watch"; confirmation_state = "NO CLEAN EDGE"
        next_confirmation = "Need ≥12-pt pressure spread + confirming resilience."
    elif pressure_direction == "LONG" and regime == "SUPPORTIVE":
        posture = "SUPPORTIVE TAPE"; posture_tone = "positive"; confirmation_state = "LONG PRESSURE"
        next_confirmation = "Long spread ≥12 pts · resilience ≥55."
    elif pressure_direction == "SHORT" and regime == "HOSTILE":
        posture = "HOSTILE TAPE"; posture_tone = "negative"; confirmation_state = "SHORT PRESSURE"
        next_confirmation = "Short spread ≥12 pts · resilience <45."
    else:
        posture = "WAIT FOR CONFIRMATION"; posture_tone = "watch"; confirmation_state = "CONFLICTED"
        next_confirmation = "Wait for regime + pressure to align."

    if resilience is None and confidence != "LOW":
        next_confirmation += " Resilience missing."

    return {
        "posture": posture, "posture_tone": posture_tone,
        "pressure_direction": pressure_direction, "pressure_tone": pressure_tone,
        "pressure_detail": pressure_detail, "confirmation_state": confirmation_state,
        "next_confirmation": next_confirmation,
    }


def tape_series(security: Security, months: int = 12) -> dict[str, Any]:
    """Materialize the web-native Tape Engine V2 from stored/provider evidence.

    Large/Very Large/Whale labels are trade-size proxies, never beneficial-owner
    identity. Normal GET navigation reads the cached output of this function.
    """
    months = 6 if int(months) <= 6 else 12
    cutoff = date.today() - timedelta(days=31 * months)
    prices = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security.id,
        HistoricalPrice.trade_date >= cutoff,
    ).order_by(HistoricalPrice.trade_date.asc()).all()

    weekly: list[dict[str, Any]] = []
    last_week = None
    daily_market: list[dict[str, Any]] = []
    for row in prices:
        price = n(row.close_split_adjusted) or n(row.close_raw)
        volume = n(row.volume)
        if price is None:
            continue
        open_price = n(getattr(row, "open_split_adjusted", None)) or n(getattr(row, "open_raw", None))
        high = n(getattr(row, "high_split_adjusted", None)) or n(getattr(row, "high_raw", None))
        low = n(getattr(row, "low_split_adjusted", None)) or n(getattr(row, "low_raw", None))
        daily_market.append({
            "date": row.trade_date.isoformat(),
            "price": price,
            "open": open_price,
            "high": high,
            "low": low,
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

    # Merge stored Alpaca flow windows from successive background refreshes.
    positioning_events = Event.query.filter_by(
        company_id=security.company_id,
        event_type="ALPACA_POSITIONING",
    ).order_by(Event.event_date.desc(), Event.id.desc()).limit(120).all()
    positioning = dict((positioning_events[0].payload or {}) if positioning_events else {})
    flow_by_date: dict[str, dict[str, Any]] = {}
    for event in reversed(positioning_events):
        payload = dict(event.payload or {})
        for row in ((payload.get("flow") or {}).get("rows") or []):
            day = str(row.get("date") or "")[:10]
            if day and day >= cutoff.isoformat():
                flow_by_date[day] = dict(row)
    flow_rows = [flow_by_date[key] for key in sorted(flow_by_date)]

    for idx, row in enumerate(flow_rows):
        recent5 = flow_rows[max(0, idx - 4):idx + 1]
        recent20 = flow_rows[max(0, idx - 19):idx + 1]
        row["cumulative_5d"] = sum(n(item.get("net_large")) or 0.0 for item in recent5)
        row["cumulative_20d"] = sum(n(item.get("net_large")) or 0.0 for item in recent20)
        row["cumulative_5d_sessions"] = len(recent5)
        row["cumulative_20d_sessions"] = len(recent20)

    ats_event = Event.query.filter_by(
        company_id=security.company_id,
        event_type="FINRA_ATS_SERIES",
    ).order_by(Event.event_date.desc(), Event.id.desc()).first()
    ats_source_rows = list((ats_event.payload or {}).get("rows") or []) if ats_event else []
    ats_rows = [
        dict(row)
        for row in ats_source_rows
        if str(row.get("week_start") or "") >= cutoff.isoformat()
    ]

    borrow = dict(positioning.get("borrow") or {})
    options = dict(positioning.get("options") or {})
    locate = dict(positioning.get("locate") or {})
    put_call = n(options.get("put_call_oi"))
    borrow_fee_event = Event.query.filter_by(
        company_id=security.company_id,
        event_type="BORROW_FEE_OBSERVATION",
    ).order_by(Event.event_date.desc(), Event.id.desc()).first()
    borrow_fee_payload = dict((borrow_fee_event.payload or {}) if borrow_fee_event else {})
    borrow_fee_pct = n(borrow_fee_payload.get("annualized_fee_pct"))

    short_map = {str(row.get("date")): n(row.get("short_pct")) for row in short_volume if row.get("date")}
    flow_map = {str(row.get("date")): row for row in flow_rows if row.get("date")}
    latest_interest = finra.get("latest_short_interest") or {}
    si_change = n(latest_interest.get("change_percent"))

    tape_daily: list[dict[str, Any]] = []
    volumes: list[float] = []
    prior_price = None
    for idx, row in enumerate(daily_market):
        price = n(row.get("price"))
        volume = n(row.get("volume"))
        prior_volumes = volumes[-20:]
        avg_volume = mean(prior_volumes) if prior_volumes else None
        volume_ratio = (volume / avg_volume) if volume is not None and avg_volume not in (None, 0) else 1.0
        if volume is not None:
            volumes.append(volume)
        ret_pct = ((price / prior_price - 1.0) * 100.0) if price is not None and prior_price not in (None, 0) else None
        prior_price = price if price is not None else prior_price
        high, low = n(row.get("high")), n(row.get("low"))
        close_location = ((price - low) / (high - low) * 100.0) if price is not None and high is not None and low is not None and high > low else 50.0

        day = str(row.get("date"))
        flow = flow_map.get(day) or {}
        short_pct = short_map.get(day)
        is_latest = idx == len(daily_market) - 1
        score = score_tape_day(
            return_pct=ret_pct,
            volume_ratio=volume_ratio,
            close_location=close_location,
            short_pct=short_pct,
            net_large_ratio=flow.get("net_large_ratio"),
            net_whale_ratio=flow.get("net_whale_ratio"),
            flow_confidence=flow.get("flow_confidence_pct"),
            si_change_pct=si_change if is_latest else None,
            put_call_oi=put_call if is_latest else None,
            hard_to_borrow=("hard" in str(borrow.get("borrow_status") or "").lower()) if is_latest else False,
            has_market=True,
            has_short_volume=short_pct is not None,
            has_flow=bool(flow),
            has_positioning=bool(positioning) if is_latest else False,
        )
        tape_daily.append({
            "date": day,
            "price": price,
            "volume": volume,
            "return_pct": ret_pct,
            "volume_ratio": volume_ratio,
            "close_location": close_location,
            "short_pct": short_pct,
            "net_large": n(flow.get("net_large")),
            "net_whale": n(flow.get("net_whale")),
            "net_large_ratio": n(flow.get("net_large_ratio")),
            "net_whale_ratio": n(flow.get("net_whale_ratio")),
            "flow_confidence_pct": n(flow.get("flow_confidence_pct")),
            **score,
        })

    latest_score = tape_daily[-1] if tape_daily else score_tape_day(
        return_pct=None, volume_ratio=None, close_location=None,
        has_market=False, has_short_volume=False, has_flow=False, has_positioning=bool(positioning),
    )
    previous_score = tape_daily[-2] if len(tape_daily) > 1 else None

    def ret(days: int) -> float | None:
        if len(daily_market) < 2:
            return None
        end_price = n(daily_market[-1].get("price"))
        idx = max(0, len(daily_market) - 1 - days)
        start_price = n(daily_market[idx].get("price"))
        return (end_price / start_price - 1.0) * 100.0 if end_price is not None and start_price not in (None, 0) else None

    vols = [n(x.get("volume")) for x in daily_market if n(x.get("volume")) not in (None, 0)]
    recent_vol = mean(vols[-20:]) if vols[-20:] else None
    prior_vol = mean(vols[-60:-20]) if len(vols) > 20 and vols[-60:-20] else None
    volume_ratio = recent_vol / prior_vol if recent_vol is not None and prior_vol not in (None, 0) else None
    turnovers = [n(x.get("turnover")) for x in daily_market if n(x.get("turnover")) not in (None, 0)]
    recent_turnover = mean(turnovers[-20:]) if turnovers[-20:] else None
    prior_turnover = mean(turnovers[-60:-20]) if len(turnovers) > 20 and turnovers[-60:-20] else None
    turnover_ratio = recent_turnover / prior_turnover if recent_turnover is not None and prior_turnover not in (None, 0) else None

    sv = [n(x.get("short_pct")) for x in short_volume if n(x.get("short_pct")) is not None]
    short_5 = mean(sv[-5:]) if sv else None
    short_20 = mean(sv[-20:]) if sv else None
    latest_flow = flow_rows[-1] if flow_rows else {}

    confidence_score = n(latest_score.get("data_confidence")) or 0.0
    confidence = "HIGH" if confidence_score >= 75 else "MEDIUM" if confidence_score >= 55 else "LOW"
    path = str(latest_score.get("path_regime") or "MIXED")
    metric_seed = {
        "long_demand": latest_score.get("long_demand"),
        "bear_pressure": latest_score.get("short_pressure"),
        "price_resilience": latest_score.get("price_resilience"),
        "battle_intensity": latest_score.get("battle_intensity"),
        "confidence": confidence,
        "regime": path,
    }
    tape_context = tape_context_metrics(metric_seed)

    return {
        "months": months,
        "market": weekly,
        "daily_market": daily_market,
        "short_interest": short_interest,
        "short_volume": short_volume,
        "institutional_flow": flow_rows,
        "ats": ats_rows,
        "tape_daily": tape_daily,
        "positioning": positioning,
        "what_changed": what_changed(latest_score, previous_score),
        "what_would_change_regime": what_would_change_regime(latest_score),
        "metrics": {
            "return_1m_pct": ret(20),
            "return_3m_pct": ret(60),
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
            "institutional_flow": latest_score.get("institutional_flow"),
            "absorption": latest_score.get("absorption"),
            "price_resilience": latest_score.get("price_resilience"),
            "long_demand": latest_score.get("long_demand"),
            "bear_pressure": latest_score.get("short_pressure"),
            "battle_intensity": latest_score.get("battle_intensity"),
            "net_tape": latest_score.get("net_tape"),
            "rank_score": latest_score.get("net_tape"),
            "rank": latest_score.get("rank"),
            "forensic_regime": latest_score.get("forensic_regime"),
            "machine_read": latest_score.get("machine_read"),
            "data_confidence": latest_score.get("data_confidence"),
            "regime": path,
            "confidence": confidence,
            "flow_feed": latest_flow.get("feed") or "",
            "flow_feed_scope": latest_flow.get("feed_scope") or "",
            "flow_source_status": latest_flow.get("source_status") or "",
            "flow_method": latest_flow.get("classification_method") or "",
            "flow_confidence_pct": n(latest_flow.get("flow_confidence_pct")),
            "large_threshold": n(latest_flow.get("large_threshold")),
            "very_large_threshold": n(latest_flow.get("very_large_threshold")),
            "whale_threshold": n(latest_flow.get("whale_threshold")),
            "large_buy": n(latest_flow.get("large_buy")),
            "large_sell": n(latest_flow.get("large_sell")),
            "net_large": n(latest_flow.get("net_large")),
            "very_large_buy": n(latest_flow.get("very_large_buy")),
            "very_large_sell": n(latest_flow.get("very_large_sell")),
            "net_very_large": n(latest_flow.get("net_very_large")),
            "whale_buy": n(latest_flow.get("whale_buy")),
            "whale_sell": n(latest_flow.get("whale_sell")),
            "net_whale": n(latest_flow.get("net_whale")),
            "large_share_pct": n(latest_flow.get("large_share_pct")),
            "whale_share_pct": n(latest_flow.get("whale_share_pct")),
            **tape_context,
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
