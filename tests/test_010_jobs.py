from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.core_models import Job
from mfapp.jobs import enqueue_job, run_jobs
from mfapp.security import encrypt_secret, hash_password


def test_unknown_job_fails_explicitly_not_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'jobs.db'}", "AUTO_MIGRATE": True})
    with app.app_context():
        user = User(email="c@example.com", display_name="C", role="CONTROL", password_hash=hash_password("abcdefghijklmnop"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.commit()
        job = enqueue_job("NOT_A_REAL_JOB", user_id=user.id); job.max_attempts = 1; db.session.commit()
        results = run_jobs(limit=1); db.session.refresh(job)
        assert results[0]["status"] == "FAILED"
        assert job.status == "FAILED"
        assert "Unknown job type" in job.error_message
