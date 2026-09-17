from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from math import isfinite
from typing import Any, Iterable

CALCULATION_VERSION = "0.1.0"


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return n if isfinite(n) else None


def pct_change(current: Any, previous: Any) -> float | None:
    c, p = number(current), number(previous)
    if c is None or p in (None, 0):
        return None
    return (c / p - 1.0) * 100.0


def ratio(a: Any, b: Any, scale: float = 1.0) -> float | None:
    x, y = number(a), number(b)
    if x is None or y in (None, 0):
        return None
    return x / y * scale


def normalize_probabilities(values: Iterable[Any]) -> list[float]:
    raw = []
    for value in values:
        n = number(value)
        if n is None:
            n = 0.0
        if n > 1.0:
            n /= 100.0
        raw.append(max(0.0, n))
    total = sum(raw)
    if total <= 0:
        count = len(raw)
        return [1.0 / count] * count if count else []
    return [x / total for x in raw]


@dataclass(frozen=True)
class ValuationResult:
    bear: float | None
    base: float | None
    bull: float | None
    bear_probability: float
    base_probability: float
    bull_probability: float
    expected_value: float | None
    current_price: float | None
    downside_pct: float | None
    base_upside_pct: float | None
    bull_upside_pct: float | None
    probability_warning: str
    calculation_version: str = CALCULATION_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_valuation(
    *,
    bear: Any,
    base: Any,
    bull: Any,
    bear_probability: Any,
    base_probability: Any,
    bull_probability: Any,
    current_price: Any,
) -> ValuationResult:
    values = [number(bear), number(base), number(bull)]
    probs_in = [bear_probability, base_probability, bull_probability]
    probs = normalize_probabilities(probs_in)
    while len(probs) < 3:
        probs.append(0.0)
    expected = None
    if all(v is not None for v in values):
        expected = sum(float(v) * p for v, p in zip(values, probs))
    price = number(current_price)
    downside = ratio(values[0], price, 100.0)
    base_upside = ratio(values[1], price, 100.0)
    bull_upside = ratio(values[2], price, 100.0)
    downside = downside - 100.0 if downside is not None else None
    base_upside = base_upside - 100.0 if base_upside is not None else None
    bull_upside = bull_upside - 100.0 if bull_upside is not None else None
    raw_total = sum((number(x) or 0) / (100.0 if (number(x) or 0) > 1 else 1.0) for x in probs_in)
    warning = "" if abs(raw_total - 1.0) <= 0.001 else "Probabilities were normalized to 100%."
    return ValuationResult(
        bear=values[0],
        base=values[1],
        bull=values[2],
        bear_probability=probs[0],
        base_probability=probs[1],
        bull_probability=probs[2],
        expected_value=expected,
        current_price=price,
        downside_pct=downside,
        base_upside_pct=base_upside,
        bull_upside_pct=bull_upside,
        probability_warning=warning,
    )


