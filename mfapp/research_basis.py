from __future__ import annotations

import hashlib
import json
from typing import Any

from .core_models import FinancialPeriod, NormalizedFinancial, Source
from .extensions import db


FINANCIAL_REVIEW_GATES = {
    "overview",
    "fundamentals",
    "expectations",
    "valuation",
    "bear-case",
    "catalysts",
    "financial-flows",
    "management",
    "monitoring",
}


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def latest_financial_basis(company_id: int | None) -> dict[str, Any]:
    """Return the newest material filed financial basis already normalized in the DB.

    This function performs no provider work.  A basis exists only after a material
    FY/Q filing has a normalized financial row, so incomplete/raw SEC ingest does
    not invalidate Research prematurely.
    """
    if not company_id:
        return {
            "available": False,
            "token": "",
            "period_type": "",
            "fiscal_year": None,
            "period_end": None,
            "filed_at": None,
            "accession_no": "",
            "source_id": None,
            "source_title": "",
            "source_form": "",
        }

    rows = (
        FinancialPeriod.query
        .join(NormalizedFinancial, NormalizedFinancial.financial_period_id == FinancialPeriod.id)
        .filter(
            FinancialPeriod.company_id == int(company_id),
            FinancialPeriod.period_type.in_(["FY", "Q1", "Q2", "Q3", "Q4"]),
        )
        .order_by(
            FinancialPeriod.filed_at.desc(),
            FinancialPeriod.end_date.desc(),
            FinancialPeriod.id.desc(),
        )
        .limit(8)
        .all()
    )
    period = next((row for row in rows if row.filed_at or row.end_date), None)
    if period is None:
        return {
            "available": False,
            "token": "",
            "period_type": "",
            "fiscal_year": None,
            "period_end": None,
            "filed_at": None,
            "accession_no": "",
            "source_id": None,
            "source_title": "",
            "source_form": "",
        }

    source = db.session.get(Source, period.source_id) if period.source_id else None
    source_meta = dict((source.meta or {}) if source else {})
    normalized = getattr(period, "normalized", None)
    materialized_at = period.created_at
    if normalized is not None and normalized.updated_at is not None and (materialized_at is None or normalized.updated_at > materialized_at):
        materialized_at = normalized.updated_at
    material = {
        "period_id": period.id,
        "period_type": str(period.period_type or ""),
        "fiscal_year": period.fiscal_year,
        "period_end": period.end_date.isoformat() if period.end_date else None,
        "filed_at": period.filed_at.isoformat() if period.filed_at else None,
        "accession_no": str(period.accession_no or (source.accession_no if source else "") or ""),
        "source_id": source.id if source else None,
        "source_hash": str(source.content_hash or "") if source else "",
    }
    token = _hash(material)
    return {
        "available": True,
        "token": token,
        "period_id": period.id,
        "period_type": material["period_type"],
        "fiscal_year": period.fiscal_year,
        "period_end": material["period_end"],
        "filed_at": material["filed_at"],
        "accession_no": material["accession_no"],
        "source_id": material["source_id"],
        "source_title": str(source.title or "") if source else "",
        "source_form": str(source_meta.get("form") or source_meta.get("document") or period.period_type or ""),
    }


def gate_depends_on_financial_basis(gate_key: str) -> bool:
    return str(gate_key or "") in FINANCIAL_REVIEW_GATES


def financial_basis_evidence(company_id: int | None, gate_key: str) -> dict[str, Any]:
    """Small deterministic evidence object included only in materially dependent gates."""
    if not gate_depends_on_financial_basis(gate_key):
        return {}
    basis = latest_financial_basis(company_id)
    return {
        "financial_basis_token": basis.get("token") or "",
        "period_type": basis.get("period_type") or "",
        "fiscal_year": basis.get("fiscal_year"),
        "period_end": basis.get("period_end"),
        "filed_at": basis.get("filed_at"),
        "accession_no": basis.get("accession_no") or "",
    }


__all__ = [
    "FINANCIAL_REVIEW_GATES",
    "latest_financial_basis",
    "gate_depends_on_financial_basis",
    "financial_basis_evidence",
]
