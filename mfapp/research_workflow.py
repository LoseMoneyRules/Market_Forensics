"""Decision-oriented Research navigation built only from already-stored evidence.

No provider/network calls take place while a page is rendered. The 0.4.x
RESEARCH_REFRESH job remains the single source-fetch and materialization path.
"""
from __future__ import annotations

from typing import Any

# One research question, source family and human interpretation per step.
STEP_SPEC = {
    "overview": ("What is the decision TODAY, and what would change it?", "Source-backed thesis, variant perception and downside", "Write the thesis, opposing evidence and variant. Then approve each gate below."),
    "business": ("How does this company earn economic profit?", "Issuer filings, business segmentation and sourced peer relationships", "Judge the durable drivers, customers, competition and moat. Do not turn unsourced anecdotes into facts."),
    "fundamentals": ("Do filed earnings turn into resilient cash?", "Canonical SEC financial years and quarters; audited provenance", "Explain margins, balance-sheet pressure, accounting quality and missing years."),
    "expectations": ("What growth and margins are already priced in?", "Filed operating history, explicit forecasts, market-implied diagnostics", "Record what consensus/market assumes versus your model and which KPI resolves the difference."),
    "valuation": ("What are the independently defensible Bear / Base / Bull prices?", "Materialized intrinsic model, share-basis checks and market quotes", "Verify share count, assumptions, probabilities and fair value quality before relying on any upside."),
    "bear-case": ("What breaks first and how large can the loss become?", "Valuation Bear scenario, balance sheet, negative operating evidence", "Add falsifiable risks, probabilities, severity and the evidence that would make you abandon the thesis."),
    "catalysts": ("What dated event could close the gap?", "Issuer events, filings and dated analyst-entered catalysts", "Describe the expected event, when, direction and observable confirmation."),
    "financial-flows": ("Where does revenue and cash actually go?", "Reconciled SEC Income Statement / Cash Flow financial bridges", "Review weak cash conversion, working-capital leaks and unreconciled edges by year."),
    "management": ("Can management execute and allocate capital honestly?", "Sourced management promises, capital allocation, outcomes", "Distinguish documented commitments from your qualitative assessment."),
    "tape": ("Is positioning confirming, opposing or merely noisy?", "Persisted tape, historical price, FINRA and positioning evidence", "Never treat short volume as short interest or partial prints as verified accumulation."),
    "monitoring": ("What must be watched and when does the thesis die?", "Locked thesis invalidation, observable monitoring rules and alerts", "Set numerical conditions before investing; monitor evidence instead of price alone."),
    "journal": ("What exact action are we committing to, with what evidence?", "Frozen decisions, historical entry/exit conditions and outcomes", "Document LONG / SHORT / WAIT / CLOSE separately from the Research stance; portfolio execution remains separate."),
    "audit": ("Could another analyst reconstruct every important number?", "Source URLs, retrieval dates, normalized fields and refresh errors", "Resolve missing/disputed sources before approval. An analyst URL is not automatically verified."),
    "validate": ("Would this reasoning have survived historical point-in-time replay?", "Frozen historical scenarios and no-lookahead walk-forward results", "Interpret sample size, history coverage and stability before acting. Validation does not submit an order."),
}

