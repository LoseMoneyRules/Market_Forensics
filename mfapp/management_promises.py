from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any

from .core_models import Event, Source
from .current_financials import annual_rows
from .extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


GUIDANCE_WORDS = r"(?:expect(?:s|ed)?|guidance|outlook|forecast(?:s|ed)?|anticipat(?:e|es|ed)|target(?:s|ed)?)"


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>", " ", raw or "")
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _scale_money(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit.startswith("b"):
        return value * 1_000_000_000
    if unit.startswith("m"):
        return value * 1_000_000
    return value


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in chunks if 20 <= len(s.strip()) <= 900]


def extract_promises(text: str, *, source_id: int | None = None) -> list[dict[str, Any]]:
    """Extract only explicit numeric guidance with an explicit target year.

    Conservative by design: if a year/metric/range cannot be read confidently,
    the sentence is not converted into a scored promise.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sentence in _sentences(text):
        low_sentence = sentence.lower()
        if not re.search(GUIDANCE_WORDS, low_sentence):
            continue
        year_match = re.search(r"\b(20[2-4]\d)\b", sentence)
        if not year_match:
            continue
        target_year = int(year_match.group(1))

        specs = [
            ("revenue_growth_pct", r"(?:revenue|sales)[^.;]{0,100}?(?:growth|increase|decline)[^.;]{0,80}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)(?:\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?", "%"),
            ("operating_margin_pct", r"operating\s+margin[^.;]{0,100}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)(?:\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?", "%"),
            ("gross_margin_pct", r"gross\s+margin[^.;]{0,100}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)(?:\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?", "%"),
            ("net_margin_pct", r"net\s+margin[^.;]{0,100}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)(?:\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?", "%"),
            ("revenue", r"(?:revenue|sales)[^.;]{0,100}?\$\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|m)\b(?:\s*(?:to|-|–)\s*\$?\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|m)\b)?", "USD"),
            ("fcf", r"(?:free\s+cash\s+flow|fcf)[^.;]{0,100}?\$\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|m)\b(?:\s*(?:to|-|–)\s*\$?\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|m)\b)?", "USD"),
        ]

        for metric, pattern, unit in specs:
            match = re.search(pattern, sentence, flags=re.I)
            if not match:
                continue
            groups = match.groups()
            if unit == "%":
                low = float(groups[0])
                high = float(groups[1]) if len(groups) > 1 and groups[1] else low
            else:
                low = _scale_money(float(groups[0]), str(groups[1]))
                if len(groups) > 3 and groups[2]:
                    high = _scale_money(float(groups[2]), str(groups[3] or groups[1]))
                else:
                    high = low
            if low > high:
                low, high = high, low
            fingerprint = hashlib.sha256(f"{source_id}|{metric}|{target_year}|{low}|{high}|{sentence}".encode()).hexdigest()[:24]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append({
                "metric": metric,
                "target_year": target_year,
                "low": low,
                "high": high,
                "unit": unit,
                "operator": "RANGE" if low != high else "TARGET",
                "statement": sentence[:800],
                "fingerprint": fingerprint,
                "source_id": source_id,
                "origin": "AUTO_FILING",
            })
    return out


def store_promises(company_id: int, promises: list[dict[str, Any]], *, source_id: int | None = None) -> int:
    stored = 0
    source = db.session.get(Source, source_id) if source_id else None
    event_date = (source.published_at if source and source.published_at else None) or (source.retrieved_at if source and source.retrieved_at else None) or utcnow()
    for row in promises:
        fp = str(row.get("fingerprint") or "")
        existing = Event.query.filter_by(company_id=company_id, event_type="MANAGEMENT_PROMISE").all()
        if any(str((event.payload or {}).get("fingerprint") or "") == fp for event in existing):
            continue
        payload = dict(row)
        payload["status"] = "PENDING"
        db.session.add(Event(
            company_id=company_id,
            source_id=source_id or row.get("source_id"),
            event_type="MANAGEMENT_PROMISE",
            title=f"{row.get('metric')} guidance for {row.get('target_year')}",
            event_date=event_date,
            payload=payload,
        ))
        stored += 1
    if stored:
        db.session.commit()
    return stored


def _actual_for_year(company_id: int, metric: str, year: int) -> float | None:
    rows = annual_rows(company_id, 20)
    row = next((r for r in rows if int(r.get("fiscal_year") or 0) == int(year)), None)
    if not row:
        return None
    if metric in {"revenue", "fcf"}:
        value = row.get(metric)
    else:
        value = (row.get("metrics") or {}).get(metric)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def evaluate_promises(company_id: int) -> list[dict[str, Any]]:
    events = Event.query.filter_by(company_id=company_id, event_type="MANAGEMENT_PROMISE").order_by(Event.event_date.desc(), Event.id.desc()).all()
    out = []
    dirty = False
    for event in events:
        payload = dict(event.payload or {})
        metric = str(payload.get("metric") or "")
        year = int(payload.get("target_year") or 0)
        actual = _actual_for_year(company_id, metric, year) if metric and year else None
        low = payload.get("low")
        high = payload.get("high")
        status = "PENDING"
        if actual is not None and low is not None and high is not None:
            low_f, high_f = float(low), float(high)
            if low_f == high_f:
                tolerance = max(abs(low_f) * .05, 0.5 if str(payload.get("unit")) == "%" else 1.0)
                status = "MET" if abs(actual - low_f) <= tolerance else "MISS"
            else:
                status = "MET" if low_f <= actual <= high_f else "MISS"
        if payload.get("status") != status or payload.get("actual") != actual:
            payload["status"] = status
            payload["actual"] = actual
            event.payload = payload
            dirty = True
        out.append({
            "event": event,
            "metric": metric,
            "target_year": year,
            "low": low,
            "high": high,
            "unit": payload.get("unit") or "",
            "statement": payload.get("statement") or "",
            "origin": payload.get("origin") or "MANUAL",
            "actual": actual,
            "status": status,
        })
    if dirty:
        db.session.commit()
    return out


def add_manual_promise(
    company_id: int,
    *,
    metric: str,
    target_year: int,
    low: float,
    high: float,
    unit: str,
    statement: str,
    source_id: int | None = None,
) -> Event:
    low, high = (high, low) if low > high else (low, high)
    fp = hashlib.sha256(f"manual|{company_id}|{metric}|{target_year}|{low}|{high}|{statement}".encode()).hexdigest()[:24]
    event = Event(
        company_id=company_id,
        source_id=source_id,
        event_type="MANAGEMENT_PROMISE",
        title=f"{metric} guidance for {target_year}",
        event_date=utcnow(),
        payload={
            "metric": metric,
            "target_year": int(target_year),
            "low": float(low),
            "high": float(high),
            "unit": unit,
            "statement": statement,
            "fingerprint": fp,
            "origin": "MANUAL",
            "status": "PENDING",
        },
    )
    db.session.add(event)
    db.session.commit()
    return event


__all__ = ["html_to_text", "extract_promises", "store_promises", "evaluate_promises", "add_manual_promise"]
