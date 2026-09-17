from pathlib import Path

import mfapp.routes_013  # noqa: F401

from mfapp import create_app
from mfapp.research_intelligence import build_research_intelligence
from mfapp.routes import SECTIONS


def _row(revenue, op_income, net_income, fcf, inventory, receivables, cash, debt, metrics):
    return {"revenue": revenue, "operating_income": op_income, "net_income": net_income, "fcf": fcf, "inventory": inventory, "receivables": receivables, "cash": cash, "debt": debt, "metrics": metrics}


def test_research_intelligence_surfaces_long_candidate_when_evidence_aligns():
    financials = [
        _row(1200, 180, 140, 155, 100, 90, 300, 100, {"revenue_growth_pct": 10.0, "operating_margin_pct": 15.0, "inventory_growth_pct": 2.0, "receivables_growth_pct": 4.0, "net_debt": -200}),
        _row(1090, 130, 110, 100, 98, 87, 250, 120, {"operating_margin_pct": 11.9}),
        _row(1000, 115, 95, 90, 95, 82, 220, 130, {"operating_margin_pct": 11.5}),
        _row(930, 100, 80, 75, 90, 80, 200, 140, {"operating_margin_pct": 10.8}),
    ]
    result = build_research_intelligence(financials, {"base": 75, "expected_value": 72, "current_price": 50}, market_price=50, valuation_quality="INTRINSIC")
    assert result["action"] == "BUY"; assert result["bias"] == "LONG"; assert result["confidence"] == "HIGH"; assert result["base_gap_pct"] == 50.0
    assert any(x["label"] == "Margin inflection" for x in result["signals"])


def test_research_intelligence_waits_when_data_quality_is_provisional():
    financials = [_row(1000, 80, 50, 20, 200, 180, 20, 300, {"revenue_growth_pct": -8.0, "operating_margin_pct": 8.0, "inventory_growth_pct": 20.0, "receivables_growth_pct": 18.0, "net_debt": 280})]
    result = build_research_intelligence(financials, {"base": 80, "expected_value": 75, "current_price": 40}, market_price=40, valuation_quality="PROVISIONAL_REFERENCE_FALLBACK", data_quality_issues=2)
    assert result["action"] == "WAIT"; assert result["warnings"]


def test_company_research_tabs_exclude_portfolio_risk_and_position():
    keys = [key for key, _ in SECTIONS]
    assert "risk" not in keys; assert "position" not in keys; assert "valuation" in keys; assert "historical-test" in keys


def test_016_version_and_login_assets(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "016", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '016.db'}", "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": False})
    assert app.config["VERSION"] == "0.1.6"
    page = app.test_client().get("/login").get_data(as_text=True)
    assert "v0.0.1" not in page; assert "Evidence first" not in page; assert "Invite-only" in page


def test_templates_compile_and_major_015_ui_surfaces_exist(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "015-ui", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '015-ui.db'}", "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": False})
    with app.app_context():
        for name in ("base.html", "company_section.html", "valuation_013.html", "historical_test_013.html", "financial_flows_013.html", "publication_preview.html", "publications.html", "published.html", "published_index.html", "settings.html"):
            app.jinja_env.get_template(name)
    company = Path("mfapp/templates/company_section.html").read_text(); valuation = Path("mfapp/templates/valuation_013.html").read_text(); historical = Path("mfapp/templates/historical_test_013.html").read_text(); preview = Path("mfapp/templates/publication_preview.html").read_text(); base = Path("mfapp/templates/base.html").read_text(); css = Path("mfapp/static/css/v015.css").read_text()
    assert "decision-brief" in company; assert "current_financial" in company; assert "management_engine" in company; assert "monitor_plan" in company; assert "journal_prefill" in company
    assert "valuation-model-wide" in valuation; assert "current_financial_basis" in valuation
    assert "mf-historical-chart" in historical; assert "Oldest replay" in historical; assert "Actual replay span" in historical
    assert 'name="visibility"' not in preview; assert "all invited members" in preview
    assert "mf-mobile-menu" in base; assert "v015.css" in base; assert "--primary:#3a6f99" in css


def test_normal_ui_hides_diagnostics_and_product_version_lives_in_settings():
    base = Path("mfapp/templates/base.html").read_text(); dashboard = Path("mfapp/templates/dashboard.html").read_text(); settings = Path("mfapp/templates/settings.html").read_text()
    assert "trace.console" not in base; assert "Diagnostics" not in base; assert "Web-native" not in dashboard; assert "mf_version" not in dashboard; assert "mf_version" in settings; assert "v{{ mf_version }}" in settings