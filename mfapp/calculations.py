from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Iterable

from .economic_reality import economic_from_row, metric as economic_metric

CALCULATION_VERSION = "0.2.0"


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
    if c is None or p in (None, 0): return None
    return (c / p - 1.0) * 100.0


def ratio(a: Any, b: Any, scale: float = 1.0) -> float | None:
    x, y = number(a), number(b)
    if x is None or y in (None, 0): return None
    return x / y * scale


def normalize_probabilities(values: Iterable[Any]) -> list[float]:
    raw: list[float] = []
    for value in values:
        n = number(value)
        if n is None: n = 0.0
        if n > 1.0: n /= 100.0
        raw.append(max(0.0, n))
    total = sum(raw)
    if total <= 0:
        count = len(raw); return [1.0 / count] * count if count else []
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

    def as_dict(self) -> dict[str, Any]: return asdict(self)


def calculate_valuation(*, bear: Any, base: Any, bull: Any, bear_probability: Any,
                        base_probability: Any, bull_probability: Any, current_price: Any) -> ValuationResult:
    values = [number(bear), number(base), number(bull)]
    probs_in = [bear_probability, base_probability, bull_probability]
    probs = normalize_probabilities(probs_in)
    while len(probs) < 3: probs.append(0.0)
    expected = sum(float(v) * p for v, p in zip(values, probs)) if all(v is not None for v in values) else None
    price = number(current_price)
    downside = ratio(values[0], price, 100.0); base_upside = ratio(values[1], price, 100.0); bull_upside = ratio(values[2], price, 100.0)
    downside = downside - 100.0 if downside is not None else None
    base_upside = base_upside - 100.0 if base_upside is not None else None
    bull_upside = bull_upside - 100.0 if bull_upside is not None else None
    raw_total = sum((number(x) or 0.0) / (100.0 if (number(x) or 0.0) > 1.0 else 1.0) for x in probs_in)
    warning = "" if abs(raw_total - 1.0) <= 0.001 else "Probabilities were normalized to 100%."
    return ValuationResult(values[0], values[1], values[2], probs[0], probs[1], probs[2], expected, price, downside, base_upside, bull_upside, warning)


def valuation_sensitivity(base_value: Any, current_price: Any) -> list[dict[str, float | None]]:
    base = number(base_value)
    if base is None: return []
    price = number(current_price); rows = []
    for shock in (-0.20, -0.10, 0.0, 0.10, 0.20):
        value = base * (1.0 + shock); gap = (value / price - 1.0) * 100.0 if price not in (None, 0) else None
        rows.append({"shock_pct": shock * 100.0, "value": value, "gap_pct": gap})
    return rows


