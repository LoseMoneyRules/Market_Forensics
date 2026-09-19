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


MANAGEMENT_SCAN_VERSION = "5"
ORIGINAL_ACTUAL_VERSION = "1"

GUIDANCE_WORDS = (
    r"(?:expect(?:s|ed|ing)?|guidance|outlook|forecast(?:s|ed|ing)?|"
    r"anticipat(?:e|es|ed|ing)|target(?:s|ed|ing)?|project(?:s|ed|ing)?|"
    r"estimat(?:e|es|ed|ing))"
)
INTERIM_WORDS = re.compile(
    r"\b(?:q[1-4]|quarter|quarterly|first\s+half|second\s+half|h[12]|"
    r"six\s+months|nine\s+months|ytd|year[- ]to[- ]date)\b",
    re.I,
)
FULL_YEAR_PATTERNS = (
    re.compile(r"\b(?:fy|fiscal(?:\s+year)?|full[-\s]?year)\s*(20[2-4]\d)\b", re.I),
    re.compile(r"\b(20[2-4]\d)\s*(?:fy|fiscal(?:\s+year)?|full[-\s]?year)\b", re.I),
)
NON_GAAP_WORDS = re.compile(
    r"\b(?:adjusted|non[-\s]?gaap|organic|constant[-\s]?currency|currency[-\s]?neutral|"
    r"comparable\s+sales|excluding|exclude(?:s|d)?|underlying)\b",
    re.I,
)
DECLINE_WORDS = re.compile(r"\b(?:declin(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)|down|contract(?:s|ed|ing)?)\b", re.I)
QUALITATIVE_WORDS = re.compile(
    r"\b(?:low|mid|high)[-\s]single[-\s]digit(?:s)?\b|"
    r"\b(?:single|double)[-\s]digit(?:s)?\b|"
    r"\b(?:roughly\s+flat|approximately\s+flat|about\s+flat|flat)\b|"
    r"\b(?:increase|growth|grow|decline|decrease|down|up)\b[^.;]{0,70}",
    re.I,
)

ORIGINAL_ACTUAL_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForAdditionsToPropertyPlantAndEquipment"),
    "eps": ("EarningsPerShareDiluted",),
}


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>", " ", raw or "")
    # Preserve filing structure before stripping tags. Earnings-release exhibits
    # frequently use HTML tables; flattening every tag to one space can turn the
    # entire document into one oversized unparseable "sentence".
    text = re.sub(r"(?is)<br\s*/?>|</(?:p|div|tr|li|h[1-6])\s*>", ". ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[\t\r\n ]+", " ", text)
    text = re.sub(r"(?:\s*\.\s*){2,}", ". ", text)
    return text.strip()


