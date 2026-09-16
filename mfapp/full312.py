from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .marketdata import get_secret


def initialize_engine() -> None:
    from market_forensics import db
    db.init_db()


def sync_engine_credentials(user_id: int) -> None:
    """Bridge encrypted hosted secrets into the private V3.1.12 runtime directory.

    The original desktop engine expects local JSON files. They are generated server-side from
    encrypted MariaDB secrets and never exposed to FRIEND/INSIDER or committed to Git.
    """
    from market_forensics import config

    settings = config.load_settings()
    sec_ua = get_secret(user_id, "sec_user_agent")
    if sec_ua:
        settings["sec_user_agent"] = sec_ua
    config.save_settings(settings)

    alpaca_key = get_secret(user_id, "alpaca_key")
    alpaca_secret = get_secret(user_id, "alpaca_secret")
    if alpaca_key and alpaca_secret:
        config.SECRETS_PATH.write_text(
            json.dumps({"ALPACA_API_KEY": alpaca_key, "ALPACA_SECRET_KEY": alpaca_secret}, indent=2),
            encoding="utf-8",
        )

    market_keys = {
        "TIINGO_API_KEY": get_secret(user_id, "tiingo_token"),
        "ALPHA_VANTAGE_API_KEY": get_secret(user_id, "alpha_vantage_key"),
        "MASSIVE_API_KEY": get_secret(user_id, "massive_key"),
    }
    clean = {k: v for k, v in market_keys.items() if v}
    if clean:
        config.MARKET_DATA_SECRETS_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")


def _date10(value):
    text = str(value or "")[:10]
    return text or None


