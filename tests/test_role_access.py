import os

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def test_friend_cannot_open_control():
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-key",
    })
    with app.app_context():
        db.create_all()
        friend = User(
            email="friend@example.com",
            display_name="Friend",
            role="FRIEND",
            password_hash=hash_password("temporary-test-value"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(friend)
        db.session.commit()
        friend_id = friend.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = friend_id

    response = client.get("/control")
    assert response.status_code == 403
