from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .extensions import db
from .calculations import CALCULATION_VERSION, bias_flags, calculate_valuation, financial_metrics
from .valuation_engine import canonical_valuation_quality, valuation_is_decision_grade
from .core_models import (
    BearCaseItem,
    Catalyst,
    Company,
    Coverage,
    DecisionJournal,
    Expectation,
    FinancialFlow,
    FinancialPeriod,
    InvestmentState,
    ManagementAssessment,
    MarketSnapshot,
    MonitoringHistory,
    MonitoringRule,
    NormalizedFinancial,
    Position,
    Provenance,
    Publication,
    ResearchState,
    RiskPlan,
    Security,
    Snapshot,
    Source,
    ValuationModel,
    ValuationScenario,
)

ROLE_RANK = {"FRIEND": 1, "INSIDER": 2, "CONTROL": 3}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def latest_market_snapshot(security_id: int) -> MarketSnapshot | None:
    return MarketSnapshot.query.filter_by(security_id=security_id).order_by(MarketSnapshot.as_of.desc(), MarketSnapshot.id.desc()).first()

def ensure_security_from_validation(validation) -> tuple[Security, bool]:
    """Return/create the canonical Security for a validated symbol without creating Coverage."""
    ticker = str(validation.ticker or "").strip().upper()
    security = (
        Security.query.filter(db.func.upper(Security.ticker) == ticker)
        .order_by(Security.active.desc(), Security.is_primary.desc(), Security.id.asc())
        .first()
    )
    if security is not None:
        security.active = True
        if validation.exchange and not security.exchange:
            security.exchange = validation.exchange
        if validation.currency and not security.currency:
            security.currency = validation.currency
        if validation.instrument_type and not security.security_type:
            security.security_type = validation.instrument_type
        security.validation_source = validation.source or security.validation_source
        security.validated_at = utcnow()
        return security, False

    company = Company(
        legal_name=validation.name or ticker,
        display_name=validation.name or ticker,
    )
    db.session.add(company)
    db.session.flush()
    security = Security(
        company_id=company.id,
        ticker=ticker,
        exchange=validation.exchange or "",
        security_type=validation.instrument_type or "COMMON_STOCK",
        currency=validation.currency or "USD",
        provider_symbol=ticker.replace(".", "-"),
        validation_source=validation.source or "",
        validated_at=utcnow(),
        active=True,
        is_primary=True,
    )
    db.session.add(security)
    db.session.flush()
    return security, True



def coverage_for_ticker(user_id: int, ticker: str) -> Coverage | None:
    return (
        Coverage.query.join(Security, Coverage.security_id == Security.id)
        .filter(Coverage.user_id == user_id, db.func.upper(Security.ticker) == ticker.upper())
        .first()
    )


def ensure_workspace(coverage: Coverage, user_id: int) -> tuple[ResearchState, RiskPlan, InvestmentState, ValuationModel]:
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    if research is None:
        research = ResearchState(coverage_id=coverage.id, updated_by=user_id)
        db.session.add(research)
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    if risk is None:
        risk = RiskPlan(coverage_id=coverage.id, updated_by=user_id)
        db.session.add(risk)
    investment = InvestmentState.query.filter_by(coverage_id=coverage.id).first()
    if investment is None:
        investment = InvestmentState(coverage_id=coverage.id, updated_by=user_id)
        db.session.add(investment)
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    if model is None:
        model = ValuationModel(coverage_id=coverage.id, name="Primary", method="MANUAL_PER_SHARE", updated_by=user_id)
        db.session.add(model); db.session.flush()
        for name, prob in (("BEAR", 0.25), ("BASE", 0.50), ("BULL", 0.25)):
            db.session.add(ValuationScenario(model_id=model.id, name=name, probability=prob))
    db.session.commit()
    return research, risk, investment, model


