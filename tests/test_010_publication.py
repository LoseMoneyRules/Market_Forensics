from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.core_models import Company, Coverage, Position, Security
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import create_snapshot, ensure_workspace, publication_payload


def app_with_control(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'pub.db'}", "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": True})
    with app.app_context():
        user = User(email="c@example.com", display_name="C", role="CONTROL", password_hash=hash_password("abcdefghijklmnop"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.flush()
        company = Company(legal_name="Nike", display_name="Nike"); db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="NKE", exchange="NYSE", validated_at=None); db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, research_state="ATTRACTIVE"); db.session.add(coverage); db.session.flush()
        research, risk, investment, model = ensure_workspace(coverage, user.id)
        research.thesis = "Long-term thesis"; risk.thesis_invalidation = "Revenue declines below threshold"; risk.max_position_pct = 8
        db.session.add(Position(user_id=user.id, security_id=security.id, shares=508, avg_cost=48)); db.session.commit()
        snap = create_snapshot(coverage, user.id); return app, snap.id


def test_publication_strips_private_position_and_invalidation(tmp_path, monkeypatch):
    app, snap_id = app_with_control(tmp_path, monkeypatch)
    with app.app_context():
        from mfapp.core_models import Snapshot
        snap = db.session.get(Snapshot, snap_id); public = publication_payload(snap, "INSIDER")
        assert "position" not in public
        assert "investment_state" not in public
        assert "max_position_pct" not in public["risk"]
        assert "thesis_invalidation" not in public["risk"]
        assert public["research"]["thesis"] == "Long-term thesis"
