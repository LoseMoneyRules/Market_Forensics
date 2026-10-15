from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, RefreshRun, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.refresh_engine import STAGES, run_research_refresh, schedule_research_refresh


def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "040-refresh-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '040.db'}",
        "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": False,
    })
    with app.app_context():
        db.create_all()
        user = User(email="control040@example.com", display_name="Control",
                    role="CONTROL", password_hash="unused", totp_secret_enc="unused", is_active=True)
        company = Company(legal_name="Refresh Test Co", display_name="Refresh Test Co")
        db.session.add_all([user, company])
        db.session.flush()
        sec = Security(company_id=company.id, ticker="RFH", exchange="NASDAQ",
                       validation_source="TEST", active=True)
        db.session.add(sec)
        db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=sec.id, status="RESEARCH")
        db.session.add(coverage)
        from mfapp.services import ensure_workspace
        db.session.flush()
        ensure_workspace(coverage, user.id)
        db.session.commit()
        return app, user.id, sec.id, coverage.id


def test_040_global_refresh_creates_one_staged_job_per_security(tmp_path, monkeypatch):
    app, uid, sec_id, coverage_id = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("mfapp.refresh_engine.provider_status", lambda _uid: {"sec": True})
    with app.app_context():
        first = schedule_research_refresh(uid, "all")
        second = schedule_research_refresh(uid, "all")
        jobs = Job.query.filter_by(user_id=uid, job_type="RESEARCH_REFRESH").all()
        assert len(jobs) == 1
        assert jobs[0].security_id == sec_id
        assert jobs[0].payload["coverage_id"] == coverage_id
        assert first["jobs_queued"] == 2  # one security and one portfolio materialization
        assert second["jobs_reused"] == 1
        assert not Job.query.filter_by(user_id=uid, job_type="SEC_INGEST").count()
        assert not Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count()


def test_040_stage_failure_is_visible_and_recalculate_happens_once(tmp_path, monkeypatch):
    app, uid, sec_id, coverage_id = _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("mfapp.refresh_engine.provider_status", lambda _uid: {"sec": True})
    monkeypatch.setattr("mfapp.refresh_engine._due", lambda *args: True)
    called = []

    def simulated(kind, security, user_id, coverage_id):
        called.append(kind)
        if kind == "FINRA_IMPORT":
            raise RuntimeError("FINRA temporarily unavailable")
        return {"source": kind}

    monkeypatch.setattr("mfapp.refresh_engine._execute_stage", simulated)
    with app.app_context():
        schedule_research_refresh(uid, "all")
        job = Job.query.filter_by(user_id=uid, job_type="RESEARCH_REFRESH").first()
        result = run_research_refresh(job)
        assert called == list(STAGES)
        assert called.count("RECALCULATE") == 1
        assert result["state"] == "PARTIAL"
        assert result["stages"]["FINRA_IMPORT"] == "FAILED"
        assert result["stages"]["RECALCULATE"] == "DONE"
        assert len(result["failures"]) == 1
        logs = RefreshRun.query.filter_by(job_id=job.id).all()
        assert len(logs) == len(STAGES)
        assert len([x for x in logs if x.status == "FAILED"]) == 1


def test_040_manual_refresh_dispatches_without_browser_polling(tmp_path, monkeypatch):
    app, uid, sec_id, coverage_id = _workspace(tmp_path, monkeypatch)
    import mfapp.routes_publish as publish
    dispatched = []
    monkeypatch.setattr(publish, "_spawn_job_runner", lambda user_id: dispatched.append(user_id) or 400)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["view_as"] = "CONTROL"
    response = client.post("/company/RFH/refresh/all")
    assert response.status_code == 302
    assert dispatched == [uid]
    with app.app_context():
        jobs = Job.query.filter_by(user_id=uid, job_type="RESEARCH_REFRESH").all()
        assert len(jobs) == 1
        assert jobs[0].payload["mode"] == "all"
    page = client.get("/company/RFH/overview")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Update RFH" in html
    assert "Refresh FINRA" not in client.get("/company/RFH/tape").get_data(as_text=True)


def test_040_research_buttons_have_only_two_entry_points():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "mfapp" / "templates"
    dashboard = (root / "dashboard.html").read_text()
    header = (root / "_company_header.html").read_text()
    company = (root / "company_section.html").read_text()
    settings = (root / "settings.html").read_text()
    assert dashboard.count("Update all securities") == 1
    assert header.count("Update {{ security.ticker }}") == 1
    assert "Refresh SEC + TTM" not in company
    assert "Refresh FINRA" not in company
    assert "Queue full refresh" not in settings


