from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, NormalizedFinancial, Security
from mfapp.extensions import db
from mfapp.financial_flow_engine import build_income_statement_flow
from mfapp.models import User
from mfapp.routes import SECTIONS
from mfapp.secdata import _AV_BALANCE_FIELDS, _AV_CASH_FIELDS, _AV_INCOME_FIELDS, _finish_normalized
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch, name="028"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "028-release",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control_workspace(app, ticker="EXM"):
    with app.app_context():
        db.create_all()
        user = User(
            email=f"{ticker.lower()}028@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        company = Company(
            legal_name="Example Industrial Co",
            display_name="Example Industrial Co",
            sector="Industrials",
            industry="Industrial Machinery",
        )
        db.session.add(company)
        db.session.flush()
        security = Security(
            company_id=company.id,
            ticker=ticker,
            exchange="NYSE",
            currency="USD",
            active=True,
            is_primary=True,
        )
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(
            user_id=user.id,
            security_id=security.id,
            status="RESEARCH",
            research_state="UNDER_REVIEW",
        )
        db.session.add(coverage)
        db.session.flush()
        ensure_workspace(coverage, user.id)
        db.session.commit()
        return user.id


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_028_reports_do_not_use_wsgi_file_wrapper(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "report_wrapper")
    uid = seed_control_workspace(app)
    client = app.test_client()
    login(client, uid)

    def hostile_wrapper(*args, **kwargs):
        raise AssertionError("wsgi.file_wrapper must not be used for in-memory reports")

    for url, prefix in (
        ("/company/EXM/report/pdf?mode=full", b"%PDF"),
        ("/company/EXM/report/docx?mode=full", b"PK"),
    ):
        response = client.get(url, environ_overrides={"wsgi.file_wrapper": hostile_wrapper})
        assert response.status_code == 200
        assert response.data.startswith(prefix)
        assert "attachment;" in response.headers.get("Content-Disposition", "")
        assert response.headers.get("Cache-Control") == "no-store, max-age=0"


def test_028_income_statement_is_sequential_revenue_to_net_waterfall():
    flow = build_income_statement_flow({
        "period_label": "FY2025",
        "revenue": 1000,
        "cogs": 600,
        "gross_profit": 400,
        "operating_expenses": 150,
        "operating_income": 250,
        "pretax_income": 220,
        "income_tax": 50,
        "net_income": 170,
    })
    assert flow["presentation"] == "WATERFALL"
    steps = flow["bridge_steps"]
    assert [row["label"] for row in steps] == [
        "Revenue",
        "COGS / Cost of Revenue",
        "Gross Profit",
        "Operating Expenses / Costs",
        "Operating Income",
        "Other / Interest",
        "Pre-Tax Income",
        "Income Tax",
        "Net Income",
    ]
    assert steps[0]["result"] == 1000
    assert steps[1]["value"] == -600
    assert steps[3]["value"] == -150
    assert steps[5]["value"] == -30
    assert steps[7]["value"] == -50
    assert steps[-1]["result"] == 170


def test_028_sec_normalization_recovers_gross_profit_from_direct_cogs():
    row = NormalizedFinancial(
        revenue=Decimal("1000"),
        cogs=Decimal("600"),
        gross_profit=None,
        operating_income=Decimal("250"),
    )
    source_map = {"revenue": {"method": "DIRECT_FY"}, "cogs": {"method": "DIRECT_FY"}}
    _finish_normalized(row, source_map, period_type="FY")
    assert row.gross_profit == Decimal("400")
    assert row.operating_expenses == Decimal("150")
    assert source_map["gross_profit"]["method"] == "REVENUE_MINUS_COGS"


def test_028_optional_fundamental_fallback_covers_all_three_statements():
    assert _AV_INCOME_FIELDS["gross_profit"] == ("grossProfit",)
    assert "equity" in _AV_BALANCE_FIELDS
    assert "cash" in _AV_BALANCE_FIELDS
    assert _AV_CASH_FIELDS["cfo"] == ("operatingCashflow",)
    assert _AV_CASH_FIELDS["capex"] == ("capitalExpenditures",)


def test_028_evidence_is_consolidated_at_bottom_before_reports():
    template = Path("mfapp/templates/company_section.html").read_text()
    evidence = template.index('class="panel evidence-summary-panel evidence-consolidated"')
    reports = template.index('class="panel export-research"')
    thesis = template.index("Thesis & variant perception")
    assert thesis < evidence < reports
    assert "For / against / signals" in template
    assert "Evidence signals</h2>" not in template
    assert "expectations-basis-strip" not in template


def test_028_validate_is_after_audit_and_links_to_real_validate_route():
    keys = [key for key, _ in SECTIONS]
    assert keys[-2:] == ["audit", "validate"]
    tabs = Path("mfapp/templates/_research_tabs.html").read_text()
    template = Path("mfapp/templates/company_section.html").read_text()
    assert "web.validate_company" in tabs
    assert "web.validate_company" in template


def test_028_deploy_excludes_vendor_trees_from_normal_mirrors():
    workflow = Path(".github/workflows/deploy-namecheap.yml").read_text()
    assert "rm -rf .release-payload/mfapp/_reporting_vendor .release-payload/mfapp/_vendor" in workflow
    assert workflow.count("--exclude='^_reporting_vendor(/|$)'") >= 4
    assert workflow.count("--exclude='^_vendor(/|$)'") >= 4
    assert "MF_REPORTING_VENDOR_UPLOAD" in workflow
