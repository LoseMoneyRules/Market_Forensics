from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace
from mfapp.upgrade_012 import queue_existing_coverage_prefill


def test_upgrade_queues_one_prefill_per_existing_coverage_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "upgrade-test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'upgrade.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })
    with app.app_context():
        db.create_all()
        user = User(email="upgrade@example.com", display_name="Control", role="CONTROL",
                    password_hash=hash_password("abcdefghijklmnop"),
                    totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.flush()
        company = Company(legal_name="Upgrade Corp", display_name="Upgrade Corp")
        db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="UPG", exchange="NYSE", validation_source="TEST", active=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush()
        ensure_workspace(coverage, user.id)
        db.session.commit()

        first = queue_existing_coverage_prefill()
        assert first["status"] == "applied"
        assert first["jobs_queued"] == 1
        job = Job.query.filter_by(job_type="RESEARCH_PREFILL", user_id=user.id, security_id=security.id).one()
        assert job.payload["coverage_id"] == coverage.id
        assert job.payload["upgrade"] == "0.1.2"

        second = queue_existing_coverage_prefill()
        assert second["status"] == "already-applied"
        assert Job.query.filter_by(job_type="RESEARCH_PREFILL", user_id=user.id, security_id=security.id).count() == 1