def financial_metrics(current: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}; revenue = number(current.get("revenue")); cogs = number(current.get("cogs"))
    gross_profit = number(current.get("gross_profit"))
    # Gross margin is a core operating metric. When the filing supplies Revenue
    # and COGS but omits a separate Gross Profit fact, Revenue - COGS is an exact
    # accounting bridge, not an estimate. Preserve None only when the filing
    # inputs themselves are insufficient.
    if gross_profit is None and revenue is not None and cogs is not None:
        gross_profit = revenue - cogs
    metrics = {
        "revenue_growth_pct": pct_change(current.get("revenue"), previous.get("revenue")),
        "gross_margin_pct": ratio(gross_profit, revenue, 100.0),
        "operating_margin_pct": ratio(current.get("operating_income"), revenue, 100.0),
        "net_margin_pct": ratio(current.get("net_income"), revenue, 100.0),
        "fcf_margin_pct": ratio(current.get("fcf"), revenue, 100.0),
        "cfo_margin_pct": ratio(current.get("cfo"), revenue, 100.0),
        "dso": ratio(current.get("receivables"), revenue, 365.0),
        "dio": ratio(current.get("inventory"), cogs, 365.0),
        "dpo": ratio(current.get("payables"), cogs, 365.0),
        "cash_conversion_days": None,
        "inventory_growth_pct": pct_change(current.get("inventory"), previous.get("inventory")),
        "receivables_growth_pct": pct_change(current.get("receivables"), previous.get("receivables")),
        "payables_growth_pct": pct_change(current.get("payables"), previous.get("payables")),
        "fcf_growth_pct": pct_change(current.get("fcf"), previous.get("fcf")),
        "net_debt": None,
        "net_debt_to_fcf": None,
        "cfo_to_net_income": ratio(current.get("cfo"), current.get("net_income"), 1.0),
        "fcf_to_net_income": ratio(current.get("fcf"), current.get("net_income"), 1.0),
        "inventory_to_revenue_pct": ratio(current.get("inventory"), revenue, 100.0),
        "receivables_to_revenue_pct": ratio(current.get("receivables"), revenue, 100.0),
        "share_count_growth_pct": pct_change(
            current.get("diluted_shares") if current.get("diluted_shares") is not None else current.get("shares_outstanding"),
            previous.get("diluted_shares") if previous.get("diluted_shares") is not None else previous.get("shares_outstanding"),
        ),
        "asset_turnover": ratio(revenue, current.get("assets"), 1.0),
        "working_capital": None,
        "roic_pct": None,
        "calculation_version": CALCULATION_VERSION,
    }
    if metrics["dso"] is not None and metrics["dio"] is not None and metrics["dpo"] is not None:
        metrics["cash_conversion_days"] = metrics["dso"] + metrics["dio"] - metrics["dpo"]
    cash, debt = number(current.get("cash")), number(current.get("debt")); fcf = number(current.get("fcf"))
    reported_net_debt = None
    if cash is not None or debt is not None:
        reported_net_debt = (debt or 0.0) - (cash or 0.0)
    economic = economic_from_row(current)
    economic_net_debt = economic_metric(economic, "economic_net_debt")
    canonical_net_debt = economic_net_debt if economic_net_debt is not None else reported_net_debt
    metrics["reported_net_debt"] = reported_net_debt
    metrics["economic_net_debt"] = economic_net_debt
    metrics["net_debt"] = canonical_net_debt
    metrics["net_debt_basis"] = (
        str(economic.get("debt_basis") or "ECONOMIC_REALITY")
        if economic_net_debt is not None else "REPORTED_DEBT_MINUS_CASH"
    )
    metrics["economic_reality_quality"] = str(economic.get("quality") or "UNAVAILABLE")
    metrics["economic_reality_unresolved"] = bool(economic.get("material_unresolved"))
    metrics["operating_lease_liability"] = economic_metric(economic, "operating_lease_liability")
    metrics["operating_lease_share_of_liabilities_pct"] = economic_metric(economic, "operating_lease_share_of_liabilities_pct")
    metrics["lease_revenue_productivity_x"] = economic_metric(economic, "lease_revenue_productivity_x")
    metrics["growth_capex_proxy"] = economic_metric(economic, "growth_capex_proxy")
    metrics["maintenance_capex_proxy"] = economic_metric(economic, "maintenance_capex_proxy")
    metrics["owner_cash_proxy"] = economic_metric(economic, "owner_cash_proxy")
    metrics["fcf_after_sbc"] = economic_metric(economic, "fcf_after_sbc")
    metrics["economic_roic_pct"] = economic_metric(economic, "economic_roic_pct")
    metrics["lease_adjusted_roic_pct"] = economic_metric(economic, "lease_adjusted_roic_pct")
    metrics["economic_reality_flags"] = list(economic.get("flags") or [])
    metrics["economic_reality_suppressions"] = list(economic.get("suppressions") or [])
    if canonical_net_debt is not None and fcf not in (None, 0) and fcf > 0:
        metrics["net_debt_to_fcf"] = canonical_net_debt / fcf
    rec, inv, pay = number(current.get("receivables")), number(current.get("inventory")), number(current.get("payables"))
    if rec is not None or inv is not None or pay is not None:
        metrics["working_capital"] = (rec or 0.0) + (inv or 0.0) - (pay or 0.0)

    # Local V3.1.12 exposed ROIC. The web version keeps that signal only when
    # every required filing fact is actually present; unlike the Local fallback,
    # it does not invent a default tax rate when pretax/tax are missing.
    operating_income = number(current.get("operating_income"))
    pretax_income = number(current.get("pretax_income"))
    income_tax = number(current.get("income_tax"))
    equity = number(current.get("equity"))
    if operating_income is not None and pretax_income not in (None, 0) and income_tax is not None and debt is not None and cash is not None and equity is not None:
        tax_rate = max(0.0, min(0.35, income_tax / pretax_income))
        invested_capital = debt + equity - cash
        if invested_capital > 0:
            metrics["reported_roic_pct"] = operating_income * (1.0 - tax_rate) / invested_capital * 100.0
            metrics["roic_pct"] = metrics["reported_roic_pct"]
    if metrics.get("economic_roic_pct") is not None:
        metrics["roic_pct"] = metrics["economic_roic_pct"]
        metrics["roic_basis"] = "ECONOMIC_CAPITAL"
    elif metrics.get("roic_pct") is not None:
        metrics["roic_basis"] = "REPORTED_DEBT_EQUITY_CASH"
    else:
        metrics["roic_basis"] = "UNRESOLVED"
    return metrics


