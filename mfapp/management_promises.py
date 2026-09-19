from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime, timezone
from html import unescape
from typing import Any

from .core_models import Event, FinancialPeriod, Source
from .current_financials import annual_rows
from .extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


GUIDANCE_WORDS = r"(?:expect(?:s|ed)?|guidance|outlook|forecast(?:s|ed)?|anticipat(?:e|es|ed)|target(?:s|ed)?)"
INTERIM_WORDS = re.compile(
    r"\b(?:q[1-4]|quarter|quarterly|first\s+half|second\s+half|h[12]|six\s+months|nine\s+months|ytd|year[- ]to[- ]date)\b",
    re.I,
)
FULL_YEAR_PATTERNS = (
    re.compile(r"\b(?:fy|fiscal(?:\s+year)?|full[-\s]?year)\s*(20[2-4]\d)\b", re.I),
    re.compile(r"\b(20[2-4]\d)\s*(?:fy|fiscal(?:\s+year)?|full[-\s]?year)\b", re.I),
)
NON_GAAP_WORDS = re.compile(
    r"\b(?:adjusted|non[-\s]?gaap|organic|constant[-\s]?currency|currency[-\s]?neutral|comparable\s+sales|excluding)\b",
    re.I,
)


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


def _iso_day(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value)).date().isoformat()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except (TypeError, ValueError):
            return None


def _target_period(sentence: str) -> tuple[int | None, str, str, str | None]:
    """Return target year/type/label and any ambiguity reason.

    Multiple years are never resolved by simply taking the first year. An explicit
    FY/full-year marker or a year attached to the forward-looking clause wins.
    """
    for pattern in FULL_YEAR_PATTERNS:
        match = pattern.search(sentence)
        if match:
            year = int(match.group(1))
            return year, "FY", f"FY{year}", None

    years = [int(value) for value in re.findall(r"\b(20[2-4]\d)\b", sentence)]
    unique_years = list(dict.fromkeys(years))
    interim = bool(INTERIM_WORDS.search(sentence))

    forward_year = None
    match = re.search(GUIDANCE_WORDS + r"[^.;]{0,120}?\b(20[2-4]\d)\b", sentence, re.I)
    if match:
        forward_year = int(match.group(1))
    if forward_year is None:
        match = re.search(r"\b(20[2-4]\d)\b[^.;]{0,80}?" + GUIDANCE_WORDS, sentence, re.I)
        if match:
            forward_year = int(match.group(1))

    if forward_year is not None:
        return (
            forward_year,
            "INTERIM" if interim else "FY",
            f"INTERIM FY{forward_year}" if interim else f"FY{forward_year}",
            "INTERIM_GUIDANCE" if interim else None,
        )

    if len(unique_years) == 1:
        year = unique_years[0]
        return (
            year,
            "INTERIM" if interim else "FY",
            f"INTERIM FY{year}" if interim else f"FY{year}",
            "INTERIM_GUIDANCE" if interim else None,
        )
    if len(unique_years) > 1:
        return None, "UNRESOLVED", "", "AMBIGUOUS_TARGET_YEAR_VS_COMPARATOR"
    return None, "UNRESOLVED", "", "TARGET_YEAR_NOT_EXPLICIT"


def _basis_context(sentence: str, metric: str) -> tuple[str, str, str, str]:
    low = sentence.lower()
    if metric == "fcf":
        return (
            "MANAGEMENT_DEFINED_FCF",
            "Management free-cash-flow definition is not proven identical to Market Forensics CFO - capex.",
            "NON_COMPARABLE",
            "MANAGEMENT_DEFINED_FCF",
        )
    if NON_GAAP_WORDS.search(sentence):
        return (
            "ADJUSTED_NON_GAAP",
            "Guidance contains adjusted/non-GAAP or management-defined modifiers.",
            "NON_COMPARABLE",
            "BASIS_NOT_CANONICAL",
        )
    if "gaap" in low:
        return "GAAP", "Explicit GAAP basis.", "COMPARABLE", ""
    return "REPORTED", "Reported metric; no adjusted/non-GAAP modifier detected.", "COMPARABLE", ""


