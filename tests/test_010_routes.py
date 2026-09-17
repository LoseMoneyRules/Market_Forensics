from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, MarketSnapshot, ResearchState, Security, ValuationModel
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "route-test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'routes.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": True,
    })


def seed_control(app):
    with app.app_context():
        user = User(email="control@example.com", display_name="Control", role="CONTROL",
                    password_hash=hash_password("abcdefghijklmnop"),
                    totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.flush()
        company = Company(legal_name="Nike, Inc.", display_name="Nike")
        db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="NKE", exchange="NYSE", currency="USD",
                            validation_source="TEST", is_primary=True, active=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush()
        ensure_workspace(coverage, user.id)
        db.session.commit()
        return user.id


def login_session(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def counts():
    return {
        "coverage": Coverage.query.count(),
        "research": ResearchState.query.count(),
        "valuation": ValuationModel.query.count(),
        "jobs": Job.query.count(),
        "market": MarketSnapshot.query.count(),
    }


def test_primary_get_routes_render_without_mutating_research_state(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch)
    user_id = seed_control(app)
    client = app.test_client(); login_session(client, user_id)
    with app.app_context():
        before = counts()
    for path in ("/", "/discovery", "/company/NKE/overview", "/company/NKE/valuation", "/portfolio", "/publications", "/settings"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.get_data(as_text=True)[:500])
    with app.app_context():
        assert counts() == before


def test_health_is_public_and_identifies_web_native_release(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch)
    response = app.test_client().get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["version"] == "0.1.0"
    assert payload["architecture"] == "web-native"