def valuation_result(coverage: Coverage) -> dict[str, Any]:
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    if not model:
        out = calculate_valuation(
            bear=None, base=None, bull=None,
            bear_probability=.25, base_probability=.5, bull_probability=.25,
            current_price=None,
        ).as_dict()
        out.update({"quality": "DATA_WARNING", "base_quality": "DATA_WARNING", "decision_grade": False})
        return out

    scenarios = {row.name.upper(): row for row in model.scenarios}
    market = latest_market_snapshot(coverage.security_id)
    out = calculate_valuation(
        bear=(scenarios.get("BEAR").equity_value_per_share if scenarios.get("BEAR") else None),
        base=(scenarios.get("BASE").equity_value_per_share if scenarios.get("BASE") else None),
        bull=(scenarios.get("BULL").equity_value_per_share if scenarios.get("BULL") else None),
        bear_probability=(scenarios.get("BEAR").probability if scenarios.get("BEAR") else .25),
        base_probability=(scenarios.get("BASE").probability if scenarios.get("BASE") else .5),
        bull_probability=(scenarios.get("BULL").probability if scenarios.get("BULL") else .25),
        current_price=market.price if market else None,
    ).as_dict()

    latest_engine = dict((model.assumptions or {}).get("latest_engine_result") or {})
    latest_scenarios = dict(latest_engine.get("scenarios") or {})
    base_row = scenarios.get("BASE")
    base_outputs = dict(base_row.outputs or {}) if base_row else {}
    base_quality = canonical_valuation_quality(
        base_outputs.get("quality")
        or (latest_scenarios.get("BASE") or {}).get("quality")
        or latest_engine.get("quality")
    )
    overall_quality = canonical_valuation_quality(latest_engine.get("quality") or base_quality)
    out.update({
        "quality": overall_quality,
        "base_quality": base_quality,
        "decision_grade": valuation_is_decision_grade({"base_quality": base_quality}),
        "company_quality": dict(latest_engine.get("company_quality") or {}),
        "valuation_policy": dict(latest_engine.get("valuation_policy") or {}),
        "valuation_impact_ledger": list(latest_engine.get("valuation_impact_ledger") or []),
        "method_exclusions": list(latest_engine.get("method_exclusions") or []),
        "effective_input_weights": dict(latest_engine.get("effective_input_weights") or {}),
        "engine_version": latest_engine.get("engine_version"),
        "warnings": list(latest_engine.get("warnings") or []),
    })
    return out


def financial_rows(company_id: int, limit: int = 10) -> list[dict[str, Any]]:
    periods = FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").order_by(FinancialPeriod.fiscal_year.desc()).limit(limit).all()
    out = []
    prior_map = {}
    for period in reversed(periods):
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if not normalized:
            continue
        row = {
            "period_id": period.id,
            "fiscal_year": period.fiscal_year,
            "period_end": period.end_date.isoformat(),
            "filed_at": period.filed_at.isoformat() if period.filed_at else None,
            "revenue": normalized.revenue,
            "cogs": normalized.cogs,
            "gross_profit": normalized.gross_profit,
            "operating_income": normalized.operating_income,
            "net_income": normalized.net_income,
            "cfo": normalized.cfo,
            "capex": normalized.capex,
            "fcf": normalized.fcf,
            "receivables": normalized.receivables,
            "inventory": normalized.inventory,
            "payables": normalized.payables,
            "cash": normalized.cash,
            "debt": normalized.debt,
            "diluted_shares": normalized.diluted_shares,
            "shares_outstanding": normalized.shares_outstanding,
            "source_map": normalized.source_map,
            "quality": normalized.quality,
        }
        row["metrics"] = financial_metrics(row, prior_map)
        prior_map = row
        out.append(row)
    return list(reversed(out))