def _scale_money(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit.startswith("b"):
        return value * 1_000_000_000
    if unit.startswith("m"):
        return value * 1_000_000
    if unit.startswith("k"):
        return value * 1_000
    return value


def _sentences(text: str) -> list[str]:
    # SEC HTML often loses paragraph structure. Split on punctuation, bullets and
    # semicolons while keeping enough local context for guidance ranges.
    normalized = re.sub(r"[\u2022\u25aa\u25cf]", ". ", text or "")
    chunks = re.split(r"(?<=[.!?;])\s+|\s{2,}", normalized)
    return [s.strip(" -–—\t") for s in chunks if 20 <= len(s.strip()) <= 1200]


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

    Multiple years are never resolved by simply taking the first year. Any interim
    marker in the evidence wins over an FY token: conservative false negatives are
    preferable to comparing a quarter/half-year target with a full-year actual.
    """
    interim = bool(INTERIM_WORDS.search(sentence))
    for pattern in FULL_YEAR_PATTERNS:
        match = pattern.search(sentence)
        if match:
            year = int(match.group(1))
            if interim:
                return year, "INTERIM", f"INTERIM FY{year}", "INTERIM_GUIDANCE"
            return year, "FY", f"FY{year}", None

    years = [int(value) for value in re.findall(r"\b(20[2-4]\d)\b", sentence)]
    unique_years = list(dict.fromkeys(years))

    forward_year = None
    match = re.search(GUIDANCE_WORDS + r"[^.;]{0,120}?\b(20[2-4]\d)\b", sentence, re.I)
    if match:
        forward_year = int(match.group(1))
    if forward_year is None:
        match = re.search(r"\b(20[2-4]\d)\b[^.;]{0,80}?" + GUIDANCE_WORDS, sentence, re.I)
        if match:
            forward_year = int(match.group(1))

    if forward_year is not None:
        if interim:
            return forward_year, "INTERIM", f"INTERIM FY{forward_year}", "INTERIM_GUIDANCE"
        return forward_year, "UNRESOLVED", f"YEAR {forward_year}", "TARGET_PERIOD_NOT_EXPLICIT"

    if len(unique_years) == 1:
        year = unique_years[0]
        if interim:
            return year, "INTERIM", f"INTERIM FY{year}", "INTERIM_GUIDANCE"
        return year, "UNRESOLVED", f"YEAR {year}", "TARGET_PERIOD_NOT_EXPLICIT"
    if len(unique_years) > 1:
        return None, "UNRESOLVED", "", "AMBIGUOUS_TARGET_YEAR_VS_COMPARATOR"
    return None, "UNRESOLVED", "", "TARGET_YEAR_NOT_EXPLICIT"


def _basis_context(sentence: str, metric: str) -> tuple[str, str, str, str]:
    low = sentence.lower()
    if NON_GAAP_WORDS.search(sentence):
        return (
            "ADJUSTED_NON_GAAP",
            "Guidance contains adjusted/non-GAAP or management-defined modifiers.",
            "NON_COMPARABLE",
            "BASIS_NOT_CANONICAL",
        )
    if metric == "fcf":
        canonical = bool(re.search(
            r"(?:cash\s+(?:flow\s+)?from\s+operations|operating\s+cash\s+flow|cfo)"
            r"[^.;]{0,80}?(?:less|minus|-)[^.;]{0,50}?(?:capex|capital\s+expenditures?)",
            sentence,
            re.I,
        ))
        if canonical:
            return (
                "CFO_MINUS_CAPEX",
                "Guidance explicitly defines free cash flow as operating cash flow less capital expenditure.",
                "COMPARABLE",
                "",
            )
        return (
            "MANAGEMENT_DEFINED_FCF",
            "Management free-cash-flow definition is not proven identical to Market Forensics CFO - capex.",
            "NON_COMPARABLE",
            "MANAGEMENT_DEFINED_FCF",
        )
    if metric == "eps":
        if re.search(r"\bgaap\b", low):
            return "GAAP", "Explicit GAAP diluted EPS basis.", "COMPARABLE", ""
        return (
            "UNRESOLVED_EPS_BASIS",
            "EPS guidance is preserved, but GAAP diluted-EPS equivalence is not explicit in the extracted statement.",
            "NON_COMPARABLE",
            "EPS_BASIS_NOT_EXPLICIT",
        )
    if "gaap" in low:
        return "GAAP", "Explicit GAAP basis.", "COMPARABLE", ""
    return "REPORTED", "Reported metric; no adjusted/non-GAAP modifier detected.", "COMPARABLE", ""


def _metric_hint(sentence: str) -> str:
    low = sentence.lower()
    if re.search(r"\b(?:diluted\s+)?(?:earnings\s+per\s+share|eps)\b", low):
        return "eps"
    if re.search(r"\b(?:free\s+cash\s+flow|fcf)\b", low):
        return "fcf"
    if "operating margin" in low:
        return "operating_margin_pct"
    if "gross margin" in low:
        return "gross_margin_pct"
    if "net margin" in low:
        return "net_margin_pct"
    if re.search(r"\b(?:revenue|sales)\b", low):
        return "revenue_growth_pct" if re.search(r"\b(?:growth|increase|decline|decrease|down|up)\b", low) else "revenue"
    return "guidance"


def _qualitative_target(sentence: str) -> str:
    match = QUALITATIVE_WORDS.search(sentence)
    if match:
        return match.group(0).strip(" ,;:.")[:180]
    return sentence[:180].strip()


def _numeric_range(match: re.Match[str], unit: str, *, negative: bool = False) -> tuple[float, float]:
    groups = match.groups()
    if unit == "%":
        low = float(groups[0])
        high = float(groups[1]) if len(groups) > 1 and groups[1] else low
    elif unit == "USD/share":
        low = float(groups[0])
        high = float(groups[1]) if len(groups) > 1 and groups[1] else low
    else:
        low = _scale_money(float(groups[0]), str(groups[1]))
        if len(groups) > 3 and groups[2]:
            high = _scale_money(float(groups[2]), str(groups[3] or groups[1]))
        else:
            high = low
    if negative and low >= 0 and high >= 0:
        low, high = -high, -low
    if low > high:
        low, high = high, low
    return low, high


def extract_promises(
    text: str,
    *,
    source_id: int | None = None,
    document_url: str = "",
    document_name: str = "",
) -> list[dict[str, Any]]:
    """Extract numeric and qualitative management guidance conservatively.

    Numeric promises may become MET/MISS only when period, basis, unit and a
    point-in-time original actual are comparable. Qualitative guidance is useful
    evidence too, but always remains EVIDENCE_ONLY.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    specs = [
        (
            "revenue_growth_pct",
            r"(?:revenue|sales)[^.;]{0,120}?(?:growth|increase|up|decline|decrease|down)"
            r"[^.;]{0,90}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)"
            r"(?:\s*(?:to|-|–|and)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?",
            "%",
        ),
        (
            "operating_margin_pct",
            r"operating\s+margin[^.;]{0,120}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)"
            r"(?:\s*(?:to|-|–|and)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?",
            "%",
        ),
        (
            "gross_margin_pct",
            r"gross\s+margin[^.;]{0,120}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)"
            r"(?:\s*(?:to|-|–|and)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?",
            "%",
        ),
        (
            "net_margin_pct",
            r"net\s+margin[^.;]{0,120}?(-?\d+(?:\.\d+)?)\s*(?:%|percent)"
            r"(?:\s*(?:to|-|–|and)\s*(-?\d+(?:\.\d+)?)\s*(?:%|percent))?",
            "%",
        ),
        (
            "eps",
            r"(?:diluted\s+)?(?:earnings\s+per\s+share|eps)[^.;]{0,140}?"
            r"(?:(?:of|between|range(?:\s+of)?|to\s+be|approximately|about|:)\s*\$?\s*|\$\s*)"
            r"(-?\d+(?:\.\d+)?)"
            r"(?:\s*(?:to|-|–|and)\s*\$?\s*(-?\d+(?:\.\d+)?))?",
            "USD/share",
        ),
        (
            "revenue",
            r"(?:revenue|sales)[^.;]{0,140}?\$\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|mm|m|thousand|k)\b"
            r"(?:\s*(?:to|-|–|and)\s*\$?\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|mm|m|thousand|k)\b)?",
            "USD",
        ),
        (
            "fcf",
            r"(?:free\s+cash\s+flow|fcf)[^.;]{0,140}?\$\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|mm|m|thousand|k)\b"
            r"(?:\s*(?:to|-|–|and)\s*\$?\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|mm|m|thousand|k)\b)?",
            "USD",
        ),
    ]

    for sentence in _sentences(text):
        if not re.search(GUIDANCE_WORDS, sentence, re.I):
            continue

        # Normalize common "$50 to $51 billion" wording into a form where each
        # endpoint carries its unit, without changing the stored evidence text.
        parse_sentence = re.sub(
            r"(\$\s*\d+(?:\.\d+)?)\s*(to|-|–|and)\s*(\$?\s*\d+(?:\.\d+)?)\s*"
            r"(billion|million|bn|mm|m|thousand|k)\b",
            lambda m: f"{m.group(1)} {m.group(4)} {m.group(2)} {m.group(3)} {m.group(4)}",
            sentence,
            flags=re.I,
        )

        target_year, period_type, target_period, period_issue = _target_period(sentence)
        if not target_period:
            target_period = "UNRESOLVED"

        found_numeric = False
        for metric, pattern, unit in specs:
            match = re.search(pattern, parse_sentence, flags=re.I)
            if not match:
                continue
            found_numeric = True
            negative = metric == "revenue_growth_pct" and bool(DECLINE_WORDS.search(sentence))
            low, high = _numeric_range(match, unit, negative=negative)
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
                "target_text": "",
                "unit": unit,
                "operator": "RANGE" if low != high else "TARGET",
                "basis": basis,
                "definition": definition,
                "comparability": comparability,
                "comparability_reason": reason,
                "statement": sentence[:1000],
                "fingerprint": fingerprint,
                "source_id": source_id,
                "origin": "AUTO_FILING",
                "document_url": document_url,
                "document_name": document_name,
                "parser_version": MANAGEMENT_SCAN_VERSION,
            })

        if found_numeric:
            continue

        # Do not make up a number from "low-single-digit", "roughly flat", etc.
        # Preserve it as source-backed evidence so the table is informative.
        if not QUALITATIVE_WORDS.search(sentence):
            continue
        metric = _metric_hint(sentence)
        basis, definition, _, basis_reason = _basis_context(sentence, metric)
        reason = period_issue or basis_reason or "QUALITATIVE_GUIDANCE"
        fingerprint = hashlib.sha256(
            f"{source_id}|QUALITATIVE|{metric}|{target_year}|{period_type}|{sentence}".encode()
        ).hexdigest()[:24]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append({
            "metric": metric,
            "target_year": target_year,
            "target_period_type": period_type,
            "target_period": target_period,
            "low": None,
            "high": None,
            "target_text": _qualitative_target(sentence),
            "unit": "",
            "operator": "QUALITATIVE",
            "basis": basis,
            "definition": definition,
            "comparability": "NON_COMPARABLE",
            "comparability_reason": reason,
            "statement": sentence[:1000],
            "fingerprint": fingerprint,
            "source_id": source_id,
            "origin": "AUTO_FILING",
            "document_url": document_url,
            "document_name": document_name,
            "parser_version": MANAGEMENT_SCAN_VERSION,
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
    changed = 0
    source = db.session.get(Source, source_id) if source_id else None
    event_date = (
        (source.published_at if source and source.published_at else None)
        or (source.retrieved_at if source and source.retrieved_at else None)
        or utcnow()
    )
    source_fields = _source_fields(source)
    existing_events = Event.query.filter_by(
        company_id=company_id,
        event_type="MANAGEMENT_PROMISE",
    ).all()
    by_fingerprint = {
        str((event.payload or {}).get("fingerprint") or ""): event
        for event in existing_events
        if (event.payload or {}).get("fingerprint")
    }

    for row in promises:
        payload = dict(row)
        payload["source_id"] = source_id or row.get("source_id")
        payload.update(source_fields)
        payload["status"] = "EVIDENCE_ONLY" if payload.get("comparability") == "NON_COMPARABLE" else "PENDING"
        payload["parser_version"] = str(payload.get("parser_version") or MANAGEMENT_SCAN_VERSION)
        fp = str(payload.get("fingerprint") or "")
        event = by_fingerprint.get(fp)

        if event is None and str(payload.get("origin") or "").upper() == "AUTO_FILING":
            # Parser upgrades may intentionally change the fingerprint (for
            # example fixing the sign of "revenue decline 8-10%"). Reuse the
            # semantic event instead of showing old and corrected rows together.
            event = next(
                (
                    candidate for candidate in existing_events
                    if int(candidate.source_id or 0) == int(payload.get("source_id") or 0)
                    and str((candidate.payload or {}).get("origin") or "").upper() == "AUTO_FILING"
                    and str((candidate.payload or {}).get("metric") or "") == str(payload.get("metric") or "")
                    and str((candidate.payload or {}).get("statement") or "") == str(payload.get("statement") or "")
                ),
                None,
            )

        if event is not None:
            if dict(event.payload or {}) != payload:
                event.payload = payload
                event.title = f"{row.get('metric')} guidance for {row.get('target_period') or row.get('target_year') or 'UNRESOLVED'}"
                changed += 1
            if fp:
                by_fingerprint[fp] = event
            continue

        event = Event(
            company_id=company_id,
            source_id=source_id or row.get("source_id"),
            event_type="MANAGEMENT_PROMISE",
            title=f"{row.get('metric')} guidance for {row.get('target_period') or row.get('target_year') or 'UNRESOLVED'}",
            event_date=event_date,
            payload=payload,
        )
        db.session.add(event)
        existing_events.append(event)
        if fp:
            by_fingerprint[fp] = event
        changed += 1
    if changed:
        db.session.commit()
    return changed

def _duration_days(row: dict[str, Any]) -> int | None:
    try:
        return (date.fromisoformat(str(row.get("end") or "")[:10]) - date.fromisoformat(str(row.get("start") or "")[:10])).days
    except (TypeError, ValueError):
        return None


def _fiscal_year_from_end(row: dict[str, Any], fiscal_year_end: str = "") -> int | None:
    try:
        end = date.fromisoformat(str(row.get("end") or "")[:10])
    except (TypeError, ValueError):
        return None
    fye = str(fiscal_year_end or "").strip()
    if len(fye) == 4 and fye.isdigit():
        month, day = int(fye[:2]), int(fye[2:])
        if 1 <= month <= 12 and 1 <= day <= 31:
            return end.year + (1 if (end.month, end.day) > (month, day) else 0)
    return end.year


def _companyfact_rows(companyfacts: dict[str, Any], tags: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    facts = companyfacts.get("facts") or {}
    priority = {tag: index for index, tag in enumerate(tags)}
    for namespace, concepts in facts.items():
        for tag in tags:
            node = (concepts or {}).get(tag) or {}
            for unit, rows in (node.get("units") or {}).items():
                for row in rows or []:
                    item = dict(row)
                    item["_mf_tag"] = tag
                    item["_mf_namespace"] = namespace
                    item["_mf_unit"] = unit
                    item["_mf_priority"] = priority.get(tag, 999)
                    out.append(item)
    return out


def _original_annual_record(
    companyfacts: dict[str, Any],
    tags: tuple[str, ...],
    year: int,
    fiscal_year_end: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in _companyfact_rows(companyfacts, tags):
        if str(row.get("form") or "").upper() not in {"10-K", "10-K/A"}:
            continue
        if str(row.get("fp") or "").upper() != "FY":
            continue
        days = _duration_days(row)
        if days is None or not 300 <= days <= 430:
            continue
        if _fiscal_year_from_end(row, fiscal_year_end) != int(year):
            continue
        try:
            value = float(row.get("val"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        candidates.append(row)
    if not candidates:
        return None
    # Earliest public filing wins. Later comparative restatements must never
    # rewrite Management Delivery history.
    return sorted(
        candidates,
        key=lambda row: (
            str(row.get("filed") or "9999-12-31"),
            int(row.get("_mf_priority") or 999),
            str(row.get("accn") or ""),
        ),
    )[0]


def _actual_payload(
    metric: str,
    year: int,
    value: float,
    records: list[dict[str, Any]],
    *,
    definition: str,
) -> dict[str, Any]:
    filed = sorted(str(row.get("filed") or "") for row in records if row.get("filed"))
    ends = sorted(str(row.get("end") or "") for row in records if row.get("end"))
    accessions = list(dict.fromkeys(str(row.get("accn") or "") for row in records if row.get("accn")))
    tags = list(dict.fromkeys(str(row.get("_mf_tag") or "") for row in records if row.get("_mf_tag")))
    return {
        "metric": metric,
        "fiscal_year": int(year),
        "value": float(value),
        "period_type": "FY",
        "period_end": ends[-1][:10] if ends else None,
        "filed_at": filed[-1][:10] if filed else None,
        "source_accession": " + ".join(accessions),
        "source_tags": tags,
        "source_provider": "SEC_COMPANYFACTS",
        "definition": definition,
        "point_in_time_original": True,
        "actual_version": ORIGINAL_ACTUAL_VERSION,
    }


def original_actuals_from_companyfacts(
    companyfacts: dict[str, Any],
    fiscal_year_end: str = "",
) -> list[dict[str, Any]]:
    """Build earliest-public FY actuals used by Management Delivery.

    This is intentionally separate from the current normalized statement view:
    current Fundamentals may prefer a later corrected filing, while Management
    accountability must compare the promise with what was first reported.
    """
    years: set[int] = set()
    all_tags = tuple(dict.fromkeys(tag for tags in ORIGINAL_ACTUAL_TAGS.values() for tag in tags))
    for row in _companyfact_rows(companyfacts, all_tags):
        if str(row.get("form") or "").upper() not in {"10-K", "10-K/A"}:
            continue
        fy = _fiscal_year_from_end(row, fiscal_year_end)
        if fy is not None:
            years.add(fy)

    base: dict[int, dict[str, dict[str, Any]]] = {}
    for year in sorted(years):
        fields: dict[str, dict[str, Any]] = {}
        for field, tags in ORIGINAL_ACTUAL_TAGS.items():
            record = _original_annual_record(companyfacts, tags, year, fiscal_year_end)
            if record is None:
                continue
            try:
                value = float(record.get("val"))
            except (TypeError, ValueError):
                continue
            fields[field] = {"value": value, "record": record}
        if fields:
            base[year] = fields

    out: list[dict[str, Any]] = []
    for year, fields in sorted(base.items()):
        revenue = fields.get("revenue")
        if revenue:
            out.append(_actual_payload(
                "revenue", year, revenue["value"], [revenue["record"]],
                definition="SEC-reported annual revenue as first publicly filed.",
            ))
        eps = fields.get("eps")
        if eps:
            out.append(_actual_payload(
                "eps", year, eps["value"], [eps["record"]],
                definition="SEC-reported GAAP diluted EPS as first publicly filed.",
            ))

        if revenue and revenue["value"] not in (0, None):
            for metric, field in (
                ("gross_margin_pct", "gross_profit"),
                ("operating_margin_pct", "operating_income"),
                ("net_margin_pct", "net_income"),
            ):
                item = fields.get(field)
                if item:
                    out.append(_actual_payload(
                        metric,
                        year,
                        item["value"] / revenue["value"] * 100.0,
                        [item["record"], revenue["record"]],
                        definition=f"Derived from first-public SEC annual {field} / revenue.",
                    ))

        prior_revenue = (base.get(year - 1) or {}).get("revenue")
        if revenue and prior_revenue and prior_revenue["value"] not in (0, None):
            out.append(_actual_payload(
                "revenue_growth_pct",
                year,
                (revenue["value"] / prior_revenue["value"] - 1.0) * 100.0,
                [revenue["record"], prior_revenue["record"]],
                definition="Derived from first-public SEC annual revenue for consecutive fiscal years.",
            ))

        cfo, capex = fields.get("cfo"), fields.get("capex")
        if cfo and capex:
            out.append(_actual_payload(
                "fcf",
                year,
                cfo["value"] - capex["value"],
                [cfo["record"], capex["record"]],
                definition="Canonical Market Forensics FCF = first-public SEC CFO - CapEx.",
            ))
    return out


def store_original_actuals(company_id: int, rows: list[dict[str, Any]]) -> int:
    stored = 0
    existing_rows = Event.query.filter_by(
        company_id=company_id,
        event_type="MANAGEMENT_ACTUAL_ORIGINAL",
    ).all()
    existing = {
        (
            str((item.payload or {}).get("metric") or ""),
            int((item.payload or {}).get("fiscal_year") or 0),
        ): item
        for item in existing_rows
    }
    for row in rows:
        metric = str(row.get("metric") or "")
        year = int(row.get("fiscal_year") or 0)
        if not metric or not year:
            continue
        event = existing.get((metric, year))
        payload = dict(row)
        payload["actual_version"] = ORIGINAL_ACTUAL_VERSION
        payload["fingerprint"] = hashlib.sha256(
            f"{company_id}|{metric}|{year}|{payload.get('value')}|{payload.get('filed_at')}|{payload.get('source_accession')}".encode()
        ).hexdigest()[:24]
        if event is None:
            filed = _iso_day(payload.get("filed_at"))
            db.session.add(Event(
                company_id=company_id,
                event_type="MANAGEMENT_ACTUAL_ORIGINAL",
                title=f"{metric} original actual FY{year}",
                event_date=datetime.fromisoformat(filed) if filed else utcnow(),
                payload=payload,
            ))
            stored += 1
        elif dict(event.payload or {}) != payload:
            event.payload = payload
            stored += 1
    if stored:
        db.session.commit()
    return stored


def _original_actual_for_year(company_id: int, metric: str, year: int) -> dict[str, Any] | None:
    events = Event.query.filter_by(
        company_id=company_id,
        event_type="MANAGEMENT_ACTUAL_ORIGINAL",
    ).order_by(Event.event_date.asc(), Event.id.asc()).all()
    for event in events:
        payload = dict(event.payload or {})
        if str(payload.get("metric") or "") != metric:
            continue
        if int(payload.get("fiscal_year") or 0) != int(year):
            continue
        return {
            "value": payload.get("value"),
            "period_type": "FY",
            "fiscal_year": year,
            "period_end": payload.get("period_end"),
            "filed_at": payload.get("filed_at"),
            "is_restated": False,
            "source_id": event.source_id,
            "source_accession": payload.get("source_accession") or "",
            "source_title": "Original point-in-time SEC annual actual",
            "source_tags": payload.get("source_tags") or [],
            "definition": payload.get("definition") or "",
            "point_in_time_original": True,
        }
    return None


def _current_actual_for_year(company_id: int, metric: str, year: int) -> dict[str, Any] | None:
    rows = annual_rows(company_id, 20)
    row = next((r for r in rows if int(r.get("fiscal_year") or 0) == int(year)), None)
    if not row:
        return None
    if metric in {"revenue", "fcf"}:
        value = row.get(metric)
    elif metric == "eps":
        value = None
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
        "point_in_time_original": False,
    }


def _actual_for_year(company_id: int, metric: str, year: int) -> dict[str, Any] | None:
    return _original_actual_for_year(company_id, metric, year) or _current_actual_for_year(company_id, metric, year)

def _expected_unit(metric: str) -> str:
    if metric.endswith("_pct"):
        return "%"
    if metric == "eps":
        return "USD/SHARE"
    return "USD"


def _comparability(payload: dict[str, Any], actual: dict[str, Any] | None) -> tuple[str, str]:
    origin = str(payload.get("origin") or "").upper()
    period_type = str(payload.get("target_period_type") or ("FY" if origin == "MANUAL" else "UNRESOLVED")).upper()
    basis = str(payload.get("basis") or ("CONTROL_CONFIRMED" if origin == "MANUAL" else "UNRESOLVED")).upper()
    metric = str(payload.get("metric") or "")
    unit = str(payload.get("unit") or "").upper()

    if str(payload.get("operator") or "").upper() == "QUALITATIVE":
        return "NON_COMPARABLE", str(payload.get("comparability_reason") or "QUALITATIVE_GUIDANCE")
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

    source_date = _iso_day(payload.get("source_date"))
    period_end = _iso_day(actual.get("period_end"))
    if source_date and period_end and source_date > period_end:
        return "NON_COMPARABLE", "GUIDANCE_PUBLISHED_AFTER_TARGET_PERIOD"

    if not actual.get("point_in_time_original"):
        return "NON_COMPARABLE", "ORIGINAL_ACTUAL_NOT_VERIFIED"
    if actual.get("is_restated"):
        return "NON_COMPARABLE", "ACTUAL_IS_LATER_RESTATEMENT"

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
                unit = str(payload.get("unit") or "").upper()
                if unit == "%":
                    tolerance = max(abs(low_f) * .05, 0.5)
                elif unit == "USD/SHARE":
                    tolerance = max(abs(low_f) * .05, 0.01)
                else:
                    tolerance = max(abs(low_f) * .05, 1.0)
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
            "target_text": payload.get("target_text") or "",
            "unit": payload.get("unit") or "",
            "operator": payload.get("operator") or "",
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
            "document_name": payload.get("document_name") or "",
            "document_url": payload.get("document_url") or "",
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
            "target_text": "",
            "unit": unit,
            "operator": "RANGE" if float(low) != float(high) else "TARGET",
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


__all__ = [
    "MANAGEMENT_SCAN_VERSION", "ORIGINAL_ACTUAL_VERSION",
    "html_to_text", "extract_promises", "store_promises",
    "original_actuals_from_companyfacts", "store_original_actuals",
    "evaluate_promises", "add_manual_promise",
]
