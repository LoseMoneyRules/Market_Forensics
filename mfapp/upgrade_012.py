from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .core_models import Coverage, Job, SchemaMigration, Security
from .extensions import db

MIGRATION_KEY = "release_0_1_2_queue_research_prefill"
ACTIVE_STATUSES = ("QUEUED", "RUNNING")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def queue_existing_coverage_prefill() -> dict[str, Any]:
    """One-time 0.1.2 upgrade hook for coverage that already exists in MariaDB.

    It never performs network I/O at WSGI startup and never edits research directly.
    It only adds one low-priority local RESEARCH_PREFILL job per existing coverage.
    The job itself respects manual overrides and only fills blank/previously-auto fields.
    """
    applied = SchemaMigration.query.filter_by(migration_key=MIGRATION_KEY).first()
    if applied is not None:
        return {"status": "already-applied", **dict(applied.details or {})}

    queued = 0
    reused = 0
    skipped = 0
    now = utcnow()
    for coverage in Coverage.query.order_by(Coverage.id.asc()).all():
        security = db.session.get(Security, coverage.security_id)
        if security is None:
            skipped += 1
            continue
        existing = Job.query.filter(
            Job.user_id == coverage.user_id,
            Job.job_type == "RESEARCH_PREFILL",
            Job.security_id == security.id,
            Job.status.in_(ACTIVE_STATUSES),
        ).order_by(Job.id.asc()).first()
        if existing is not None:
            reused += 1
            continue
        db.session.add(Job(
            job_type="RESEARCH_PREFILL",
            status="QUEUED",
            priority=80,
            user_id=coverage.user_id,
            company_id=security.company_id,
            security_id=security.id,
            payload={"coverage_id": coverage.id, "upgrade": "0.1.2"},
            run_after=now,
        ))
        queued += 1

    details = {"jobs_queued": queued, "active_jobs_reused": reused, "coverages_skipped": skipped}
    db.session.add(SchemaMigration(migration_key=MIGRATION_KEY, details=details))
    db.session.commit()
    return {"status": "applied", **details}


__all__ = ["MIGRATION_KEY", "queue_existing_coverage_prefill"]
