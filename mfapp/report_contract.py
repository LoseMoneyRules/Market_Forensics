from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Any

from .core_models import (
    BearCaseItem, Catalyst, Expectation, FinancialFlow, FinancialPeriod,
    HistoricalPrice, HistoricalTestRun, HistoricalTestSample, ManagementAssessment,
    MonitoringHistory, MonitoringRule, Source,
)
from .current_financials import annual_rows, current_row


REPORT_CONTRACT_VERSION = "0.3.1"


def _n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _iso(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value or "")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [dict(row) for row in rows[:limit]]


def _fundamentals(company_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history = list(reversed(annual_rows(company_id, 8)))
    out = []
    for row in history:
        metrics = dict(row.get("metrics") or {})
        out.append({
            "period": f"FY{row.get('fiscal_year')}",
            "period_end": _iso(row.get("period_end")),
            "revenue": _n(row.get("revenue")),
            "gross_profit": _n(row.get("gross_profit")),
            "operating_income": _n(row.get("operating_income")),
            "pretax_income": _n(row.get("pretax_income")),
            "net_income": _n(row.get("net_income")),
            "cfo": _n(row.get("cfo")),
            "fcf": _n(row.get("fcf")),
            "inventory": _n(row.get("inventory")),
            "receivables": _n(row.get("receivables")),
            "diluted_shares": _n(row.get("diluted_shares")),
            "revenue_growth_pct": _n(metrics.get("revenue_growth_pct")),
            "gross_margin_pct": _n(metrics.get("gross_margin_pct")),
            "operating_margin_pct": _n(metrics.get("operating_margin_pct")),
            "net_margin_pct": _n(metrics.get("net_margin_pct")),
            "fcf_margin_pct": _n(metrics.get("fcf_margin_pct")),
            "cfo_margin_pct": _n(metrics.get("cfo_margin_pct")),
            "cfo_to_net_income": _n(metrics.get("cfo_to_net_income")),
            "fcf_to_net_income": _n(metrics.get("fcf_to_net_income")),
            "roic_pct": _n(metrics.get("roic_pct")),
            "reported_roic_pct": _n(metrics.get("reported_roic_pct")),
            "economic_roic_pct": _n(metrics.get("economic_roic_pct")),
            "lease_adjusted_roic_pct": _n(metrics.get("lease_adjusted_roic_pct")),
            "net_debt_to_fcf": _n(metrics.get("net_debt_to_fcf")),
            "reported_net_debt": _n(metrics.get("reported_net_debt")),
            "economic_net_debt": _n(metrics.get("economic_net_debt")),
            "net_debt_basis": _text(metrics.get("net_debt_basis")),
            "operating_lease_liability": _n(metrics.get("operating_lease_liability")),
            "lease_revenue_productivity_x": _n(metrics.get("lease_revenue_productivity_x")),
            "growth_capex_proxy": _n(metrics.get("growth_capex_proxy")),
            "owner_cash_proxy": _n(metrics.get("owner_cash_proxy")),
            "fcf_after_sbc": _n(metrics.get("fcf_after_sbc")),
            "economic_reality_quality": _text(metrics.get("economic_reality_quality")),
            "inventory_to_revenue_pct": _n(metrics.get("inventory_to_revenue_pct")),
            "receivables_to_revenue_pct": _n(metrics.get("receivables_to_revenue_pct")),
            "dso": _n(metrics.get("dso")),
            "dio": _n(metrics.get("dio")),
            "dpo": _n(metrics.get("dpo")),
            "ccc": _n(metrics.get("cash_conversion_days")),
            "share_count_growth_pct": _n(metrics.get("share_count_growth_pct")),
        })
    current = current_row(company_id) or {}
    metrics = dict(current.get("metrics") or {})
    current_out = {
        "period": _text(current.get("period_label")),
        "comparison_basis": _text(current.get("comparison_basis")),
        "revenue": _n(current.get("revenue")),
        "gross_margin_pct": _n(metrics.get("gross_margin_pct")),
        "operating_margin_pct": _n(metrics.get("operating_margin_pct")),
        "net_margin_pct": _n(metrics.get("net_margin_pct")),
        "fcf": _n(current.get("fcf")),
        "fcf_margin_pct": _n(metrics.get("fcf_margin_pct")),
        "cfo_to_net_income": _n(metrics.get("cfo_to_net_income")),
        "roic_pct": _n(metrics.get("roic_pct")),
        "reported_roic_pct": _n(metrics.get("reported_roic_pct")),
        "economic_roic_pct": _n(metrics.get("economic_roic_pct")),
        "lease_adjusted_roic_pct": _n(metrics.get("lease_adjusted_roic_pct")),
        "net_debt_to_fcf": _n(metrics.get("net_debt_to_fcf")),
        "reported_net_debt": _n(metrics.get("reported_net_debt")),
        "economic_net_debt": _n(metrics.get("economic_net_debt")),
        "net_debt_basis": _text(metrics.get("net_debt_basis")),
        "operating_lease_liability": _n(metrics.get("operating_lease_liability")),
        "operating_lease_share_of_liabilities_pct": _n(metrics.get("operating_lease_share_of_liabilities_pct")),
        "lease_revenue_productivity_x": _n(metrics.get("lease_revenue_productivity_x")),
        "growth_capex_proxy": _n(metrics.get("growth_capex_proxy")),
        "maintenance_capex_proxy": _n(metrics.get("maintenance_capex_proxy")),
        "owner_cash_proxy": _n(metrics.get("owner_cash_proxy")),
        "fcf_after_sbc": _n(metrics.get("fcf_after_sbc")),
        "economic_reality_quality": _text(metrics.get("economic_reality_quality")),
        "economic_reality_unresolved": bool(metrics.get("economic_reality_unresolved")),
        "economic_reality_flags": list(metrics.get("economic_reality_flags") or []),
        "inventory_to_revenue_pct": _n(metrics.get("inventory_to_revenue_pct")),
        "receivables_to_revenue_pct": _n(metrics.get("receivables_to_revenue_pct")),
        "dso": _n(metrics.get("dso")), "dio": _n(metrics.get("dio")),
        "dpo": _n(metrics.get("dpo")), "ccc": _n(metrics.get("cash_conversion_days")),
        "share_count_growth_pct": _n(metrics.get("share_count_growth_pct")),
    }
    return out, current_out


def _valuation(ctx: dict[str, Any]) -> dict[str, Any]:
    valuation = dict(ctx.get("valuation") or {})
    model = ctx.get("model")
    saved = dict(getattr(model, "assumptions", None) or {})
    latest = dict(saved.get("latest_engine_result") or {})
    engine_scenarios = dict(latest.get("scenarios") or {})
    db_scenarios = {str(row.name or "").upper(): row for row in (getattr(model, "scenarios", None) or [])}
    scenarios = []
    for name in ("BEAR", "BASE", "BULL"):
        db_row = db_scenarios.get(name)
        engine = dict(engine_scenarios.get(name) or {})
        outputs = dict(getattr(db_row, "outputs", None) or {})
        detail = engine or outputs
        target = _n(detail.get("fair_value"))
        if target is None:
            target = _n(getattr(db_row, "equity_value_per_share", None))
        if target is None:
            target = _n(valuation.get(name.lower()))
        probability = _n(detail.get("probability"))
        if probability is None:
            probability = _n(getattr(db_row, "probability", None))
        methods = []
        labels = (("pe", "P/E"), ("ev_sales", "EV / Sales"), ("fcf_yield", "FCF Yield"))
        effective = dict(detail.get("effective_weights") or {})
        for key, label in labels:
            value = _n(detail.get(key))
            weight = _n(effective.get(key))
            if value is not None or weight is not None:
                methods.append({"key": key, "label": label, "value": value, "weight": weight})
        scenarios.append({
            "name": name,
            "target": target,
            "probability": probability,
            "quality": _text(detail.get("quality") or valuation.get("base_quality") if name == "BASE" else detail.get("quality")),
            "fallback_source": _text(detail.get("fallback_source")),
            "range_low": _n(detail.get("range_low")),
            "range_high": _n(detail.get("range_high")),
            "dcf": _n(detail.get("dcf")),
            "methods": methods,
            "flags": [str(x) for x in (detail.get("flags") or []) if x],
            "inputs": dict(getattr(db_row, "inputs", None) or {}),
        })
    base_quality = _text(valuation.get("base_quality") or ((engine_scenarios.get("BASE") or {}).get("quality")) or latest.get("quality") or "DATA_WARNING").upper()
    decision_grade = bool(valuation.get("decision_grade")) or base_quality in {"INTRINSIC", "MANUAL_OVERRIDE"}
    warnings = list(dict.fromkeys(
        [str(x) for x in (latest.get("warnings") or []) if x] +
        [flag for row in scenarios for flag in row["flags"] if str(flag).startswith("DATA WARNING")]
    ))
    return {
        "current_price": _n(valuation.get("current_price") if valuation.get("current_price") is not None else ctx.get("market").price if ctx.get("market") else None),
        "bear": _n(valuation.get("bear")), "base": _n(valuation.get("base")), "bull": _n(valuation.get("bull")),
        "expected_value": _n(valuation.get("expected_value") if valuation.get("expected_value") is not None else latest.get("expected_value")),
        "base_gap_pct": _n((ctx.get("intelligence") or {}).get("base_gap_pct")),
        "quality": _text(valuation.get("quality") or latest.get("quality") or base_quality).upper(),
        "base_quality": base_quality,
        "decision_grade": decision_grade,
        "provisional": not decision_grade,
        "warnings": warnings,
        "share_basis": {
            "shares": _n(saved.get("current_shares")),
            "source": _text(saved.get("share_source") or "UNRESOLVED"),
            "verified": bool(saved.get("share_basis_verified")),
            "note": _text(saved.get("share_basis_note")),
        },
        "weights": {k: _n(v) for k, v in dict(saved.get("weights") or {}).items()},
        "horizon_years": int(saved.get("horizon_years") or 5),
        "calibration": dict(saved.get("calibration") or {}),
        "scenarios": scenarios,
        "company_quality": dict(latest.get("company_quality") or valuation.get("company_quality") or {}),
        "valuation_policy": dict(latest.get("valuation_policy") or valuation.get("valuation_policy") or {}),
        "valuation_impact_ledger": list(latest.get("valuation_impact_ledger") or valuation.get("valuation_impact_ledger") or []),
        "method_exclusions": list(latest.get("method_exclusions") or valuation.get("method_exclusions") or []),
        "effective_input_weights": dict(latest.get("effective_input_weights") or valuation.get("effective_input_weights") or {}),
        "engine_version": _text(latest.get("engine_version") or getattr(model, "calculation_version", "")),
    }


def _price_history(security_id: int) -> list[dict[str, Any]]:
    rows = HistoricalPrice.query.filter_by(security_id=security_id).order_by(HistoricalPrice.trade_date.desc()).limit(520).all()
    out = []
    for row in reversed(rows):
        price = _n(getattr(row, "close_split_adjusted", None))
        if price is None:
            price = _n(getattr(row, "close_raw", None))
        if price is not None:
            out.append({"date": _iso(row.trade_date), "price": price})
    return out


def _validation(coverage_id: int, readiness: dict[str, Any]) -> dict[str, Any]:
    run = HistoricalTestRun.query.filter_by(coverage_id=coverage_id).order_by(HistoricalTestRun.created_at.desc(), HistoricalTestRun.id.desc()).first()
    policy = dict(readiness.get("validation") or {})
    if run is None:
        return {
            "state": _text(policy.get("state") or "NOT RUN"), "available": False, "status": "NOT RUN",
            "sample_count": 0, "reliability": None, "lookback_years": None, "history_span": "",
            "valuation_accuracy": None, "direction_accuracy": None, "range_coverage": None,
            "assumption_accuracy": None, "summary": {}, "samples": [],
        }
    samples = HistoricalTestSample.query.filter_by(run_id=run.id).order_by(HistoricalTestSample.anchor_date.asc()).all()
    sample_rows = []
    for row in samples:
        outcomes = dict(row.outcomes or {})
        sample_rows.append({
            "date": _iso(row.anchor_date),
            "price_then": _n(row.anchor_price), "bear_then": _n(row.bear_value),
            "base_then": _n(row.base_value), "bull_then": _n(row.bull_value),
            "expected_then": _n(row.expected_value),
            "price_1y": _n(outcomes.get("price_1y")),
            "status": _text(row.status),
        })
    span = ""
    if sample_rows:
        span = f"{sample_rows[0]['date']} to {sample_rows[-1]['date']}"
    return {
        "state": _text(policy.get("state") or run.status or "REVIEW"), "available": True,
        "status": _text(run.status), "sample_count": int(run.sample_size or len(samples)),
        "reliability": _n(run.reliability_score), "lookback_years": int(run.lookback_years or 0),
        "history_span": span, "valuation_accuracy": _n(run.valuation_accuracy),
        "direction_accuracy": _n(run.direction_accuracy), "range_coverage": _n(run.range_coverage),
        "assumption_accuracy": _n(run.assumption_accuracy), "summary": dict(run.summary or {}),
        "samples": sample_rows,
    }


def _monitoring(coverage_id: int) -> list[dict[str, Any]]:
    rules = MonitoringRule.query.filter_by(coverage_id=coverage_id, is_active=True).order_by(MonitoringRule.locked_pre_investment.desc(), MonitoringRule.updated_at.desc(), MonitoringRule.id.desc()).all()
    out = []
    for rule in rules:
        hist = MonitoringHistory.query.filter_by(rule_id=rule.id).order_by(MonitoringHistory.observed_at.desc(), MonitoringHistory.id.desc()).first()
        out.append({
            "name": rule.name, "metric": rule.metric, "operator": rule.operator,
            "threshold": _n(rule.threshold_value), "threshold_text": rule.threshold_text,
            "unit": rule.unit, "severity": rule.severity, "locked_pre_investment": bool(rule.locked_pre_investment),
            "current_value": _n(hist.observed_value) if hist else None,
            "status": _text(hist.status if hist else "NOT OBSERVED"),
            "triggered": bool(hist and str(hist.status or "").upper() in {"TRIGGERED", "FAIL", "BREACH"}),
            "last_observation": _iso(hist.observed_at) if hist else "",
            "note": _text(hist.note) if hist else "",
        })
    return out


def _financial_flows(company_id: int) -> list[dict[str, Any]]:
    periods = FinancialPeriod.query.filter_by(company_id=company_id).order_by(FinancialPeriod.end_date.desc(), FinancialPeriod.id.desc()).limit(6).all()
    period_ids = [row.id for row in periods]
    if not period_ids:
        return []
    flows = FinancialFlow.query.filter(FinancialFlow.financial_period_id.in_(period_ids)).order_by(FinancialFlow.created_at.desc(), FinancialFlow.id.desc()).all()
    by_period: dict[int, dict[str, Any]] = {}
    for period in periods:
        by_period[period.id] = {
            "period": f"FY{period.fiscal_year}" if period.period_type == "FY" else f"{period.period_type} {period.fiscal_year}",
            "period_end": _iso(period.end_date), "income_statement": None, "cash_flow": None,
        }
    seen = set()
    for row in flows:
        key = (row.financial_period_id, row.flow_type)
        if key in seen:
            continue
        seen.add(key)
        target = by_period.get(row.financial_period_id)
        if not target:
            continue
        name = "income_statement" if str(row.flow_type).upper() == "INCOME_STATEMENT" else "cash_flow"
        target[name] = dict(row.payload or {})
    return [by_period[p.id] for p in periods if by_period.get(p.id)]


def _sources(company_id: int) -> list[dict[str, Any]]:
    rows = Source.query.filter_by(company_id=company_id).order_by(Source.retrieved_at.desc(), Source.id.desc()).limit(60).all()
    return [{
        "provider": row.provider, "type": row.source_type, "title": row.title,
        "accession": row.accession_no, "published_at": _iso(row.published_at),
        "retrieved_at": _iso(row.retrieved_at), "url": row.url,
        "document": _text((row.meta or {}).get("document") or (row.meta or {}).get("form") or row.accession_no),
    } for row in rows]


def build_report_data(ctx: dict[str, Any], *, mode: str = "full", branding: dict[str, str] | None = None) -> dict[str, Any]:
    coverage = ctx["coverage"]; company = ctx["company"]; security = ctx["security"]
    research = ctx["research"]; risk = ctx["risk"]
    cache = dict(ctx.get("research_cache") or {})
    readiness = dict(ctx.get("readiness") or {})
    intelligence = dict(ctx.get("intelligence") or {})
    lenses = dict(ctx.get("decision_lenses") or {})
    tape = dict(cache.get("tape") or {})
    synthesis = dict(cache.get("synthesis") or {})
    fundamentals, current = _fundamentals(company.id)
    valuation = _valuation(ctx)

    expectations = Expectation.query.filter_by(coverage_id=coverage.id).order_by(Expectation.period_label, Expectation.metric).all()
    bears = BearCaseItem.query.filter_by(coverage_id=coverage.id).order_by(BearCaseItem.invalidates.desc(), BearCaseItem.status, BearCaseItem.id).all()
    catalysts = Catalyst.query.filter_by(coverage_id=coverage.id).order_by(Catalyst.expected_date.asc(), Catalyst.id.asc()).all()
    management = ManagementAssessment.query.filter_by(coverage_id=coverage.id).order_by(ManagementAssessment.as_of.desc(), ManagementAssessment.id.desc()).all()

    promises = list(cache.get("management_promises") or [])
    accountability = list(cache.get("management_accountability") or [])
    management_engine = dict(cache.get("management") or {})

    brand = dict(branding or {})
    report = {
        "contract_version": REPORT_CONTRACT_VERSION,
        "mode": "executive" if str(mode).lower() == "executive" else "full",
        "branding": {
            "title": _text(brand.get("title") or "Market Forensics"),
            "prepared_by": _text(brand.get("prepared_by")),
            "footer": _text(brand.get("footer") or "Lose Money Rules"),
            "logo_url": _text(brand.get("logo_url")),
        },
        "identity": {
            "ticker": security.ticker, "company": company.display_name, "sector": company.sector or "",
            "industry": company.industry or "", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "market_provider": getattr(ctx.get("market"), "provider", "") if ctx.get("market") else "",
            "market_as_of": _iso(getattr(ctx.get("market"), "as_of", None)) if ctx.get("market") else "",
        },
        "conclusion": _text(lenses.get("research_conclusion") or "DATA REVIEW"),
        "decision_lenses": {
            "value": _text(lenses.get("value") or intelligence.get("stance") or "UNVERIFIED"),
            "expectations": _text(lenses.get("expectations") or "UNAVAILABLE"),
            "variant": _text(lenses.get("variant") or "UNPROVEN"),
            "path": _text(lenses.get("path") or "UNCLEAR"),
            "model_confidence": _text(lenses.get("model_confidence") or intelligence.get("confidence") or "UNVALIDATED"),
            "thesis_control": _text(lenses.get("thesis_control") or "UNRESOLVED"),
            "business": _text(lenses.get("business") or "UNPROVEN"),
            "rows": list(lenses.get("rows") or []),
        },
        "valuation": valuation,
        "company_quality": dict(synthesis.get("company_quality") or valuation.get("company_quality") or {}),
        "valuation_impact_ledger": list(synthesis.get("valuation_impact_ledger") or valuation.get("valuation_impact_ledger") or []),
        "thesis": {
            "thesis": _text(research.thesis), "counter_evidence": _text(research.counter_evidence),
            "market_view": _text(research.variant_market), "our_view": _text(research.variant_us),
            "variant_evidence": _text(research.variant_evidence),
            "what_must_be_true": _compact([{"text": str(x)} for x in (synthesis.get("what_changes") or [])], 4),
            "what_proves_wrong": _compact([{"text": str(x)} for x in (synthesis.get("what_kills") or [])], 4),
        },
        "evidence": {
            "for": _compact(list(intelligence.get("supporting_evidence") or []), 5),
            "against": _compact(list(intelligence.get("opposing_evidence") or []), 5),
            "score": _n(intelligence.get("score")), "warnings": [str(x) for x in (intelligence.get("warnings") or [])],
            "blockers": [str(x) for x in (intelligence.get("blockers") or [])],
        },
        "business": {"summary": _text(research.business)},
        "fundamentals": {"current": current, "history": fundamentals, "summary": _text(research.numbers), "forensics": dict(cache.get("fundamentals_forensics") or {})},
        "expectations": {
            "summary": _text(research.expectations),
            "implied": dict(lenses.get("implied_expectations") or {}),
            "rows": [{
                "metric": row.metric, "period": row.period_label, "market": _n(row.market_value),
                "ours": _n(row.internal_value), "unit": row.unit, "confidence": row.confidence,
                "notes": row.notes,
            } for row in expectations],
        },
        "flows": {"summary": _text(research.flows_summary), "periods": _financial_flows(company.id)},
        "management": {
            "summary": _text(research.management_summary), "engine": management_engine,
            "accountability": accountability, "promises": promises,
            "assessments": [{
                "as_of": _iso(row.as_of), "execution": row.execution, "capital_allocation": row.capital_allocation,
                "red_flags": row.red_flags, "notes": row.notes,
            } for row in management[:8]],
        },
        "catalysts": [{
            "event": row.title, "timing": _iso(row.expected_date), "direction": row.direction,
            "status": row.status, "type": row.catalyst_type, "evidence": row.evidence,
        } for row in catalysts],
        "bear_case": [{
            "risk": row.title, "severity": row.severity, "probability": _n(row.probability),
            "invalidates": bool(row.invalidates), "status": row.status, "evidence": row.evidence,
        } for row in bears],
        "tape": {
            "summary": _text(research.tape_summary), "metrics": dict(tape.get("metrics") or {}),
            "market": list(tape.get("market") or []), "daily_market": list(tape.get("daily_market") or []),
            "short_interest": list(tape.get("short_interest") or []), "short_volume": list(tape.get("short_volume") or []),
            "institutional_flow": list(tape.get("institutional_flow") or []), "tape_daily": list(tape.get("tape_daily") or []),
            "ats": list(tape.get("ats") or []), "what_changed": tape.get("what_changed") or "",
            "what_would_change_regime": tape.get("what_would_change_regime") or "",
        },
        "monitoring": {
            "thesis_invalidation": _text(risk.thesis_invalidation),
            "locked_at": _iso(risk.invalidation_locked_at), "summary": _text(research.risk_summary),
            "rules": _monitoring(coverage.id),
        },
        "validation": _validation(coverage.id, readiness),
        "sources": _sources(company.id),
        "price_history": _price_history(security.id),
        "readiness": {
            "done": int(readiness.get("done") or 0), "total": int(readiness.get("total") or 0),
            "ready_to_validate": bool(readiness.get("ready_to_validate")),
        },
        "triangulation": dict(cache.get("triangulation") or {}),
        "data_contract": {
            "materialized_cache_event": cache.get("_event_id"), "cache_generated_at": cache.get("_generated_at"),
            "provider_refresh_started": False, "heavy_analytics_started": False,
        },
    }
    return report


__all__ = ["REPORT_CONTRACT_VERSION", "build_report_data"]