def load_state(ticker: str) -> dict[str, Any]:
    """Headless equivalent of V3.1.12 workstation.load_state().

    It intentionally calls the preserved original calculation modules, but never imports the
    Streamlit presentation layer. This is the canonical CONTROL state for the hosted beta.
    """
    from market_forensics import (
        audit,
        db,
        decision,
        financial_flows,
        forecast,
        forensics,
        fundamentals,
        management,
        opportunity,
        service,
        triangulation,
        valuation,
    )

    initialize_engine()
    t = str(ticker or "").upper().strip()
    snap = service.latest_snapshot(t)
    bars = [dict(x) for x in (snap.get("bars") or [])]
    fund_rows = [dict(x) for x in (snap.get("fund") or [])]
    bar = dict(snap.get("bar") or {}) if snap.get("bar") else None
    scores = dict(snap.get("scores") or {})
    price = bar.get("close") if bar else None

    basis_evidence = db.current_share_basis_evidence(t)
    override = db.share_basis_override(t)
    metrics = fundamentals.metrics_from_rows(
        fund_rows, price, bars=bars, current_basis_evidence=basis_evidence
    ) if fund_rows else {}
    metrics = fundamentals.apply_share_basis_override(metrics, override) if metrics else metrics

    coverage = db.coverage_row(t) or {}
    assumptions, calibration, legacy_valuation = valuation.resolve_assumptions(
        db.load_json_assumptions(t), metrics, fund_rows, bars, coverage.get("company_type", "Generic")
    )
    scenarios, expected_value = valuation.scenario_values(metrics, assumptions) if metrics else ({}, None)
    base_value = (scenarios.get("base") or {}).get("fair_value") if scenarios else None
    upside = (base_value / price - 1.0) if base_value is not None and price not in (None, 0) else None

    kpis = [dict(x) for x in db.query(
        "SELECT * FROM monitoring_kpis WHERE ticker=? ORDER BY importance DESC,id", (t,)
    )]
    kpi_eval = []
    for row in kpis:
        value, status = decision.kpi_status(row, metrics, snap)
        kpi_eval.append({**row, "current_value": value, "kpi_status": status})

    state: dict[str, Any] = {
        "ticker": t,
        "snap": snap,
        "bars": bars,
        "fund_rows": fund_rows,
        "bar": bar,
        "scores": scores,
        "metrics": metrics,
        "assumptions": assumptions,
        "scenarios": scenarios,
        "expected_value": expected_value,
        "base_value": base_value,
        "price": price,
        "display_price": price,
        "model_price": price,
        "upside": upside,
        "coverage": coverage,
        "gates": [],
        "gate_counts": {"PASS": 0, "WATCH": 0, "FAIL": 0, "INCOMPLETE": 16},
        "gate_label": "RESEARCH INCOMPLETE",
        "kpis": kpis,
        "kpi_eval": kpi_eval,
        "calibration": calibration,
        "legacy_valuation": legacy_valuation,
        "research": db.load_research(t),
        "risk": db.risk_plan(t) or {},
        "portfolio": db.portfolio_position(t) or {},
        "validation": db.latest_validation_result(t) or {},
        "share_basis_evidence": basis_evidence or {},
        "share_basis_override": override or {},
    }

    ar = audit.build_audit(t, state)
    state["audit"] = ar
    calibration_for_conf = dict(calibration or {})
    calibration_for_conf["regime_score"] = (assumptions.get("calibration") or {}).get("regime_score")
    state["valuation_regime"] = valuation.multiple_regime_diagnostics(metrics, price, calibration)
    revision_count = len(db.query("SELECT 1 FROM sec_revisions WHERE ticker=?", (t,)))
    state["valuation_confidence"] = valuation.valuation_confidence(
        metrics,
        scenarios,
        calibration_for_conf,
        coverage.get("company_type", "Generic"),
        ar.get("counts"),
        reviewed=bool(assumptions.get("reviewed_at")),
        revision_count=revision_count,
        regime_breaks=state["valuation_regime"],
    )
    state["forensics"] = forensics.build_forensic_signals(t, state, calibration, ar)
    state["management"] = management.credibility_summary(t)
    state["quality_validation"] = management.quality_cross_checks(
        t, metrics, state["forensics"], state["management"]
    )
    auto_forecast = forecast.auto_assumptions(metrics, fund_rows, assumptions) if metrics else {
        "model_version": "2.7", "years": 5, "bear": {}, "base": {}, "bull": {}, "reviewed_at": None
    }
    state["forecast_assumptions"] = forecast.normalize_assumptions(
        db.load_forecast_assumptions(t), auto_forecast
    )
    state["forecast_scenarios"] = forecast.scenario_summary(
        metrics, state["forecast_assumptions"], price
    ) if metrics else {}
    state["price_reconciliation"] = forecast.price_reconciliation(
        price, metrics, state["forecast_assumptions"], state["forecast_scenarios"]
    ) if metrics and price else {}
    state["variant_case"] = db.load_variant_case(t)
    state["triangulation"] = triangulation.compare(t)

    gates = decision.investment_gates(t, metrics, scenarios, expected_value, coverage, state=state)
    gate_counts, gate_label = decision.gate_summary(gates)
    state["gates"] = gates
    state["gate_counts"] = gate_counts
    state["gate_label"] = gate_label
    state["opportunity"] = opportunity.evaluate(state, service.last_successful_refresh(t))

    annual = financial_flows.annual_rows(fund_rows)
    selected = annual[-1] if annual else None
    state["financial_flow_years"] = [r.get("fiscal_year") for r in annual]
    state["income_flow"] = financial_flows.income_statement_flow(selected or {}) if selected else {
        "ok": False, "reason": "No annual fundamentals stored yet.", "rows": []
    }
    state["cash_flow"] = financial_flows.cash_flow_flow(selected or {}) if selected else {
        "ok": False, "reason": "No annual fundamentals stored yet.", "rows": []
    }

    state["sources"] = [dict(x) for x in db.query(
        "SELECT * FROM sec_provenance WHERE ticker=? ORDER BY metric", (t,)
    )]
    state["refresh_runs"] = [dict(x) for x in db.query(
        "SELECT * FROM refresh_runs WHERE ticker=? ORDER BY id DESC LIMIT 10", (t,)
    )]
    state["short_interest"] = [dict(x) for x in (snap.get("si") or [])]
    state["flow_rows"] = [dict(x) for x in (snap.get("flows") or [])]
    state["short_volume"] = [dict(x) for x in (snap.get("shorts") or [])]
    return state


