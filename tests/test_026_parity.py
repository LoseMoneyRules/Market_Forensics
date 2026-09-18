from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, InvestmentState, MarketSnapshot, PortfolioRiskPlan, Position,
    PositionProfile, ResearchGateApproval, RiskPlan, Security,
)
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace
from mfapp.symbols import SymbolValidation


def make_app(tmp_path, monkeypatch, name="026"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "026-parity",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control026@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_026_portfolio_accepts_real_position_before_research(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "portfolio_only")
    uid = seed_control(app)
    client = app.test_client()
    login_control(client, uid)

    monkeypatch.setattr(
        "mfapp.routes_edit.validate_ticker",
        lambda ticker: SymbolValidation(
            ticker=ticker,
            status="VALID",
            source="TEST",
            name="New Portfolio Co",
            exchange="NYSE",
            currency="USD",
            instrument_type="EQUITY",
        ),
    )
    response = client.post("/portfolio/position", data={
        "ticker": "NEW",
        "side": "SHORT",
        "shares": "100",
        "avg_cost": "50",
        "tags": "consumer, rates",
        "notes": "Portfolio first",
    })
    assert response.status_code == 302

    with app.app_context():
        security = Security.query.filter_by(ticker="NEW").one()
        assert Coverage.query.filter_by(user_id=uid, security_id=security.id).first() is None
        position = Position.query.filter_by(user_id=uid, security_id=security.id).one()
        profile = PositionProfile.query.filter_by(user_id=uid, security_id=security.id).one()
        assert float(position.shares) == 100
        assert float(position.avg_cost) == 50
        assert profile.side == "SHORT"
        assert profile.tags == "consumer, rates"
        db.session.add(MarketSnapshot(
            security_id=security.id,
            provider="TEST",
            price=Decimal("40"),
            currency="USD",
            as_of=datetime.now(timezone.utc).replace(tzinfo=None),
            quality="OBSERVED",
            payload={},
        ))
        db.session.commit()

    detail = client.get("/portfolio/NEW")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "PORTFOLIO ONLY" in html
    assert "RISK / POSITION" in html
    assert "Start Research" in html

    risk = client.post("/portfolio/NEW/risk", data={
        "risk_budget_pct": "0.75",
        "sizing_reference_price": "30",
        "event_liquidity_haircut_pct": "5",
        "max_position_pct": "10",
        "correlation_notes": "Consumer factor",
        "kill_switch": "Exit if portfolio thesis breaks",
        "add_conditions": "Add on evidence, not on price",
    })
    assert risk.status_code == 302

    detail = client.get("/portfolio/NEW")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "25.0%" in html
    assert "30.0%" in html
    assert "2.5%" in html

    with app.app_context():
        security = Security.query.filter_by(ticker="NEW").one()
        money_risk = PortfolioRiskPlan.query.filter_by(user_id=uid, security_id=security.id).one()
        assert float(money_risk.risk_budget_pct) == .75
        assert float(money_risk.max_position_pct) == 10


def test_026_position_removal_preserves_research_and_money_risk(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "preserve")
    uid = seed_control(app)
    with app.app_context():
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add(company)
        db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", active=True, is_primary=True)
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(user_id=uid, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage)
        db.session.flush()
        ensure_workspace(coverage, uid)
        db.session.add(Position(user_id=uid, security_id=security.id, shares=10, avg_cost=20, currency="USD"))
        db.session.add(PositionProfile(user_id=uid, security_id=security.id, side="LONG", tags="industrial"))
        db.session.add(PortfolioRiskPlan(user_id=uid, security_id=security.id, risk_budget_pct=.75, max_position_pct=10, updated_by=uid))
        db.session.commit()
        coverage_id = coverage.id
        security_id = security.id

    client = app.test_client()
    login_control(client, uid)
    response = client.post("/portfolio/EXM/remove")
    assert response.status_code == 302
    with app.app_context():
        assert Position.query.filter_by(user_id=uid, security_id=security_id).first() is None
        assert Coverage.query.filter_by(id=coverage_id).first() is not None
        assert PortfolioRiskPlan.query.filter_by(user_id=uid, security_id=security_id).first() is not None
        investment = InvestmentState.query.filter_by(coverage_id=coverage_id).one()
        assert investment.state == "WATCHLIST"


def test_026_additive_migration_preserves_old_risk_and_renames_gate(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "migration")
    uid = seed_control(app)
    with app.app_context():
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add(company)
        db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", active=True, is_primary=True)
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(user_id=uid, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage)
        db.session.flush()
        research, old_risk, investment, _ = ensure_workspace(coverage, uid)
        old_risk.max_loss_pct = Decimal("0.75")
        old_risk.max_position_pct = Decimal("10")
        old_risk.add_conditions = "Evidence only"
        investment.state = "EXISTING_SHORT"
        db.session.add(Position(user_id=uid, security_id=security.id, shares=5, avg_cost=100, currency="USD"))
        db.session.add(ResearchGateApproval(
            coverage_id=coverage.id,
            gate_key="numbers",
            evidence_hash="abc",
            approved_by=uid,
        ))
        db.session.commit()

        from mfapp.upgrade_026 import migrate_local_web_parity
        result = migrate_local_web_parity()
        assert result["status"] == "applied"
        assert RiskPlan.query.filter_by(coverage_id=coverage.id).one().add_conditions == "Evidence only"
        profile = PositionProfile.query.filter_by(user_id=uid, security_id=security.id).one()
        money_risk = PortfolioRiskPlan.query.filter_by(user_id=uid, security_id=security.id).one()
        assert profile.side == "SHORT"
        assert float(money_risk.risk_budget_pct) == .75
        assert money_risk.add_conditions == "Evidence only"
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage.id, gate_key="fundamentals").one()
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage.id, gate_key="numbers").first() is None


