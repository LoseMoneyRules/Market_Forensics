from __future__ import annotations

from typing import Any

import requests
from sqlalchemy import func

from .core_models import Coverage, Security
from .data_providers import get_secret
from .extensions import db
from .symbols import validate_ticker


def _covered(user_id: int) -> set[str]:
    rows = (
        db.session.query(Security.ticker)
        .join(Coverage, Coverage.security_id == Security.id)
        .filter(Coverage.user_id == user_id)
        .all()
    )
    return {str(row[0]).upper() for row in rows}


def _alpaca_search(query: str, user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return []
    response = requests.get(
        "https://paper-api.alpaca.markets/v2/assets",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        params={"status": "active", "asset_class": "us_equity"},
        timeout=12,
    )
    if response.status_code != 200:
        return []
    q = query.strip().upper()
    out = []
    for row in response.json() or []:
        symbol = str(row.get("symbol") or "").upper()
        name = str(row.get("name") or "")
        exchange = str(row.get("exchange") or "")
        if not row.get("tradable") or not symbol:
            continue
        hay = f"{symbol} {name}".upper()
        if q and q not in hay:
            continue
        score = 0
        if symbol == q:
            score += 100
        elif symbol.startswith(q):
            score += 50
        if name.upper().startswith(q):
            score += 25
        out.append({
            "ticker": symbol,
            "name": name or symbol,
            "exchange": exchange,
            "tradable": True,
            "source": "Alpaca assets",
            "match_score": score,
        })
    out.sort(key=lambda x: (-x["match_score"], len(x["ticker"]), x["ticker"]))
    return out[:limit]


def search_universe(query: str, user_id: int, limit: int = 30) -> dict[str, Any]:
    """Search outside current Coverage without building a shadow market database.

    Shared-hosting friendly: external discovery is query driven. It does not download
    fundamentals for the whole US market. A candidate is validated again before it can
    enter Coverage.
    """
    q = str(query or "").strip().upper()
    covered = _covered(user_id)
    rows = _alpaca_search(q, user_id, limit=limit) if q else []
    if q and not rows and len(q) <= 16:
        validated = validate_ticker(q)
        if validated.valid:
            rows = [{
                "ticker": q,
                "name": validated.name or q,
                "exchange": validated.exchange or "",
                "tradable": True,
                "source": validated.source or "symbol validation",
                "match_score": 100,
            }]
    for row in rows:
        row["in_coverage"] = row["ticker"] in covered
    return {
        "query": q,
        "results": rows,
        "outside_coverage": [row for row in rows if not row["in_coverage"]],
        "covered_matches": [row for row in rows if row["in_coverage"]],
        "provider": "Alpaca assets" if get_secret(user_id, "alpaca_key") else "symbol validation",
    }


def classify_coverage(intelligence: dict[str, Any], readiness: dict[str, Any]) -> list[str]:
    """Discovery lenses for already-known companies.

    These labels are evidence navigation aids, never recommendations.
    """
    labels: list[str] = []
    gap = intelligence.get("base_gap_pct")
    bias = str(intelligence.get("bias") or "NEUTRAL")
    stance = str(intelligence.get("stance") or "NO EDGE")
    confidence = str(intelligence.get("confidence") or "LOW")
    negatives = int(intelligence.get("negatives") or 0)
    positives = int(intelligence.get("positives") or 0)
    ready_ratio = (readiness.get("done", 0) / readiness.get("total", 1)) if readiness.get("total") else 0
    decision_grade = intelligence.get("valuation_decision_grade")
    decision_grade = True if decision_grade is None else bool(decision_grade)

    if decision_grade and gap is not None and gap >= 20 and confidence in {"MEDIUM", "HIGH"} and negatives <= positives:
        labels.append("QUALITY AT DISCOUNT")
    if decision_grade and gap is not None and gap >= 25 and bias == "LONG":
        labels.append("LONG DISLOCATION")
    if decision_grade and gap is not None and gap <= -15 and bias == "SHORT":
        labels.append("SHORT DISLOCATION")
    if decision_grade and gap is not None and gap >= 25 and negatives > positives:
        labels.append("POTENTIAL VALUE TRAP")
    if negatives >= 2 and abs(float(gap or 0)) < 20:
        labels.append("FORENSIC DIVERGENCE")
    if stance == "DATA REVIEW" or ready_ratio < .5:
        labels.append("RESEARCH QUEUE")
    return labels or ["NO EDGE / MONITOR"]


__all__ = ["search_universe", "classify_coverage"]
