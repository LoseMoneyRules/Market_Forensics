from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from flask import g

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, Job, ResearchGateApproval, ResearchState, Security,
)
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "023-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '023.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control023@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add_all([user, company])
        db.session.flush()
        security = Security(
            company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD",
            validation_source="TEST", active=True, is_primary=True,
        )
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(
            user_id=user.id, security_id=security.id,
            status="RESEARCH", research_state="UNDER_REVIEW",
        )
        db.session.add(coverage)
        db.session.flush()
        ensure_workspace(coverage, user.id)
        research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
        research.thesis = "Evidence-backed thesis"
        research.counter_evidence = "Explicit counter evidence"
        research.variant_us = "Variant perception"
        db.session.commit()
        return user.id, company.id, security.id, coverage.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_023_process_readiness_is_immediate_and_not_a_background_job(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, coverage_id = seed_control(app)
    client = app.test_client()
    login_control(client, uid)

    with app.app_context():
        before = Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count()

    response = client.post(
        "/company/EXM/readiness/overview",
        headers={"Accept": "application/json"},
        data={"action": "approve"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert 'data-gate-key="overview"' in payload["html"]
    assert "APPROVED" in payload["html"]
    assert "Reopen" in payload["html"]

    with app.app_context():
        after = Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count()
        assert after == before
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage_id, gate_key="overview").first() is not None


def test_023_discovery_request_survives_provider_and_stored_payload_failures(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, _ = seed_control(app)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        db.session.add(Job(
            job_type="DISCOVERY_SCAN", status="DONE", priority=70, user_id=uid,
            payload={}, result={"market_scan": {"candidates": [
                {"ticker": "BAD", "scan_score": "not-a-number", "why_found": "malformed-old-value"}
            ]}},
            attempts=1, max_attempts=3, run_after=now, started_at=now, finished_at=now,
        ))
        db.session.commit()

    import mfapp.routes as routes
    monkeypatch.setattr(routes, "search_universe", lambda q, user_id: (_ for _ in ()).throw(RuntimeError("provider down")))
    monkeypatch.setattr(routes, "_cached_coverage_rows", lambda user_id: ([], False))

    client = app.test_client()
    login_control(client, uid)
    response = client.get("/discovery?q=AAPL")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "SEARCH UNAVAILABLE" in page
    assert "No candidate was invented or silently substituted." in page
    assert "BAD" not in page


def test_023_missing_price_history_queues_ticker_scoped_backfill(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_control(app)
    from mfapp.research_routes import _price_history_context

    with app.test_request_context("/company/EXM/valuation"):
        g.user = db.session.get(User, uid)
        ctx = {
            "company": db.session.get(Company, company_id),
            "security": db.session.get(Security, security_id),
            "coverage": db.session.get(Coverage, coverage_id),
        }
        history, status = _price_history_context(ctx)
        assert history == []
        assert status["needs_refresh"] is True
        assert status["job_id"] is not None
        job = db.session.get(Job, status["job_id"])
        assert job.job_type == "PRICE_HISTORY_REFRESH"
        assert job.security_id == security_id
        assert job.payload["lookback_years"] == 3


def test_023_job_status_exposes_ticker_and_global_scope(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_control(app)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        db.session.add_all([
            Job(job_type="PRICE_HISTORY_REFRESH", status="QUEUED", priority=35, user_id=uid,
                company_id=company_id, security_id=security_id, payload={"coverage_id": coverage_id},
                attempts=0, max_attempts=3, run_after=now),
            Job(job_type="DISCOVERY_SCAN", status="QUEUED", priority=70, user_id=uid,
                payload={}, attempts=0, max_attempts=3, run_after=now),
        ])
        db.session.commit()
        from mfapp.routes_publish import _queue_status
        state = _queue_status(uid)
        targets = {(row["type"], row["target"], row["scope"]) for row in state["active_jobs"]}
        assert ("PRICE_HISTORY_REFRESH", "EXM", "SECURITY") in targets
        assert ("DISCOVERY_SCAN", "GLOBAL", "GLOBAL") in targets


def test_023_ui_contracts_are_single_source_and_compact():
    dashboard = Path("mfapp/templates/dashboard.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    flows = Path("mfapp/static/js/flows.js").read_text()
    settings = Path("mfapp/templates/settings.html").read_text()
    valuation = Path("mfapp/templates/valuation.html").read_text()
    app_js = Path("mfapp/static/js/app.js").read_text()

    header = dashboard.split("<thead>", 1)[1].split("</thead>", 1)[0]
    expected = [
        "Security", "Research conclusion", "Value / Path", "Model confidence", "Price",
        "Base", "Base gap", "Process", "Validate", "Freshness", "Next action", "Lens", "Manage",
    ]
    positions = [header.index(">"+label+"<") for label in expected]
    assert positions == sorted(positions)
    assert ".coverage-table th{white-space:nowrap}" in css
    assert ".coverage-table th:last-child,.coverage-table td:last-child{width:1%;white-space:nowrap" in css
    assert "Latest point-in-time walk-forward validation status" in dashboard

    assert ".flow-canvas{min-height:0" in css
    assert ".flow-svg{display:block;width:auto;height:auto;max-width:none;min-height:0;margin:0}" in css
    assert "const colGap=180,nodeW=150,nodeH=52,top=14,side=10,rowGap=12;" in flows

    for path in (
        "mfapp/templates/company_section.html",
        "mfapp/templates/valuation.html",
        "mfapp/templates/financial_flows.html",
        "mfapp/templates/validate.html",
        "mfapp/templates/portfolio_security.html",
    ):
        template = Path(path).read_text()
        assert '{% include "_company_header.html" %}' in template
        assert "data-live-price" not in template
    canonical = Path("mfapp/templates/_company_header.html").read_text()
    assert canonical.count("data-live-price") == 2

    assert "<th>Target</th>" in settings
    assert "GLOBAL" in settings
    assert "price_history_status.rows" in valuation
    assert "Refresh 2Y price history" in valuation
    assert "gate-approval-form" in Path("mfapp/templates/_process_readiness.html").read_text()
    assert "current.replaceWith(next)" in app_js


def test_023_release_identity_and_state_contract():
    version = Path("VERSION").read_text().strip()
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert f"**State-Version: {version}**" in state
    assert "PRICE_HISTORY_REFRESH" in state
    assert "ticker, company or GLOBAL" in state
    assert "Process Readiness" in state and "Approve → Reopen" in state
