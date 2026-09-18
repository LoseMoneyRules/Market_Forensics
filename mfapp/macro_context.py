from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from math import isfinite
from typing import Any

import requests

from .core_models import Company, Event, Source
from .extensions import db

FRED_GRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES = {
    "rates": {"id": "DGS10", "label": "US 10Y yield", "unit": "%", "mode": "LEVEL"},
    "credit": {"id": "BAMLH0A0HYM2", "label": "US high-yield OAS", "unit": "%", "mode": "LEVEL"},
    "usd": {"id": "DTWEXBGS", "label": "Trade-weighted USD", "unit": "index", "mode": "INDEX"},
    "oil": {"id": "DCOILWTICO", "label": "WTI crude", "unit": "$/bbl", "mode": "INDEX"},
    "inflation": {"id": "CPIAUCSL", "label": "US CPI", "unit": "index", "mode": "INDEX"},
    "industrial": {"id": "INDPRO", "label": "US industrial production", "unit": "index", "mode": "INDEX"},
    "consumer": {"id": "RSAFS", "label": "US retail sales", "unit": "$m", "mode": "INDEX"},
}

DEFAULT_EXPOSURES = {
    "rates": "FALLING",
    "credit": "FALLING",
    "industrial": "RISING",
}

EXPOSURE_RULES = [
    (("apparel", "footwear", "retail", "consumer", "restaurant", "beverage"),
     {"consumer": "RISING", "inflation": "FALLING", "rates": "FALLING", "credit": "FALLING", "usd": "RISING"}),
    (("industrial", "machinery", "equipment", "aerospace", "manufacturing"),
     {"industrial": "RISING", "rates": "FALLING", "credit": "FALLING", "usd": "FALLING", "oil": "FALLING"}),
    (("software", "cloud", "saas", "technology", "semiconductor"),
     {"rates": "FALLING", "credit": "FALLING", "industrial": "RISING"}),
    (("bank", "financial", "insurance"),
     {"credit": "FALLING", "consumer": "RISING", "industrial": "RISING"}),
    (("energy", "oil", "gas", "petroleum"),
     {"oil": "RISING", "industrial": "RISING", "credit": "FALLING"}),
    (("auto", "vehicle", "automotive"),
     {"rates": "FALLING", "credit": "FALLING", "consumer": "RISING", "industrial": "RISING", "oil": "FALLING"}),
    (("real estate", "reit", "housing"),
     {"rates": "FALLING", "credit": "FALLING", "inflation": "FALLING"}),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _n(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _fetch_series(series_id: str) -> dict[str, Any]:
    response = requests.get(
        FRED_GRAPH,
        params={"id": series_id},
        headers={"User-Agent": "MarketForensics/0.2.7"},
        timeout=12,
    )
    response.raise_for_status()
    rows = []
    for row in csv.DictReader(StringIO(response.text)):
        raw = row.get(series_id)
        value = _n(raw) if raw not in (None, "", ".") else None
        date_value = row.get("DATE") or row.get("observation_date") or next(iter(row.values()), None)
        if value is not None and date_value:
            rows.append((str(date_value), value))
    if not rows:
        raise RuntimeError(f"FRED {series_id} returned no usable observations")

    latest_date, latest = rows[-1]
    prior_3 = rows[max(0, len(rows) - 1 - min(65, len(rows) - 1))][1]
    prior_12 = rows[max(0, len(rows) - 1 - min(260, len(rows) - 1))][1]
    return {
        "series_id": series_id,
        "as_of": latest_date,
        "value": latest,
        "change_3m": latest - prior_3,
        "change_12m": latest - prior_12,
        "change_3m_pct": ((latest / prior_3 - 1.0) * 100.0) if prior_3 else None,
        "change_12m_pct": ((latest / prior_12 - 1.0) * 100.0) if prior_12 else None,
    }


def _trend(row: dict[str, Any], mode: str) -> str:
    if mode == "LEVEL":
        delta = _n(row.get("change_3m"))
        threshold = 0.15
    else:
        delta = _n(row.get("change_3m_pct"))
        threshold = 1.0
    if delta is None or abs(delta) < threshold:
        return "STABLE"
    return "RISING" if delta > 0 else "FALLING"


def exposures_for_company(company: Company) -> dict[str, str]:
    text = f"{company.sector or ''} {company.industry or ''}".lower()
    exposures = dict(DEFAULT_EXPOSURES)
    for tokens, mapped in EXPOSURE_RULES:
        if any(token in text for token in tokens):
            exposures.update(mapped)
    return exposures


def refresh_macro_context(company_id: int) -> dict[str, Any]:
    company = db.session.get(Company, company_id)
    if company is None:
        raise RuntimeError("Company not found")

    factors = {}
    errors = []
    for key, spec in SERIES.items():
        try:
            row = _fetch_series(spec["id"])
            factors[key] = {**spec, **row, "trend": _trend(row, spec["mode"])}
        except Exception as exc:
            errors.append(f"{spec['id']}: {type(exc).__name__}")

    if not factors:
        raise RuntimeError("Macro refresh returned no usable FRED factors")

    source = Source(
        company_id=company.id,
        provider="FRED",
        source_type="MACRO_CONTEXT",
        title="FRED macro context",
        url="https://fred.stlouisfed.org/",
        retrieved_at=utcnow(),
        meta={"series": [row["id"] for row in SERIES.values()], "errors": errors},
    )
    db.session.add(source)
    db.session.flush()
    event = Event(
        company_id=company.id,
        source_id=source.id,
        event_type="MACRO_CONTEXT",
        title=f"{company.display_name} macro context",
        event_date=utcnow(),
        payload={"factors": factors, "errors": errors},
    )
    db.session.add(event)
    db.session.commit()
    return macro_context(company.id)


def macro_context(company_id: int) -> dict[str, Any]:
    company = db.session.get(Company, company_id)
    if company is None:
        return {"available": False, "for": [], "against": [], "watch": [], "factors": [], "reason": "Company not found."}

    event = Event.query.filter_by(company_id=company.id, event_type="MACRO_CONTEXT").order_by(Event.event_date.desc(), Event.id.desc()).first()
    exposures = exposures_for_company(company)
    if event is None:
        return {
            "available": False,
            "for": [],
            "against": [],
            "watch": [],
            "factors": [],
            "exposures": exposures,
            "reason": "Macro snapshot not refreshed yet. Run Refresh macro or Refresh stale.",
        }

    payload = dict(event.payload or {})
    factors = []
    for key, sensitivity in exposures.items():
        row = dict((payload.get("factors") or {}).get(key) or {})
        if not row:
            continue
        trend = str(row.get("trend") or "STABLE")
        if trend == "STABLE":
            stance = "WATCH"
        elif trend == sensitivity:
            stance = "FOR"
        else:
            stance = "AGAINST"
        factors.append({
            "key": key,
            "label": row.get("label") or key,
            "series_id": row.get("series_id"),
            "value": row.get("value"),
            "unit": row.get("unit"),
            "as_of": row.get("as_of"),
            "trend": trend,
            "sensitivity": sensitivity,
            "stance": stance,
            "change_3m": row.get("change_3m"),
            "change_3m_pct": row.get("change_3m_pct"),
        })

    return {
        "available": bool(factors),
        "for": [row for row in factors if row["stance"] == "FOR"],
        "against": [row for row in factors if row["stance"] == "AGAINST"],
        "watch": [row for row in factors if row["stance"] == "WATCH"],
        "factors": factors,
        "exposures": exposures,
        "errors": payload.get("errors") or [],
        "as_of": event.event_date.isoformat() if event.event_date else None,
        "source": "FRED",
        "reason": "" if factors else "Macro snapshot exists but no mapped factor is usable.",
    }


__all__ = ["SERIES", "exposures_for_company", "macro_context", "refresh_macro_context"]
