from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.core_models import Job
from mfapp.jobs import enqueue_job, run_jobs
from mfapp.security import encrypt_secret, hash_password


def build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({"TESTING": True, "SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'jobs.db'}", "AUTO_MIGRATE": True})


def add_user(email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0], role="CONTROL",
                password_hash=hash_password("abcdefghijklmnop"),
                totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
    db.session.add(user); db.session.commit()
    return user


def test_unknown_job_fails_explicitly_not_silently(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        user = add_user("c@example.com")
        job = enqueue_job("NOT_A_REAL_JOB", user_id=user.id); job.max_attempts = 1; db.session.commit()
        results = run_jobs(limit=1, user_id=user.id); db.session.refresh(job)
        assert results[0]["status"] == "FAILED"
        assert job.status == "FAILED"
        assert "Unknown job type" in job.error_message


def test_equivalent_active_global_job_is_not_duplicated(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        user = add_user("dedupe@example.com")
        first = enqueue_job("DISCOVERY_SCAN", user_id=user.id, priority=70)
        second = enqueue_job("DISCOVERY_SCAN", user_id=user.id, priority=70)
        assert first.id == second.id
        assert getattr(second, "_mf_reused", False) is True
        assert Job.query.filter_by(user_id=user.id, job_type="DISCOVERY_SCAN", status="QUEUED").count() == 1


def test_finished_job_does_not_block_new_job(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        user = add_user("again@example.com")
        first = enqueue_job("DISCOVERY_SCAN", user_id=user.id)
        results = run_jobs(limit=1, user_id=user.id)
        assert results[0]["status"] == "DONE"
        second = enqueue_job("DISCOVERY_SCAN", user_id=user.id)
        assert second.id != first.id
        assert getattr(second, "_mf_reused", False) is False


def test_worker_only_processes_requested_users_jobs(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        first_user = add_user("first@example.com")
        second_user = add_user("second@example.com")
        first_job = enqueue_job("DISCOVERY_SCAN", user_id=first_user.id, priority=1)
        second_job = enqueue_job("DISCOVERY_SCAN", user_id=second_user.id, priority=1)
        results = run_jobs(limit=1, user_id=second_user.id)
        assert results[0]["job_id"] == second_job.id
        db.session.refresh(first_job); db.session.refresh(second_job)
        assert first_job.status == "QUEUED"
        assert second_job.status == "DONE"
