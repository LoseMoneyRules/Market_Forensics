from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, MarketSnapshot, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "022-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '022.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control022@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(
            company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD",
            validation_source="TEST", active=True, is_primary=True,
        )
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        db.session.add(MarketSnapshot(
            security_id=security.id, provider="TEST", price=Decimal("40"), currency="USD",
            as_of=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20),
            quality="LAST_GOOD", payload={},
        ))
        db.session.commit()
        return user.id, company.id, security.id, coverage.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_022_market_refresh_has_terminal_cooldown_and_does_not_storm(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_control(app)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        db.session.add(Job(
            job_type="MARKET_REFRESH", status="DONE", priority=10, user_id=uid,
            company_id=company_id, security_id=security_id, payload={"coverage_id": coverage_id},
            attempts=1, max_attempts=3, run_after=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1), finished_at=now - timedelta(seconds=20),
        ))
        db.session.commit()
        before = Job.query.filter_by(user_id=uid, security_id=security_id, job_type="MARKET_REFRESH").count()
    client = app.test_client(); login_control(client, uid)
    response = client.post("/company/EXM/price/refresh")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "COOLDOWN"
    assert payload["fresh"] is False and payload["queued"] is False
    with app.app_context():
        after = Job.query.filter_by(user_id=uid, security_id=security_id, job_type="MARKET_REFRESH").count()
        assert after == before
        active = Job(
            job_type="MARKET_REFRESH", status="QUEUED", priority=10, user_id=uid,
            company_id=company_id, security_id=security_id, payload={"coverage_id": coverage_id},
            attempts=0, max_attempts=3, run_after=now,
        )
        db.session.add(active); db.session.commit(); active_id = active.id
    reused = client.post("/company/EXM/price/refresh").get_json()
    assert reused["status"] == "REUSED"
    assert reused["job_id"] == active_id and reused["reused"] is True and reused["queued"] is False


def test_022_background_completion_never_forces_page_reload():
    js = Path("mfapp/static/js/app.js").read_text()
    assert "if(!dirty){\n      window.location.reload();" not in js
    assert "if(ticker) readQuote();" in js
    assert "Click when you want to refresh the full research surface" in js


def test_022_discovery_is_two_sided_transparent_and_target_aware(monkeypatch):
    import mfapp.market_discovery as md

    class FakeResponse:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload

    def fake_get(url, **kwargs):
        if "most-actives" in url:
            return FakeResponse({"most_actives": [
                {"symbol": "LONGX", "volume": 10_000_000},
                {"symbol": "SHORTX", "volume": 9_000_000},
                {"symbol": "NEARX", "volume": 8_000_000},
            ]})
        if "movers" in url:
            return FakeResponse({"gainers": [{"symbol": "SHORTX", "percent_change": 11.0}],
                                 "losers": [{"symbol": "LONGX", "percent_change": -10.0},
                                            {"symbol": "NEARX", "percent_change": -8.5}]})
        return FakeResponse({})

    monkeypatch.setattr(md, "_headers", lambda user_id: {"x": "y"})
    monkeypatch.setattr(md.requests, "get", fake_get)
    monkeypatch.setattr(md, "_snapshot_map", lambda symbols, headers, errors: {})
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {
        "LONGX": {"cache_ready": True, "base_gap_pct": 30.0, "discovery_labels": [], "known": True},
        "SHORTX": {"cache_ready": True, "base_gap_pct": -25.0, "discovery_labels": [], "known": True},
        "NEARX": {"cache_ready": True, "base_gap_pct": 3.0, "discovery_labels": [], "known": True},
    })
    result = md.market_scan(1)
    rows = {row["ticker"]: row for row in result["candidates"]}
    assert rows["LONGX"]["research_side"] == "LONG"
    assert rows["SHORTX"]["research_side"] == "SHORT"
    assert "NEARX" not in rows
    assert result["excluded_breakdown"]["AT / NEAR BASE"] == 1
    assert any("Stored Base gap" in reason for reason in rows["LONGX"]["why_found"])
    assert result["long_count"] >= 1 and result["short_count"] >= 1


def test_022_command_center_dark_valuation_flow_and_tape_contracts():
    dashboard = Path("mfapp/templates/dashboard.html").read_text()
    company = Path("mfapp/templates/company_section.html").read_text()
    valuation = Path("mfapp/templates/valuation.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    flows = Path("mfapp/static/js/flows.js").read_text()
    app_js = Path("mfapp/static/js/app.js").read_text()

    header = dashboard.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert "<th>Base</th>" in header and "<th>Bear</th>" not in header and "<th>Bull</th>" not in header
    assert "alert_items" in dashboard and "exception-ticker" in dashboard
    assert "repeat(5,minmax(0,1fr))" in css
    assert "grid-template-columns:minmax(210px,230px)" in css
    assert 'html[data-theme="dark"] .metric-grid strong' in css

    assert '"current":engine_result.get("current_price")' in valuation
    assert "instead of rendering blank" in valuation
    assert "optionalNumber" in app_js

    assert "requestAnimationFrame(()=>render(root))" in flows and "calculation_version" not in flows
    assert "Price + FINRA Short Interest" in company
    assert 'data-mf-chart="tape-price-short"' in company
    assert "FINRA Daily Short Volume %" in company
    assert "tapePriceShortChart" in app_js


def test_022_workflows_never_hardcode_release_version():
    for workflow in (Path(".github/workflows/tests.yml"), Path(".github/workflows/deploy-namecheap.yml")):
        text = workflow.read_text()
        assert '== "0.2.' not in text
        assert "== '0.2." not in text
        assert "VERSION" in text


def test_022_release_identity_and_state_contract():
    version = Path("VERSION").read_text().strip()
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert f"**State-Version: {version}**" in state
    assert "non-disruptive" in state.lower()
    assert "LONG / SHORT" in state