def test_026_fundamentals_is_canonical_and_numbers_redirects(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "fundamentals")
    uid = seed_control(app)
    with app.app_context():
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add(company)
        db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", active=True, is_primary=True)
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(user_id=uid, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage)
        db.session.flush()
        ensure_workspace(coverage, uid)
        db.session.commit()

    client = app.test_client()
    login_control(client, uid)
    old = client.get("/company/EXM/numbers")
    assert old.status_code == 301
    assert "/company/EXM/fundamentals" in old.headers["Location"]
    current = client.get("/company/EXM/fundamentals")
    assert current.status_code == 200
    assert "Fundamentals · FY + current TTM" in current.get_data(as_text=True)

    from mfapp.routes import SECTIONS
    assert ("fundamentals", "Fundamentals") in SECTIONS
    assert all(key != "numbers" for key, _ in SECTIONS)


def test_026_visual_contracts_cover_working_capital_and_wrapped_flows():
    app_js = Path("mfapp/static/js/app.js").read_text()
    flow_js = Path("mfapp/static/js/flows.js").read_text()
    template = Path("mfapp/templates/company_section.html").read_text()
    dashboard = Path("mfapp/templates/dashboard.html").read_text()

    assert "function dualAxisWorkingCapitalChart" in app_js
    assert "Inventory · left scale" in app_js
    assert "Receivables · right scale" in app_js
    assert 'data-mf-chart="working-capital"' in template
    assert "nodeW=150,nodeH=68" in flow_js
    command_table = dashboard.split('<table class="data-table coverage-table">', 1)[1].split("</table>", 1)[0]
    assert command_table.count("<strong>") == 1


def test_026_reports_are_first_class_production_dependencies():
    requirements = Path("requirements.txt").read_text()
    optional = Path("requirements-reporting.txt").read_text()
    workflow = Path(".github/workflows/deploy-namecheap.yml").read_text()
    reporting = Path("mfapp/reporting.py").read_text()

    assert "-r requirements-reporting.txt" in requirements
    assert "python-docx" in optional and "reportlab" in optional
    assert "assert payload['reports'] == 'rich'" in workflow
    assert "'\"reports\":\"rich\"'" in workflow
    assert "Fundamentals" in reporting
    assert "Numbers" not in reporting


def test_026_version_and_state_are_locked():
    assert Path("VERSION").read_text().strip() == "0.2.6"
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert "**State-Version: 0.2.6**" in state
    assert "Local parity recovery" in state
    assert "Production remains on deployed 0.2.4 until 0.2.6 passes every gate." in state
