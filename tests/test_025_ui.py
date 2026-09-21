from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, MarketSnapshot, ResearchGateApproval, ResearchState, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "025-ui",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '025_ui.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control025ui@example.com", display_name="Control", role="CONTROL",
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
        research.thesis = "Evidence-backed thesis"
        research.counter_evidence = "Explicit counter evidence"
        research.variant_us = "Variant perception"
        db.session.add(MarketSnapshot(security_id=security.id, provider="TEST", price=Decimal("40"), currency="USD", as_of=datetime.now(timezone.utc).replace(tzinfo=None), quality="OBSERVED", payload={}))
        db.session.commit()
        return user.id, coverage.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_025_settings_version_and_collapsed_jobs(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _ = seed_control(app)
    client = app.test_client(); login_control(client, uid)
    response = client.get("/settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "application-version-value" in html
    assert f"v{Path('VERSION').read_text().strip()}" in html
    assert '<details class="panel recent-jobs">' in html
    assert "<summary class=\"recent-jobs-summary\">" in html
    assert "Click to view the job list." in html
    assert '<details class="panel recent-jobs" open' not in html


def test_025_command_center_compact_contract():
    html = Path("mfapp/templates/dashboard.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    assert html.count('class="coverage-number-col"') >= 6
    assert 'class="coverage-fresh-col"' in html
    assert '<th class="coverage-manage-col">Manage</th>' in html
    assert ".coverage-table .coverage-number-col,.coverage-table .coverage-fresh-col{width:1%;white-space:nowrap" in css
    assert ".coverage-table .coverage-manage-col{width:62px;min-width:62px" in css
    note = html.split('class="command-table-note">', 1)[1].split("</p>", 1)[0]
    assert "<strong>" not in note and "<b>" not in note
    assert "Process = approved Research gates." in note
    assert "Validate = latest point-in-time walk-forward validation status" in note
    freshness = html.split('<th class="coverage-fresh-col">Freshness</th>', 1)[1].split("</table>", 1)[0]
    assert "strftime" not in freshness and "%Y-" not in freshness
    table = html.split('<table class="data-table coverage-table">', 1)[1].split("</table>", 1)[0]
    assert table.count("<strong>") == 1
    assert "<strong>{{ row.security.ticker }}</strong>" in table


def test_025_explicit_reopen_endpoint_never_405(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, coverage_id = seed_control(app)
    client = app.test_client(); login_control(client, uid)
    with app.app_context():
        before = Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count()

    approve = client.post("/company/EXM/readiness/overview/approve", headers={"Accept": "application/json"})
    assert approve.status_code == 200 and "Reopen" in approve.get_json()["html"]
    reopen = client.post("/company/EXM/readiness/overview/revoke", headers={"Accept": "application/json"})
    assert reopen.status_code == 200 and "Approve" in reopen.get_json()["html"]
    approve_again = client.post("/company/EXM/readiness/overview/approve", headers={"Accept": "application/json"})
    assert approve_again.status_code == 200

    with app.app_context():
        assert Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count() == before
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage_id, gate_key="overview").first() is not None


def test_025_flows_full_width_without_redundant_legend():
    template = Path("mfapp/templates/financial_flows.html").read_text()
    js = Path("mfapp/static/js/flows.js").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    assert ".flow-svg{display:block;width:100%;height:auto;min-height:0;margin:0}" in css
    assert "targetWidth=Math.max(720,visible>0?visible-24:720)" in js
    assert "(targetWidth-side*2-nodeW)/layout.maxDepth" in js
    for old in ("Operating / bridge", "Profit / retained cash", "Cost / distribution / loss"):
        assert old not in template
    assert "Income Statement" in template and "Cash Flow" in template


def test_025_discovery_legibility_and_valuation_visual_legend_contract():
    discovery = Path("mfapp/templates/discovery.html").read_text()
    valuation = Path("mfapp/templates/valuation.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()

    assert "discovery-list-row" in discovery and "BROAD UNIVERSE DISCOVERY" in discovery
    assert ".discovery-list-title strong{font-size:15px" in css
    assert ".discovery-list-title>span:last-child" in css and "font-size:14px" in css
    assert ".discovery-list-metrics" in css and "font-size:13px" in css
    assert ".discovery-forensic-signals span{font-size:13px" in css
    assert ".discovery-method" in css and "font-size:13px" in css

    assert 'class="kpi-grid valuation-scenario-kpis"' in valuation
    assert 'valuation-kpi-{{ name|lower }}' in valuation
    assert "valuation-quality-grid" in valuation
    assert ".valuation-scenario-kpis .valuation-kpi-bear{border-top-color:var(--mf-chart-bear)}" in css
    assert ".valuation-scenario-kpis .valuation-kpi-base{border-top-color:var(--mf-chart-price)}" in css
    assert ".valuation-scenario-kpis .valuation-kpi-bull{border-top-color:var(--mf-chart-bull)}" in css
    assert ".valuation-impact-ledger .valuation-quality-grid{grid-template-columns:repeat(4,minmax(0,1fr))}" in css
    assert ".chart-key.base i{background:var(--mf-chart-price)}" in css


def test_025_global_ui_semantics_readability_and_sticky_security_contract():
    base = Path("mfapp/templates/base.html").read_text()
    company = Path("mfapp/templates/company_section.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    js = Path("mfapp/static/js/app.js").read_text()

    assert 'id="mf-topbar-security"' in base
    assert "data-topbar-live-price" in base
    assert "syncTopbarSecurity" in js
    assert "security-context-visible" in css
    assert "topbarPriceNodes" in js

    assert "data-status-value=\"{{ tm.posture or 'WAIT' }}\"" in company
    assert ".tape-context-card{display:grid;gap:6px" in css
    assert ".tape-context-card.watch{border-left-color:var(--semantic-caution)" in css
    assert ".tape-context-card.negative{border-left-color:var(--semantic-negative)" in css
    assert ".tape-context-card.positive{border-left-color:var(--semantic-positive)" in css
    assert '.tape-context-card[data-semantic="caution"]' in css
    assert '.tape-context-card[data-semantic="neutral"]' in css

    assert "neutral: ['NEUTRAL','FAIR','BALANCED'" in js
    caution_block = js.split("caution:", 1)[1].split("info:", 1)[0]
    assert "'NEUTRAL'" not in caution_block
    assert "'WATCH'" in caution_block and "'UNRESOLVED'" in caution_block
    assert 'html[data-theme="dark"] .status-chip[data-semantic="positive"]' in css
    assert 'html[data-theme="dark"] .status-chip[data-semantic="negative"]' in css
    assert 'html[data-theme="dark"] .status-chip[data-semantic="caution"]' in css
    assert 'html[data-theme="dark"] .status-chip[data-semantic="neutral"]' in css

    assert "font-size:12px" not in css
    assert "font-size:12.5px" not in css
    assert "font='12px system-ui'" not in js
    assert not any(word in css.lower() for word in ("purple", "violet", "magenta"))
