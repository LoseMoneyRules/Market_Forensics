import os

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import Company, Publication, ResearchWorkspace, User
from mfapp.research_core import merged_payload
from mfapp.security import encrypt_secret, hash_password


def make_app():
    return create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-key",
    })


def test_retired_control_bootstrap_is_not_registered(monkeypatch):
    # Even if an obsolete environment variable is accidentally left behind, 0.0.4 must not expose
    # the one-time CONTROL creation surface again after the production owner account exists.
    monkeypatch.setenv("MF_BOOTSTRAP_TOKEN", "obsolete-bootstrap-token")
    app = make_app()
    client = app.test_client()
    assert client.get("/bootstrap-control").status_code == 404
    assert client.post("/bootstrap-control", data={"token": "obsolete-bootstrap-token"}).status_code == 404
    assert client.get("/bootstrap-control/2fa").status_code == 404


def test_real_publish_route_creates_immutable_snapshot_and_friend_preview_reads_it():
    app = make_app()
    with app.app_context():
        db.create_all()
        user = User(
            email="control@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("temporary-test-value"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        company = Company(ticker="NKE", name="NIKE, Inc.", status="READY", summary="Published company summary")
        db.session.add_all([user, company])
        db.session.flush()
        payload = merged_payload({
            "business_summary": "Published business thesis",
            "numbers_summary": "Published numbers evidence",
            "expectations_summary": "Published expectations",
            "valuation_summary": "Published valuation conclusion",
            "bear_case": "Published bear case",
            "catalysts": "Published catalyst",
            "flows": "Published flow context",
            "risk_notes": "Published risk",
            "monitoring_summary": "Published monitoring",
            "sources": "Published sources",
            "thesis": "Private thesis",
            "invalidation": "Private invalidation",
            "position_size_plan": "PRIVATE SIZING MUST NEVER PUBLISH",
            "bear_value": 30,
            "base_value": 45,
            "bull_value": 60,
            "bear_prob": .25,
            "base_prob": .50,
            "bull_prob": .25,
        })
        db.session.add(ResearchWorkspace(company_id=company.id, payload=payload, updated_by=user.id))
        db.session.commit()
        uid = user.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["view_as"] = "CONTROL"

    response = client.post("/publish/NKE", data={"visibility": "FRIEND"}, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        pubs = Publication.query.filter_by(company_id=Company.query.filter_by(ticker="NKE").first().id).all()
        assert len(pubs) == 1
        pub = pubs[0]
        assert pub.version == 1
        assert pub.visibility == "FRIEND"
        assert pub.is_current is True
        assert pub.model_version == "0.0.2"
        assert "PRIVATE SIZING MUST NEVER PUBLISH" not in str(pub.payload)

    assert client.post("/view-as", data={"role": "FRIEND"}, follow_redirects=False).status_code == 302
    page = client.get("/company/NKE")
    assert page.status_code == 200
    assert b"Published business thesis" in page.data
    assert b"PRIVATE SIZING MUST NEVER PUBLISH" not in page.data