def readiness(coverage: Coverage) -> dict[str, Any]:
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).first()
    valuation = valuation_result(coverage)
    monitor_count = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).count()
    journal_count = DecisionJournal.query.filter_by(coverage_id=coverage.id).count()
    research_data = {
        "business": research.business if research else "",
        "numbers": research.numbers if research else "",
        "expectations": research.expectations if research else "",
        "thesis": research.thesis if research else "",
        "counter_evidence": research.counter_evidence if research else "",
        "variant_us": research.variant_us if research else "",
        "variant_evidence": research.variant_evidence if research else "",
        "confirmation_bias_notes": research.confirmation_bias_notes if research else "",
        "thesis_drift_notes": research.thesis_drift_notes if research else "",
    }
    expectation_count = Expectation.query.filter_by(coverage_id=coverage.id).count()
    company = db.session.get(Company, db.session.get(Security, coverage.security_id).company_id)
    flow_count = (FinancialFlow.query.join(FinancialPeriod, FinancialFlow.financial_period_id == FinancialPeriod.id).filter(FinancialPeriod.company_id == company.id).count() if company else 0)
    source_count = Source.query.filter_by(company_id=company.id).count() if company else 0
    gates = [
        ("Business", bool(research and research.business.strip())),
        ("Fundamentals", bool(research and research.numbers.strip())),
        ("Expectations", bool(research and research.expectations.strip()) or expectation_count > 0),
        ("Valuation", all(valuation.get(k) is not None for k in ("bear", "base", "bull"))),
        ("Bear case", bool(research and research.bear_case_summary.strip()) or BearCaseItem.query.filter_by(coverage_id=coverage.id).count() > 0),
        ("Catalysts", bool(research and research.catalysts_summary.strip()) or Catalyst.query.filter_by(coverage_id=coverage.id).count() > 0),
        ("Flows", bool(research and (research.flows_summary.strip() or research.tape_summary.strip())) or flow_count > 0),
        ("Risk invalidation", bool(risk and risk.thesis_invalidation.strip() and risk.invalidation_locked_at)),
        ("Position sizing", bool(risk and risk.max_position_pct is not None)),
        ("Monitoring", monitor_count > 0),
        ("Sources / audit", source_count > 0),
        ("Journal", journal_count > 0),
    ]
    return {
        "done": sum(1 for _, ok in gates if ok),
        "total": len(gates),
        "gates": [{"label": label, "ok": ok} for label, ok in gates],
        "bias_flags": bias_flags(research_data, journal_count),
    }


def _research_payload(coverage: Coverage) -> dict[str, Any]:
    research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
    if not research:
        return {}
    return {
        "business": research.business,
        "numbers": research.numbers,
        "expectations": research.expectations,
        "valuation_notes": research.valuation_notes,
        "bear_case_summary": research.bear_case_summary,
        "catalysts_summary": research.catalysts_summary,
        "flows_summary": research.flows_summary,
        "management_summary": research.management_summary,
        "tape_summary": research.tape_summary,
        "risk_summary": research.risk_summary,
        "thesis": research.thesis,
        "counter_evidence": research.counter_evidence,
        "variant_market": research.variant_market,
        "variant_us": research.variant_us,
        "variant_evidence": research.variant_evidence,
        "narrative_fit_notes": research.narrative_fit_notes,
        "confirmation_bias_notes": research.confirmation_bias_notes,
        "thesis_drift_notes": research.thesis_drift_notes,
    }


