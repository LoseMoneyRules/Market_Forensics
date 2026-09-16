from __future__ import annotations

import math
from typing import Iterable


def _f(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def annual_rows(rows: Iterable[dict]) -> list[dict]:
    out = []
    for raw in rows or []:
        r = dict(raw)
        if str(r.get("period_type") or "").upper() != "FY":
            continue
        fy = r.get("fiscal_year")
        try:
            fy = int(fy)
        except Exception:
            continue
        r["fiscal_year"] = fy
        out.append(r)
    return sorted(out, key=lambda r: r["fiscal_year"])


def income_statement_flow(row: dict) -> dict:
    """Build an adaptive, audit-friendly FY income-statement Sankey.

    V3.1.12 deliberately treats Gross Profit, Operating Income, Pretax Income and Tax as
    *optional presentation layers*. US GAAP filers do not all present the same subtotals (UPS is
    a common example: Revenue -> Operating Expenses -> Operating Profit, with no Gross Profit).
    Missing optional subtotals are therefore omitted instead of blocking the whole visualization.

    The only essential anchors are positive Revenue and a reported Net Income/Loss. Intermediate
    nodes are used only when they reconcile monotonically. Exact residuals are labelled as
    aggregate/reconciliation buckets; no undisclosed SG&A/R&D composition is invented.
    """
    r = dict(row or {})
    revenue = _f(r.get("revenue"))
    gp = _f(r.get("gross_profit"))
    oi = _f(r.get("flow_operating_income"))
    if oi is None:
        oi = _f(r.get("operating_income"))
    oi_method = str(r.get("flow_operating_income_method") or ("REPORTED_OPERATING_INCOME" if oi is not None else ""))
    pretax = _f(r.get("pretax"))
    tax = _f(r.get("tax"))
    ni = _f(r.get("net_income"))

    missing = [name for name, val in (("Revenue", revenue), ("Net income/loss", ni)) if val is None]
    if missing:
        return {"ok": False, "reason": "Missing essential statement anchor(s): " + ", ".join(missing), "rows": []}
    if revenue <= 0:
        return {"ok": False, "reason": "Reported annual revenue is zero/negative, so a Revenue-to-Net Sankey is not meaningful for this fiscal year.", "rows": []}

    tol = max(1.0, abs(revenue) * 1e-8)
    labels: list[str] = []
    links: list[tuple[int, int, float]] = []
    rows: list[dict] = []

    def idx(label: str) -> int:
        if label not in labels:
            labels.append(label)
        return labels.index(label)

    def link(source: int, target: int, value):
        v = _f(value)
        if v is not None and v > tol * 1e-6:
            links.append((source, target, v))

    def add_row(label: str, amount):
        v = _f(amount)
        rows.append({"Line": label, "Amount": v, "% Revenue": (v / revenue if v is not None and revenue else None)})

    rev_i = idx("Revenue")
    add_row("Revenue", revenue)
    current_i = rev_i
    current_value = revenue
    current_stage = "Revenue"
    used_gp = False
    used_oi = False
    used_pretax = False
    notes = []

    if gp is not None and -tol <= gp <= revenue + tol:
        gp = min(revenue, max(0.0, gp))
        oi_for_check = oi if oi is not None and oi >= 0 else None
        if oi_for_check is None or gp + tol >= oi_for_check:
            cogs = max(0.0, revenue - gp)
            cogs_i = idx("Cost of revenue")
            gp_i = idx("Gross profit")
            link(rev_i, cogs_i, cogs)
            link(rev_i, gp_i, gp)
            add_row("Cost of revenue", cogs)
            add_row("Gross profit", gp)
            current_i, current_value, current_stage = gp_i, gp, "Gross profit"
            used_gp = True
        else:
            notes.append("Gross profit was reported but omitted because it does not reconcile above the selected operating subtotal.")
    elif gp is not None:
        notes.append("Gross profit was reported but omitted because its sign/order is not compatible with a non-negative Sankey.")
    else:
        notes.append("Gross profit is not presented by this filer; the diagram uses the next verifiable subtotal instead.")

    if oi is not None and oi >= -tol and oi <= current_value + tol:
        oi = min(current_value, max(0.0, oi))
        expense = max(0.0, current_value - oi)
        expense_label = "Operating expenses (aggregate)" if used_gp else "Operating expenses / costs (aggregate)"
        exp_i = idx(expense_label)
        oi_i = idx("Operating income")
        link(current_i, exp_i, expense)
        link(current_i, oi_i, oi)
        add_row(expense_label, expense)
        add_row("Operating income", oi)
        current_i, current_value, current_stage = oi_i, oi, "Operating income"
        used_oi = True
    elif oi is not None:
        notes.append("Operating income was omitted because it does not reconcile monotonically with the preceding subtotal.")
    else:
        notes.append("Operating income is unavailable as a unique reported/exactly-derived subtotal; the diagram bridges to the next verified line.")

    if pretax is not None and pretax >= -tol:
        pretax = max(0.0, pretax)
        pt_i = idx("Pretax income")
        if current_value + tol >= pretax:
            nonop = max(0.0, current_value - pretax)
            if nonop > tol:
                nonop_label = "Net non-operating / other expense" if used_oi else "Costs / expenses before pretax (reconciliation)"
                nonop_i = idx(nonop_label)
                link(current_i, nonop_i, nonop)
                add_row(nonop_label, nonop)
            link(current_i, pt_i, pretax)
        else:
            link(current_i, pt_i, current_value)
            extra = pretax - current_value
            other_i = idx("Other / non-operating income")
            link(other_i, pt_i, extra)
            add_row("Other / non-operating income", extra)
        add_row("Pretax income", pretax)
        current_i, current_value, current_stage = pt_i, pretax, "Pretax income"
        used_pretax = True
    elif pretax is not None:
        notes.append("Pretax income is negative; the positive-width Sankey skips that subtotal and reconciles directly to the reported net result.")
    else:
        notes.append("Pretax income is not uniquely available; it is omitted rather than blocking the visualization.")

    if ni >= 0:
        net_i = idx("Net income")
        if current_value + tol >= ni:
            residual = max(0.0, current_value - ni)
            if used_pretax:
                tax_exp = max(0.0, tax or 0.0)
                tax_used = min(tax_exp, residual)
                if tax_used > tol:
                    tax_i = idx("Income tax")
                    link(current_i, tax_i, tax_used)
                    add_row("Income tax", tax_used)
                rem = max(0.0, residual - tax_used)
                if rem > tol:
                    recon_i = idx("Below-line / reconciliation")
                    link(current_i, recon_i, rem)
                    add_row("Below-line / reconciliation", rem)
                if tax is None and residual > tol:
                    notes.append("Tax was unavailable; the exact pretax-to-net residual is shown as a reconciliation bucket.")
            else:
                if residual > tol:
                    recon_label = "Remaining expenses / tax / reconciliation"
                    recon_i = idx(recon_label)
                    link(current_i, recon_i, residual)
                    add_row(recon_label, residual)
            link(current_i, net_i, ni)
        else:
            link(current_i, net_i, current_value)
            benefit = ni - current_value
            benefit_i = idx("Other income / tax benefit / reconciliation")
            link(benefit_i, net_i, benefit)
            add_row("Other income / tax benefit / reconciliation", benefit)
        add_row("Net income", ni)
    else:
        absorbed_label = "Costs / charges absorbing remaining income"
        absorbed_i = idx(absorbed_label)
        link(current_i, absorbed_i, current_value)
        if current_value > tol:
            add_row(absorbed_label, current_value)
        loss_i = idx("Net loss")
        deficit_i = idx("Excess costs / losses over income")
        link(deficit_i, loss_i, abs(ni))
        add_row("Net loss", ni)
        notes.append("Loss-year view: the signed net loss is shown as a separate deficit flow; no negative Sankey width is fabricated.")

    basis_parts = ["Adaptive FY statement flow using reported SEC values and exact arithmetic residuals only."]
    if used_gp:
        basis_parts.append("Gross profit is shown because this filer reports a reconcilable gross-profit subtotal.")
    else:
        basis_parts.append("Gross profit is optional and was bypassed for this filing presentation.")
    if oi_method and oi_method != "REPORTED_OPERATING_INCOME" and used_oi:
        basis_parts.append("Operating income uses exact bridge: " + oi_method.replace("DERIVED_", "").replace("_", " ") + ".")
    if notes:
        basis_parts.append(" ".join(notes))

    return {
        "ok": True,
        "labels": labels,
        "links": [(s, t, v) for s, t, v in links if v is not None and v > 0],
        "rows": rows,
        "basis": " ".join(basis_parts),
        "resolved": {
            "gross_profit": gp if used_gp else None,
            "operating_income": oi if used_oi else None,
            "pretax": pretax if used_pretax else None,
            "tax": tax,
            "net_income": ni,
            "operating_income_method": oi_method,
            "presentation_path": " -> ".join([x for x in ("Revenue", "Gross profit" if used_gp else None, "Operating income" if used_oi else None, "Pretax income" if used_pretax else None, "Net income" if ni >= 0 else "Net loss") if x]),
        },
    }


def cash_flow_flow(row: dict) -> dict:
    """Build an adaptive cash-conversion Sankey from reliably stored FY cash-flow metrics.

    CFO is the only essential cash anchor. NI and Capex improve the bridge when available but no
    longer block the page. Negative CFO/FCF years are represented explicitly with deficit/funding
    nodes rather than being converted into misleading positive operating cash generation.
    """
    r = dict(row or {})
    ni = _f(r.get("net_income"))
    cfo = _f(r.get("cfo"))
    capex = _f(r.get("capex"))
    fcf = _f(r.get("fcf"))
    buybacks = max(0.0, _f(r.get("buybacks")) or 0.0)
    dividends = max(0.0, _f(r.get("dividends")) or 0.0)

    if cfo is None:
        return {"ok": False, "reason": "Missing essential cash-flow anchor: Cash from operations", "rows": []}
    if capex is not None and capex < 0:
        capex = abs(capex)
        capex_sign_note = "Capex sign was normalized to an outflow magnitude for visualization."
    else:
        capex_sign_note = ""

    labels: list[str] = []
    links: list[tuple[int, int, float]] = []
    rows: list[dict] = []

    def idx(label: str) -> int:
        if label not in labels:
            labels.append(label)
        return labels.index(label)

    def link(source: int, target: int, value):
        v = _f(value)
        if v is not None and v > 0:
            links.append((source, target, v))

    base = abs(cfo) if cfo else None
    def add_row(label, amount):
        v = _f(amount)
        rows.append({"Line": label, "Amount": v, "% CFO": (v / base if v is not None and base else None)})

    notes = []
    cfo_positive = cfo >= 0

    if cfo_positive:
        cfo_i = idx("Cash from operations")
        if ni is not None and ni >= 0:
            ni_i = idx("Net income")
            add_row("Net income", ni)
            if cfo >= ni:
                link(ni_i, cfo_i, ni)
                bridge = cfo - ni
                if bridge > 0:
                    bridge_i = idx("Non-cash + working-capital bridge")
                    link(bridge_i, cfo_i, bridge)
                    add_row("Non-cash + working-capital bridge", bridge)
            else:
                link(ni_i, cfo_i, cfo)
                bridge = ni - cfo
                if bridge > 0:
                    bridge_i = idx("Cash absorption: non-cash + working capital")
                    link(ni_i, bridge_i, bridge)
                    add_row("Cash absorption: non-cash + working capital", bridge)
        else:
            notes.append("Net income is missing/negative, so the cash diagram starts from reported CFO instead of fabricating a positive NI bridge.")
            if ni is not None:
                add_row("Net income / loss", ni)
        add_row("Cash from operations", cfo)
    else:
        cfo_i = idx("Cash used in operations")
        funding_i = idx("Cash draw / financing for operations")
        link(funding_i, cfo_i, abs(cfo))
        if ni is not None:
            add_row("Net income / loss", ni)
        add_row("Cash from operations", cfo)
        notes.append("Negative CFO is shown as an operating cash deficit funded by cash draw/financing; no positive CFO is fabricated.")

    positive_fcf = None
    capital_source_i = None
    capital_source_value = None
    if cfo_positive and capex is not None:
        calc_fcf = cfo - capex
        if fcf is None or abs(fcf - calc_fcf) > max(1.0, abs(cfo) * 1e-6):
            if fcf is not None:
                notes.append("Stored FCF did not reconcile to CFO − Capex; visualization uses the exact CFO − Capex bridge.")
            fcf = calc_fcf
        capex_i = idx("Capex")
        add_row("Capex", capex)
        if capex <= cfo:
            fcf_i = idx("Free cash flow")
            link(cfo_i, capex_i, capex)
            link(cfo_i, fcf_i, cfo - capex)
            positive_fcf = cfo - capex
            add_row("Free cash flow", positive_fcf)
            capital_source_i, capital_source_value = fcf_i, positive_fcf
        else:
            link(cfo_i, capex_i, cfo)
            shortfall = capex - cfo
            ext_i = idx("External financing / cash draw for Capex")
            link(ext_i, capex_i, shortfall)
            neg_i = idx("Negative free cash flow")
            link(ext_i, neg_i, shortfall)
            add_row("Free cash flow", -shortfall)
            notes.append("Capex exceeded CFO; the exact FCF deficit is shown separately as a financing/cash-draw requirement.")
    elif cfo_positive:
        notes.append("Capex is unavailable, so FCF is omitted rather than blocking the cash-flow visualization.")
        capital_source_i, capital_source_value = cfo_i, cfo
    else:
        if capex is not None:
            capex_i = idx("Capex")
            ext_i = idx("Cash draw / financing for Capex")
            link(ext_i, capex_i, capex)
            add_row("Capex", capex)
        notes.append("Capital allocation cannot be funded from negative CFO; any disclosed returns are shown against an explicit financing/cash-draw pool.")

    returns = buybacks + dividends
    if returns > 0:
        pool = idx("Capital allocation pool")
        available = max(0.0, capital_source_value or 0.0)
        from_internal = min(available, returns)
        if capital_source_i is not None and from_internal > 0:
            link(capital_source_i, pool, from_internal)
        gap = max(0.0, returns - from_internal)
        if gap > 0:
            ext_i = idx("External financing / cash draw")
            link(ext_i, pool, gap)
        if buybacks > 0:
            link(pool, idx("Buybacks"), buybacks)
        if dividends > 0:
            link(pool, idx("Dividends"), dividends)
        add_row("Buybacks", buybacks)
        add_row("Dividends", dividends)
        if gap > 0:
            add_row("External financing / cash draw needed for disclosed capital returns", gap)
        retained = max(0.0, available - from_internal)
        if retained > 0 and capital_source_i is not None:
            retained_i = idx("Retained cash / other uses")
            link(capital_source_i, retained_i, retained)
            add_row("Retained cash / other uses", retained)
    elif cfo_positive and capital_source_i is not None and (capital_source_value or 0) > 0:
        retained_i = idx("Retained cash / other uses")
        link(capital_source_i, retained_i, capital_source_value)
        add_row("Retained cash / other uses", capital_source_value)

    basis = "Adaptive FY cash flow using reported CFO and exact arithmetic bridges only. Missing optional NI/Capex/FCF layers are omitted rather than blocking the view."
    if capex_sign_note:
        basis += " " + capex_sign_note
    if notes:
        basis += " " + " ".join(notes)
    return {"ok": True, "labels": labels, "links": [x for x in links if x[2] > 0], "rows": rows, "basis": basis}
