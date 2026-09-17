from __future__ import annotations

from math import isfinite
from typing import Any

FLOW_VERSION = "0.2.0"


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _edge(source: str, target: str, value: float | None, label: str, field: str, kind: str = "FLOW") -> dict[str, Any] | None:
    if value is None or abs(value) < 1e-12:
        return None
    return {
        "source": source,
        "target": target,
        "value": abs(value),
        "signed_value": value,
        "label": label,
        "source_field": field,
        "kind": kind,
        "sign": "POSITIVE" if value >= 0 else "NEGATIVE",
    }


def _recon(label: str, expected: float | None, actual: float | None) -> dict[str, Any] | None:
    if expected is None or actual is None:
        return None
    delta = actual - expected
    scale = max(1.0, abs(expected), abs(actual))
    return {"label": label, "expected": expected, "actual": actual, "delta": delta, "ok": abs(delta) <= scale * 0.015}


def _node(label: str, value: float | None, role: str, order: int, field: str, derived: bool = False) -> dict[str, Any]:
    return {"label": label, "value": value, "role": role, "order": order, "source_field": field, "derived": derived}


def build_income_statement_flow(row: dict[str, Any]) -> dict[str, Any]:
    rev = n(row.get("revenue"))
    cogs = n(row.get("cogs"))
    gp = n(row.get("gross_profit"))
    op_ex = n(row.get("operating_expenses"))
    op_inc = n(row.get("operating_income"))
    pretax = n(row.get("pretax_income"))
    tax = n(row.get("income_tax"))
    net = n(row.get("net_income"))
    period = row.get("period_label") or row.get("fiscal_year")

    edges: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    reconciliations: list[dict[str, Any]] = []
    derived: list[str] = []
    warnings: list[str] = []
    chain: list[str] = []

    def add(edge: dict[str, Any] | None) -> None:
        if not edge:
            return
        (edges if edge["signed_value"] >= 0 else exceptions).append(edge)

    if rev is None or rev <= 0:
        warnings.append("Positive Revenue is unavailable; a Revenue-to-Net bridge cannot be drawn reliably.")
        return {
            "flow_type": "INCOME_STATEMENT", "period": period, "edges": [], "nodes": [], "statement_chain": [],
            "signed_exceptions": [], "derived": derived, "reconciliations": reconciliations,
            "warnings": warnings, "calculation_version": FLOW_VERSION,
        }

    nodes.append(_node("Revenue", rev, "ANCHOR", 0, "revenue")); chain.append("Revenue")
    current_label = "Revenue"; current_value = rev; current_order = 0

    # Gross-profit layer when the filer reports enough evidence. If not, bypass it rather than breaking the whole chart.
    if gp is None and cogs is not None:
        gp = rev - cogs; derived.append("Gross Profit = Revenue - COGS")
    if cogs is None and gp is not None:
        cogs = rev - gp; derived.append("COGS = Revenue - Gross Profit")
    if gp is not None and cogs is not None and gp >= 0 and cogs >= 0:
        add(_edge("Revenue", "COGS", cogs, "Cost of revenue", "cogs"))
        add(_edge("Revenue", "Gross Profit", gp, "Gross profit", "gross_profit"))
        nodes.extend([
            _node("COGS", cogs, "DEDUCTION", 1, "cogs", "COGS =" in " ".join(derived)),
            _node("Gross Profit", gp, "SUBTOTAL", 1, "gross_profit", "Gross Profit =" in " ".join(derived)),
        ])
        rec = _recon("Revenue bridge", cogs + gp, rev)
        if rec: reconciliations.append(rec)
        current_label, current_value, current_order = "Gross Profit", gp, 1; chain.append("Gross Profit")
    else:
        warnings.append("Gross Profit/COGS presentation is incomplete; the flow bypasses that optional layer and uses the next reconcilable subtotal.")

    # Operating layer. It can be derived directly from Revenue for filers that do not present gross profit.
    op_costs_derived = False
    if op_inc is not None:
        if op_ex is None and current_value is not None:
            op_ex = current_value - op_inc; op_costs_derived = True
            derived.append(f"Operating costs/expenses = {current_label} - Operating Income")
        if op_ex is not None and op_ex >= 0 and op_inc >= 0 and current_value is not None:
            expense_label = "Operating Expenses" if current_label == "Gross Profit" else "Operating Costs / Expenses"
            add(_edge(current_label, expense_label, op_ex, expense_label, "operating_expenses", "BRIDGE" if op_costs_derived else "FLOW"))
            add(_edge(current_label, "Operating Income", op_inc, "Operating income", "operating_income"))
            nodes.extend([
                _node(expense_label, op_ex, "DEDUCTION", current_order + 1, "operating_expenses", op_costs_derived),
                _node("Operating Income", op_inc, "SUBTOTAL", current_order + 1, "operating_income"),
            ])
            rec = _recon("Operating bridge", op_ex + op_inc, current_value)
            if rec: reconciliations.append(rec)
            current_label, current_value, current_order = "Operating Income", op_inc, current_order + 1; chain.append("Operating Income")
        else:
            warnings.append("Operating Income is present but cannot be placed as a non-negative reconciled subtotal for this period.")

    # Pretax layer, including other/interest expense or income as an explicit side flow.
    if pretax is not None and pretax >= 0:
        if current_value is not None and current_label != "Pre-Tax Income":
            if pretax <= current_value:
                other_exp = current_value - pretax
                if other_exp > 0:
                    add(_edge(current_label, "Other / Interest Expense", other_exp, "Net other / interest expense", "derived_other_pre_tax", "BRIDGE"))
                    nodes.append(_node("Other / Interest Expense", other_exp, "DEDUCTION", current_order + 1, "derived_other_pre_tax", True))
                add(_edge(current_label, "Pre-Tax Income", pretax, "Pre-tax income", "pretax_income"))
            else:
                add(_edge(current_label, "Pre-Tax Income", current_value, "Operating contribution", current_label.lower().replace(" ", "_")))
                other_income = pretax - current_value
                add(_edge("Other / Interest Income", "Pre-Tax Income", other_income, "Net other / interest income", "derived_other_pre_tax", "BRIDGE"))
                nodes.append(_node("Other / Interest Income", other_income, "CONTRIBUTION", current_order + 1, "derived_other_pre_tax", True))
            nodes.append(_node("Pre-Tax Income", pretax, "SUBTOTAL", current_order + 1, "pretax_income"))
            derived.append("Other / Interest bridge = Pre-Tax Income - preceding subtotal")
            current_label, current_value, current_order = "Pre-Tax Income", pretax, current_order + 1; chain.append("Pre-Tax Income")
    elif pretax is not None:
        exceptions.append(_edge(current_label, "Pre-Tax Loss", pretax, "Pre-tax loss", "pretax_income") or {})
        nodes.append(_node("Pre-Tax Loss", pretax, "LOSS", current_order + 1, "pretax_income"))
        warnings.append("Pre-tax result is negative; it is shown as a signed exception instead of a fake positive Sankey width.")

    # Final bridge to reported Net Income/Loss. If pretax is unavailable, preserve continuity from the last verified subtotal.
    if net is not None and net >= 0 and current_value is not None:
        residual = current_value - net
        if current_label == "Pre-Tax Income" and tax is not None:
            if tax >= 0:
                tax_used = min(max(tax, 0.0), max(residual, 0.0))
                if tax_used > 0:
                    add(_edge(current_label, "Income Tax", tax_used, "Income tax", "income_tax"))
                    nodes.append(_node("Income Tax", tax_used, "DEDUCTION", current_order + 1, "income_tax"))
                remainder = residual - tax_used
                if remainder > 1e-9:
                    add(_edge(current_label, "Below-line / Reconciliation", remainder, "Below-line / reconciliation", "derived_below_line", "BRIDGE"))
                    nodes.append(_node("Below-line / Reconciliation", remainder, "DEDUCTION", current_order + 1, "derived_below_line", True))
                rec = _recon("Pretax-to-net bridge", net + max(tax, 0.0), pretax)
                if rec: reconciliations.append(rec)
            else:
                add(_edge(current_label, "Net Income", current_value, "Pre-tax contribution", "pretax_income"))
                add(_edge("Tax Benefit", "Net Income", abs(tax), "Tax benefit", "income_tax", "BRIDGE"))
                nodes.append(_node("Tax Benefit", abs(tax), "CONTRIBUTION", current_order + 1, "income_tax"))
                residual = 0
        elif residual > 0:
            label = "Remaining Expenses / Tax / Reconciliation"
            add(_edge(current_label, label, residual, label, "derived_final_bridge", "BRIDGE"))
            nodes.append(_node(label, residual, "DEDUCTION", current_order + 1, "derived_final_bridge", True))
        if not any(e.get("target") == "Net Income" for e in edges):
            add(_edge(current_label, "Net Income", net, "Net income", "net_income"))
        nodes.append(_node("Net Income", net, "RESULT", current_order + 1, "net_income")); chain.append("Net Income")
    elif net is not None:
        exceptions.append(_edge(current_label, "Net Loss", net, "Net loss", "net_income") or {})
        nodes.append(_node("Net Loss", net, "LOSS", current_order + 1, "net_income")); chain.append("Net Loss")
        warnings.append("Net result is a loss; the signed loss is retained explicitly rather than converted into a positive width.")
    else:
        warnings.append("Net Income/Loss is unavailable; the final statement anchor is missing.")

    if any(not item["ok"] for item in reconciliations):
        warnings.append("One or more accounting bridges exceed the 1.5% reconciliation tolerance; review source facts/restatements.")

    return {
        "flow_type": "INCOME_STATEMENT",
        "period": period,
        "edges": [x for x in edges if x],
        "nodes": nodes,
        "statement_chain": chain,
        "signed_exceptions": [x for x in exceptions if x],
        "derived": derived,
        "reconciliations": reconciliations,
        "warnings": warnings,
        "calculation_version": FLOW_VERSION,
    }


