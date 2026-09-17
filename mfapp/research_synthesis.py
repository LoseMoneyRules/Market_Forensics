from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any

from .core_models import (
    BearCaseItem,
    CalculationRun,
    Catalyst,
    Coverage,
    DataQualityIssue,
    Expectation,
    FinancialPeriod,
    HistoricalPrice,
    MarketSnapshot,
    Provenance,
    RefreshRun,
    Security,
    Source,
    ValuationModel,
)
from .current_financials import current_row
from .extensions import db
from .services import valuation_result


ENGINE_VERSION = "0.2.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def business_update_status(company_id: int, readiness: dict[str, Any]) -> dict[str, Any]:
    latest_period = FinancialPeriod.query.filter_by(company_id=company_id).order_by(
        FinancialPeriod.end_date.desc(), FinancialPeriod.id.desc()
    ).first()
    latest_source = Source.query.filter_by(company_id=company_id).order_by(Source.retrieved_at.desc()).first()
    latest_refresh = RefreshRun.query.filter_by(company_id=company_id).order_by(RefreshRun.started_at.desc()).first()
    next_estimate = None
    if latest_period and latest_period.filed_at:
        next_estimate = latest_period.filed_at + timedelta(days=91)
        while next_estimate < date.today():
            next_estimate += timedelta(days=91)
    outstanding = [gate["label"] for gate in readiness.get("gates", []) if not gate.get("approved")]
    return {
        "fundamentals_cadence": "SEC fundamentals refresh on each 10-Q/10-K; quote refresh is independent and runs on the market freshness policy.",
        "latest_period": f"{latest_period.period_type} FY{latest_period.fiscal_year} · {latest_period.end_date}" if latest_period else None,
        "latest_filing_date": _iso(latest_period.filed_at) if latest_period else None,
        "latest_source_retrieved": _iso(latest_source.retrieved_at) if latest_source else None,
        "latest_refresh": {
            "type": latest_refresh.refresh_type,
            "status": latest_refresh.status,
            "started_at": _iso(latest_refresh.started_at),
            "finished_at": _iso(latest_refresh.finished_at),
        } if latest_refresh else None,
        "estimated_next_filing": _iso(next_estimate),
        "outstanding_gates": outstanding,
        "outstanding_count": len(outstanding),
    }


def expectations_chart(coverage_id: int) -> list[dict[str, Any]]:
    rows = []
    for item in Expectation.query.filter_by(coverage_id=coverage_id).order_by(Expectation.period_label, Expectation.metric).all():
        market = _num(item.market_value)
        internal = _num(item.internal_value)
        delta = None
        delta_pct = None
        if market is not None and internal is not None:
            delta = internal - market
            if market != 0:
                delta_pct = (internal / market - 1.0) * 100.0
        rows.append({
            "label": f"{item.metric} {item.period_label}".strip(),
            "metric": item.metric,
            "period": item.period_label,
            "market": market,
            "internal": internal,
            "delta": delta,
            "delta_pct": delta_pct,
            "unit": item.unit,
            "confidence": item.confidence,
        })
    return rows