def _edge(source: str, target: str, value: Any, *, label: str, source_field: str, kind: str = "FLOW") -> dict[str, Any] | None:
    n = number(value)
    if n is None: return None
    return {"source": source, "target": target, "value": abs(n), "signed_value": n, "label": label, "source_field": source_field, "kind": kind, "sign": "POSITIVE" if n >= 0 else "NEGATIVE"}


def _recon(label: str, expected: Any, actual: Any) -> dict[str, Any] | None:
    a, b = number(expected), number(actual)
    if a is None or b is None: return None
    delta = b - a; scale = max(1.0, abs(a), abs(b)); ok = abs(delta) <= scale * 0.015
    return {"label": label, "expected": a, "actual": b, "delta": delta, "ok": ok}


def build_income_statement_flow(row: dict[str, Any]) -> dict[str, Any]:
    rev = number(row.get("revenue")); gp = number(row.get("gross_profit")); cogs = number(row.get("cogs"))
    op_inc = number(row.get("operating_income")); op_ex = number(row.get("operating_expenses")); pretax = number(row.get("pretax_income")); tax = number(row.get("income_tax")); net = number(row.get("net_income"))
    derived: list[str] = []; warnings: list[str] = []; edges: list[dict[str, Any]] = []; exceptions: list[dict[str, Any]] = []; reconciliations: list[dict[str, Any]] = []
    if cogs is None and rev is not None and gp is not None: cogs = rev - gp; derived.append("COGS = Revenue - Gross Profit")
    if op_ex is None and gp is not None and op_inc is not None: op_ex = gp - op_inc; derived.append("Operating Expenses = Gross Profit - Operating Income")

    def add(edge):
        if not edge: return
        (edges if edge["signed_value"] >= 0 else exceptions).append(edge)

    add(_edge("Revenue", "COGS", cogs, label="Cost of revenue", source_field="cogs"))
    add(_edge("Revenue", "Gross Profit", gp, label="Gross profit", source_field="gross_profit"))
    add(_edge("Gross Profit", "Operating Expenses", op_ex, label="Operating expenses", source_field="operating_expenses"))
    add(_edge("Gross Profit", "Operating Income", op_inc, label="Operating income", source_field="operating_income"))
    rec = _recon("Revenue bridge", (cogs + gp) if cogs is not None and gp is not None else None, rev)
    if rec: reconciliations.append(rec)
    rec = _recon("Gross-profit bridge", (op_ex + op_inc) if op_ex is not None and op_inc is not None else None, gp)
    if rec: reconciliations.append(rec)

    if pretax is not None and op_inc is not None:
        other = pretax - op_inc
        if other < 0:
            add(_edge("Operating Income", "Other / Interest Expense", abs(other), label="Net other / interest expense", source_field="derived_other_pre_tax", kind="BRIDGE"))
            add(_edge("Operating Income", "Pre-Tax Income", max(pretax, 0), label="Pre-tax income", source_field="pretax_income"))
        elif other > 0:
            add(_edge("Operating Income", "Pre-Tax Income", max(op_inc, 0), label="Operating contribution", source_field="operating_income"))
            add(_edge("Other / Interest Income", "Pre-Tax Income", other, label="Net other / interest income", source_field="derived_other_pre_tax", kind="BRIDGE"))
        else:
            add(_edge("Operating Income", "Pre-Tax Income", max(pretax, 0), label="Pre-tax income", source_field="pretax_income"))
        derived.append("Other / Interest bridge = Pre-Tax Income - Operating Income")

    if pretax is not None and net is not None:
        if tax is not None and tax >= 0:
            add(_edge("Pre-Tax Income", "Income Tax", tax, label="Income tax", source_field="income_tax"))
            add(_edge("Pre-Tax Income", "Net Income", max(net, 0), label="Net income", source_field="net_income"))
            rec = _recon("Tax bridge", net + tax, pretax)
            if rec: reconciliations.append(rec)
        elif tax is not None and tax < 0:
            add(_edge("Pre-Tax Income", "Net Income", max(pretax, 0), label="Pre-tax contribution", source_field="pretax_income"))
            add(_edge("Tax Benefit", "Net Income", abs(tax), label="Tax benefit", source_field="income_tax", kind="BRIDGE"))
        else:
            add(_edge("Pre-Tax Income", "Net Income", max(net, 0), label="Net income", source_field="net_income"))

    if rev is None: warnings.append("Revenue is unavailable; the income flow is incomplete.")
    if any(not r["ok"] for r in reconciliations): warnings.append("One or more financial bridges do not reconcile within 1.5%; inspect source facts/restatements.")
    return {"flow_type": "INCOME_STATEMENT", "period": row.get("period_label") or row.get("fiscal_year"), "edges": edges, "signed_exceptions": exceptions, "derived": derived, "reconciliations": reconciliations, "warnings": warnings, "calculation_version": CALCULATION_VERSION}


