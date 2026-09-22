from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from .calculations import financial_metrics
from .core_models import Company, Coverage, HistoricalPrice, ResearchState, Security, ValuationModel, ValuationScenario
from .current_financials import history_with_current
from .data_providers import latest_snapshot
from .decision_support import management_engine
from .extensions import db
from .finra import stored_summary as finra_stored_summary
from .formatting import format_number
from .historical_data import preferred_provider, price_on_or_after
from .decision_engine import build_research_intelligence
from .valuation_engine import ENGINE_VERSION, calibrate_multiples, default_cases, evaluate, infer_company_type, metrics_from_history, n
from .economic_reality import economic_from_row, metric as economic_metric

# Kept as a public import for compatibility. 0.2.0 never renders/stores a visible autofill marker.
AUTO_MARKER = ""
LEGACY_AUTO_RE = re.compile(r"^\[AUTO\s+[^\]]+\]\s*", re.I)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_auto(value: str | None) -> str:
    return LEGACY_AUTO_RE.sub("", str(value or "").strip())


def _text_hash(value: str | None) -> str:
    return hashlib.sha256(_clean_auto(value).encode("utf-8")).hexdigest()


def _replaceable(field_key: str, text: str | None, auto_hashes: dict[str, str]) -> bool:
    value = str(text or "").strip()
    if not value or value.startswith("[AUTO "):
        return True
    return bool(auto_hashes.get(field_key) and auto_hashes.get(field_key) == _text_hash(value))


def _set_auto(obj: Any, attr: str, field_key: str, value: str, auto_hashes: dict[str, str]) -> None:
    clean = _clean_auto(value)
    setattr(obj, attr, clean)
    auto_hashes[field_key] = _text_hash(clean)


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _margin(value: Any, revenue: Any) -> float | None:
    numerator, denominator = n(value), n(revenue)
    return numerator / denominator * 100.0 if numerator is not None and denominator not in (None, 0) else None


def _financial_history(company_id: int) -> list[dict[str, Any]]:
    """Chronological FY history plus a current TTM row when four quarters exist."""
    return history_with_current(company_id, 16)


def _intelligence_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chronological: list[dict[str, Any]] = []
    previous: dict[str, Any] = {}
    for source in history:
        row = dict(source)
        if not row.get("metrics"):
            row["metrics"] = financial_metrics(row, previous)
        chronological.append(row)
        previous = row
    return list(reversed(chronological))



def _point_in_time_calibration(security_id: int, history: list[dict[str, Any]], company_type: str) -> dict[str, Any]:
    """Build company-specific multiple history from filing-date market anchors."""
    provider = preferred_provider(security_id)
    observations: list[dict[str, Any]] = []
    annual = [row for row in history if str(row.get("period_type") or "FY") == "FY"]
    latest_anchor_price = None
    latest_anchor_date = None
    if provider:
        for row in annual:
            filed = row.get("filed_at")
            try:
                filing_date = datetime.fromisoformat(str(filed)[:10]).date() if filed else None
            except Exception:
                filing_date = None
            if filing_date is None:
                continue
            market = price_on_or_after(security_id, filing_date, 14, provider=provider)
            shares = n(row.get("diluted_shares")) or n(row.get("shares_outstanding"))
            if not market or shares in (None, 0):
                continue
            economic = economic_from_row(row)
            economic_net_debt = economic_metric(economic, "economic_net_debt")
            operating_income = n(row.get("operating_income"))
            depreciation_amortization = economic_metric(economic, "depreciation_amortization")
            ebitda = (
                operating_income + depreciation_amortization
                if operating_income is not None and depreciation_amortization is not None
                else None
            )
            observations.append({
                "fiscal_year": row.get("fiscal_year"),
                "anchor_date": market.trade_date.isoformat(),
                "price": n(market.close_raw),
                "shares": shares,
                "revenue": row.get("revenue"),
                "net_income": row.get("net_income"),
                "fcf": row.get("fcf"),
                "ebitda": ebitda,
                "equity": row.get("equity"),
                "net_debt": (
                    economic_net_debt
                    if economic and not economic.get("material_unresolved")
                    else None
                ),
            })
            if latest_anchor_date is None or market.trade_date.isoformat() > latest_anchor_date:
                latest_anchor_date = market.trade_date.isoformat()
                latest_anchor_price = n(market.close_raw)
    result = calibrate_multiples(observations, company_type)
    result["latest_filing_anchor_price"] = latest_anchor_price
    result["latest_filing_anchor_date"] = latest_anchor_date
    result["observation_count"] = len(observations)
    return result

