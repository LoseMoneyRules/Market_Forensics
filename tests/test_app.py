import os
from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

import pyotp
from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def make_app():
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test-key"})
    with app.app_context():
        db.create_all()
    return app


def test_health():
    app = make_app()
    c = app.test_client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json["version"] == "0.0.1"


def test_login_requires_2fa():
    app = make_app()
    with app.app_context():
        secret = pyotp.random_base32()
        u = User(email="control@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("A-secure-password-123"), totp_secret_enc=encrypt_secret(secret), is_active=True)
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    r = c.post("/login", data={"email": "control@example.com", "password": "A-secure-password-123"}, follow_redirects=False)
    assert r.status_code == 302
    assert "/verify" in r.location
    code = pyotp.TOTP(secret).now()
    r = c.post("/verify", data={"code": code}, follow_redirects=False)
    assert r.status_code == 302
    assert r.location.endswith("/")


def test_control_is_protected():
    app = make_app()
    c = app.test_client()
    r = c.get("/control")
    assert r.status_code == 302
    assert "/login" in r.location
