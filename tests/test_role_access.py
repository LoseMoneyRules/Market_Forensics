import os

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def make_app():
    return create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-key",
    })


def add_user(app, email, role):
    with app.app_context():
        db.create_all()
        user = User(
            email=email,
            display_name=role.title(),
            role=role,
            password_hash=hash_password("temporary-test-value"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_friend_cannot_open_control():
    app = make_app()
    friend_id = add_user(app, "friend@example.com", "FRIEND")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = friend_id
    response = client.get("/control")
    assert response.status_code == 403


def test_control_can_preview_friend_without_changing_real_role():
    app = make_app()
    control_id = add_user(app, "control@example.com", "CONTROL")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = control_id
        session["view_as"] = "CONTROL"

    response = client.post("/view-as", data={"role": "FRIEND"}, follow_redirects=False)
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == control_id
        assert session["view_as"] == "FRIEND"

    # A CONTROL previewing FRIEND sees public surfaces only.
    assert client.get("/control").status_code == 404
    assert client.get("/decision-queue").status_code == 404
    assert client.get("/").status_code == 200

    # Preview does not mutate the authorization role stored in the database.
    with app.app_context():
        assert db.session.get(User, control_id).role == "CONTROL"

    response = client.post("/view-as", data={"role": "CONTROL"}, follow_redirects=False)
    assert response.status_code == 302
    assert client.get("/control").status_code == 200


def test_friend_cannot_use_view_as():
    app = make_app()
    friend_id = add_user(app, "friend2@example.com", "FRIEND")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = friend_id
    assert client.post("/view-as", data={"role": "CONTROL"}).status_code == 403
