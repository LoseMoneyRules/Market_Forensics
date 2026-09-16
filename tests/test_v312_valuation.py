import math
import os

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfengine.v312.valuation_core import dcf_value, pe_value, robust_blend, scenario_values
from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import Company, FundamentalPeriod, MarketSnapshot, ResearchWorkspace, User
from mfapp.research_core import merged_payload
from mfapp.security import encrypt_secret, hash_password


def test_v312_pe_formula():
    value = pe_value(1000, .10, .10, 20, 100)
    assert math.isclose(value, 22.0, rel_tol=1e-12)


def test_v312_dcf_does_not_subtract_net_debt_twice():
    a = dcf_value(1000, .05, .10, .10, .025, 5, 0, 100)
    b = dcf_value(1000, .05, .10, .10, .025, 5, 999999, 100)
    assert math.isclose(a, b, rel_tol=1e-12)


def test_robust_blend_reduces_large_cross_method_outlier():
    blend, weights, flags = robust_blend({"pe": 50, "ev_sales": 52, "fcf_yield": 120}, {"pe": .4, "ev_sales": .3, "fcf_yield": .3})
    assert blend is not None
    assert weights["fcf_yield"] < .3
    assert flags


def test_share_basis_gate_blocks_all_current_targets():
    cases = {name: {"growth": .05, "net_margin": .10, "fcf_margin": .08, "pe": 20, "ev_sales": 2, "target_fcf_yield": .05, "equity_discount_rate": .10, "terminal_growth": .025, "prob": prob} for name, prob in (("bear", .25), ("base", .5), ("bull", .25))}
    out, ev = scenario_values({"revenue": 1000, "net_debt": 0, "valuation_shares": 100, "valuation_share_basis_usable": False}, cases)
    assert ev is None
    assert all(row["fair_value"] is None for row in out.values())
    assert all(row["flags"][0].startswith("DATA REVIEW") for row in out.values())


def _app_client():
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test-key"})
    with app.app_context():
        db.create_all()
        user = User(email="control@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("temporary-test-value"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        company = Company(ticker="NKE", name="NIKE, Inc.", status="RESEARCH", summary="test")
        db.session.add_all([user, company]); db.session.flush()
        db.session.add(ResearchWorkspace(company_id=company.id, payload=merged_payload({}), updated_by=user.id))
        db.session.add(MarketSnapshot(company_id=company.id, provider="test", price=40, quality="OBSERVED", payload={}))
        db.session.add(FundamentalPeriod(company_id=company.id, period_key="FY2025", period_type="FY", fiscal_year=2025, revenue=50000, operating_income=6000, pretax=5800, tax=900, net_income=4900, cfo=6500, capex=1000, fcf=5500, cash=6000, debt=9000, diluted_shares=1500, source="SEC Companyfacts", provenance={}))
        db.session.commit(); uid=user.id
    client=app.test_client()
    with client.session_transaction() as s:
        s["user_id"]=uid; s["view_as"]="CONTROL"
    return app, client


def test_valuation_route_writes_v312_cases_into_decide_workspace():
    app, client = _app_client()
    response = client.post("/research/NKE/valuation", data={
        "company_type": "Consumer / Brand",
        "share_source": "USER_VERIFIED_CURRENT",
        "current_shares": "1500",
        "share_basis_verified": "1",
        "share_basis_note": "verified test basis",
        "horizon_years": "5",
        "bear_growth": "0.00", "base_growth": "0.05", "bull_growth": "0.10",
        "bear_net_margin": "0.07", "base_net_margin": "0.10", "bull_net_margin": "0.13",
        "bear_fcf_margin": "0.06", "base_fcf_margin": "0.10", "bull_fcf_margin": "0.14",
        "bear_pe": "15", "base_pe": "21", "bull_pe": "27",
        "bear_ev_sales": "0.9", "base_ev_sales": "1.8", "bull_ev_sales": "3.0",
        "bear_target_fcf_yield": "0.07", "base_target_fcf_yield": "0.05", "bull_target_fcf_yield": "0.035",
        "bear_equity_discount_rate": "0.11", "base_equity_discount_rate": "0.10", "bull_equity_discount_rate": "0.09",
        "bear_terminal_growth": "0.015", "base_terminal_growth": "0.025", "bull_terminal_growth": "0.03",
        "bear_prob": "0.25", "base_prob": "0.50", "bull_prob": "0.25",
        "weight_pe": "0.4", "weight_ev_sales": "0.25", "weight_fcf_yield": "0.35",
    }, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        ws = ResearchWorkspace.query.join(Company).filter(Company.ticker == "NKE").first()
        assert ws.payload["v312_valuation"]["share_basis_verified"] is True
        assert ws.payload["bear_value"] is not None
        assert ws.payload["base_value"] is not None
        assert ws.payload["bull_value"] is not None
        assert "V3.1.12" in ws.payload["valuation_method"]
    page = client.get("/research/NKE/valuation")
    assert page.status_code == 200
    assert b"ENGINE 3.1.12" in page.data
