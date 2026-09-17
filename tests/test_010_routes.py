from __future__ import annotations

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, MarketSnapshot, ResearchState, Security, ValuationModel
from mfapp.extensions import db
from mfapp.jobs import enqueue_job
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({"TESTING": True,"SECRET_KEY": "route-test","SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'routes.db'}","WTF_CSRF_ENABLED": False,"AUTO_MIGRATE": True})


def seed_control(app):
    with app.app_context():
        user = User(email="control@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("abcdefghijklmnop"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.flush()
        company = Company(legal_name="Nike, Inc.", display_name="Nike")
        db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="NKE", exchange="NYSE", currency="USD", validation_source="TEST", is_primary=True, active=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id); db.session.commit(); return user.id


def login_session(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id; session["view_as"] = "CONTROL"


def counts():
    return {"coverage": Coverage.query.count(),"research": ResearchState.query.count(),"valuation": ValuationModel.query.count(),"jobs": Job.query.count(),"market": MarketSnapshot.query.count()}


def test_anonymous_private_routes_redirect_to_auth_login(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); client = app.test_client()
    for path in ("/", "/settings", "/portfolio", "/company/NKE/overview"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 303); assert "/login" in response.headers["Location"]; assert "next=" in response.headers["Location"]
        assert "Something went wrong" not in response.get_data(as_text=True)


def test_primary_get_routes_render_without_mutating_research_state(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id = seed_control(app); client = app.test_client(); login_session(client, user_id)
    with app.app_context(): before = counts()
    for path in ("/", "/discovery", "/company/NKE/overview", "/company/NKE/numbers", "/company/NKE/management", "/company/NKE/monitoring", "/company/NKE/journal", "/company/NKE/valuation", "/company/NKE/historical-test", "/company/NKE/financial-flows", "/company/NKE/tape", "/portfolio", "/publications", "/settings"):
        response = client.get(path); assert response.status_code == 200, (path, response.status_code, response.get_data(as_text=True)[:500])
    with app.app_context(): assert counts() == before


def test_company_publish_entrypoint_and_settings_controls_are_visible(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id = seed_control(app); client = app.test_client(); login_session(client, user_id)
    overview = client.get("/company/NKE/overview").get_data(as_text=True)
    assert "Publish research" in overview
    assert "One publication" in overview
    assert "Process readiness" in overview
    assert "PENDING APPROVAL" in overview or "MISSING EVIDENCE" in overview
    settings = client.get("/settings").get_data(as_text=True); assert "FINRA Public API Client ID" in settings; assert "FINRA Public API Client Secret" in settings; assert "Financial numbers" in settings


def test_live_quote_endpoints_exist_for_control(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id = seed_control(app); client = app.test_client(); login_session(client, user_id)
    response = client.get("/company/NKE/price/live")
    assert response.status_code == 200
    assert response.get_json()["ticker"] == "NKE"
    queued = client.post("/company/NKE/price/refresh")
    assert queued.status_code == 200
    assert queued.get_json()["status"] in {"QUEUED", "FRESH"}


def test_control_browser_worker_pumps_one_due_job(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id = seed_control(app); client = app.test_client(); login_session(client, user_id)
    with app.app_context(): job = enqueue_job("DISCOVERY_SCAN", user_id=user_id, priority=1); job_id = job.id
    status = client.get("/jobs/status"); assert status.status_code == 200; assert status.get_json()["due"] == 1
    response = client.post("/jobs/pump"); assert response.status_code == 200; payload = response.get_json(); assert payload["processed"]; assert payload["processed"][0]["job_id"] == job_id; assert payload["processed"][0]["status"] == "DONE"; assert payload["queued"] == 0
    with app.app_context(): assert db.session.get(Job, job_id).status == "DONE"


def test_health_is_public_and_identifies_web_native_release(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); response = app.test_client().get("/health"); assert response.status_code == 200
    payload = response.get_json(); assert payload["version"] == "0.1.5"; assert payload["architecture"] == "web-native"
