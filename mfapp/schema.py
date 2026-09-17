from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, text

from .extensions import db
from .models import User
from .core_models import (
    Company,
    Coverage,
    DecisionJournal,
    FinancialPeriod,
    InvestmentState,
    MarketSnapshot,
    MonitoringHistory,
    MonitoringRule,
    NormalizedFinancial,
    Position,
    Publication,
    ResearchState,
    ResearchVersion,
    RiskPlan,
    SchemaMigration,
    Security,
    Snapshot,
    ValuationModel,
    ValuationScenario,
)

LEGACY_MIGRATION_KEY = "legacy_0_0_4_to_0_1_0"


def _date(value: Any, fallback: date | None = None) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            pass
    return fallback


def _num(value: Any):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _payload(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return out[:140] or "research"


def _rows(table: str) -> list[dict]:
    result = db.session.execute(text(f"SELECT * FROM `{table}`"))
    return [dict(row._mapping) for row in result]


def _legacy_tables() -> set[str]:
    return set(inspect(db.engine).get_table_names())


def _control_user_id() -> int | None:
    row = User.query.filter(db.func.upper(User.role) == "CONTROL").order_by(User.id).first()
    return row.id if row else None


def bootstrap_schema(*, migrate_legacy: bool = True) -> dict[str, Any]:
    """Create/verify the current Market Forensics schema once per process startup, never per page request.

    Existing account/security tables are retained in place. New research tables use mf_*
    names so deployment can be atomic and the one-time converter can run without keeping
    two live cores synchronized.
    """
    db.create_all()
    result = {"schema": "0.2.0", "legacy_migration": "skipped"}
    if migrate_legacy:
        result["legacy_migration"] = migrate_legacy_004()
    return result


def migrate_legacy_004() -> str:
    if SchemaMigration.query.filter_by(migration_key=LEGACY_MIGRATION_KEY).first():
        return "already-applied"

    tables = _legacy_tables()
    if "company" not in tables:
        db.session.add(SchemaMigration(migration_key=LEGACY_MIGRATION_KEY, details={"legacy": "not-present"}))
        db.session.commit()
        return "not-present"

    owner_id = _control_user_id()
    if owner_id is None:
        raise RuntimeError("Cannot migrate legacy 0.0.4 research without an existing CONTROL account.")

    counts = {
        "companies": 0,
        "coverage": 0,
        "financial_periods": 0,
        "positions": 0,
        "monitoring": 0,
        "journal": 0,
        "publications": 0,
    }
    mapping: dict[int, dict[str, int]] = {}

    for old in _rows("company"):
        ticker = str(old.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        security = Security.query.filter(db.func.upper(Security.ticker) == ticker).first()
        if security is None:
            company = Company(
                legal_name=str(old.get("name") or ticker),
                display_name=str(old.get("name") or ticker),
                created_at=old.get("created_at") or datetime.utcnow(),
            )
            db.session.add(company)
            db.session.flush()
            security = Security(
                company_id=company.id,
                ticker=ticker,
                exchange="",
                provider_symbol=ticker.replace(".", "-"),
                validation_source="LEGACY_0.0.4",
                validated_at=datetime.utcnow(),
            )
            db.session.add(security)
            db.session.flush()
            counts["companies"] += 1
        coverage = Coverage.query.filter_by(user_id=owner_id, security_id=security.id).first()
        if coverage is None:
            status = str(old.get("status") or "MONITOR").upper()
            if status not in {"MONITOR", "RESEARCH", "READY", "ARCHIVED"}:
                status = "RESEARCH"
            coverage = Coverage(
                user_id=owner_id,
                security_id=security.id,
                status=status,
                owner_summary=str(old.get("summary") or ""),
            )
            db.session.add(coverage)
            db.session.flush()
            counts["coverage"] += 1
        mapping[int(old["id"])] = {"company_id": security.company_id, "security_id": security.id, "coverage_id": coverage.id}
        if coverage.research is None:
            db.session.add(ResearchState(coverage_id=coverage.id, updated_by=owner_id))
        if coverage.investment is None:
            db.session.add(InvestmentState(coverage_id=coverage.id, updated_by=owner_id))
        if coverage.risk_plan is None:
            db.session.add(RiskPlan(coverage_id=coverage.id, updated_by=owner_id))
        model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).first()
        if model is None:
            model = ValuationModel(coverage_id=coverage.id, name="Legacy converted", method="MANUAL_PER_SHARE", updated_by=owner_id)
            db.session.add(model)
            db.session.flush()
            for scenario_name, probability in (("BEAR", Decimal("0.25")), ("BASE", Decimal("0.50")), ("BULL", Decimal("0.25"))):
                db.session.add(ValuationScenario(model_id=model.id, name=scenario_name, probability=probability))
    db.session.flush()

    if "research_workspace" in tables:
        for old in _rows("research_workspace"):
            target = mapping.get(int(old.get("company_id") or 0))
            if not target:
                continue
            coverage = db.session.get(Coverage, target["coverage_id"])
            research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
            p = _payload(old.get("payload"))
            research.business = str(p.get("business_summary") or "")
            research.numbers = str(p.get("numbers_summary") or "")
            research.expectations = str(p.get("expectations_summary") or "")
            research.valuation_notes = str(p.get("valuation_summary") or "")
            research.bear_case_summary = str(p.get("bear_case") or "")
            research.catalysts_summary = str(p.get("catalysts") or "")
            research.flows_summary = str(p.get("flows") or "")
            research.risk_summary = str(p.get("risk_notes") or "")
            research.thesis = str(p.get("thesis") or "")
            research.counter_evidence = str(p.get("kills_thesis") or "")
            research.variant_market = str(p.get("variant_market") or "")
            research.variant_us = str(p.get("variant_we") or "")
            research.variant_evidence = str(p.get("variant_evidence") or "")
            research.updated_by = int(old.get("updated_by") or owner_id)
            state = str(old.get("research_state") or "").upper()
            coverage.research_state = {"DRAFT": "UNRATED", "REVIEW": "UNDER_REVIEW", "READY": "READY"}.get(state, state or "UNRATED")

            model = ValuationModel.query.filter_by(coverage_id=coverage.id, is_active=True).first()
            if model is None:
                model = ValuationModel(
                    coverage_id=coverage.id,
                    name="Legacy converted",
                    method=str(p.get("valuation_method") or "MANUAL_PER_SHARE")[:48],
                    assumptions={"legacy_note": p.get("freshness_note") or ""},
                    updated_by=owner_id,
                )
                db.session.add(model)
                db.session.flush()
            for name, value_key, prob_key in (("BEAR", "bear_value", "bear_prob"), ("BASE", "base_value", "base_prob"), ("BULL", "bull_value", "bull_prob")):
                scenario = ValuationScenario.query.filter_by(model_id=model.id, name=name).first()
                if scenario is None:
                    scenario = ValuationScenario(model_id=model.id, name=name)
                    db.session.add(scenario)
                scenario.equity_value_per_share = _num(p.get(value_key))
                probability = _num(p.get(prob_key))
                scenario.probability = probability if probability is not None else Decimal("0")

            risk = RiskPlan.query.filter_by(coverage_id=coverage.id).first()
            if risk is None:
                risk = RiskPlan(coverage_id=coverage.id, updated_by=owner_id)
                db.session.add(risk)
            risk.thesis_invalidation = str(p.get("invalidation") or "")
            risk.entry_conditions = str(p.get("why_now") or "")
            risk.add_conditions = "ADD ON EVIDENCE, NOT ON PRICE" if p.get("position_size_plan") else ""
            risk.notes = str(p.get("risk_notes") or "")
            if p.get("invalidation_locked"):
                risk.invalidation_locked_at = old.get("updated_at") or datetime.utcnow()

            max_version = db.session.query(db.func.max(ResearchVersion.version)).filter(ResearchVersion.coverage_id == coverage.id).scalar() or 0
            db.session.add(ResearchVersion(
                coverage_id=coverage.id,
                version=max_version + 1,
                payload=p,
                reason="Legacy 0.0.4 conversion",
                created_by=owner_id,
                created_at=old.get("updated_at") or datetime.utcnow(),
            ))

    if "market_snapshot" in tables:
        for old in _rows("market_snapshot"):
            target = mapping.get(int(old.get("company_id") or 0))
            price = _num(old.get("price"))
            if not target or price is None:
                continue
            db.session.add(MarketSnapshot(
                security_id=target["security_id"],
                provider=str(old.get("provider") or "LEGACY_0.0.4"),
                price=price,
                currency=str(old.get("currency") or "USD"),
                as_of=old.get("as_of") or old.get("updated_at") or datetime.utcnow(),
                quality=str(old.get("quality") or "LEGACY"),
                payload=_payload(old.get("payload")),
                created_at=old.get("updated_at") or datetime.utcnow(),
            ))

    if "fundamental_period" in tables:
        fields = [
            "revenue", "gross_profit", "operating_income", "pretax", "tax", "net_income", "cfo", "capex", "fcf",
            "buybacks", "dividends", "diluted_shares", "shares_outstanding", "cash", "debt", "receivables", "inventory",
            "payables", "assets", "liabilities", "equity",
        ]
        for old in _rows("fundamental_period"):
            target = mapping.get(int(old.get("company_id") or 0))
            if not target:
                continue
            fy = int(old.get("fiscal_year") or 0)
            if fy <= 0:
                continue
            end_date = _date(old.get("period_end"), date(fy, 12, 31))
            period = FinancialPeriod.query.filter_by(company_id=target["company_id"], period_type=str(old.get("period_type") or "FY"), fiscal_year=fy, end_date=end_date).first()
            if period is None:
                period = FinancialPeriod(
                    company_id=target["company_id"],
                    period_type=str(old.get("period_type") or "FY"),
                    fiscal_year=fy,
                    end_date=end_date,
                    filed_at=_date(old.get("period_filed")),
                    currency="USD",
                )
                db.session.add(period)
                db.session.flush()
            normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
            if normalized is None:
                normalized = NormalizedFinancial(financial_period_id=period.id, source_map=_payload(old.get("provenance")), quality={"legacy": True})
                db.session.add(normalized)
            normalized.revenue = _num(old.get("revenue"))
            normalized.gross_profit = _num(old.get("gross_profit"))
            normalized.operating_income = _num(old.get("operating_income"))
            normalized.pretax_income = _num(old.get("pretax"))
            normalized.income_tax = _num(old.get("tax"))
            normalized.net_income = _num(old.get("net_income"))
            normalized.cfo = _num(old.get("cfo"))
            normalized.capex = _num(old.get("capex"))
            normalized.fcf = _num(old.get("fcf"))
            for field in fields[9:]:
                setattr(normalized, field, _num(old.get(field)))
            if normalized.revenue is not None and normalized.gross_profit is not None:
                normalized.cogs = normalized.revenue - normalized.gross_profit
            if normalized.gross_profit is not None and normalized.operating_income is not None:
                normalized.operating_expenses = normalized.gross_profit - normalized.operating_income
            counts["financial_periods"] += 1

    if "private_position" in tables:
        for old in _rows("private_position"):
            target = mapping.get(int(old.get("company_id") or 0))
            if not target:
                continue
            user_id = int(old.get("user_id") or owner_id)
            position = Position.query.filter_by(user_id=user_id, security_id=target["security_id"]).first()
            if position is None:
                position = Position(user_id=user_id, security_id=target["security_id"])
                db.session.add(position)
            position.shares = _num(old.get("shares")) or Decimal("0")
            position.avg_cost = _num(old.get("avg_cost")) or Decimal("0")
            position.notes = str(old.get("notes") or "")
            counts["positions"] += 1

    if "monitoring_item" in tables:
        for old in _rows("monitoring_item"):
            target = mapping.get(int(old.get("company_id") or 0))
            if not target:
                continue
            rule = MonitoringRule(
                coverage_id=target["coverage_id"],
                name=str(old.get("label") or "Legacy monitoring rule"),
                threshold_text=str(old.get("threshold") or ""),
                severity=str(old.get("status") or "WATCH"),
                is_active=bool(old.get("is_active", True)),
                created_by=owner_id,
                created_at=old.get("created_at") or datetime.utcnow(),
            )
            db.session.add(rule)
            db.session.flush()
            db.session.add(MonitoringHistory(
                rule_id=rule.id,
                status=str(old.get("status") or "WATCH"),
                note=" | ".join(x for x in [str(old.get("current_value") or ""), str(old.get("notes") or "")] if x),
                observed_at=old.get("updated_at") or datetime.utcnow(),
            ))
            counts["monitoring"] += 1

    if "decision_journal" in tables:
        for old in _rows("decision_journal"):
            target = mapping.get(int(old.get("company_id") or 0))
            if not target:
                continue
            coverage = db.session.get(Coverage, target["coverage_id"])
            db.session.add(DecisionJournal(
                coverage_id=coverage.id,
                user_id=int(old.get("user_id") or owner_id),
                decision=str(old.get("decision") or "LEGACY"),
                research_state=coverage.research_state,
                investment_state=coverage.investment.state if coverage.investment else "NO_POSITION",
                thesis_snapshot={"text": str(old.get("thesis_snapshot") or "")},
                risk_snapshot={"invalidation": str(old.get("invalidation_snapshot") or "")},
                valuation_snapshot={},
                bias_notes=str(old.get("notes") or ""),
                created_at=old.get("created_at") or datetime.utcnow(),
            ))
            counts["journal"] += 1

    if "publication" in tables:
        for old in _rows("publication"):
            target = mapping.get(int(old.get("company_id") or 0))
            if not target:
                continue
            coverage = db.session.get(Coverage, target["coverage_id"])
            security = db.session.get(Security, target["security_id"])
            p = _payload(old.get("payload"))
            snap_version = db.session.query(db.func.max(Snapshot.version)).filter(Snapshot.coverage_id == coverage.id).scalar() or 0
            snapshot = Snapshot(
                coverage_id=coverage.id,
                version=snap_version + 1,
                snapshot_type="PUBLICATION",
                payload=p,
                calculation_version=str(old.get("model_version") or "legacy"),
                created_by=owner_id,
                created_at=old.get("published_at") or datetime.utcnow(),
            )
            db.session.add(snapshot)
            db.session.flush()
            pub_version = int(old.get("version") or 1)
            if Publication.query.filter_by(coverage_id=coverage.id, version=pub_version).first():
                continue
            db.session.add(Publication(
                coverage_id=coverage.id,
                snapshot_id=snapshot.id,
                version=pub_version,
                visibility=str(old.get("visibility") or "FRIEND"),
                title=f"{security.ticker} Research",
                slug=_slug(f"{security.ticker}-research"),
                payload=p,
                published_by=owner_id,
                published_at=old.get("published_at") or datetime.utcnow(),
            ))
            counts["publications"] += 1

    db.session.add(SchemaMigration(migration_key=LEGACY_MIGRATION_KEY, details=counts))
    db.session.commit()
    return "applied"