def build_cash_flow(row: dict[str, Any]) -> dict[str, Any]:
    cfo = n(row.get("cfo")); capex = n(row.get("capex")); fcf = n(row.get("fcf")); buybacks = n(row.get("buybacks")); dividends = n(row.get("dividends"))
    period = row.get("period_label") or row.get("fiscal_year")
    edges: list[dict[str, Any]] = []; exceptions: list[dict[str, Any]] = []; nodes: list[dict[str, Any]] = []
    reconciliations: list[dict[str, Any]] = []; derived: list[str] = []; warnings: list[str] = []

    def add(edge: dict[str, Any] | None) -> None:
        if not edge: return
        (edges if edge["signed_value"] >= 0 else exceptions).append(edge)

    if cfo is None:
        return {"flow_type": "CASH_FLOW", "period": period, "edges": [], "nodes": [], "statement_chain": [], "signed_exceptions": [], "derived": [], "reconciliations": [], "warnings": ["Operating Cash Flow is unavailable."], "calculation_version": FLOW_VERSION}
    nodes.append(_node("Operating Cash Flow", cfo, "ANCHOR", 0, "cfo"))
    chain = ["Operating Cash Flow"]
    if cfo < 0:
        exceptions.append(_edge("Funding / Balance Sheet", "Operating Cash Deficit", cfo, "Negative operating cash flow", "cfo") or {})
        nodes.append(_node("Operating Cash Deficit", cfo, "LOSS", 1, "cfo"))
        warnings.append("Operating cash flow is negative; no positive cash generation is fabricated.")
        return {"flow_type": "CASH_FLOW", "period": period, "edges": edges, "nodes": nodes, "statement_chain": chain + ["Operating Cash Deficit"], "signed_exceptions": exceptions, "derived": derived, "reconciliations": reconciliations, "warnings": warnings, "calculation_version": FLOW_VERSION}

    if capex is not None and capex < 0:
        capex = abs(capex); derived.append("CapEx sign normalized to outflow magnitude")
    if fcf is None and capex is not None:
        fcf = cfo - capex; derived.append("FCF = Operating Cash Flow - CapEx")
    if capex is not None:
        add(_edge("Operating Cash Flow", "Capital Expenditure", capex, "Capital expenditure", "capex"))
        nodes.append(_node("Capital Expenditure", capex, "DEDUCTION", 1, "capex"))
    if fcf is not None and fcf >= 0:
        add(_edge("Operating Cash Flow", "Free Cash Flow", fcf, "Free cash flow", "fcf"))
        nodes.append(_node("Free Cash Flow", fcf, "SUBTOTAL", 1, "fcf")); chain.append("Free Cash Flow")
        rec = _recon("FCF bridge", (fcf + capex) if capex is not None else None, cfo)
        if rec: reconciliations.append(rec)
        distributions = max(buybacks or 0, 0) + max(dividends or 0, 0)
        if buybacks is not None and buybacks > 0:
            add(_edge("Free Cash Flow", "Buybacks", buybacks, "Share repurchases", "buybacks")); nodes.append(_node("Buybacks", buybacks, "DEDUCTION", 2, "buybacks"))
        if dividends is not None and dividends > 0:
            add(_edge("Free Cash Flow", "Dividends", dividends, "Dividends", "dividends")); nodes.append(_node("Dividends", dividends, "DEDUCTION", 2, "dividends"))
        residual = fcf - distributions
        if residual >= 0:
            add(_edge("Free Cash Flow", "Retained / Debt / M&A / Other", residual, "Residual free cash flow", "derived_residual", "BRIDGE"))
            nodes.append(_node("Retained / Debt / M&A / Other", residual, "RESULT", 2, "derived_residual", True))
        else:
            add(_edge("External Funding / Balance Sheet", "Distributions", abs(residual), "Distributions above FCF", "derived_funding_gap", "WARNING"))
            nodes.append(_node("External Funding / Balance Sheet", abs(residual), "CONTRIBUTION", 2, "derived_funding_gap", True))
            warnings.append("Buybacks + dividends exceed Free Cash Flow; the difference requires balance-sheet or other funding.")
        derived.append("Residual = FCF - Buybacks - Dividends")
    elif fcf is not None:
        exceptions.append(_edge("Operating Cash Flow", "Free Cash Flow Deficit", fcf, "Negative free cash flow", "fcf") or {})
        nodes.append(_node("Free Cash Flow Deficit", fcf, "LOSS", 1, "fcf")); chain.append("Free Cash Flow Deficit")
    if any(not item["ok"] for item in reconciliations):
        warnings.append("FCF bridge exceeds the 1.5% reconciliation tolerance; inspect filing facts.")
    return {"flow_type": "CASH_FLOW", "period": period, "edges": edges, "nodes": nodes, "statement_chain": chain, "signed_exceptions": exceptions, "derived": derived, "reconciliations": reconciliations, "warnings": warnings, "calculation_version": FLOW_VERSION}


__all__ = ["FLOW_VERSION", "build_income_statement_flow", "build_cash_flow"]