def _case_from_row(row: ValuationScenario | None, fallback: dict[str, Any], force: bool) -> tuple[dict[str, Any], bool]:
    if row is None:
        return dict(fallback), True
    inputs = dict(row.inputs or {})
    auto_owned = bool(inputs.get("auto_prefill"))
    if force or auto_owned or row.equity_value_per_share is None:
        return dict(fallback), True
    return {
        "growth": n(inputs.get("growth")),
        "net_margin": n(inputs.get("net_margin")),
        "fcf_margin": n(inputs.get("fcf_margin")),
        "ebitda_margin": n(inputs.get("ebitda_margin")),
        "share_growth": n(inputs.get("share_growth")),
        "pe": n(inputs.get("pe")),
        "p_sales": n(inputs.get("p_sales")),
        "ev_sales": n(inputs.get("ev_sales")),
        "ev_ebitda": n(inputs.get("ev_ebitda")),
        "target_fcf_yield": n(inputs.get("target_fcf_yield")),
        "equity_discount_rate": n(inputs.get("equity_discount_rate")),
        "terminal_growth": n(inputs.get("terminal_growth")),
        "probability": n(row.probability),
        "manual_override": n(inputs.get("manual_override")),
        "scenario_multiplier": n(inputs.get("scenario_multiplier")) or 1.0,
        "liquidation_floor": n(inputs.get("liquidation_floor")),
        "method_exclusions": list(inputs.get("method_exclusions") or []),
        "life_cycle": str(inputs.get("life_cycle") or ""),
        "solvency_state": str(inputs.get("solvency_state") or ""),
        "integrity_notes": list(inputs.get("integrity_notes") or []),
    }, False


def _reference_price(security_id: int) -> float | None:
    market = latest_snapshot(security_id)
    if market and n(market.price) not in (None, 0):
        return n(market.price)
    row = HistoricalPrice.query.filter_by(security_id=security_id).order_by(HistoricalPrice.trade_date.desc(), HistoricalPrice.id.desc()).first()
    if row:
        return n(row.close_split_adjusted) or n(row.close_raw)
    return None


def _case_line(case: dict[str, Any]) -> str:
    bits = []
    for key, label in (("growth", "revenue growth"), ("net_margin", "net margin"), ("fcf_margin", "FCF margin")):
        value = n(case.get(key))
        if value is not None:
            bits.append(f"{label} {value * 100:.1f}%")
    pe = n(case.get("pe")); ps = n(case.get("p_sales")); evs = n(case.get("ev_sales")); eve = n(case.get("ev_ebitda")); yld = n(case.get("target_fcf_yield"))
    if pe is not None: bits.append(f"P/E {pe:.1f}x")
    if ps is not None: bits.append(f"P/S {ps:.2f}x")
    if eve is not None: bits.append(f"EV/EBITDA {eve:.1f}x")
    if evs is not None: bits.append(f"EV/Sales {evs:.2f}x")
    if yld is not None: bits.append(f"FCF yield {yld * 100:.1f}%")
    return ", ".join(bits) if bits else "insufficient operating inputs"