def create_snapshot(coverage: Coverage, user_id: int, snapshot_type: str = "DECISION", decision_context: dict[str, Any] | None = None) -> Snapshot:
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id)
    market = latest_market_snapshot(security.id)
    risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
    investment = InvestmentState.query.filter_by(coverage_id=coverage.id).first()
    position = Position.query.filter_by(user_id=user_id, security_id=security.id).first()
    model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(ValuationModel.id.desc()).first()
    version = (db.session.query(db.func.max(Snapshot.version)).filter(Snapshot.coverage_id == coverage.id).scalar() or 0) + 1
    scenarios = []
    if model:
        for row in model.scenarios:
            scenarios.append({
                "name": row.name,
                "probability": float(row.probability) if row.probability is not None else None,
                "equity_value_per_share": float(row.equity_value_per_share) if row.equity_value_per_share is not None else None,
                "enterprise_value": float(row.enterprise_value) if row.enterprise_value is not None else None,
                "confidence": row.confidence,
                "inputs": row.inputs or {},
                "outputs": row.outputs or {},
            })
    expectations = [{
        "metric": e.metric, "period_label": e.period_label,
        "market_value": float(e.market_value) if e.market_value is not None else None,
        "internal_value": float(e.internal_value) if e.internal_value is not None else None,
        "unit": e.unit, "confidence": e.confidence, "source_id": e.source_id, "notes": e.notes,
    } for e in Expectation.query.filter_by(coverage_id=coverage.id).order_by(Expectation.id).all()]
    bear_items = [{"title": b.title, "evidence": b.evidence, "probability": float(b.probability) if b.probability is not None else None, "severity": b.severity, "invalidates": b.invalidates, "status": b.status} for b in BearCaseItem.query.filter_by(coverage_id=coverage.id).order_by(BearCaseItem.id).all()]
    catalysts = [{"title": c.title, "type": c.catalyst_type, "expected_date": c.expected_date.isoformat() if c.expected_date else None, "direction": c.direction, "status": c.status, "evidence": c.evidence, "source_id": c.source_id} for c in Catalyst.query.filter_by(coverage_id=coverage.id).order_by(Catalyst.id).all()]
    management = [{"as_of": m.as_of.isoformat(), "capital_allocation": m.capital_allocation, "execution": m.execution, "incentives": m.incentives, "communication": m.communication, "red_flags": m.red_flags, "notes": m.notes, "source_id": m.source_id} for m in ManagementAssessment.query.filter_by(coverage_id=coverage.id).order_by(ManagementAssessment.id).all()]
    monitoring = []
    for rule in MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(MonitoringRule.id).all():
        last = MonitoringHistory.query.filter_by(rule_id=rule.id).order_by(MonitoringHistory.observed_at.desc()).first()
        monitoring.append({"name": rule.name, "metric": rule.metric, "operator": rule.operator, "threshold_value": float(rule.threshold_value) if rule.threshold_value is not None else None, "threshold_text": rule.threshold_text, "unit": rule.unit, "severity": rule.severity, "locked_pre_investment": rule.locked_pre_investment, "last_status": last.status if last else None, "last_value": float(last.observed_value) if last and last.observed_value is not None else None, "last_observed_at": last.observed_at.isoformat() if last else None})
    financials = financial_rows(company.id, 5)
    for row in financials:
        for key, value in list(row.items()):
            if hasattr(value, "as_tuple"):
                row[key] = float(value)
        row["metrics"] = {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in (row.get("metrics") or {}).items()}
    # Publications must freeze the same forensic fair value used by the private
    # research workspace. Relative peer evidence is DB-only here (no provider
    # call) and remains bounded/auditable by the triangulation engine.
    intrinsic_valuation = valuation_result(coverage)
    from .triangulation_engine import automatic_triangulation, apply_peer_valuation_overlay
    triangulation = automatic_triangulation(company.id, user_id)
    forensic_valuation = apply_peer_valuation_overlay(intrinsic_valuation, triangulation)

    payload = {
        "security": {"ticker": security.ticker, "exchange": security.exchange, "currency": security.currency},
        "company": {"name": company.display_name, "cik": company.cik, "sector": company.sector, "industry": company.industry},
        "research": _research_payload(coverage),
        "research_state": coverage.research_state,
        "investment_state": investment.state if investment else "NO_POSITION",
        "investment_action": investment.action if investment else "WAIT",
        "decision": decision_context or {},
        "expectations": expectations,
        "valuation_model": {"name": model.name if model else None, "method": model.method if model else None, "assumptions": model.assumptions if model else {}, "calculation_version": model.calculation_version if model else CALCULATION_VERSION, "scenarios": scenarios},
        "valuation": forensic_valuation,
        "bear_case_items": bear_items,
        "catalysts": catalysts,
        "management": management,
        "monitoring": monitoring,
        "financials": financials,
        "risk": {
            "thesis_invalidation": risk.thesis_invalidation if risk else "",
            "invalidation_locked_at": risk.invalidation_locked_at.isoformat() if risk and risk.invalidation_locked_at else None,
            "max_loss_pct": float(risk.max_loss_pct) if risk and risk.max_loss_pct is not None else None,
            "max_position_pct": float(risk.max_position_pct) if risk and risk.max_position_pct is not None else None,
            "entry_conditions": risk.entry_conditions if risk else "",
            "add_conditions": risk.add_conditions if risk else "",
            "trim_conditions": risk.trim_conditions if risk else "",
            "exit_conditions": risk.exit_conditions if risk else "",
        },
        "position": {"shares": float(position.shares) if position else 0.0, "avg_cost": float(position.avg_cost) if position else 0.0},
        "market": {"price": float(market.price) if market else None, "provider": market.provider if market else None, "as_of": market.as_of.isoformat() if market else None},
        "readiness": readiness(coverage),
        "sources": [{"id": x.id, "provider": x.provider, "type": x.source_type, "title": x.title, "url": x.url, "retrieved_at": x.retrieved_at.isoformat()} for x in Source.query.filter_by(company_id=company.id).order_by(Source.retrieved_at.desc()).limit(100).all()],
        "calculation_version": CALCULATION_VERSION,
        "captured_at": utcnow().isoformat(),
    }
    snapshot = Snapshot(coverage_id=coverage.id, version=version, snapshot_type=snapshot_type, payload=payload, calculation_version=CALCULATION_VERSION, created_by=user_id)
    db.session.add(snapshot); db.session.commit()
    return snapshot


