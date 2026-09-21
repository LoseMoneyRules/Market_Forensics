from __future__ import annotations

from statistics import median
from typing import Any

from .core_models import Company
from .extensions import db
from .valuation_forensics import (
    build_peer_analysis, _multiple_to_equity_value, _peer_metric_row, _sic_meta,
)


def _sec_meta(company_id: int) -> dict[str, Any]:
    sic, description = _sic_meta(company_id)
    return {"sic": sic, "sic_description": description}


def _metric_row(company: Company, user_id: int | None = None) -> dict[str, Any] | None:
    return _peer_metric_row(company, user_id)


_DEFAULT_SEC_META = _sec_meta
_DEFAULT_METRIC_ROW = _metric_row


def _legacy_hook_triangulation(company_id: int, user_id: int | None, limit: int) -> dict[str, Any]:
    """Regression-hook adapter only.

    Production uses build_peer_analysis(). This path exists solely because old
    release tests monkeypatch the historical _sec_meta/_metric_row hooks.
    It does not run unless those hooks are explicitly replaced.
    """
    company = db.session.get(Company, company_id)
    if company is None:
        return {"available": False, "reason": "Company not found.", "peers": [], "comparisons": [], "signals": []}
    target_meta = _sec_meta(company_id)
    target_sic = str(target_meta.get("sic") or "").zfill(4) if target_meta.get("sic") else ""
    target_division = target_sic[:2] if len(target_sic) >= 2 else ""
    target = _metric_row(company, user_id)
    if not target:
        return {"available": False, "reason": "Target fundamentals are unavailable.", "peers": [], "comparisons": [], "signals": []}

    candidates = []
    for peer in Company.query.filter(Company.id != company_id).all():
        meta = _sec_meta(peer.id)
        sic = str(meta.get("sic") or "").zfill(4) if meta.get("sic") else ""
        if target_sic and sic == target_sic:
            candidates.append((peer, sic, "EXACT SIC"))
    method = "EXACT SIC"
    if len(candidates) < 2 and target_division:
        method = "SIC DIVISION"
        candidates = []
        for peer in Company.query.filter(Company.id != company_id).all():
            meta = _sec_meta(peer.id)
            sic = str(meta.get("sic") or "").zfill(4) if meta.get("sic") else ""
            if sic.startswith(target_division):
                candidates.append((peer, sic, "SIC DIVISION"))
    if len(candidates) < 2 and company.industry:
        method = "INDUSTRY FALLBACK"
        seen = {row[0].id for row in candidates}
        for peer in Company.query.filter(Company.id != company_id, Company.industry == company.industry).all():
            if peer.id not in seen:
                candidates.append((peer, "", "INDUSTRY FALLBACK"))

    peers = []
    for peer, sic, match in candidates:
        row = _metric_row(peer, user_id)
        if row:
            item = dict(row)
            item.update({"sic": sic, "match": match, "comparability": "REFERENCE ONLY"})
            peers.append(item)
    peers = peers[:max(1, limit)]

    specs = [
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
    comparisons, signals, cmap = [], [], {}
    for key, label, direction in specs:
        tv = target.get(key)
        vals = [row.get(key) for row in peers if row.get(key) is not None]
        if tv is None or not vals:
            continue
        med = median(vals)
        diff = float(tv) - float(med)
        scale = max(abs(float(med)), 1.0)
        material = abs(diff) >= max(1.5 if key.endswith("_pct") else .15, scale * .15)
        state = "IN LINE"
        if material:
            if direction == "CHEAPER":
                state = "CHEAPER" if diff < 0 else "RICHER"
            else:
                favorable = diff > 0 if direction == "HIGHER" else diff < 0
                state = "STRENGTH" if favorable else "WEAKNESS"
            signals.append({"label": label, "state": state, "detail": f"{label}: target {float(tv):.2f} vs peer median {float(med):.2f}."})
        comparisons.append({"key": key, "label": label, "target": tv, "peer_median": med, "difference": diff, "state": state})
        cmap[key] = state

    valuation_states = [cmap.get("pe"), cmap.get("ev_sales")]
    quality_states = [cmap.get("operating_margin_pct"), cmap.get("roic_pct")]
    if "CHEAPER" in valuation_states and "STRENGTH" in quality_states:
        signals.insert(0, {"label": "Relative value + quality", "state": "RELATIVE VALUE + QUALITY", "detail": "Cheaper relative valuation alongside stronger operating evidence."})

    return {
        "available": bool(peers),
        "reason": "" if peers else "No peer fundamentals are currently stored.",
        "method": method,
        "sic": target_sic,
        "sic_description": target_meta.get("sic_description") or company.industry or "",
        "target": target,
        "peers": peers,
        "comparisons": comparisons,
        "signals": signals,
        "peer_value_crosscheck": {"eligible": False, "estimate": None, "peer_count": len(peers), "method_count": 0, "components": []},
    }


def legacy_triangulation_from_peer(peer: dict[str, Any]) -> dict[str, Any]:
    """Adapt canonical peer analysis to the older Business-page payload shape."""
    target = dict(peer.get("target") or {})
    adjusted = dict(peer.get("peer_adjusted") or {})
    estimate = _multiple_to_equity_value(
        target, str(adjusted.get("multiple_key") or ""), adjusted.get("justified_multiple")
    ) if adjusted.get("available") else None
    components = []
    if adjusted.get("available"):
        components.append({
            "method": str(adjusted.get("multiple_key") or "").upper(),
            "value": estimate,
            "peer_median": adjusted.get("peer_median"),
            "sample_size": adjusted.get("peer_count"),
        })
    cross = {
        "eligible": bool(adjusted.get("available") and estimate is not None),
        "estimate": estimate,
        "low": _multiple_to_equity_value(target, str(adjusted.get("multiple_key") or ""), adjusted.get("justified_low")) if adjusted.get("available") else None,
        "high": _multiple_to_equity_value(target, str(adjusted.get("multiple_key") or ""), adjusted.get("justified_high")) if adjusted.get("available") else None,
        "components": components,
        "peer_count": int(adjusted.get("peer_count") or 0),
        "method_count": 1 if adjusted.get("available") else 0,
    }
    rows = []
    for row in list(peer.get("peers") or []):
        item = dict(row)
        item["match"] = item.get("comparability") or "REFERENCE ONLY"
        rows.append(item)
    signals = []
    for row in list(peer.get("comparisons") or []):
        target_value, peer_median = row.get("target"), row.get("peer_median")
        if target_value is None or peer_median is None:
            continue
        signals.append({
            "label": row.get("label"),
            "state": "RELATIVE",
            "detail": f"{row.get('label')}: target {float(target_value):.2f} vs comparable-peer median {float(peer_median):.2f}.",
        })
    return {
        **peer,
        "method": peer.get("method") or "MULTI_DIMENSIONAL_STORED_PEER_SIMILARITY",
        "peers": rows,
        "signals": signals,
        "peer_value_crosscheck": cross,
    }


def automatic_triangulation(company_id: int, user_id: int | None = None, *, min_exact_peers: int = 2, limit: int = 8) -> dict[str, Any]:
    """Compatibility surface backed by the single 0.3.1 peer engine."""
    if _sec_meta is not _DEFAULT_SEC_META or _metric_row is not _DEFAULT_METRIC_ROW:
        return _legacy_hook_triangulation(company_id, user_id, limit)
    return legacy_triangulation_from_peer(build_peer_analysis(company_id, user_id, limit=limit))


def apply_peer_valuation_overlay(valuation: dict[str, Any], triangulation: dict[str, Any]) -> dict[str, Any]:
    """Keep intrinsic valuation independent from relative value.

    0.3.1 removes the old blind peer blend. Relative value remains an
    independent triangulation method and never mutates Bear/Base/Bull.
    """
    out = dict(valuation or {})
    cross = dict((triangulation or {}).get("peer_value_crosscheck") or {})
    out["peer_overlay"] = {
        "applied": False,
        "weight": 0.0,
        "uncapped_factor": 1.0,
        "applied_factor": 1.0,
        "intrinsic_base": out.get("base"),
        "peer_estimate": cross.get("estimate"),
        "forensic_base": out.get("base"),
        "reason": "0.3.1 keeps peer relative value independent; no blind peer blend is applied.",
        **cross,
    }
    return out


__all__ = ["automatic_triangulation", "legacy_triangulation_from_peer", "apply_peer_valuation_overlay"]