def _auto_research_sections(
    *, coverage: Coverage, research: ResearchState, company: Company, security: Security,
    history: list[dict[str, Any]], result: dict[str, Any], cases: dict[str, dict[str, Any]],
    current_price: float | None, company_type: str, auto_hashes: dict[str, str],
) -> list[str]:
    updates: list[str] = []
    evidence_rows = _intelligence_rows(history)
    finra = finra_stored_summary(company.id)
    intelligence = build_research_intelligence(
        evidence_rows,
        {"bear": result["scenarios"]["BEAR"].get("fair_value"), "base": result["scenarios"]["BASE"].get("fair_value"), "bull": result["scenarios"]["BULL"].get("fair_value"), "expected_value": result.get("expected_value"), "current_price": current_price},
        market_price=current_price,
        valuation_quality=str(((result.get("scenarios") or {}).get("BASE") or {}).get("quality") or result.get("quality") or ""),
        finra_summary=finra,
    )

    if _replaceable("owner_summary", coverage.owner_summary, auto_hashes):
        gap = intelligence.get("base_gap_pct")
        gap_text = f" · Base gap {gap:+.1f}%" if gap is not None else ""
        _set_auto(coverage, "owner_summary", "owner_summary", f"{intelligence['summary']}{gap_text}.", auto_hashes)
        updates.append("overview-summary")

    positives = [x for x in intelligence.get("signals", []) if x.get("tone") == "positive"]
    negatives = [x for x in intelligence.get("signals", []) if x.get("tone") in {"negative", "watch"}]
    warnings = list(intelligence.get("warnings") or [])

    if _replaceable("thesis", research.thesis, auto_hashes):
        lines = [f"Evidence-first draft: {intelligence['summary']}."] + [f"+ {x['label']}: {x['detail']}" for x in positives[:4]]
        if len(lines) == 1: lines.append("No positive signal currently clears the automatic evidence thresholds.")
        _set_auto(research, "thesis", "thesis", "\n".join(lines), auto_hashes); updates.append("thesis")

    if _replaceable("counter_evidence", research.counter_evidence, auto_hashes):
        lines = ["Evidence that argues against the current read."] + [f"- {x['label']}: {x['detail']}" for x in negatives[:5]] + [f"! {w}" for w in warnings[:4]]
        if len(lines) == 1: lines.append("No automatic numeric counter-signal clears the threshold; qualitative disconfirmation still needs review.")
        _set_auto(research, "counter_evidence", "counter_evidence", "\n".join(lines), auto_hashes); updates.append("counter-evidence")

    if _replaceable("variant_us", research.variant_us, auto_hashes):
        gap = intelligence.get("base_gap_pct")
        text = f"Model-derived variant: intrinsic Base is {gap:+.1f}% versus the verified market reference. Validate operating assumptions and external expectations before treating this as a true market variant." if gap is not None else "DATA WARNING: a verified market-vs-intrinsic gap is not available yet."
        _set_auto(research, "variant_us", "variant_us", text, auto_hashes); updates.append("variant")

    if _replaceable("expectations", research.expectations, auto_hashes):
        lines = ["Model-implied operating expectations. These are internal forecasts, not sell-side consensus estimates."]
        for name in ("BEAR", "BASE", "BULL"):
            lines.append(f"{name.title()}: {_case_line(cases[name])}.")
        _set_auto(research, "expectations", "expectations", "\n".join(lines), auto_hashes); updates.append("expectations")

    if _replaceable("bear_case_summary", research.bear_case_summary, auto_hashes):
        lines = ["Evidence-linked bear case / hidden-risk scan."] + [f"- {x['label']}: {x['detail']}" for x in negatives[:6]] + [f"! {w}" for w in warnings[:3]]
        if len(lines) == 1: lines.append("No automatic numeric red flag currently clears the threshold. This does not replace qualitative bear-case work.")
        _set_auto(research, "bear_case_summary", "bear_case_summary", "\n".join(lines), auto_hashes); updates.append("bear-case")

    if _replaceable("catalysts_summary", research.catalysts_summary, auto_hashes):
        lines = ["Evidence-linked inflections to monitor; these are not invented event dates."] + [f"+ {x['label']}: {x['detail']}" for x in positives[:6]]
        if len(lines) == 1: lines.append("No positive numeric inflection currently clears the threshold; event catalysts require sourced evidence.")
        _set_auto(research, "catalysts_summary", "catalysts_summary", "\n".join(lines), auto_hashes); updates.append("catalysts")

    if _replaceable("management_summary", research.management_summary, auto_hashes):
        mgmt = management_engine(company.id)
        lines = [f"Execution-confidence engine: {mgmt.get('label')} · score {mgmt.get('score') if mgmt.get('score') is not None else '—'}/100 · evidence coverage {mgmt.get('coverage_pct', 0)}%."]
        for item in mgmt.get("components", []):
            score = f"{item['score']:.0f}/100" if item.get("score") is not None else "insufficient evidence"
            lines.append(f"{item['label']}: {score} · {item['detail']}")
        lines.append("This is evidence-based execution confidence, not a personality or integrity judgment.")
        _set_auto(research, "management_summary", "management_summary", "\n".join(lines), auto_hashes); updates.append("management")

    if _replaceable("tape_summary", research.tape_summary, auto_hashes):
        lines = ["Positioning / flow evidence, kept separate from intrinsic value."]
        si = finra.get("latest_short_interest") or {}
        if si:
            if si.get("current_short") is not None: lines.append(f"Short interest: {format_number(si.get('current_short'), 'AUTO')} as of {si.get('settlement_date') or 'latest report'}.")
            if si.get("change_percent") is not None: lines.append(f"Short-interest change: {float(si.get('change_percent')):+.1f}% vs prior report.")
            if si.get("days_to_cover") is not None: lines.append(f"Days to cover: {float(si.get('days_to_cover')):.2f}.")
        if finra.get("daily_20d_short_pct") is not None: lines.append(f"FINRA daily short-sale volume 20d average: {float(finra.get('daily_20d_short_pct')) * 100:.1f}% of FINRA-reported volume.")
        if len(lines) == 1: lines.append("No FINRA positioning series is stored yet; refresh FINRA before drawing a flow conclusion.")
        _set_auto(research, "tape_summary", "tape_summary", "\n".join(lines), auto_hashes); updates.append("tape")
    return updates