def valuation_price_history(security_id: int, days: int = 730) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=max(365, min(int(days), 900)))
    rows = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security_id,
        HistoricalPrice.trade_date >= cutoff,
    ).order_by(HistoricalPrice.trade_date.asc(), HistoricalPrice.id.asc()).all()
    by_day: dict[date, Any] = {}
    for row in rows:
        by_day[row.trade_date] = row
    ordered = [by_day[key] for key in sorted(by_day)]
    step = max(1, len(ordered) // 360)
    sampled = ordered[::step]
    if ordered and sampled[-1].trade_date != ordered[-1].trade_date:
        sampled.append(ordered[-1])
    return [
        {
            "date": row.trade_date.isoformat(),
            "price": _num(row.close_split_adjusted) or _num(row.close_raw),
            "provider": row.provider,
            "quality": row.quality,
            "retrieved_at": _iso(row.retrieved_at),
        }
        for row in sampled
    ]


def build_synthesis(*, coverage: Coverage, security: Security, company: Any, research: Any, risk: Any,
                    model: Any, market: Any, valuation: dict[str, Any], intelligence: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    price = _num(market.price) if market else _num(valuation.get("current_price"))
    bear = _num(valuation.get("bear")); base = _num(valuation.get("base")); bull = _num(valuation.get("bull"))
    expected = _num(valuation.get("expected_value"))
    base_gap = ((base / price - 1.0) * 100.0) if base is not None and price not in (None, 0) else None
    expected_gap = ((expected / price - 1.0) * 100.0) if expected is not None and price not in (None, 0) else None
    horizon = int((model.assumptions or {}).get("horizon_years") or 5) if model else 5
    target_year = date.today().year + horizon

    positives = [x for x in intelligence.get("signals", []) if str(x.get("tone")).lower() == "positive"]
    negatives = [x for x in intelligence.get("signals", []) if str(x.get("tone")).lower() in {"negative", "watch"}]
    micro_for = [{"label": x.get("label"), "detail": x.get("detail"), "section": x.get("section")} for x in positives[:5]]
    micro_against = [{"label": x.get("label"), "detail": x.get("detail"), "section": x.get("section")} for x in negatives[:5]]

    macro_notes = []
    if getattr(company, "sector", "") or getattr(company, "industry", ""):
        macro_notes.append({"label": "Industry context", "detail": " · ".join(x for x in (company.sector, company.industry) if x), "source": "company classification"})
    if getattr(research, "variant_market", "").strip():
        macro_notes.append({"label": "Market / industry view", "detail": research.variant_market.strip(), "source": "CONTROL research note"})
    if not macro_notes:
        macro_notes.append({"label": "Macro / industry evidence", "detail": "No explicit sourced macro/industry evidence is stored yet.", "source": "missing evidence"})

    catalysts = Catalyst.query.filter_by(coverage_id=coverage.id, status="OPEN").order_by(Catalyst.expected_date.asc(), Catalyst.id.asc()).limit(5).all()
    bear_items = BearCaseItem.query.filter_by(coverage_id=coverage.id, status="OPEN").order_by(BearCaseItem.invalidates.desc(), BearCaseItem.id.asc()).limit(5).all()
    expectations = expectations_chart(coverage.id)
    expectation_diffs = [row for row in expectations if row["delta_pct"] is not None]
    expectation_diffs.sort(key=lambda row: abs(row["delta_pct"]), reverse=True)

    why = []
    if base_gap is not None:
        why.append(f"Base fair value is {base_gap:+.1f}% vs the verified market reference.")
    if expectation_diffs:
        top = expectation_diffs[0]
        why.append(f"Largest stored expectation variant: {top['label']} {top['delta_pct']:+.1f}% vs market input.")
    if getattr(research, "variant_us", "").strip():
        why.append(research.variant_us.strip())
    if not why:
        why.append("Model/market difference is not yet fully evidenced; complete Expectations and variant-perception inputs.")

    next_steps = []
    pending = [gate["label"] for gate in readiness.get("gates", []) if not gate.get("approved")]
    if pending:
        next_steps.append("Approve current evidence hashes after review: " + ", ".join(pending[:5]) + ("…" if len(pending) > 5 else ""))
    if catalysts:
        dated = next((row for row in catalysts if row.expected_date), None)
        if dated:
            next_steps.append(f"Watch {dated.title} around {dated.expected_date.isoformat()}.")
    if risk and getattr(risk, "thesis_invalidation", "").strip():
        next_steps.append("Test the locked thesis invalidation before changing direction.")
    if not next_steps:
        next_steps.append("Continue monitoring; change the thesis only when evidence changes.")

    price_verified = bool(market and str(getattr(market, "quality", "")).upper() not in {"", "FALLBACK", "ERROR"})

    gate_map = {str(g.get("key")): g for g in readiness.get("gates", [])}
    lens_specs = [
        ("Business", "business"),
        ("Numbers", "numbers"),
        ("Expectations", "expectations"),
        ("Valuation", "valuation"),
        ("Bear Case", "bear-case"),
        ("Catalysts", "catalysts"),
        ("Financial Flows", "financial-flows"),
        ("Management", "management"),
        ("Tape / Flows", "tape"),
        ("Monitoring", "monitoring"),
        ("Sources / Audit", "audit"),
    ]
    lenses = []
    for label, key in lens_specs:
        gate = gate_map.get(key) or {}
        if gate.get("approved"):
            state = "APPROVED"
        elif gate.get("evidence_ready"):
            state = "REVIEW"
        else:
            state = "MISSING"
        lenses.append({"label": label, "key": key, "state": state})

    why_now = []
    if base_gap is not None and abs(base_gap) >= 15:
        why_now.append(f"Valuation dislocation is material at {base_gap:+.1f}% vs Base.")
    if catalysts:
        dated = next((row for row in catalysts if row.expected_date), None)
        if dated:
            why_now.append(f"Open catalyst: {dated.title} around {dated.expected_date.isoformat()}.")
    if positives:
        why_now.append(f"{len(positives)} weighted supporting evidence signal(s) are active.")
    if not why_now:
        why_now.append("No forcing event is strong enough yet; keep the company in evidence-driven monitoring.")

    why_not_yet = []
    if pending:
        why_not_yet.append("Research gates still pending: " + ", ".join(pending[:5]) + ("…" if len(pending) > 5 else ""))
    if intelligence.get("warnings"):
        why_not_yet.extend(str(x) for x in intelligence.get("warnings", [])[:2])
    if negatives:
        why_not_yet.append(f"{len(negatives)} opposing/watch evidence signal(s) remain unresolved.")
    if not why_not_yet:
        why_not_yet.append("No major process blocker is visible; use Validate before treating the research file as decision-ready.")

    what_changes = list(next_steps[:3])
    if expectation_diffs:
        top = expectation_diffs[0]
        what_changes.append(f"Resolve the largest expectation gap: {top['label']} ({top['delta_pct']:+.1f}%).")
    if not what_changes:
        what_changes.append("A new filing, catalyst outcome, or threshold breach should change the read—not price movement alone.")

    what_kills = []
    if risk and getattr(risk, "thesis_invalidation", "").strip():
        what_kills.append(risk.thesis_invalidation.strip())
    for item in bear_items:
        if item.invalidates and item.title:
            what_kills.append(item.title)
    if not what_kills:
        what_kills.append("No explicit thesis-kill condition is locked yet.")

    return {
        "framework": "PRICE → FAIR VALUE → WHY → WHEN",
        "price": price,
        "price_verified": price_verified,
        "price_provider": getattr(market, "provider", None) if market else None,
        "price_as_of": _iso(getattr(market, "as_of", None)) if market else None,
        "bear": bear, "base": base, "bull": bull, "expected_value": expected,
        "base_gap_pct": base_gap, "expected_gap_pct": expected_gap,
        "why": why[:4],
        "micro_for": micro_for,
        "micro_against": micro_against,
        "macro": macro_notes[:4],
        "catalysts": [{"title": row.title, "direction": row.direction, "date": _iso(row.expected_date), "evidence": row.evidence} for row in catalysts],
        "bear_case": [{"title": row.title, "severity": row.severity, "invalidates": row.invalidates, "evidence": row.evidence} for row in bear_items],
        "invalidation": getattr(risk, "thesis_invalidation", "") if risk else "",
        "invalidation_locked": bool(risk and getattr(risk, "invalidation_locked_at", None)),
        "horizon_years": horizon,
        "target_year": target_year,
        "next": next_steps,
        "lenses": lenses,
        "why_now": why_now[:4],
        "why_not_yet": why_not_yet[:4],
        "what_changes": what_changes[:4],
        "what_kills": what_kills[:4],
        "research_action": intelligence.get("action") or "WAIT",
        "confidence": intelligence.get("confidence") or "LOW",
        "readiness": {"done": readiness.get("done", 0), "total": readiness.get("total", 0)},
        "engine_version": ENGINE_VERSION,
    }


def audit_2_summary(company_id: int, security_id: int, coverage_id: int | None = None) -> dict[str, Any]:
    """Return evidence/calculation lineage only for the current company/security/research coverage."""
    now = utcnow()
    sources = Source.query.filter_by(company_id=company_id).order_by(Source.retrieved_at.desc()).all()
    periods = FinancialPeriod.query.filter_by(company_id=company_id).order_by(FinancialPeriod.end_date.desc()).all()
    period_ids = [row.id for row in periods]
    provenance = Provenance.query.join(FinancialPeriod, Provenance.financial_period_id == FinancialPeriod.id).filter(
        FinancialPeriod.company_id == company_id
    ).order_by(Provenance.created_at.desc()).all()
    issues = DataQualityIssue.query.filter_by(company_id=company_id, status="OPEN").order_by(DataQualityIssue.detected_at.desc()).all()
    refreshes = RefreshRun.query.filter(
        db.or_(RefreshRun.company_id == company_id, RefreshRun.security_id == security_id)
    ).order_by(RefreshRun.started_at.desc()).limit(50).all()

    stale_sources = []
    for source in sources:
        age_days = (now - source.retrieved_at).total_seconds() / 86400.0 if source.retrieved_at else None
        limit = 3 if source.provider.upper() not in {"SEC"} else 120
        if age_days is not None and age_days > limit:
            stale_sources.append({"id": source.id, "provider": source.provider, "title": source.title, "age_days": round(age_days, 1), "retrieved_at": _iso(source.retrieved_at)})

    market = MarketSnapshot.query.filter_by(security_id=security_id).order_by(MarketSnapshot.as_of.desc(), MarketSnapshot.id.desc()).first()
    if market and market.as_of:
        market_age_hours = (now - market.as_of).total_seconds() / 3600.0
        if market_age_hours > 24:
            stale_sources.append({
                "id": f"market:{market.id}", "provider": market.provider, "title": "Current market price",
                "age_days": round(market_age_hours / 24.0, 1), "retrieved_at": _iso(market.created_at),
            })

    missing_provenance = []
    latest_periods = periods[:8]
    fields = ("revenue", "gross_profit", "operating_income", "net_income", "cfo", "fcf")
    prov_keys = {(p.financial_period_id, p.field_name) for p in provenance}
    for period in latest_periods:
        for field in fields:
            if (period.id, field) not in prov_keys:
                missing_provenance.append({"period_id": period.id, "period": f"{period.period_type} FY{period.fiscal_year}", "field": field})

    source_disagreement = [
        {"code": row.code, "severity": row.severity, "message": row.message, "detected_at": _iso(row.detected_at)}
        for row in issues if "DISAGREE" in row.code.upper() or "MISMATCH" in row.code.upper() or "BRIDGE" in row.code.upper()
    ]

    lineage = []
    if market:
        lineage.append({
            "field": "current_price", "provider": market.provider, "source": "Market snapshot", "accession": None,
            "period": _iso(market.as_of), "retrieved_at": _iso(market.created_at), "freshness_at": _iso(market.as_of),
            "transformation": market.quality or "OBSERVED", "calculation_version": "source observation",
            "manual_override": False, "restated": False, "notes": f"{market.currency} {market.price}",
        })

    valuation_model = None
    if coverage_id is not None:
        valuation_model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).order_by(ValuationModel.updated_at.desc()).first()
    if valuation_model:
        lineage.append({
            "field": "bear_base_bull_expected_value", "provider": "INTERNAL",
            "source": valuation_model.name or "Valuation model", "accession": None, "period": "current research horizon",
            "retrieved_at": _iso(valuation_model.updated_at), "freshness_at": _iso(valuation_model.updated_at),
            "transformation": valuation_model.method, "calculation_version": valuation_model.calculation_version or ENGINE_VERSION,
            "manual_override": False, "restated": False,
            "notes": "Scenario outputs use the active stored assumptions and current verified market context.",
        })

    for p in provenance[:200]:
        source = db.session.get(Source, p.source_id) if p.source_id else None
        period = db.session.get(FinancialPeriod, p.financial_period_id) if p.financial_period_id else None
        lineage.append({
            "field": p.field_name,
            "provider": p.provider or (source.provider if source else ""),
            "source": source.title if source else None,
            "accession": source.accession_no if source else None,
            "period": f"{period.period_type} FY{period.fiscal_year} · {period.end_date}" if period else None,
            "retrieved_at": _iso(source.retrieved_at if source else p.created_at),
            "freshness_at": _iso(p.freshness_at),
            "transformation": p.raw_or_normalized,
            "calculation_version": p.calculation_version or ENGINE_VERSION,
            "manual_override": bool(p.manual_override),
            "restated": bool(p.restated),
            "notes": p.notes,
        })

    calc_query = CalculationRun.query
    if coverage_id is not None:
        clauses = [CalculationRun.coverage_id == coverage_id]
        if period_ids:
            clauses.append(CalculationRun.financial_period_id.in_(period_ids))
        calc_query = calc_query.filter(db.or_(*clauses))
    elif period_ids:
        calc_query = calc_query.filter(CalculationRun.financial_period_id.in_(period_ids))
    latest_calc = calc_query.order_by(CalculationRun.started_at.desc()).limit(50).all()
    calc_lineage = [{
        "type": row.calculation_type, "version": row.calculation_version, "status": row.status,
        "started_at": _iso(row.started_at), "finished_at": _iso(row.finished_at), "error_id": row.error_id,
    } for row in latest_calc]

    warnings = []
    if stale_sources: warnings.append(f"{len(stale_sources)} stale source record(s) detected under provider-specific freshness policy.")
    if source_disagreement: warnings.append(f"{len(source_disagreement)} source/accounting disagreement or bridge issue(s) are open.")
    if missing_provenance: warnings.append(f"{len(missing_provenance)} latest-period KPI lineage link(s) are missing.")
    failed = [row for row in refreshes if row.status == "FAILED"]
    if failed: warnings.append(f"{len(failed)} recent refresh failure(s) are visible in the audit trail.")

    return {
        "engine_version": ENGINE_VERSION,
        "scope": {"company_id": company_id, "security_id": security_id, "coverage_id": coverage_id},
        "source_count": len(sources), "provenance_count": len(provenance),
        "stale_sources": stale_sources[:30], "source_disagreement": source_disagreement[:30],
        "missing_provenance": missing_provenance[:50], "lineage": lineage,
        "calculation_lineage": calc_lineage, "warnings": warnings,
    }


__all__ = [
    "ENGINE_VERSION", "business_update_status", "expectations_chart", "valuation_price_history",
    "build_synthesis", "audit_2_summary",
]
