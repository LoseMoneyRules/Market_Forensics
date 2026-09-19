from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, MarketSnapshot, ResearchState, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "025-smoke",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '025_smoke.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control025smoke@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co", sector="Industrials", industry="Machinery")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", active=True, is_primary=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
        research.thesis = "Thesis"
        research.counter_evidence = "Counter"
        research.variant_us = "Variant"
        db.session.add(MarketSnapshot(security_id=security.id, provider="TEST", price=Decimal("40"), currency="USD", as_of=datetime.now(timezone.utc).replace(tzinfo=None), quality="OBSERVED", payload={}))
        db.session.commit()
        return user.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_025_every_research_section_renders_without_500(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid = seed_control(app)
    client = app.test_client(); login_control(client, uid)
    from mfapp.routes import SECTIONS
    for section, _ in SECTIONS:
        response = client.get(f"/company/EXM/{section}")
        assert response.status_code == 200, (section, response.status_code, response.data[:500])


def test_025_core_control_pages_render_without_500(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid = seed_control(app)
    client = app.test_client(); login_control(client, uid)
    for path in ("/", "/discovery", "/portfolio", "/settings", "/publications"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.data[:500])


def test_025_handoff_is_ready_for_next_chat():
    version = Path("VERSION").read_text().strip()
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert version == "0.2.7"
    assert "**State-Version: 0.2.7**" in state
    assert "FORENSIC_FAIR_VALUE_V1" in state
    assert "Settings is the ONLY user-facing version surface" in state
    assert "A 405 is a release blocker." in state
    assert "**Production:** 0.2.6 on Namecheap" in state
