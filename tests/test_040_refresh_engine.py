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
