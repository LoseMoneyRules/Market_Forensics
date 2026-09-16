from __future__ import annotations

import json
from datetime import date
from typing import Any

from .marketdata import get_secret


def initialize_engine() -> None:
    from market_forensics import db
    db.init_db()


def sync_engine_credentials(user_id: int) -> None:
    """Bridge encrypted hosted secrets into the private V3.1.12 runtime directory."""
    from market_forensics import config

    settings = config.load_settings()
    settings["sec_user_agent"] = get_secret(user_id, "sec_user_agent") or ""
    config.save_settings(settings)

    alpaca_key = get_secret(user_id, "alpaca_key")
    alpaca_secret = get_secret(user_id, "alpaca_secret")
    if alpaca_key and alpaca_secret:
        config.SECRETS_PATH.write_text(
            json.dumps({"ALPACA_API_KEY": alpaca_key, "ALPACA_SECRET_KEY": alpaca_secret}, indent=2),
            encoding="utf-8",
        )
    else:
        config.SECRETS_PATH.unlink(missing_ok=True)

    market_keys = {
        "TIINGO_API_KEY": get_secret(user_id, "tiingo_token"),
        "ALPHA_VANTAGE_API_KEY": get_secret(user_id, "alpha_vantage_key"),
        "MASSIVE_API_KEY": get_secret(user_id, "massive_key"),
    }
    clean = {k: v for k, v in market_keys.items() if v}
    config.MARKET_DATA_SECRETS_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")


def _date10(value):
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat() if text else None
    except Exception:
        return None


