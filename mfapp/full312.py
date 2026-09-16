from __future__ import annotations

import json
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


def load_state(ticker: str) -> dict[str, Any]:
    """Load the canonical V3.1.12 workstation state without reimplementing its calculations."""
    from market_forensics import db, decision, financial_flows, opportunity, service, workstation

    initialize_engine()
    t = str(ticker or "").upper().strip()

    # This is the same canonical state loader used by the tested V3.1.12 local workstation,
    # including current-price/share-basis safeguards and corporate-action verification.
    state = dict(workstation.load_state(t, live_price=True))
    state["ticker"] = t

    # Web-only surfaces are additive. They do not replace any engine calculation.
    state["research"] = db.load_research(t)
    state["risk"] = db.risk_plan(t) or {}
    state["portfolio"] = db.portfolio_position(t) or {}
    state["validation"] = db.latest_validation_result(t) or {}
    state["share_basis_evidence"] = db.current_share_basis_evidence(t) or {}
    state["share_basis_override"] = db.share_basis_override(t) or {}
    state["gate_overrides"] = db.gate_overrides(t)
    state["opportunity"] = opportunity.evaluate(state, service.last_successful_refresh(t))

    # Preserve the local workstation's secondary evidence/control surfaces too.
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