def _fmt(value: Any, *, money: bool = False, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    try:
        if money:
            return f"${float(value):,.2f}"
        if isinstance(value, (float, int)) or hasattr(value, "as_tuple"):
            numeric = float(value)
            formatted = f"{numeric:,.0f}" if numeric.is_integer() else f"{numeric:,.2f}".rstrip("0").rstrip(".")
            return formatted + suffix
    except (ValueError, TypeError, OverflowError):
        return "—"
    return str(value)

def _n(source: Any, key: str, fallback=None):
    return source.get(key, fallback) if isinstance(source, dict) else fallback

def build_research_workflow(ctx: dict, steps: list[tuple[str, str]], reviews: dict, sources: list) -> dict:
    """Render hints are diagnostic, not a new investment score or research cache."""
    readiness = ctx.get("readiness") or {}
    gates = {x["key"]: x for x in readiness.get("gates") or []}
    valuation = ctx.get("valuation") or {}
    research_cache = ctx.get("research_cache") or {}
    company = ctx.get("company")
    market = ctx.get("market")
    current = _n(ctx.get("brief") or {}, "current_financial") or {}
    metrics = _n(current, "metrics") or {}
    lens = ctx.get("decision_lenses") or {}
    latest_source = sources[0] if sources else None
    price = valuation.get("current_price")
    gap = ((float(valuation["base"]) / float(price) - 1) * 100
           if valuation.get("base") is not None and price not in (None, 0) else None)

    def f(title: str, value: Any) -> dict:
        return {"label": title, "value": _fmt(value)}

    highlights = {
        "overview": [f("Readiness", f"{readiness.get('done',0)}/{readiness.get('total',0)} approved"),
                     f("Decision posture", lens.get("research_conclusion") or "DATA REVIEW"),
                     f("Base / market", f"{gap:+.1f}%" if gap is not None else "—")],
        "business": [f("Sector", getattr(company, "sector", "") or "Unclassified"),
                     f("Industry", getattr(company, "industry", "") or "Unclassified"),
                     f("Sourced relationships", len(_n(research_cache.get("triangulation"), "comparisons") or []))],
        "fundamentals": [f("Filed FY count", _n(gates.get("fundamentals",{}).get("evidence"), "annual_periods")),
                         f("Missing FY in target", len(_n(gates.get("fundamentals",{}).get("evidence"), "annual_missing_years") or [])),
                         f("Quarterly periods", _n(gates.get("fundamentals",{}).get("evidence"), "quarter_periods"))],
        "expectations": [f("Structured estimates", _n(gates.get("expectations",{}).get("evidence"), "structured")),
                          f("Current base", _fmt(valuation.get("base"), money=True)),
                          f("Base / market", f"{gap:+.1f}%" if gap is not None else "—")],
        "valuation": [f("Bear", _fmt(valuation.get("bear"), money=True)),
                       f("Base", _fmt(valuation.get("base"), money=True)),
                       f("Bull", _fmt(valuation.get("bull"), money=True)),
                       f("Quality", valuation.get("base_quality") or "UNVERIFIED")],
        "bear-case": [f("Bear target", _fmt(valuation.get("bear"), money=True)),
                       f("Bear items", _n(gates.get("bear-case",{}).get("evidence"), "structured")),
                       f("Net debt / FCF", _fmt(metrics.get("net_debt_to_fcf"), suffix="x"))],
        "catalysts": [f("Catalysts entered", _n(gates.get("catalysts",{}).get("evidence"), "structured")),
                      f("Model status", valuation.get("base_quality") or "UNVERIFIED")],
        "financial-flows": [f("Reconciled flow records", _n(gates.get("financial-flows",{}).get("evidence"), "flows")),
                             f("Cash generation", _fmt(current.get("fcf"), money=True))],
        "management": [f("Reviewed assessments", _n(gates.get("management",{}).get("evidence"), "assessments")),
                        f("Engine assessment", _n(research_cache.get("management"), "score"))],
        "tape": [f("Observed provider sources", _n(gates.get("tape",{}).get("evidence"), "sources")),
                 f("Data quality", valuation.get("base_quality") or "UNVERIFIED")],
        "monitoring": [f("Active rules", _n(gates.get("monitoring",{}).get("evidence"), "rules")),
                        f("Invalidation locked", "Yes" if _n(gates.get("monitoring",{}).get("evidence"), "invalidation_locked") else "No")],
        "journal": [f("Recorded decisions", _n(gates.get("journal",{}).get("evidence"), "entries")),
                     f("Research conclusion", lens.get("research_conclusion") or "DATA REVIEW")],
        "audit": [f("Stored source records", _n(gates.get("audit",{}).get("evidence"), "sources")),
                   f("Latest source", getattr(latest_source, "provider", None) or "—")],
        "validate": [f("Research approved", f"{readiness.get('done',0)}/{readiness.get('total',0)}"),
                      f("Historical state", _n(readiness.get("validation"), "state") or "NOT RUN")],
    }

    by_key = {}
    for index, (key, label) in enumerate(steps):
        question, auto, manual = STEP_SPEC[key]
        gate = gates.get(key)
        by_key[key] = {
            "key": key, "label": label, "number": index + 1, "total": len(steps),
            "question": question, "auto": auto, "manual": manual,
            "facts": highlights.get(key, []), "review": reviews.get(key),
            "gate": gate, "last_source": latest_source,
            "previous": steps[index - 1] if index else None,
            "next": steps[index + 1] if index + 1 < len(steps) else None,
            "no_data_warning": bool(ctx.get("cache_pending") or readiness.get("degraded")),
        }
    return {"by_key": by_key}