def _engine_state(ticker: str, live_price: bool = True) -> dict[str, Any]:
    """Headless, line-for-line equivalent of V3.1.12 workstation.load_state().

    The local workstation module imports Streamlit/Plotly/Pandas presentation code. The hosted Flask
    application intentionally calls the same original core modules in the same order instead, so
    production does not need the Streamlit UI runtime. A regression test compares this state with the
    original workstation loader.
    """
    from market_forensics import (
        audit,
        db,
        decision,
        forecast,
        forensics,
        fundamentals,
        management,
        marketdata,
        triangulation,
        valuation,
        service,
    )
    from market_forensics.config import load_settings

    snap = service.latest_snapshot(ticker)
    bars = snap.get("bars", [])
    fund_rows = snap.get("fund", [])
    bar = snap.get("bar")
    scores = snap.get("scores") or {}
    model_price = bar.get("close") if bar else None
    basis_evidence = db.current_share_basis_evidence(ticker)
    override = db.share_basis_override(ticker)

    metrics = fundamentals.metrics_from_rows(
        fund_rows, model_price, bars=bars, current_basis_evidence=basis_evidence
    ) if fund_rows else {}
    metrics = fundamentals.apply_share_basis_override(metrics, override) if metrics else metrics

    price_overlay = None
    if live_price:
        try:
            price_overlay = marketdata.current_price_overlay(ticker, load_settings().get("alpaca_feed", "auto"))
        except Exception:
            price_overlay = None

    display_price = (price_overlay or {}).get("price") or model_price
    valuation_price = model_price
    live_price_for_valuation = False
    live_price_basis_note = ""

    if price_overlay and price_overlay.get("price") not in (None, 0):
        overlay_date = _date10(price_overlay.get("asof"))
        if not fund_rows:
            valuation_price = price_overlay.get("price")
            live_price_for_valuation = True
            live_price_basis_note = "No fundamental share denominator is in use yet."
        elif overlay_date:
            if override and int(override.get("confirmed") or 0) == 1:
                override_date = _date10(override.get("basis_date"))
                if override_date and overlay_date <= override_date:
                    valuation_price = price_overlay.get("price")
                    live_price_for_valuation = True
                    live_price_basis_note = "Live quote date is within the user-verified share-basis date."
                else:
                    live_price_basis_note = "Display-only quote: manual share-basis approval predates this market quote. Refresh or re-verify the basis before using the newer price in valuation."
            else:
                ev = basis_evidence or {}
                verified_through = _date10(ev.get("verified_through"))
                evidence_ok = str(ev.get("status") or "").upper() == "VERIFIED" and verified_through and verified_through >= overlay_date
                if not evidence_ok:
                    anchor = _date10((metrics or {}).get("valuation_share_fact_date") or (metrics or {}).get("diluted_shares_date"))
                    if anchor:
                        try:
                            fresh_ev = marketdata.current_split_basis_evidence(ticker, anchor, overlay_date)
                            if str(fresh_ev.get("status") or "").upper() == "VERIFIED":
                                db.save_current_share_basis_evidence(ticker, fresh_ev)
                                basis_evidence = fresh_ev
                                evidence_ok = True
                        except Exception as exc:
                            live_price_basis_note = f"Display-only quote: corporate-action verification through {overlay_date} is unavailable ({exc})."
                if evidence_ok:
                    metrics = fundamentals.metrics_from_rows(
                        fund_rows, price_overlay.get("price"), bars=bars, current_basis_evidence=basis_evidence
                    )
                    valuation_price = price_overlay.get("price")
                    live_price_for_valuation = True
                    live_price_basis_note = f"Corporate-action/share basis verified through {overlay_date}."
                elif not live_price_basis_note:
                    live_price_basis_note = f"Display-only quote: share basis is not verified through {overlay_date}."
        else:
            live_price_basis_note = "Display-only quote: provider timestamp is unavailable, so valuation remains on the last verified model price."

    if price_overlay is not None:
        price_overlay = dict(price_overlay)
        price_overlay["valuation_usable"] = bool(live_price_for_valuation)
        price_overlay["basis_note"] = live_price_basis_note
        price_overlay["display_price"] = display_price
        price_overlay["valuation_price"] = valuation_price

    coverage = db.coverage_row(ticker) or {}
    assumptions, calibration, legacy_valuation = valuation.resolve_assumptions(
        db.load_json_assumptions(ticker), metrics, fund_rows, bars, coverage.get("company_type", "Generic")
    )
    scenarios, expected_value = valuation.scenario_values(metrics, assumptions) if metrics else ({}, None)
    base_value = scenarios.get("base", {}).get("fair_value") if scenarios else None
    price = valuation_price
    upside = (base_value / price - 1) if base_value is not None and price not in (None, 0) else None
    kpis = db.query("SELECT * FROM monitoring_kpis WHERE ticker=? ORDER BY importance DESC,id", (ticker.upper(),))
    kpi_eval = []
    for row in kpis:
        value, status = decision.kpi_status(row, metrics, snap)
        kpi_eval.append({**row, "current_value": value, "kpi_status": status})

    state = {
        "snap": snap, "bars": bars, "fund_rows": fund_rows, "bar": bar, "scores": scores,
        "metrics": metrics, "assumptions": assumptions, "scenarios": scenarios,
        "expected_value": expected_value, "base_value": base_value, "price": price, "display_price": display_price,
        "model_price": model_price, "price_overlay": price_overlay, "upside": upside,
        "coverage": coverage, "gates": [], "gate_counts": {"PASS": 0, "WATCH": 0, "FAIL": 0, "INCOMPLETE": 16}, "gate_label": "RESEARCH INCOMPLETE",
        "kpis": kpis, "kpi_eval": kpi_eval, "calibration": calibration, "legacy_valuation": legacy_valuation,
    }
    ar = audit.build_audit(ticker, state)
    state["audit"] = ar
    calibration_for_conf = dict(calibration or {})
    calibration_for_conf["regime_score"] = (assumptions.get("calibration") or {}).get("regime_score")
    state["valuation_regime"] = valuation.multiple_regime_diagnostics(metrics, price, calibration)
    revision_count = len(db.query("SELECT 1 FROM sec_revisions WHERE ticker=?", (ticker.upper(),)))
    state["valuation_confidence"] = valuation.valuation_confidence(
        metrics, scenarios, calibration_for_conf, coverage.get("company_type", "Generic"), ar.get("counts"),
        reviewed=bool(assumptions.get("reviewed_at")), revision_count=revision_count,
        regime_breaks=state["valuation_regime"],
    )
    state["forensics"] = forensics.build_forensic_signals(ticker, state, calibration, ar)
    state["management"] = management.credibility_summary(ticker)
    state["quality_validation"] = management.quality_cross_checks(ticker, metrics, state["forensics"], state["management"])
    f_auto = forecast.auto_assumptions(metrics, fund_rows, assumptions) if metrics else {"model_version": "2.7", "years": 5, "bear": {}, "base": {}, "bull": {}, "reviewed_at": None}
    state["forecast_assumptions"] = forecast.normalize_assumptions(db.load_forecast_assumptions(ticker), f_auto)
    state["forecast_scenarios"] = forecast.scenario_summary(metrics, state["forecast_assumptions"], price) if metrics else {}
    state["price_reconciliation"] = forecast.price_reconciliation(price, metrics, state["forecast_assumptions"], state["forecast_scenarios"]) if metrics and price else {}
    state["variant_case"] = db.load_variant_case(ticker)
    state["triangulation"] = triangulation.compare(ticker)
    gates = decision.investment_gates(ticker, metrics, scenarios, expected_value, coverage, state=state)
    gate_counts, gate_label = decision.gate_summary(gates)
    state["gates"] = gates
    state["gate_counts"] = gate_counts
    state["gate_label"] = gate_label
    return state