def test_040_sec_promotes_rich_superseded_period_without_unique_collision(tmp_path, monkeypatch):
    """MySQL's immediate uq_mf_financial_period constraint must never reject a promotion."""
    from datetime import date
    from decimal import Decimal
    from mfapp.core_models import FinancialPeriod, NormalizedFinancial, Source
    from mfapp.secdata import _upsert_period

    app, uid, security_id, coverage_id = _workspace(tmp_path, monkeypatch)
    with app.app_context():
        security = db.session.get(Security, security_id)
        company = db.session.get(Company, security.company_id)
        source = Source(company_id=company.id, provider="SEC", source_type="COMPANYFACTS",
                        title="SEC facts", url="https://www.sec.gov/test")
        current = FinancialPeriod(company_id=company.id, period_type="FY", fiscal_year=2025,
                                  end_date=date(2025, 12, 31), currency="USD")
        legacy_rich = FinancialPeriod(company_id=company.id, period_type="SUPERSEDED_FY", fiscal_year=2025,
                                      end_date=date(2025, 12, 31), currency="USD")
        db.session.add_all([source, current, legacy_rich])
        db.session.flush()
        current_id, rich_id = current.id, legacy_rich.id
        db.session.add(NormalizedFinancial(
            financial_period_id=rich_id, revenue=Decimal("100"), cfo=Decimal("20"),
            source_map={"revenue": {"provider": "SEC"}, "cfo": {"provider": "SEC"}},
        ))
        db.session.commit()

        selected = _upsert_period(
            company, source, period_type="FY", fiscal_year=2025,
            end_date=date(2025, 12, 31), anchor={"filed": "2026-03-20"},
        )
        db.session.commit()
        assert selected.id == rich_id
        assert selected.period_type == "FY"
        old = db.session.get(FinancialPeriod, current_id)
        assert old.period_type.startswith(("SUPERSEDED_", "SUP_"))
        assert FinancialPeriod.query.filter_by(
            company_id=company.id, period_type="FY",
            fiscal_year=2025, end_date=date(2025, 12, 31),
        ).count() == 1


def test_040_recent_quote_consensus_discards_stale_outlier(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from mfapp.data_providers import QuoteResult, fetch_quote
    import mfapp.data_providers as quotes

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    candidates = [
        QuoteResult(True, "Alpaca", 100, "USD", now, "OBSERVED"),
        QuoteResult(True, "Tiingo", 100.7, "USD", now - timedelta(seconds=40), "OBSERVED"),
        QuoteResult(True, "Alpha Vantage", 91, "USD", now - timedelta(days=3), "DELAYED_OBSERVED"),
        QuoteResult(True, "Public market chart", 100.5, "USD", now - timedelta(minutes=2), "PUBLIC_FALLBACK"),
    ]
    monkeypatch.setattr(quotes, "quote_candidates", lambda *_: candidates)
    result = fetch_quote("RFH", 1)
    assert result.ok
    assert result.quality == "CONSENSUS_OBSERVED"
    assert 100 <= result.price <= 100.7
    assert "Alpha Vantage" not in result.payload["selected_providers"]


def test_040_disagreement_no_longer_freezes_a_months_old_quote(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from mfapp.data_providers import QuoteResult, fetch_quote
    import mfapp.data_providers as quotes

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(quotes, "quote_candidates", lambda *_: [
        QuoteResult(True, "Alpaca", 100.0, "USD", now, "OBSERVED"),
        QuoteResult(True, "Tiingo", 104.5, "USD", now - timedelta(seconds=10), "OBSERVED"),
        QuoteResult(False, "Alpha Vantage", message="rate limited"),
        QuoteResult(True, "Public market chart", 100.3, "USD", now - timedelta(seconds=30), "PUBLIC_FALLBACK"),
    ])
    resolved = fetch_quote("RFH", 1)
    assert resolved.ok
    assert resolved.quality in {"CONSENSUS_OBSERVED", "CROSS_CHECKED_OBSERVED"}
    assert 100.0 <= resolved.price <= 100.3

    # With three mutually incompatible current providers, publish the latest
    # sourced reference as DISPUTED, not as a silently 'verified' price.
    monkeypatch.setattr(quotes, "quote_candidates", lambda *_: [
        QuoteResult(True, "Alpaca", 100, "USD", now, "OBSERVED"),
        QuoteResult(True, "Tiingo", 112, "USD", now - timedelta(minutes=1), "OBSERVED"),
        QuoteResult(False, "Alpha Vantage", message="rate limited"),
        QuoteResult(True, "Public market chart", 121, "USD", now - timedelta(minutes=2), "PUBLIC_FALLBACK"),
    ])
    disputed = fetch_quote("RFH", 1)
    assert disputed.ok and disputed.price == 100
    assert disputed.quality == "DISPUTED_OBSERVED"
    assert disputed.payload["disagreement"] is True

    monkeypatch.setattr(quotes, "quote_candidates", lambda *_: [
        QuoteResult(True, "Alpaca", 100, "USD", now - timedelta(days=70), "OBSERVED"),
        QuoteResult(False, "Tiingo", message="offline"),
        QuoteResult(False, "Alpha Vantage", message="offline"),
        QuoteResult(False, "Public market chart", message="offline"),
    ])
    missing = fetch_quote("RFH", 1)
    assert not missing.ok
    assert missing.payload["freshness_error"] is True
