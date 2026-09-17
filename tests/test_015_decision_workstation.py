from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, FinancialPeriod, NormalizedFinancial, ResearchGateApproval, Security
from mfapp.current_financials import current_row, forecast_rows
from mfapp.extensions import db
from mfapp.historical_data import _coverage_stats
from mfapp.models import User
from mfapp.readiness_015 import research_readiness
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({"TESTING": True, "SECRET_KEY": "015-decision", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '015-decision.db'}", "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": False})


def seed_workspace(app):
    with app.app_context():
        db.create_all()
        user = User(email="control@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("abcdefghijklmnop"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        company = Company(legal_name="Example", display_name="Example")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", is_primary=True, active=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        ids = (user.id, company.id, security.id, coverage.id); db.session.commit(); return ids


def add_period(company_id, period_type, fy, end_date, revenue, net_income, cfo, capex, op_income, shares=100):
    period = FinancialPeriod(company_id=company_id, period_type=period_type, fiscal_year=fy, end_date=end_date, filed_at=end_date + timedelta(days=35), currency="USD")
    db.session.add(period); db.session.flush()
    db.session.add(NormalizedFinancial(financial_period_id=period.id, revenue=Decimal(str(revenue)), gross_profit=Decimal(str(revenue * .4)), operating_income=Decimal(str(op_income)), net_income=Decimal(str(net_income)), cfo=Decimal(str(cfo)), capex=Decimal(str(capex)), fcf=Decimal(str(cfo-capex)), diluted_shares=Decimal(str(shares)), shares_outstanding=Decimal(str(shares)), cash=Decimal('50'), debt=Decimal('100'), inventory=Decimal('80'), receivables=Decimal('70'), payables=Decimal('60'), assets=Decimal('1000'), liabilities=Decimal('500'), equity=Decimal('500'), calculation_version="0.1.5"))


def test_current_row_builds_real_ttm_from_four_quarters(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch); _, company_id, _, _ = seed_workspace(app)
    with app.app_context():
        for idx, (ptype, rev) in enumerate((("Q4", 100),("Q1", 110),("Q2", 120),("Q3", 130))):
            add_period(company_id, ptype, 2026 if ptype != "Q4" else 2025, date(2025,12,31)+timedelta(days=90*idx), rev, rev*.08, rev*.12, rev*.03, rev*.11)
        db.session.commit(); row = current_row(company_id)
        assert row["period_type"] == "TTM"
        assert round(row["revenue"],2) == 460.0
        assert round(row["fcf"],2) == round(460*.09,2)
        assert row["quality"]["quarter_count"] == 4
        assert len(row["source_map"]["quarter_period_ids"]) == 4


def test_forecast_uses_base_case_without_calling_it_consensus(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch); _, company_id, _, coverage_id = seed_workspace(app)
    with app.app_context():
        add_period(company_id,"FY",2025,date(2025,12,31),1000,80,120,30,100); db.session.commit()
        coverage = db.session.get(Coverage,coverage_id); model=coverage.valuation_models[0]
        base = next(s for s in model.scenarios if s.name.upper() == "BASE")
        base.inputs={"growth":.05,"net_margin":.09,"fcf_margin":.10}; db.session.commit()
        rows=forecast_rows(company_id,model,3)
        assert len(rows)==3
        assert round(rows[0]["revenue"],1)==1050.0
        assert rows[0]["source"]=="BASE_CASE_MODEL"


def test_readiness_never_auto_approves_and_becomes_stale_when_evidence_changes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch); user_id, company_id, _, coverage_id = seed_workspace(app)
    with app.app_context():
        coverage=db.session.get(Coverage,coverage_id); research=coverage.research; research.business="Business evidence"; db.session.commit()
        first=research_readiness(coverage); business=next(g for g in first["gates"] if g["key"]=="business")
        assert business["evidence_ready"] is True; assert business["approved"] is False; assert business["status"]=="PENDING APPROVAL"
        db.session.add(ResearchGateApproval(coverage_id=coverage_id,gate_key="business",approved_by=user_id,evidence_hash=business["evidence_hash"])); db.session.commit()
        approved=research_readiness(coverage); assert next(g for g in approved["gates"] if g["key"]=="business")["approved"] is True
        research.business="Changed evidence"; db.session.commit(); stale=research_readiness(coverage); gate=next(g for g in stale["gates"] if g["key"]=="business"); assert gate["approved"] is False; assert gate["status"]=="PENDING APPROVAL"


def test_historical_provider_requires_requested_span_not_just_many_rows():
    end=date(2026,9,17); start=end-timedelta(days=366*15+45)
    short=[{"trade_date":end-timedelta(days=i)} for i in range(400)]
    full=[{"trade_date":start+timedelta(days=i*7)} for i in range(((end-start).days//7)+1)]
    assert _coverage_stats(short,start,end)["complete"] is False
    assert _coverage_stats(full,start,end)["complete"] is True


def test_015_assets_expose_mobile_blue_ttm_kpis_and_no_visible_auto_marker():
    css=Path("mfapp/static/css/v015.css").read_text()
    js=Path("mfapp/static/js/v015.js").read_text()
    template=Path("mfapp/templates/company_section.html").read_text()
    historical=Path("mfapp/templates/historical_test_013.html").read_text()
    base=Path("mfapp/templates/base.html").read_text()
    kpis=Path("mfapp/templates/_section_kpis_015.html").read_text()
    pytest_ini=Path("pytest.ini").read_text()
    assert "--primary:#3a6f99" in css
    assert "nav-open" in css and "company-tabs-toggle" in css
    assert "5 * 60 * 1000" in js and "mf-mobile-menu" in base
    assert "TTM" in template and "Decision Brief" in template
    assert "management_engine" in template and "journal_prefill" in template
    assert "[AUTO 0.1.4]" not in template
    assert "Revenue growth" in kpis and "Bear fair value" in kpis and "Positive inflections" in kpis
    assert "Run started" in kpis and "Run completed" in kpis and "Distinct replay dates" in kpis
    assert "unique(attribute='anchor_date')" in historical
    assert "test_015_*.py" in pytest_ini
