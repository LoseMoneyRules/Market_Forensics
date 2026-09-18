from __future__ import annotations

from statistics import median
from typing import Any

from .core_models import Company, Coverage, Security, Source, ValuationModel
from .current_financials import current_row, history_with_current
from .data_providers import latest_snapshot
from .extensions import db
from .services import valuation_result


def _n(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _sec_meta(company_id: int) -> dict[str, Any]:
    row = Source.query.filter_by(
        company_id=company_id,
        provider="SEC",
        source_type="COMPANYFACTS",
    ).order_by(Source.retrieved_at.desc(), Source.id.desc()).first()
    return dict((row.meta or {}) if row else {})


def _primary_security(company_id: int) -> Security | None:
    return Security.query.filter_by(company_id=company_id, active=True).order_by(Security.is_primary.desc(), Security.id.asc()).first()


def _metric_row(company: Company, user_id: int | None = None) -> dict[str, Any] | None:
    current = current_row(company.id)
    if not current:
        return None
    metrics = current.get("metrics") or {}
    revenue = _n(current.get("revenue"))
    inventory = _n(current.get("inventory"))
    receivables = _n(current.get("receivables"))
    fcf = _n(current.get("fcf"))
    net_income = _n(current.get("net_income"))
    operating_income = _n(current.get("operating_income"))
    pretax_income = _n(current.get("pretax_income"))
    income_tax = _n(current.get("income_tax"))
    debt = _n(current.get("debt")) or 0.0
    cash = _n(current.get("cash")) or 0.0
    equity = _n(current.get("equity"))
    shares = _n(current.get("diluted_shares")) or _n(current.get("shares_outstanding"))

    history = list(reversed(history_with_current(company.id, 6)))
    old_shares = None
    if len(history) >= 2:
        oldest = history[0]
        old_shares = _n(oldest.get("diluted_shares")) or _n(oldest.get("shares_outstanding"))
    share_change = ((shares / old_shares - 1.0) * 100.0) if shares not in (None, 0) and old_shares not in (None, 0) else None

    security = _primary_security(company.id)
    market = latest_snapshot(security.id) if security else None
    price = _n(market.price) if market else None

    coverage = None
    valuation = {}
    if user_id is not None and security:
        coverage = Coverage.query.filter_by(user_id=user_id, security_id=security.id).first()
        if coverage:
            valuation = valuation_result(coverage)

    market_cap = price * shares if price not in (None, 0) and shares not in (None, 0) else None
    fcf_yield = (fcf / market_cap * 100.0) if fcf is not None and market_cap not in (None, 0) else None
    pe = (market_cap / net_income) if market_cap not in (None, 0) and net_income is not None and net_income > 0 else None
    enterprise_value = (market_cap + debt - cash) if market_cap is not None else None
    ev_sales = (enterprise_value / revenue) if enterprise_value is not None and revenue not in (None, 0) else None
    # Reuse the canonical filing-backed ROIC. Do not inject a default tax rate
    # into peer comparisons; a missing peer ROIC is better than false precision.
    roic = _n(metrics.get("roic_pct"))

    return {
        "company_id": company.id,
        "name": company.display_name,
        "ticker": security.ticker if security else "",
        "revenue_growth_pct": _n(metrics.get("revenue_growth_pct")),
        "operating_margin_pct": _n(metrics.get("operating_margin_pct")),
        "fcf_margin_pct": _n(metrics.get("fcf_margin_pct")),
        "inventory_to_revenue_pct": (inventory / revenue * 100.0) if inventory is not None and revenue not in (None, 0) else None,
        "receivables_to_revenue_pct": (receivables / revenue * 100.0) if receivables is not None and revenue not in (None, 0) else None,
        "asset_turnover": _n(metrics.get("asset_turnover")),
        "roic_pct": roic,
        "share_change_pct": share_change,
        "pe": pe,
        "ev_sales": ev_sales,
        "fcf_yield_pct": fcf_yield,
        "base_gap_pct": (
            ((_n(valuation.get("base")) / price - 1.0) * 100.0)
            if price not in (None, 0) and _n(valuation.get("base")) is not None
            else None
        ),
        "market_cap": market_cap,
        "revenue": revenue,
        "net_income": net_income,
        "fcf": fcf,
        "shares": shares,
        "debt": debt,
        "cash": cash,
    }


def automatic_triangulation(company_id: int, user_id: int | None = None, *, min_exact_peers: int = 2, limit: int = 8) -> dict[str, Any]:
    company = db.session.get(Company, company_id)
    if not company:
        return {"available": False, "reason": "Company not found.", "peers": [], "signals": []}

    target_meta = _sec_meta(company_id)
    target_sic = str(target_meta.get("sic") or "").zfill(4) if target_meta.get("sic") else ""
    target_division = target_sic[:2] if len(target_sic) >= 2 else ""
    target = _metric_row(company, user_id)
    if not target:
        return {"available": False, "reason": "Target fundamentals are unavailable.", "peers": [], "signals": []}

    candidates: list[tuple[Company, str, str]] = []
    for peer in Company.query.filter(Company.id != company_id).all():
        meta = _sec_meta(peer.id)
        sic = str(meta.get("sic") or "").zfill(4) if meta.get("sic") else ""
        if target_sic and sic == target_sic:
            candidates.append((peer, sic, "EXACT SIC"))

    method = "EXACT SIC"
    if len(candidates) < min_exact_peers:
        method = "SIC DIVISION"
        candidates = []
        if target_division:
            for peer in Company.query.filter(Company.id != company_id).all():
                meta = _sec_meta(peer.id)
                sic = str(meta.get("sic") or "").zfill(4) if meta.get("sic") else ""
                if sic.startswith(target_division):
                    candidates.append((peer, sic, "SIC DIVISION"))

    if len(candidates) < min_exact_peers and company.industry:
        method = "INDUSTRY FALLBACK"
        seen = {row[0].id for row in candidates}
        for peer in Company.query.filter(Company.id != company_id, Company.industry == company.industry).all():
            if peer.id not in seen:
                candidates.append((peer, "", "INDUSTRY FALLBACK"))

    peer_rows = []
    for peer, sic, match in candidates:
        row = _metric_row(peer, user_id)
        if not row:
            continue
        row["sic"] = sic
        row["match"] = match
        peer_rows.append(row)

    if target.get("market_cap") is not None:
        peer_rows.sort(key=lambda r: abs((r.get("market_cap") or target["market_cap"]) - target["market_cap"]))
    else:
        peer_rows.sort(key=lambda r: (r.get("ticker") or r.get("name") or ""))

    peer_rows = peer_rows[:max(1, limit)]
    metrics = [
        ("revenue_growth_pct", "Revenue growth", "HIGHER"),
        ("operating_margin_pct", "Operating margin", "HIGHER"),
        ("fcf_margin_pct", "FCF margin", "HIGHER"),
        ("roic_pct", "ROIC", "HIGHER"),
        ("inventory_to_revenue_pct", "Inventory / Revenue", "LOWER"),
        ("receivables_to_revenue_pct", "Receivables / Revenue", "LOWER"),
        ("asset_turnover", "Asset turnover", "HIGHER"),
        ("share_change_pct", "Share change", "LOWER"),
        ("pe", "P/E", "CHEAPER"),
        ("ev_sales", "EV / Sales", "CHEAPER"),
        ("fcf_yield_pct", "FCF yield", "HIGHER"),
    ]

    comparisons = []
    signals = []
    comparison_map: dict[str, dict[str, Any]] = {}
    for key, label, direction in metrics:
        tv = target.get(key)
        vals = [r.get(key) for r in peer_rows if r.get(key) is not None]
        if tv is None or not vals:
            continue
        med = median(vals)
        diff = tv - med
        scale = max(abs(med), 1.0)
        material = abs(diff) >= max(1.5 if key.endswith("_pct") else .15, scale * .15)
        state = "IN LINE"
        if material:
            if direction == "CHEAPER":
                state = "CHEAPER" if diff < 0 else "RICHER"
            else:
                favorable = diff > 0 if direction == "HIGHER" else diff < 0
                state = "STRENGTH" if favorable else "WEAKNESS"
            signals.append({
                "label": label,
                "state": state,
                "detail": f"{label}: target {tv:.2f} vs peer median {med:.2f}.",
            })
        comparison_map[key] = {"target": tv, "peer_median": med, "difference": diff, "state": state}
        comparisons.append({
            "key": key,
            "label": label,
            "target": tv,
            "peer_median": med,
            "difference": diff,
            "state": state,
        })

    peer_value_components = []
    shares = target.get("shares")
    if shares not in (None, 0):
        pe_vals = [r.get("pe") for r in peer_rows if r.get("pe") is not None and r.get("pe") > 0]
        if len(pe_vals) >= 2 and target.get("net_income") is not None and target.get("net_income") > 0:
            multiple = median(pe_vals)
            peer_value_components.append({"method": "P/E", "value": multiple * target["net_income"] / shares, "peer_median": multiple, "sample_size": len(pe_vals)})
        evs_vals = [r.get("ev_sales") for r in peer_rows if r.get("ev_sales") is not None and r.get("ev_sales") > 0]
        if len(evs_vals) >= 2 and target.get("revenue") is not None and target.get("revenue") > 0:
            multiple = median(evs_vals)
            equity_value = target["revenue"] * multiple - (target.get("debt") or 0.0) + (target.get("cash") or 0.0)
            if equity_value > 0:
                peer_value_components.append({"method": "EV / Sales", "value": equity_value / shares, "peer_median": multiple, "sample_size": len(evs_vals)})
        fy_vals = [r.get("fcf_yield_pct") for r in peer_rows if r.get("fcf_yield_pct") is not None and r.get("fcf_yield_pct") > 0]
        if len(fy_vals) >= 2 and target.get("fcf") is not None and target.get("fcf") > 0:
            peer_yield = median(fy_vals) / 100.0
            if peer_yield > 0:
                peer_value_components.append({"method": "FCF Yield", "value": target["fcf"] / peer_yield / shares, "peer_median": peer_yield * 100.0, "sample_size": len(fy_vals)})

    peer_values = [row["value"] for row in peer_value_components if row.get("value") is not None and row.get("value") > 0]
    peer_value_crosscheck = {
        "eligible": len(peer_rows) >= 2 and len(peer_values) >= 2,
        "estimate": median(peer_values) if peer_values else None,
        "low": min(peer_values) if peer_values else None,
        "high": max(peer_values) if peer_values else None,
        "components": peer_value_components,
        "peer_count": len(peer_rows),
        "method_count": len(peer_values),
    }

    valuation_states = [comparison_map.get("pe", {}).get("state"), comparison_map.get("ev_sales", {}).get("state")]
    quality_states = [comparison_map.get("operating_margin_pct", {}).get("state"), comparison_map.get("roic_pct", {}).get("state")]
    if "RICHER" in valuation_states and "STRENGTH" not in quality_states:
        signals.insert(0, {
            "label": "Relative value + quality",
            "state": "PREMIUM WITHOUT QUALITY",
            "detail": "Valuation is richer than peers without a matching operating-margin/ROIC advantage in stored evidence.",
        })
    elif "CHEAPER" in valuation_states and "STRENGTH" in quality_states:
        signals.insert(0, {
            "label": "Relative value + quality",
            "state": "RELATIVE VALUE + QUALITY",
            "detail": "Stored evidence shows a cheaper relative multiple alongside an operating-quality advantage.",
        })

    return {
        "available": bool(peer_rows),
        "reason": "" if peer_rows else "No peer fundamentals are currently stored. Refresh/promote more companies to deepen triangulation.",
        "sic": target_sic,
        "sic_description": target_meta.get("sic_description") or company.industry or "",
        "method": method,
        "target": target,
        "peers": peer_rows,
        "comparisons": comparisons,
        "signals": signals,
        "peer_value_crosscheck": peer_value_crosscheck,
    }


def apply_peer_valuation_overlay(valuation: dict[str, Any], triangulation: dict[str, Any]) -> dict[str, Any]:
    """Blend a bounded peer cross-check into the displayed forensic fair value.

    Intrinsic Bear/Base/Bull remains the primary engine. Peer evidence earns only
    a small, explicit weight after at least two peers and two independent relative
    valuation methods are available. The overlay is capped so market multiples
    can cross-check a thesis but cannot dictate it.
    """
    out = dict(valuation or {})
    cross = dict((triangulation or {}).get("peer_value_crosscheck") or {})
    try:
        base = float(out.get("base")) if out.get("base") is not None else None
        peer = float(cross.get("estimate")) if cross.get("estimate") is not None else None
    except (TypeError, ValueError, ArithmeticError):
        base = peer = None
    if not cross.get("eligible") or base in (None, 0) or peer in (None, 0):
        out["peer_overlay"] = {"applied": False, "reason": "Insufficient peer valuation evidence.", **cross}
        return out

    peer_count = int(cross.get("peer_count") or 0)
    method_count = int(cross.get("method_count") or 0)
    weight = 0.20 if peer_count >= 5 and method_count >= 3 else 0.15
    blended_base = base * (1.0 - weight) + peer * weight
    raw_factor = blended_base / base
    factor = max(0.90, min(1.10, raw_factor))
    original = {key: out.get(key) for key in ("bear", "base", "bull", "expected_value")}
    for key in ("bear", "base", "bull", "expected_value"):
        try:
            if out.get(key) is not None:
                out[key] = float(out[key]) * factor
        except (TypeError, ValueError, ArithmeticError):
            pass
    out["intrinsic_scenarios"] = original
    out["peer_overlay"] = {
        "applied": True,
        "weight": weight,
        "uncapped_factor": raw_factor,
        "applied_factor": factor,
        "intrinsic_base": base,
        "peer_estimate": peer,
        "forensic_base": out.get("base"),
        **cross,
    }
    return out


__all__ = ["automatic_triangulation", "apply_peer_valuation_overlay"]
