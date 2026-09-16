import os
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def make_app(tmp_path):
    os.environ["MF_V312_APP_DIR"] = str(tmp_path / "engine")
    trace_path = tmp_path / "trace.jsonl"
    app = create_app({
        "TESTING": True,
        "PROPAGATE_EXCEPTIONS": False,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "trace-test-key",
        "TRACE_LOG_PATH": str(trace_path),
        "TRACE_ENABLED": True,
    })
    with app.app_context():
        db.create_all()
    return app, trace_path


def add_user(app, role="CONTROL", email="trace-control@example.com"):
    with app.app_context():
        user = User(
            email=email,
            display_name="Trace User",
            role=role,
            password_hash=hash_password("temporary-test-value"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, user_id, role="CONTROL"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = role


def test_trace_captures_unhandled_500_without_sensitive_request_data(tmp_path):
    app, trace_path = make_app(tmp_path)

    @app.get("/__trace_test_boom")
    def _boom():
        raise RuntimeError("trace-test-boom")

    client = app.test_client()
    response = client.get(
        "/__trace_test_boom?secret=DO_NOT_LOG_QUERY",
        headers={"Cookie": "private=DO_NOT_LOG_COOKIE", "Authorization": "Bearer DO_NOT_LOG_TOKEN"},
    )
    assert response.status_code == 500
    assert b"Market Forensics trace ID" in response.data
    assert response.headers.get("X-MF-Trace-ID")

    text = trace_path.read_text(encoding="utf-8")
    assert "REQUEST_START" in text
    assert "UNHANDLED_EXCEPTION" in text
    assert "RuntimeError" in text
    assert "trace-test-boom" in text
    assert "DO_NOT_LOG_QUERY" not in text
    assert "DO_NOT_LOG_COOKIE" not in text
    assert "DO_NOT_LOG_TOKEN" not in text


def test_trace_console_is_control_only(tmp_path):
    app, _ = make_app(tmp_path)
    control_id = add_user(app)
    friend_id = add_user(app, role="FRIEND", email="trace-friend@example.com")
    client = app.test_client()

    login(client, control_id)
    response = client.get("/trace")
    assert response.status_code == 200
    assert b"0.0.4 Trace" in response.data

    login(client, friend_id, "FRIEND")
    assert client.get("/trace").status_code == 403


def test_health_identifies_trace_build(tmp_path):
    app, _ = make_app(tmp_path)
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.json["version"] == "0.0.4"
    assert response.json["engine"] == "3.1.12 FULL"
    assert response.json["trace_build"] == "0.0.4-trace1"