def publication_payload(ticker: str, state: dict) -> dict:
    research = state.get("research") or {}
    metrics = state.get("metrics") or {}
    scenarios = state.get("scenarios") or {}
    scores = state.get("scores") or {}
    forensic = state.get("forensics") or {}
    opp = state.get("opportunity") or {}
    confidence = state.get("valuation_confidence") or {}
    gates = state.get("gates") or []
    sources = state.get("sources") or []

    def fv(name):
        return (scenarios.get(name) or {}).get("fair_value")

    source_lines = []
    for row in sources[:20]:
        metric = row.get("metric") or "SEC fact"
        concept = row.get("concept") or row.get("source_concept") or ""
        source_lines.append(f"{metric}: {concept}".strip())

    monitoring = "; ".join(
        f"{g.get('Gate') or g.get('gate') or g.get('name')}: {g.get('Status') or g.get('status')}"
        for g in gates[:8]
    )
    positives = forensic.get("positives") or []
    negatives = forensic.get("negatives") or []
    numbers = (
        f"Revenue {metrics.get('revenue') or '—'}; FCF {metrics.get('fcf') or '—'}; "
        f"ROIC {metrics.get('roic') if metrics.get('roic') is not None else '—'}; "
        f"FCF margin {metrics.get('fcf_margin') if metrics.get('fcf_margin') is not None else '—'}."
    )
    return {
        "decision": state.get("gate_label") or "RESEARCH",
        "bear_value": fv("bear"),
        "base_value": fv("base"),
        "bull_value": fv("bull"),
        "model_confidence": f"{confidence.get('label','LOW')} · {confidence.get('score','—')}",
        "business_label": opp.get("setup") or "RESEARCH",
        "value_label": opp.get("focus") or "RESEARCH",
        "expectations_label": (state.get("price_reconciliation") or {}).get("label") or "REVIEW",
        "variant_label": "DEFINED" if state.get("variant_case") else "OPEN",
        "path_label": state.get("gate_label") or "RESEARCH",
        "risk_label": "REVIEWED" if state.get("risk") else "OPEN",
        "thesis": research.get("thesis_title") or "Thesis not yet written.",
        "business": research.get("business_notes") or "",
        "numbers": numbers,
        "valuation": f"V3.1.12 Bear/Base/Bull: {fv('bear')} / {fv('base')} / {fv('bull')}. Expected value: {state.get('expected_value')}.",
        "catalysts": research.get("catalysts") or "",
        "risk": research.get("primary_risk") or (state.get("risk") or {}).get("kill_switch") or "",
        "changes_decision": research.get("invalidation") or "",
        "expectations": research.get("market_narrative") or str(state.get("price_reconciliation") or ""),
        "variant_market": research.get("market_narrative") or "",
        "variant_we": research.get("variant_view") or "",
        "variant_evidence": research.get("key_evidence") or "",
        "bear_case": "; ".join(str(x.get("Signal") or x.get("signal") or x) for x in negatives[:6]),
        "flows": f"Net Tape {scores.get('Net Tape','—')} · Institutional Flow {scores.get('Institutional Flow','—')} · Short Pressure {scores.get('Short Pressure','—')}",
        "monitoring": monitoring,
        "variant_resolution": research.get("evidence_to_add") or "",
        "horizon": str((state.get("coverage") or {}).get("thesis_horizon_months") or "—") + " months",
        "sources": "\n".join(source_lines),
        "disclosure": "Independent research snapshot. General information only; not personalized investment advice.",
        "engine": "Market Forensics V3.1.12 FULL",
    }
