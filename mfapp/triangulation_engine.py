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
        "share_change_pct": share_change,
        "fcf_yield_pct": fcf_yield,
        "base_gap_pct": (
            ((_n(valuation.get("base")) / price - 1.0) * 100.0)
            if price not in (None, 0) and _n(valuation.get("base")) is not None
            else None
        ),
        "market_cap": market_cap,
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
        ("revenue_growth_pct", "Revenue growth", True),
        ("operating_margin_pct", "Operating margin", True),
        ("fcf_margin_pct", "FCF margin", True),
        ("inventory_to_revenue_pct", "Inventory / Revenue", False),
        ("receivables_to_revenue_pct", "Receivables / Revenue", False),
        ("asset_turnover", "Asset turnover", True),
        ("share_change_pct", "Share change", False),
        ("fcf_yield_pct", "FCF yield", True),
    ]

    comparisons = []
    signals = []
    for key, label, higher_better in metrics:
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
            favorable = diff > 0 if higher_better else diff < 0
            state = "STRENGTH" if favorable else "WEAKNESS"
            signals.append({
                "label": label,
                "state": state,
                "detail": f"{label}: target {tv:.2f} vs peer median {med:.2f}.",
            })
        comparisons.append({
            "key": key,
            "label": label,
            "target": tv,
            "peer_median": med,
            "difference": diff,
            "state": state,
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
    }


__all__ = ["automatic_triangulation"]
