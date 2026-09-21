from __future__ import annotations

from typing import Any

from .valuation_forensics import build_peer_analysis, _multiple_to_equity_value


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