def prefill_coverage(coverage_id: int, user_id: int, force: bool = False) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if coverage is None: raise RuntimeError("Coverage not found")
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id) if security else None
    if security is None or company is None: raise RuntimeError("Security/company not found")
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    if research is None or model is None: raise RuntimeError("Coverage workspace incomplete")

    history = _financial_history(company.id)
    saved = dict(model.assumptions or {})
    auto_hashes = dict(saved.get("auto_text_hashes") or {})
    company_type = str(saved.get("company_type") or infer_company_type(company.sector, company.industry))
    saved_engine_current = str(saved.get("engine_version") or "") == ENGINE_VERSION
    preserve_verified_share_basis = bool(saved.get("share_basis_verified", False))
    current_shares = saved.get("current_shares") if (saved_engine_current or preserve_verified_share_basis) else None
    share_source = str(saved.get("share_source") or "") if (saved_engine_current or preserve_verified_share_basis) else ""
    metrics = metrics_from_history(history, current_shares, share_source, company_type)
    if current_shares in (None, "") and metrics.get("shares") is not None:
        current_shares = metrics["shares"]; share_source = metrics.get("share_source") or share_source
    calibration = _point_in_time_calibration(security.id, history, company_type)
    defaults = default_cases(metrics, company_type, calibration)
    weights = dict(saved.get("weights") or defaults["weights"]) if saved_engine_current else dict(defaults["weights"])
    horizon_years = int(saved.get("horizon_years") or defaults["horizon_years"])
    current_price = _reference_price(security.id)
    metrics["current_price"] = current_price
    current_equity = n(metrics.get("equity"))
    current_shares_for_pb = n(metrics.get("shares"))
    pb_history = calibration.get("p_b") or (None, None, None)
    historical_pb_median = n(pb_history[1]) if len(pb_history) >= 2 else None
    if current_price not in (None, 0) and current_equity not in (None, 0) and current_equity > 0 and current_shares_for_pb not in (None, 0):
        current_pb = current_price * current_shares_for_pb / current_equity
        metrics["current_p_b"] = current_pb
        if historical_pb_median not in (None, 0):
            metrics["pb_deviation_from_history_pct"] = (current_pb / historical_pb_median - 1.0) * 100.0
    anchor_price = n(calibration.get("latest_filing_anchor_price"))
    if current_price not in (None, 0) and anchor_price not in (None, 0):
        metrics["market_move_since_filing_pct"] = (current_price / anchor_price - 1.0) * 100.0
        metrics["latest_filing_anchor_price"] = anchor_price
        metrics["latest_filing_anchor_date"] = calibration.get("latest_filing_anchor_date")

    scenario_rows = {row.name.upper(): row for row in model.scenarios}
    case_inputs: dict[str, dict[str, Any]] = {}; auto_flags: dict[str, bool] = {}
    fallback_values = {name: n(scenario_rows[name].equity_value_per_share) if name in scenario_rows else None for name in ("BEAR", "BASE", "BULL")}
    for name in ("BEAR", "BASE", "BULL"):
        case_inputs[name], auto_flags[name] = _case_from_row(scenario_rows.get(name), defaults[name], force)
    result = evaluate(metrics, case_inputs, weights, horizon_years, current_price=current_price, fallback_values=fallback_values)

    changed_scenarios: list[str] = []
    for name in ("BEAR", "BASE", "BULL"):
        row = scenario_rows.get(name)
        if row is None:
            row = ValuationScenario(model_id=model.id, name=name); db.session.add(row); scenario_rows[name] = row; auto_flags[name] = True
        output = result["scenarios"][name]
        if auto_flags[name]:
            if output.get("fair_value") is not None: row.equity_value_per_share = output.get("fair_value")
            row.probability = case_inputs[name].get("probability") or 0
            row.confidence = "MEDIUM" if output.get("quality") == "INTRINSIC" and calibration.get("source") == "COMPANY_POINT_IN_TIME_5Y_10Y" else "LOW"
            row.inputs = {"auto_prefill": True, **case_inputs[name]}; row.outputs = output; row.calculated_at = utcnow(); changed_scenarios.append(name)

    latest_period = history[-1] if history else {}
    model.method = "MULTI_METHOD_INTRINSIC"
    model.assumptions = saved | {
        "engine_version": ENGINE_VERSION, "company_type": company_type, "current_shares": current_shares,
        "share_source": share_source or metrics.get("share_source"), "share_basis_verified": bool(saved.get("share_basis_verified", False)),
        "share_basis_note": str(saved.get("share_basis_note") or ""), "weights": weights, "horizon_years": horizon_years,
        "calibration": calibration, "auto_text_hashes": auto_hashes,
        "current_financial_basis": str(latest_period.get("period_type") or "FY"),
        "current_financial_period_end": latest_period.get("period_end"),
        "auto_draft": {
            "source": calibration.get("source"), "sample_size": calibration.get("sample_size", 0),
            "latest_fiscal_year": metrics.get("fiscal_year"), "current_price": current_price,
            "current_price_role": "COMPARISON_ONLY_UNLESS_REQUIRED_AS_EXPLICIT_PROVISIONAL_FALLBACK",
            "generated_at": utcnow().isoformat(), "basis_usable": metrics.get("basis_usable"),
            "valuation_quality": result.get("quality"), "warnings": result.get("warnings") or [],
            "monte_carlo": result.get("monte_carlo") or {},
            "life_cycle": result.get("life_cycle"),
            "solvency_state": result.get("solvency_state"),
            "altman_z": result.get("altman_z") or {},
            "scenario_order_guard_applied": bool(result.get("scenario_order_guard_applied")),
            "integrity_notes": result.get("integrity_notes") or [],
            "company_quality_state": ((result.get("company_quality") or {}).get("state")),
            "valuation_policy": result.get("valuation_policy") or {},
            "valuation_impact_ledger": result.get("valuation_impact_ledger") or [],
            "financial_basis": str(latest_period.get("period_type") or "FY"),
        }, "latest_engine_result": result,
    }
    model.calculation_version = ENGINE_VERSION; model.updated_by = user_id

    text_updates: list[str] = []
    if history:
        latest = history[-1]; previous = history[-2] if len(history) > 1 else None
        label = str(latest.get("period_label") or (f"FY{latest.get('fiscal_year')}" if latest.get("fiscal_year") else "Current"))
        revenue, revenue_prev = n(latest.get("revenue")), n((previous or {}).get("revenue")); revenue_growth = _growth(revenue, revenue_prev)
        gross_margin = _margin(latest.get("gross_profit"), latest.get("revenue")); operating_margin = _margin(latest.get("operating_income"), latest.get("revenue")); net_margin = _margin(latest.get("net_income"), latest.get("revenue")); fcf_margin = _margin(latest.get("fcf"), latest.get("revenue")); inventory_growth = _growth(n(latest.get("inventory")), n((previous or {}).get("inventory")))
        lines = [f"{label} filing-derived starting point."]
        if revenue is not None: lines.append(f"Revenue {format_number(revenue, 'AUTO')}{f' ({revenue_growth:+.1f}% vs comparison period)' if revenue_growth is not None else ''}.")
        margin_bits = [f"{lbl} {val:.1f}%" for lbl, val in (("gross", gross_margin), ("operating", operating_margin), ("net", net_margin), ("FCF", fcf_margin)) if val is not None]
        if margin_bits: lines.append("Margins: " + ", ".join(margin_bits) + ".")
        if latest.get("inventory") is not None: lines.append(f"Inventory {format_number(latest.get('inventory'), 'AUTO')}{f' ({inventory_growth:+.1f}% vs comparison period)' if inventory_growth is not None else ''}.")
        if latest.get("receivables") is not None or latest.get("payables") is not None: lines.append(f"Receivables {format_number(latest.get('receivables'), 'AUTO')} · Payables {format_number(latest.get('payables'), 'AUTO')}.")
        if _replaceable("numbers", research.numbers, auto_hashes): _set_auto(research, "numbers", "numbers", "\n".join(lines), auto_hashes); text_updates.append("numbers")

        flow_lines = [f"{label} cash-flow starting point."]
        for lbl, value in (("CFO", latest.get("cfo")), ("Capex", latest.get("capex")), ("FCF", latest.get("fcf"))):
            if value is not None: flow_lines.append(f"{lbl}: {format_number(value, 'AUTO')}.")
        if _replaceable("flows_summary", research.flows_summary, auto_hashes): _set_auto(research, "flows_summary", "flows_summary", "\n".join(flow_lines), auto_hashes); text_updates.append("financial-flows")

    if _replaceable("valuation_notes", research.valuation_notes, auto_hashes):
        basis = str(latest_period.get("period_type") or "FY")
        quality_state = str(((result.get("company_quality") or {}).get("state") or "INSUFFICIENT EVIDENCE"))
        policy = dict(result.get("valuation_policy") or {})
        text = (
            f"Multi-method intrinsic valuation on {basis} fundamentals using the Economic Reality equity bridge, P/E, EV/Sales and FCF-yield robust blend; DCF is an independent cross-check. Company quality={quality_state}. Automatic downside policy: +{int(policy.get('risk_premium_bps') or 0)} bps discount-rate premium, -{int(policy.get('growth_haircut_bps') or 0)} bps growth haircut, -{int(policy.get('terminal_growth_haircut_bps') or 0)} bps terminal-growth haircut. Multiples source={calibration.get('source')}, historical sample={calibration.get('sample_size', 0)}. Current market price is comparison-only and never constructs intrinsic value."
            if result.get("quality") == "INTRINSIC"
            else "DATA WARNING: one or more valuation inputs are incomplete. Bear/Base/Bull remain visible using the last stored case or an explicit provisional fallback; fallback values are not intrinsic evidence."
        )
        _set_auto(research, "valuation_notes", "valuation_notes", text, auto_hashes); text_updates.append("valuation")

    if _replaceable("business", research.business, auto_hashes) and (company.sector or company.industry):
        quality_profile = dict(result.get("company_quality") or {})
        quality_state = str(quality_profile.get("state") or "INSUFFICIENT EVIDENCE")
        headline = str(quality_profile.get("headline") or "")
        alarms = list(quality_profile.get("alarm_bells") or [])
        strengths = list(quality_profile.get("strengths") or [])
        lines = [
            f"Company classification: sector {company.sector or '—'}; industry {company.industry or '—'}; valuation family {company_type}.",
            f"Filed economic-quality read: {quality_state}. {headline}",
        ]
        if strengths:
            lines.append("Strengths: " + "; ".join(str(x.get("detail") or "") for x in strengths[:3] if x.get("detail")) + ".")
        if alarms:
            lines.append("Alarm bells: " + "; ".join(str(x.get("detail") or "") for x in alarms[:4] if x.get("detail")) + ".")
        lines.append("This is a filing-derived economic-quality assessment. Moat, customer concentration, competitive position and product durability still require sourced Business evidence before approval.")
        _set_auto(research, "business", "business", "\n".join(lines), auto_hashes); text_updates.append("business")

    text_updates.extend(_auto_research_sections(coverage=coverage, research=research, company=company, security=security, history=history, result=result, cases=case_inputs, current_price=current_price, company_type=company_type, auto_hashes=auto_hashes))
    model.assumptions = dict(model.assumptions or {}) | {"auto_text_hashes": auto_hashes}
    research.updated_by = user_id
    db.session.commit()
    return {"coverage_id": coverage.id, "ticker": security.ticker, "scenario_values": {name: result["scenarios"][name].get("fair_value") for name in ("BEAR", "BASE", "BULL")}, "scenario_updates": changed_scenarios, "text_updates": sorted(set(text_updates)), "meta": model.assumptions.get("auto_draft") or {}}


__all__ = ["AUTO_MARKER", "prefill_coverage", "_financial_history", "_point_in_time_calibration"]
