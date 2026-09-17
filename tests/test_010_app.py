from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({"TESTING": True, "SECRET_KEY": "test-secret", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}", "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": True})


def add_control(app):
    with app.app_context():
        user = User(email="control@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("correct horse battery staple"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.commit(); return user.id


def test_health_and_version(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == "0.1.2"
    assert response.get_json()["architecture"] == "web-native"


def test_existing_control_identity_can_be_loaded_without_reset(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch); uid = add_control(app)
    with app.app_context():
        user = db.session.get(User, uid); old_hash = user.password_hash; old_totp = user.totp_secret_enc
        from mfapp.schema import bootstrap_schema
        bootstrap_schema(migrate_legacy=True)
        user = db.session.get(User, uid)
        assert user.password_hash == old_hash
        assert user.totp_secret_enc == old_totp
        assert user.role == "CONTROL"