def extract_promises(text: str, *, source_id: int | None = None) -> list[dict[str, Any]]:
    """Extract explicit numeric guidance while preserving comparability evidence.

    The parser intentionally prefers EVIDENCE_ONLY over a false MET/MISS. A target
    is scored later only when period, basis, unit and actual are economically
    comparable.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sentence in _sentences(text):
        low_sentence = sentence.lower()
        if not re.search(GUIDANCE_WORDS, low_sentence):
            continue

        target_year, period_type, target_period, period_issue = _target_period(sentence)
        if target_year is None:
            continue

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

            basis, definition, comparability, reason = _basis_context(sentence, metric)
            if period_type != "FY":
                comparability = "NON_COMPARABLE"
                reason = period_issue or "TARGET_PERIOD_NOT_FULL_YEAR"

            fingerprint = hashlib.sha256(
                f"{source_id}|{metric}|{target_year}|{period_type}|{basis}|{low}|{high}|{sentence}".encode()
            ).hexdigest()[:24]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append({
                "metric": metric,
                "target_year": target_year,
                "target_period_type": period_type,
                "target_period": target_period,
                "low": low,
                "high": high,
                "unit": unit,
                "operator": "RANGE" if low != high else "TARGET",
                "basis": basis,
                "definition": definition,
                "comparability": comparability,
                "comparability_reason": reason,
                "statement": sentence[:800],
                "fingerprint": fingerprint,
                "source_id": source_id,
                "origin": "AUTO_FILING",
            })
    return out


def _source_fields(source: Source | None) -> dict[str, Any]:
    if source is None:
        return {
            "source_provider": "",
            "source_title": "",
            "source_accession": "",
            "source_form": "",
            "source_date": None,
        }
    meta = dict(source.meta or {})
    return {
        "source_provider": source.provider or "",
        "source_title": source.title or "",
        "source_accession": source.accession_no or "",
        "source_form": str(meta.get("form") or ""),
        "source_date": _iso_day(source.published_at or source.retrieved_at),
    }


def store_promises(company_id: int, promises: list[dict[str, Any]], *, source_id: int | None = None) -> int:
    stored = 0
    source = db.session.get(Source, source_id) if source_id else None
    event_date = (source.published_at if source and source.published_at else None) or (source.retrieved_at if source and source.retrieved_at else None) or utcnow()
    source_fields = _source_fields(source)
    existing_fingerprints = {
        str((event.payload or {}).get("fingerprint") or "")
        for event in Event.query.filter_by(company_id=company_id, event_type="MANAGEMENT_PROMISE").all()
    }
    for row in promises:
        fp = str(row.get("fingerprint") or "")
        if fp in existing_fingerprints:
            continue
        payload = dict(row)
        payload["source_id"] = source_id or row.get("source_id")
        payload.update(source_fields)
        payload["status"] = "EVIDENCE_ONLY" if payload.get("comparability") == "NON_COMPARABLE" else "PENDING"
        db.session.add(Event(
            company_id=company_id,
            source_id=source_id or row.get("source_id"),
            event_type="MANAGEMENT_PROMISE",
            title=f"{row.get('metric')} guidance for {row.get('target_period') or row.get('target_year')}",
            event_date=event_date,
            payload=payload,
        ))
        existing_fingerprints.add(fp)
        stored += 1
    if stored:
        db.session.commit()
    return stored


def _actual_for_year(company_id: int, metric: str, year: int) -> dict[str, Any] | None:
    rows = annual_rows(company_id, 20)
    row = next((r for r in rows if int(r.get("fiscal_year") or 0) == int(year)), None)
    if not row:
        return None
    if metric in {"revenue", "fcf"}:
        value = row.get(metric)
    else:
        value = (row.get("metrics") or {}).get(metric)
    try:
        actual = float(value) if value is not None else None
    except (TypeError, ValueError):
        actual = None

    period = db.session.get(FinancialPeriod, row.get("period_id")) if row.get("period_id") else None
    actual_source = db.session.get(Source, period.source_id) if period and period.source_id else None
    return {
        "value": actual,
        "period_type": str(row.get("period_type") or (period.period_type if period else "FY")).upper(),
        "fiscal_year": int(row.get("fiscal_year") or year),
        "period_end": row.get("period_end") or (period.end_date.isoformat() if period and period.end_date else None),
        "filed_at": row.get("filed_at") or (period.filed_at.isoformat() if period and period.filed_at else None),
        "is_restated": bool(period.is_restated) if period else bool((row.get("quality") or {}).get("restated")),
        "source_id": period.source_id if period else None,
        "source_accession": period.accession_no if period else "",
        "source_title": actual_source.title if actual_source else "",
    }


def _expected_unit(metric: str) -> str:
    return "%" if metric.endswith("_pct") else "USD"


def _comparability(payload: dict[str, Any], actual: dict[str, Any] | None) -> tuple[str, str]:
    origin = str(payload.get("origin") or "").upper()
    period_type = str(payload.get("target_period_type") or ("FY" if origin == "MANUAL" else "UNRESOLVED")).upper()
    basis = str(payload.get("basis") or ("CONTROL_CONFIRMED" if origin == "MANUAL" else "UNRESOLVED")).upper()
    metric = str(payload.get("metric") or "")
    unit = str(payload.get("unit") or "").upper()

    if period_type != "FY":
        return "NON_COMPARABLE", "TARGET_PERIOD_NOT_FULL_YEAR"
    if unit != _expected_unit(metric).upper():
        return "NON_COMPARABLE", "UNIT_INCOMPATIBLE"
    if metric == "fcf" and origin != "MANUAL" and basis != "CFO_MINUS_CAPEX":
        return "NON_COMPARABLE", "MANAGEMENT_DEFINED_FCF"
    if basis not in {"REPORTED", "GAAP", "CONTROL_CONFIRMED", "CFO_MINUS_CAPEX"}:
        return "NON_COMPARABLE", "BASIS_NOT_CANONICAL"

    if actual is None or actual.get("value") is None:
        return "COMPARABLE", ""
    if str(actual.get("period_type") or "").upper() != "FY":
        return "NON_COMPARABLE", "ACTUAL_PERIOD_NOT_FULL_YEAR"
    if actual.get("is_restated"):
        return "NON_COMPARABLE", "ACTUAL_IS_LATER_RESTATEMENT"

    source_date = _iso_day(payload.get("source_date"))
    period_end = _iso_day(actual.get("period_end"))
    if source_date and period_end and source_date > period_end:
        return "NON_COMPARABLE", "GUIDANCE_PUBLISHED_AFTER_TARGET_PERIOD"

    low = payload.get("low")
    high = payload.get("high")
    try:
        low_f, high_f = float(low), float(high)
    except (TypeError, ValueError):
        return "NON_COMPARABLE", "TARGET_RANGE_INVALID"
    if not (math.isfinite(low_f) and math.isfinite(high_f)) or low_f > high_f:
        return "NON_COMPARABLE", "TARGET_RANGE_INVALID"
    return "COMPARABLE", ""


def evaluate_promises(company_id: int) -> list[dict[str, Any]]:
    events = Event.query.filter_by(company_id=company_id, event_type="MANAGEMENT_PROMISE").order_by(Event.event_date.desc(), Event.id.desc()).all()
    out = []
    dirty = False
    for event in events:
        payload = dict(event.payload or {})
        metric = str(payload.get("metric") or "")
        try:
            year = int(payload.get("target_year") or 0)
        except (TypeError, ValueError):
            year = 0
        actual_meta = _actual_for_year(company_id, metric, year) if metric and year else None
        actual = actual_meta.get("value") if actual_meta else None
        low = payload.get("low")
        high = payload.get("high")
        comparability, reason = _comparability(payload, actual_meta)

        status = "EVIDENCE_ONLY" if comparability != "COMPARABLE" else "PENDING"
        if status == "PENDING" and actual is not None and low is not None and high is not None:
            low_f, high_f = float(low), float(high)
            if low_f == high_f:
                tolerance = max(abs(low_f) * .05, 0.5 if str(payload.get("unit")) == "%" else 1.0)
                status = "MET" if abs(actual - low_f) <= tolerance else "MISS"
            else:
                status = "MET" if low_f <= actual <= high_f else "MISS"

        updates = {
            "status": status,
            "actual": actual,
            "comparability": comparability,
            "comparability_reason": reason,
            "actual_provenance": actual_meta or {},
        }
        if any(payload.get(key) != value for key, value in updates.items()):
            payload.update(updates)
            event.payload = payload
            dirty = True

        out.append({
            "event": event,
            "metric": metric,
            "target_year": year,
            "target_period": payload.get("target_period") or (f"FY{year}" if year else ""),
            "target_period_type": payload.get("target_period_type") or ("FY" if str(payload.get("origin") or "").upper() == "MANUAL" else "UNRESOLVED"),
            "low": low,
            "high": high,
            "unit": payload.get("unit") or "",
            "basis": payload.get("basis") or ("CONTROL_CONFIRMED" if str(payload.get("origin") or "").upper() == "MANUAL" else "UNRESOLVED"),
            "definition": payload.get("definition") or "",
            "comparability": comparability,
            "comparability_reason": reason,
            "statement": payload.get("statement") or "",
            "origin": payload.get("origin") or "MANUAL",
            "source_id": payload.get("source_id") or event.source_id,
            "source_provider": payload.get("source_provider") or "",
            "source_title": payload.get("source_title") or "",
            "source_accession": payload.get("source_accession") or "",
            "source_form": payload.get("source_form") or "",
            "source_date": payload.get("source_date"),
            "actual": actual,
            "actual_provenance": actual_meta or {},
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
    source = db.session.get(Source, source_id) if source_id else None
    source_fields = _source_fields(source)
    if source is None:
        source_fields.update({
            "source_provider": "CONTROL",
            "source_title": "Manual management promise entry",
            "source_accession": "",
            "source_form": "MANUAL",
            "source_date": None,
        })
    event = Event(
        company_id=company_id,
        source_id=source_id,
        event_type="MANAGEMENT_PROMISE",
        title=f"{metric} guidance for FY{target_year}",
        event_date=utcnow(),
        payload={
            "metric": metric,
            "target_year": int(target_year),
            "target_period": f"FY{int(target_year)}",
            "target_period_type": "FY",
            "low": float(low),
            "high": float(high),
            "unit": unit,
            "basis": "CONTROL_CONFIRMED",
            "definition": "CONTROL-confirmed canonical Market Forensics metric.",
            "comparability": "COMPARABLE",
            "comparability_reason": "",
            "statement": statement,
            "fingerprint": fp,
            "origin": "MANUAL",
            "source_id": source_id,
            **source_fields,
            "status": "PENDING",
        },
    )
    db.session.add(event)
    db.session.commit()
    return event


__all__ = ["html_to_text", "extract_promises", "store_promises", "evaluate_promises", "add_manual_promise"]
