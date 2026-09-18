from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, DecisionJournal, DecisionOutcome, InvestmentState, MarketSnapshot,
    PortfolioRiskPlan, Position, PositionProfile, ResearchGateApproval, RiskPlan, Security,
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


def test_026_fundamental_forensics_restore_local_metrics_without_fake_roic():
    from mfapp.calculations import financial_metrics

    previous = {
        "revenue": 900, "inventory": 90, "receivables": 120, "diluted_shares": 110,
    }
    current = {
        "revenue": 1000, "cogs": 600, "gross_profit": 400, "operating_income": 150,
        "pretax_income": 120, "income_tax": 24, "net_income": 100, "cfo": 130,
        "fcf": 110, "inventory": 120, "receivables": 150, "payables": 80,
        "diluted_shares": 100, "cash": 100, "debt": 200, "equity": 500, "assets": 1000,
    }
    metrics = financial_metrics(current, previous)
    assert round(metrics["cfo_to_net_income"], 2) == 1.30
    assert round(metrics["inventory_to_revenue_pct"], 1) == 12.0
    assert round(metrics["receivables_to_revenue_pct"], 1) == 15.0
    assert round(metrics["share_count_growth_pct"], 1) == -9.1
    assert metrics["dpo"] is not None and metrics["cash_conversion_days"] is not None
    assert round(metrics["roic_pct"], 1) == 20.0

    incomplete = dict(current)
    incomplete["income_tax"] = None
    assert financial_metrics(incomplete, previous)["roic_pct"] is None


def test_026_decision_journal_outcomes_are_append_only(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "journal")
    uid = seed_control(app)
    with app.app_context():
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", active=True, is_primary=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=uid, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush()
        research, _, investment, _ = ensure_workspace(coverage, uid)
        research.thesis = "Original thesis"
        investment.action = "WAIT"
        db.session.commit()

    client = app.test_client(); login_control(client, uid)
    created = client.post("/company/EXM/journal", data={
        "decision": "WAIT",
        "evidence_for": "Evidence A",
        "evidence_against": "Evidence B",
        "bias_notes": "Watch confirmation bias",
    })
    assert created.status_code == 302
    with app.app_context():
        journal = DecisionJournal.query.one()
        journal_id = journal.id
        frozen = dict(journal.thesis_snapshot)
        assert "outcome" not in frozen and "post_mortem" not in frozen and "lessons" not in frozen

    appended = client.post(f"/company/EXM/journal/{journal_id}/outcome", data={
        "outcome": "Price moved, thesis unchanged",
        "post_mortem": "Process was correct; timing was early",
        "lessons": "Do not move invalidation after the fact",
    })
    assert appended.status_code == 302
    with app.app_context():
        journal = DecisionJournal.query.get(journal_id)
        assert dict(journal.thesis_snapshot) == frozen
        outcome = DecisionOutcome.query.filter_by(journal_id=journal_id).one()
        assert "timing was early" in outcome.post_mortem


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


def test_026_capital_risk_and_position_controls_live_only_in_portfolio():
    from mfapp.routes import SECTIONS

    research_sections = {key for key, _ in SECTIONS}
    assert "risk" not in research_sections
    assert "position" not in research_sections

    research_template = Path("mfapp/templates/company_section.html").read_text()
    validate_template = Path("mfapp/templates/validate.html").read_text()
    portfolio_template = Path("mfapp/templates/portfolio_security.html").read_text()
    portfolio_index = Path("mfapp/templates/portfolio.html").read_text()

    forbidden_capital_controls = [
        "Max portfolio loss budget %",
        "Sizing reference price",
        "Liquidity / event haircut %",
        "Max position cap %",
        "Average cost",
        "Exposure tags",
    ]
    for label in forbidden_capital_controls:
        assert label not in research_template
        assert label not in validate_template

    assert "RISK / POSITION" in portfolio_template
    assert "Max portfolio loss budget %" in portfolio_template
    assert "Sizing reference price" in portfolio_template
    assert "Average cost" in portfolio_template
    assert "Exposure tags" in portfolio_template
    assert "Add / edit real position" in portfolio_index
    assert "THESIS INVALIDATION" in research_template
    assert "Research rule, not portfolio sizing." in research_template


def test_026_readability_contract_has_no_tiny_ui_text():
    css = Path("mfapp/static/css/app.css").read_text()
    app_js = Path("mfapp/static/js/app.js").read_text()
    flow_js = Path("mfapp/static/js/flows.js").read_text()
    validate_js = Path("mfapp/static/js/validate_chart.js").read_text()
    reporting = Path("mfapp/reporting.py").read_text()

    assert "font-size:9px" not in css
    assert "font-size:10px" not in css
    assert "font-size:11px" not in css
    assert "font-size:10.5px" not in css
    assert "font-size:11.5px" not in css
    assert "body{margin:0;min-height:100vh;background:var(--paper);font-size:14px;line-height:1.5}" in css
    assert "font-size:14px;line-height:1.35" in css
    assert "ctx.font='13px system-ui'" in app_js
    assert "labelFont='13',valueFont='14',labelLine='15'" in flow_js
    assert "ctx.font='13px Inter, sans-serif'" in validate_js
    assert "font_size=11; leading=15" in reporting
    assert 'fontSize=10.5,leading=14' in reporting


def test_026_reports_are_first_class_production_dependencies():
    requirements = Path("requirements.txt").read_text()
    optional = Path("requirements-reporting.txt").read_text()
    workflow = Path(".github/workflows/deploy-namecheap.yml").read_text()
    reporting = Path("mfapp/reporting.py").read_text()

    app_py = Path("app.py").read_text()
    assert "-r requirements-reporting.txt" in requirements
    assert "python-docx" in optional and "reportlab" in optional
    assert "assert payload['reports'] == 'rich'" in workflow
    assert "mfapp/_reporting_vendor" in workflow
    assert "MF_REPORTING_REQUIREMENTS_SHA" in workflow
    assert "MF_REPORTING_VENDOR_UPLOAD" in workflow
    assert workflow.count("--exclude-glob _reporting_vendor*") >= 3
    assert "pip install --no-compile --target .reporting-vendor-payload" in workflow
    assert "pip install --no-compile --target .release-payload/mfapp/_reporting_vendor" not in workflow
    assert "_reporting_vendor.next" in workflow
    assert "_reporting_vendor.rollback" in workflow
    assert "_reporting_vendor" in app_py
    assert "'\"reports\":\"rich\"'" in workflow
    assert "Fundamentals" in reporting
    assert "Numbers" not in reporting


def test_026_release_history_remains_documented():
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert "0.2.6" in state
    assert "Local parity recovery" in state