def build_cash_flow(row: dict[str, Any]) -> dict[str, Any]:
    cfo = number(row.get("cfo")); capex = number(row.get("capex")); fcf = number(row.get("fcf")); buybacks = number(row.get("buybacks")); dividends = number(row.get("dividends"))
    derived: list[str] = []; warnings: list[str] = []; edges: list[dict[str, Any]] = []; exceptions: list[dict[str, Any]] = []; reconciliations: list[dict[str, Any]] = []
    if fcf is None and cfo is not None and capex is not None: fcf = cfo - capex; derived.append("FCF = Operating Cash Flow - CapEx")

    def add(edge):
        if not edge: return
        (edges if edge["signed_value"] >= 0 else exceptions).append(edge)

    if cfo is not None and cfo >= 0:
        add(_edge("Operating Cash Flow", "Capital Expenditure", max(capex or 0, 0), label="Capital expenditure", source_field="capex"))
        if fcf is not None and fcf >= 0: add(_edge("Operating Cash Flow", "Free Cash Flow", fcf, label="Free cash flow", source_field="fcf"))
        elif fcf is not None: exceptions.append(_edge("Operating Cash Flow", "Free Cash Flow Deficit", fcf, label="Negative free cash flow", source_field="fcf") or {})
        rec = _recon("FCF bridge", (fcf or 0) + (capex or 0) if fcf is not None and capex is not None else None, cfo)
        if rec: reconciliations.append(rec)
    elif cfo is not None:
        exceptions.append(_edge("Operating Cash Flow", "Operating Cash Deficit", cfo, label="Negative operating cash flow", source_field="cfo") or {})

    if fcf is not None and fcf > 0:
        distributions = max(buybacks or 0, 0) + max(dividends or 0, 0)
        if buybacks is not None: add(_edge("Free Cash Flow", "Buybacks", max(buybacks, 0), label="Share repurchases", source_field="buybacks"))
        if dividends is not None: add(_edge("Free Cash Flow", "Dividends", max(dividends, 0), label="Dividends", source_field="dividends"))
        residual = fcf - distributions
        if residual >= 0:
            add(_edge("Free Cash Flow", "Retained / Debt / M&A / Other", residual, label="Residual free cash flow", source_field="derived_residual", kind="BRIDGE"))
        else:
            add(_edge("External Funding / Balance Sheet", "Distributions", abs(residual), label="Distributions above FCF", source_field="derived_funding_gap", kind="WARNING"))
            warnings.append("Buybacks + dividends exceed free cash flow; the difference requires balance-sheet or other funding sources.")
        derived.append("Residual = FCF - Buybacks - Dividends")
    if cfo is None: warnings.append("Operating cash flow is unavailable; the cash flow is incomplete.")
    if any(not r["ok"] for r in reconciliations): warnings.append("FCF bridge does not reconcile within 1.5%; inspect filing facts.")
    return {"flow_type": "CASH_FLOW", "period": row.get("period_label") or row.get("fiscal_year"), "edges": [x for x in edges if x], "signed_exceptions": [x for x in exceptions if x], "derived": derived, "reconciliations": reconciliations, "warnings": warnings, "calculation_version": CALCULATION_VERSION}


def bias_flags(research: dict[str, Any], journal_count: int = 0) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    thesis = str(research.get("thesis") or "").strip(); counter = str(research.get("counter_evidence") or "").strip(); variant = str(research.get("variant_us") or "").strip(); evidence = str(research.get("variant_evidence") or "").strip()
    if thesis and not counter: flags.append({"code": "COUNTER_EVIDENCE_EMPTY", "label": "Counter-evidence missing", "severity": "WATCH"})
    if variant and not evidence: flags.append({"code": "VARIANT_UNSUPPORTED", "label": "Variant perception lacks evidence", "severity": "WATCH"})
    if thesis and journal_count == 0: flags.append({"code": "NO_DECISION_JOURNAL", "label": "No decision journal entry", "severity": "WATCH"})
    if thesis and not str(research.get("confirmation_bias_notes") or "").strip(): flags.append({"code": "BIAS_REVIEW_EMPTY", "label": "Confirmation-bias review not recorded", "severity": "INFO"})
    if thesis and not str(research.get("thesis_drift_notes") or "").strip(): flags.append({"code": "THESIS_DRIFT_REVIEW_EMPTY", "label": "Thesis-drift review not recorded", "severity": "INFO"})
    return flags