def load_state(ticker: str) -> dict[str, Any]:
    """Canonical hosted state = exact V3.1.12 headless state + additive web/publication surfaces."""
    from market_forensics import db, decision, financial_flows, opportunity, service

    initialize_engine()
    t = str(ticker or "").upper().strip()
    state = _engine_state(t, live_price=True)
    state["ticker"] = t

    state["research"] = db.load_research(t)
    state["risk"] = db.risk_plan(t) or {}
    state["portfolio"] = db.portfolio_position(t) or {}
    state["validation"] = db.latest_validation_result(t) or {}
    state["share_basis_evidence"] = db.current_share_basis_evidence(t) or {}
    state["share_basis_override"] = db.share_basis_override(t) or {}
    state["gate_overrides"] = db.gate_overrides(t)
    state["opportunity"] = opportunity.evaluate(state, service.last_successful_refresh(t))

    decision.seed_bear_case(t)
    state["bear_case_items"] = [dict(x) for x in db.query(
        "SELECT * FROM bear_case_items WHERE ticker=? ORDER BY item_key", (t,)
    )]
    state["peer_links"] = [dict(x) for x in db.peer_links(t)]
    state["events"] = [dict(x) for x in db.query(
        "SELECT * FROM events WHERE ticker=? ORDER BY event_date", (t,)
    )]
    state["snapshot_changes"] = decision.what_changed(service.snapshot_history(t, 10))
    state["next_event"] = dict(decision.next_event(t) or {})

    annual = financial_flows.annual_rows(state.get("fund_rows") or [])
    by_year: dict[str, dict[str, Any]] = {}
    for row in annual:
        year = row.get("fiscal_year")
        by_year[str(year)] = {
            "income": financial_flows.income_statement_flow(row),
            "cash": financial_flows.cash_flow_flow(row),
        }
    state["financial_flow_years"] = [row.get("fiscal_year") for row in annual]
    state["financial_flows_by_year"] = by_year
    selected = annual[-1] if annual else None
    state["income_flow"] = financial_flows.income_statement_flow(selected or {}) if selected else {
        "ok": False, "reason": "No annual fundamentals stored yet.", "rows": []
    }
    state["cash_flow"] = financial_flows.cash_flow_flow(selected or {}) if selected else {
        "ok": False, "reason": "No annual fundamentals stored yet.", "rows": []
    }

    snap = state.get("snap") or {}
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
    """Build a publishable snapshot. Private portfolio/sizing data is intentionally excluded."""
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
    for row in sources[:30]:
        metric = row.get("metric") or "SEC fact"
        concept = row.get("concept") or row.get("source_concept") or ""
        source_lines.append(f"{metric}: {concept}".strip())

    monitoring = "; ".join(
        f"{g.get('Gate') or g.get('gate') or g.get('name')}: {g.get('Status') or g.get('status')}"
        for g in gates[:8]
    )
    negatives = forensic.get("negatives") or []
    numbers = (
        f"Revenue {metrics.get('revenue') if metrics.get('revenue') is not None else '—'}; "
        f"FCF {metrics.get('fcf') if metrics.get('fcf') is not None else '—'}; "
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