def financial_metrics(current: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    revenue = number(current.get("revenue"))
    metrics = {
        "revenue_growth_pct": pct_change(current.get("revenue"), previous.get("revenue")),
        "gross_margin_pct": ratio(current.get("gross_profit"), revenue, 100.0),
        "operating_margin_pct": ratio(current.get("operating_income"), revenue, 100.0),
        "net_margin_pct": ratio(current.get("net_income"), revenue, 100.0),
        "fcf_margin_pct": ratio(current.get("fcf"), revenue, 100.0),
        "dso": ratio(current.get("receivables"), revenue, 365.0),
        "dio": ratio(current.get("inventory"), current.get("cogs"), 365.0),
        "dpo": ratio(current.get("payables"), current.get("cogs"), 365.0),
        "cash_conversion_days": None,
        "inventory_growth_pct": pct_change(current.get("inventory"), previous.get("inventory")),
        "receivables_growth_pct": pct_change(current.get("receivables"), previous.get("receivables")),
        "fcf_growth_pct": pct_change(current.get("fcf"), previous.get("fcf")),
        "net_debt": None,
        "calculation_version": CALCULATION_VERSION,
    }
    if metrics["dso"] is not None and metrics["dio"] is not None and metrics["dpo"] is not None:
        metrics["cash_conversion_days"] = metrics["dso"] + metrics["dio"] - metrics["dpo"]
    cash, debt = number(current.get("cash")), number(current.get("debt"))
    if cash is not None or debt is not None:
        metrics["net_debt"] = (debt or 0.0) - (cash or 0.0)
    return metrics


def _edge(source: str, target: str, value: Any, *, label: str, source_field: str) -> dict[str, Any] | None:
    n = number(value)
    if n is None:
        return None
    return {
        "source": source,
        "target": target,
        "value": n,
        "label": label,
        "source_field": source_field,
        "sign": "POSITIVE" if n >= 0 else "NEGATIVE",
    }


def build_income_statement_flow(row: dict[str, Any]) -> dict[str, Any]:
    """Build an auditable filing-aware flow without inventing missing bridges.

    Negative values remain negative in the payload. The browser renderer never converts
    a negative amount into a fake positive-width edge; negative items are shown as signed
    exceptions beside the Sankey instead.
    """
    rev = number(row.get("revenue"))
    gp = number(row.get("gross_profit"))
    cogs = number(row.get("cogs"))
    op_inc = number(row.get("operating_income"))
    op_ex = number(row.get("operating_expenses"))
    pretax = number(row.get("pretax_income"))
    tax = number(row.get("income_tax"))
    net = number(row.get("net_income"))

    derived: list[str] = []
    if cogs is None and rev is not None and gp is not None:
        cogs = rev - gp
        derived.append("cogs = revenue - gross_profit")
    if op_ex is None and gp is not None and op_inc is not None:
        op_ex = gp - op_inc
        derived.append("operating_expenses = gross_profit - operating_income")
    other = None
    if pretax is not None and op_inc is not None:
        other = pretax - op_inc
        derived.append("other_pre_tax = pretax_income - operating_income")

    candidates = [
        _edge("Revenue", "COGS", cogs, label="Cost of revenue", source_field="cogs"),
        _edge("Revenue", "Gross Profit", gp, label="Gross profit", source_field="gross_profit"),
        _edge("Gross Profit", "Operating Expenses", op_ex, label="Operating expenses", source_field="operating_expenses"),
        _edge("Gross Profit", "Operating Income", op_inc, label="Operating income", source_field="operating_income"),
        _edge("Operating Income", "Other / Interest", other, label="Other / interest bridge", source_field="derived_other_pre_tax"),
        _edge("Operating Income", "Pre-Tax Income", pretax, label="Pre-tax income", source_field="pretax_income"),
        _edge("Pre-Tax Income", "Tax", tax, label="Income tax", source_field="income_tax"),
        _edge("Pre-Tax Income", "Net Income", net, label="Net income", source_field="net_income"),
    ]
    edges = [x for x in candidates if x is not None]
    positive_edges = [x for x in edges if x["value"] >= 0]
    signed_exceptions = [x for x in edges if x["value"] < 0]
    return {
        "flow_type": "INCOME_STATEMENT",
        "period": row.get("period_label") or row.get("fiscal_year"),
        "edges": positive_edges,
        "signed_exceptions": signed_exceptions,
        "derived": derived,
        "warnings": [] if rev is not None else ["Revenue is unavailable; the flow is incomplete."],
        "calculation_version": CALCULATION_VERSION,
    }


def build_cash_flow(row: dict[str, Any]) -> dict[str, Any]:
    cfo = number(row.get("cfo"))
    capex = number(row.get("capex"))
    fcf = number(row.get("fcf"))
    buybacks = number(row.get("buybacks"))
    dividends = number(row.get("dividends"))
    if fcf is None and cfo is not None and capex is not None:
        fcf = cfo - capex
    candidates = [
        _edge("Operating Cash Flow", "Capital Expenditure", capex, label="Capital expenditure", source_field="capex"),
        _edge("Operating Cash Flow", "Free Cash Flow", fcf, label="Free cash flow", source_field="fcf"),
        _edge("Free Cash Flow", "Buybacks", buybacks, label="Share repurchases", source_field="buybacks"),
        _edge("Free Cash Flow", "Dividends", dividends, label="Dividends", source_field="dividends"),
    ]
    edges = [x for x in candidates if x is not None]
    return {
        "flow_type": "CASH_FLOW",
        "period": row.get("period_label") or row.get("fiscal_year"),
        "edges": [x for x in edges if x["value"] >= 0],
        "signed_exceptions": [x for x in edges if x["value"] < 0],
        "derived": ["fcf = cfo - capex"] if row.get("fcf") is None and fcf is not None else [],
        "warnings": [] if cfo is not None else ["Operating cash flow is unavailable; the flow is incomplete."],
        "calculation_version": CALCULATION_VERSION,
    }


def bias_flags(research: dict[str, Any], journal_count: int = 0) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    thesis = str(research.get("thesis") or "").strip()
    counter = str(research.get("counter_evidence") or "").strip()
    variant = str(research.get("variant_us") or "").strip()
    evidence = str(research.get("variant_evidence") or "").strip()
    if thesis and not counter:
        flags.append({"code": "COUNTER_EVIDENCE_EMPTY", "label": "Counter-evidence missing", "severity": "WATCH"})
    if variant and not evidence:
        flags.append({"code": "VARIANT_UNSUPPORTED", "label": "Variant perception lacks evidence", "severity": "WATCH"})
    if thesis and journal_count == 0:
        flags.append({"code": "NO_DECISION_JOURNAL", "label": "No decision journal entry", "severity": "WATCH"})
    if str(research.get("confirmation_bias_notes") or "").strip() == "" and thesis:
        flags.append({"code": "BIAS_REVIEW_EMPTY", "label": "Confirmation-bias review not recorded", "severity": "INFO"})
    if str(research.get("thesis_drift_notes") or "").strip() == "" and thesis:
        flags.append({"code": "THESIS_DRIFT_REVIEW_EMPTY", "label": "Thesis-drift review not recorded", "severity": "INFO"})
    return flags