def snapshot_changes(snapshot: Snapshot) -> list[dict[str, Any]]:
    previous = Snapshot.query.filter(Snapshot.coverage_id == snapshot.coverage_id, Snapshot.version < snapshot.version).order_by(Snapshot.version.desc()).first()
    if previous is None:
        return []
    old = previous.payload or {}; new = snapshot.payload or {}
    paths = [
        ("Research state", ("research_state",)), ("Investment state", ("investment_state",)), ("Investment action", ("investment_action",)),
        ("Thesis", ("research", "thesis")), ("Counter-evidence", ("research", "counter_evidence")), ("Variant perception", ("research", "variant_us")),
        ("Bear value", ("valuation", "bear")), ("Base value", ("valuation", "base")), ("Bull value", ("valuation", "bull")), ("Expected value", ("valuation", "expected_value")),
        ("Market price", ("market", "price")), ("Locked invalidation", ("risk", "thesis_invalidation")), ("Max position %", ("risk", "max_position_pct")),
    ]
    def get(obj, path):
        for key in path:
            obj = (obj or {}).get(key) if isinstance(obj, dict) else None
        return obj
    changes = []
    for label, path in paths:
        before, after = get(old, path), get(new, path)
        if before != after:
            changes.append({"label": label, "before": before, "after": after})
    for label, key in (("Expectations", "expectations"), ("Bear-case items", "bear_case_items"), ("Catalysts", "catalysts"), ("Monitoring rules", "monitoring"), ("Sources", "sources")):
        before, after = len(old.get(key) or []), len(new.get(key) or [])
        if before != after:
            changes.append({"label": label, "before": before, "after": after})
    return changes


def publication_payload(snapshot: Snapshot, visibility: str) -> dict[str, Any]:
    """Build the publication boundary from an immutable snapshot.

    The private RiskPlan, InvestmentState, Position and journal data never cross this
    boundary. Only the explicitly authored research risk summary can be published.
    """
    src = snapshot.payload or {}
    research = dict(src.get("research") or {})
    out = {
        "security": src.get("security") or {},
        "company": src.get("company") or {},
        "research": research,
        "research_state": src.get("research_state"),
        "valuation": src.get("valuation") or {},
        "decision": src.get("decision") or {},
        "risk": {"summary": research.get("risk_summary", "")},
        "market": src.get("market") or {},
        "sources": src.get("sources") or [],
        "calculation_version": src.get("calculation_version") or CALCULATION_VERSION,
        "captured_at": src.get("captured_at"),
        "visibility": visibility,
    }
    if visibility == "FRIEND":
        out["research"].pop("confirmation_bias_notes", None)
        out["research"].pop("thesis_drift_notes", None)
        out["research"].pop("narrative_fit_notes", None)
    return out


def can_view_publication(publication: Publication, role: str) -> bool:
    if publication.revoked_at is not None:
        return False
    return ROLE_RANK.get(str(role).upper(), 0) >= ROLE_RANK.get(str(publication.visibility).upper(), 99)
