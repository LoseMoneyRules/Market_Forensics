from __future__ import annotations

from datetime import date
from decimal import Decimal

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, FinancialPeriod, Job, NormalizedFinancial, RefreshRun
from mfapp.extensions import db
from mfapp.jobs import dismiss_terminal_jobs, enqueue_job, run_jobs
from mfapp.models import User
from mfapp.secdata import (
    DURATION_TAGS,
    _annual_duration,
    _fiscal_quarter_from_end,
    _quarter_duration_values,
)


def make_app(tmp_path, monkeypatch, name="033"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "033-core-data-recovery",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def _fact(*, start: str, end: str, value: int, form: str, fp: str, filed: str):
    return {
        "start": start, "end": end, "val": value, "form": form, "fp": fp,
        "filed": filed, "accn": "TEST-" + filed, "fy": int(end[:4]),
    }


def test_033_non_calendar_quarters_come_from_fact_period_not_filing_fp():
    # Intuit-style July year-end. Deliberately wrong fp markers model comparative
    # Companyfacts repeated in later filings; the represented dates are canonical.
    rows = [
        _fact(start="2025-08-01", end="2025-10-31", value=100, form="10-Q", fp="Q3", filed="2025-12-01"),
        _fact(start="2025-11-01", end="2026-01-31", value=110, form="10-Q", fp="Q1", filed="2026-03-01"),
        _fact(start="2026-02-01", end="2026-04-30", value=120, form="10-Q", fp="Q2", filed="2026-06-01"),
        _fact(start="2025-08-01", end="2026-07-31", value=460, form="10-K", fp="FY", filed="2026-09-01"),
    ]
    facts = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rows}}}}}
    annual = _annual_duration(facts, DURATION_TAGS["revenue"], "0731")
    quarters = _quarter_duration_values(facts, DURATION_TAGS["revenue"], annual, fiscal_year_end="0731")

    assert _fiscal_quarter_from_end(rows[0], "0731") == "Q1"
    assert _fiscal_quarter_from_end(rows[1], "0731") == "Q2"
    assert _fiscal_quarter_from_end(rows[2], "0731") == "Q3"
    assert [quarters[(2026, q)]["value"] for q in ("Q1", "Q2", "Q3", "Q4")] == [100, 110, 120, 130]


def test_033_march_year_end_quarters_are_consecutive_for_lpg_shape():
    samples = [
        ({"end": "2025-06-30", "fp": "Q2"}, "Q1"),
        ({"end": "2025-09-30", "fp": "Q3"}, "Q2"),
        ({"end": "2025-12-31", "fp": "Q1"}, "Q3"),
        ({"end": "2026-03-31", "fp": "FY"}, "Q4"),
    ]
    assert [_fiscal_quarter_from_end(row, "0331") for row, _ in samples] == [expected for _, expected in samples]



def test_033_empty_quarter_shells_do_not_hide_valid_annual_current_basis(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "ttm_fallback")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Fiscal Co", display_name="Fiscal Co")
        db.session.add(company); db.session.flush()

        annual = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2025,
            start_date=date(2024, 8, 1), end_date=date(2025, 7, 31), currency="USD",
        )
        db.session.add(annual); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=annual.id,
            revenue=Decimal("500"), operating_income=Decimal("80"),
            net_income=Decimal("60"), cfo=Decimal("75"), capex=Decimal("20"),
            fcf=Decimal("55"), source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))

        for period_type, fiscal_year, end_date in (
            ("Q4", 2025, date(2025, 7, 31)),
            ("Q1", 2026, date(2025, 10, 31)),
            ("Q2", 2026, date(2026, 1, 31)),
            ("Q3", 2026, date(2026, 4, 30)),
        ):
            period = FinancialPeriod(
                company_id=company.id, period_type=period_type, fiscal_year=fiscal_year,
                end_date=end_date, currency="USD",
            )
            db.session.add(period); db.session.flush()
            db.session.add(NormalizedFinancial(
                financial_period_id=period.id, source_map={}, quality={},
            ))
        db.session.commit()

        current = current_row(company.id)
        assert current is not None
        assert current["period_type"] == "FY"
        assert current["comparison_basis"] == "FY_FALLBACK"
        assert current["revenue"] == 500.0

def test_033_failed_job_rolls_back_partial_business_writes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "atomic")
    with app.app_context():
        db.create_all()
        user = User(
            email="control033@example.com", display_name="Control", role="CONTROL",
            password_hash="unused-test-hash", totp_secret_enc="unused-test-totp", is_active=True,
        )
        db.session.add(user); db.session.commit()
        job = enqueue_job("RECALCULATE", user_id=user.id, company_id=999, payload={}, priority=1)
        job.max_attempts = 1
        db.session.commit()

        def broken_execute(_job):
            db.session.add(Company(legal_name="PARTIAL WRITE", display_name="PARTIAL WRITE"))
            db.session.flush()
            raise RuntimeError("simulated core failure")

        monkeypatch.setattr("mfapp.jobs._execute_with_deadline", broken_execute)
        result = run_jobs(limit=1, user_id=user.id)

        assert result[0]["status"] == "FAILED"
        assert Company.query.filter_by(legal_name="PARTIAL WRITE").first() is None
        assert db.session.get(Job, job.id).status == "FAILED"
        assert RefreshRun.query.filter_by(job_id=job.id, status="FAILED").count() == 1


def test_033_terminal_job_cleanup_preserves_row_and_marks_dismissed(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "dismiss")
    with app.app_context():
        db.create_all()
        user = User(
            email="control033b@example.com", display_name="Control", role="CONTROL",
            password_hash="unused-test-hash", totp_secret_enc="unused-test-totp", is_active=True,
        )
        db.session.add(user); db.session.flush()
        failed = Job(job_type="SEC_INGEST", status="FAILED", user_id=user.id, payload={}, result={}, error_message="provider failure")
        cancelled = Job(job_type="RECALCULATE", status="CANCELLED", user_id=user.id, payload={}, result={}, error_message="cancelled")
        done = Job(job_type="MARKET_REFRESH", status="DONE", user_id=user.id, payload={}, result={})
        db.session.add_all([failed, cancelled, done]); db.session.commit()
        ids = (failed.id, cancelled.id, done.id)

        assert dismiss_terminal_jobs(user.id) == 2
        assert db.session.get(Job, ids[0]).status == "DISMISSED"
        assert db.session.get(Job, ids[1]).status == "DISMISSED"
        assert db.session.get(Job, ids[2]).status == "DONE"
        assert (db.session.get(Job, ids[0]).result or {}).get("dismissed", {}).get("previous_status") == "FAILED"


def test_033_settings_consolidates_duplicate_job_views():
    from pathlib import Path

    template = Path("mfapp/templates/settings.html").read_text()
    assert "DATA OPERATIONS" in template
    assert "Background jobs & execution history" in template
    assert "Clear failed/cancelled" in template
    assert "data-coverage-panel" in template
    assert '<p class="eyebrow">BACKGROUND WORK</p>' not in template
    assert '<p class="eyebrow">DATA INGESTION</p>' not in template
