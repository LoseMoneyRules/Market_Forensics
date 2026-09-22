from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .core_models import FinancialPeriod, NormalizedFinancial, Source
from .extensions import db
from .current_financials import canonical_annual_pairs, canonical_quarter_pairs


FINANCIAL_REVIEW_GATES = {
    "overview", "fundamentals", "expectations", "valuation", "bear-case",
    "catalysts", "financial-flows", "management", "monitoring",
}


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc_naive(value: Any) -> datetime | None:
    """Normalize legacy/driver datetime variants before comparing evidence times."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _empty_basis() -> dict[str, Any]:
    return {
        "available": False, "token": "", "period_id": None, "period_type": "",
        "fiscal_year": None, "period_end": None, "filed_at": None,
        "materialized_at": None, "accession_no": "", "source_id": None,
        "source_title": "", "source_form": "",
    }


def latest_financial_basis(company_id: int | None) -> dict[str, Any]:
    """Deterministic current Research basis from canonical stored financial periods.

    Legacy parser revisions may have left multiple DB identities for the same
    represented end date. They remain auditable, but only the canonical FY/Q
    candidates selected by current_financials may define Research readiness,
    valuation basis or evidence fingerprints. FY wins a same-date Q4 tie.
    """
    if not company_id:
        return _empty_basis()

    pairs = canonical_annual_pairs(int(company_id)) + canonical_quarter_pairs(int(company_id))
    if not pairs:
        return _empty_basis()

    pairs.sort(
        key=lambda pair: (
            pair[0].end_date,
            1 if str(pair[0].period_type or "") == "FY" else 0,
            pair[0].filed_at or datetime.min.date(),
            pair[0].id,
        ),
        reverse=True,
    )
    pairs = pairs[:12]
    period = pairs[0][0]
    source = db.session.get(Source, period.source_id) if period.source_id else None
    source_meta = dict((source.meta or {}) if source else {})
    fingerprint = []
    materialized_times = []

    for row, normalized in pairs:
        row_source = db.session.get(Source, row.source_id) if row.source_id else None
        created = _utc_naive(row.created_at)
        updated = _utc_naive(normalized.updated_at) if normalized is not None else None
        materialized = updated if updated is not None and (created is None or updated > created) else created
        if materialized is not None:
            materialized_times.append(materialized)
        fingerprint.append({
            "period_id": row.id,
            "period_type": str(row.period_type or ""),
            "fiscal_year": row.fiscal_year,
            "period_end": row.end_date.isoformat() if row.end_date else None,
            "filed_at": row.filed_at.isoformat() if row.filed_at else None,
            "accession_no": str(row.accession_no or (row_source.accession_no if row_source else "") or ""),
            "source_hash": str(row_source.content_hash or "") if row_source else "",
            "normalized_updated_at": updated.isoformat() if updated else None,
        })

    materialized_at = max(materialized_times) if materialized_times else None
    accession = str(period.accession_no or (source.accession_no if source else "") or "")
    return {
        "available": True,
        "token": _hash(fingerprint),
        "period_id": period.id,
        "period_type": str(period.period_type or ""),
        "fiscal_year": period.fiscal_year,
        "period_end": period.end_date.isoformat() if period.end_date else None,
        "filed_at": period.filed_at.isoformat() if period.filed_at else None,
        "materialized_at": materialized_at.isoformat() if materialized_at else None,
        "accession_no": accession,
        "source_id": source.id if source else None,
        "source_title": str(source.title or "") if source else "",
        "source_form": str(source_meta.get("form") or source_meta.get("document") or period.period_type or ""),
        "evidence_period_count": len(fingerprint),
    }


def gate_depends_on_financial_basis(gate_key: str) -> bool:
    return str(gate_key or "") in FINANCIAL_REVIEW_GATES


__all__ = ["FINANCIAL_REVIEW_GATES", "latest_financial_basis", "gate_depends_on_financial_basis"]
