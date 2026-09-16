import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import Company, FundamentalPeriod, MarketSnapshot, Publication, ResearchWorkspace, User
from mfapp.research_core import merged_payload
from mfapp.security import encrypt_secret, hash_password


def build_client():
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-key",
    })
    with app.app_context():
        db.create_all()
        user = User(email="control@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("temporary-test-value"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user)
        company = Company(ticker="NKE", name="NIKE, Inc.", status="RESEARCH", summary="Test research file")
        db.session.add(company)
        db.session.flush()
        payload = merged_payload({
            "business_summary": "Global athletic brand.",
            "numbers_summary": "Illustrative test evidence.",
            "expectations_summary": "Balanced.",
            "valuation_summary": "Three-case framework.",
            "bear_case": "Margin recovery fails.",
            "catalysts": "Product cycle and channel normalization.",
            "flows": "Monitor positioning.",
            "risk_notes": "Execution and China risk.",
            "monitoring_summary": "Watch revenue, margin and inventory.",
            "sources": "SEC filings.",
            "thesis": "Test thesis.",
            "invalidation": "Revenue decline beyond threshold.",
            "invalidation_locked": True,
            "position_size_plan": "Test only.",
            "bear_value": 30,
            "base_value": 45,
            "bull_value": 60,
            "model_confidence": "MODERATE",
        })
        db.session.add(ResearchWorkspace(company_id=company.id, payload=payload, updated_by=user.id))
        db.session.add(MarketSnapshot(company_id=company.id, provider="test", price=40, as_of=datetime.now(timezone.utc).replace(tzinfo=None), quality="OBSERVED", payload={}))
        db.session.add(FundamentalPeriod(
            company_id=company.id, period_key="FY2025", period_type="FY", fiscal_year=2025,
            period_end="2025-05-31", revenue=50000, gross_profit=22000, operating_income=6000,
            flow_operating_income=6000, flow_operating_income_method="REPORTED_OPERATING_INCOME",
            pretax=5700, tax=900, net_income=4800, cfo=6500, capex=900, fcf=5600,
            buybacks=1800, dividends=2200, source="SEC Companyfacts", provenance={"test": True},
        ))
        db.session.commit()
        user_id, company_id = user.id, company.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"
    return app, client, company_id


def test_control_workflow_pages_render():
    _app, client, _company_id = build_client()

    # In 0.0.4 the authenticated CONTROL root intentionally hands off to the FULL workstation.
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302
    assert "/workstation" in root.location

    # Retained 0.0.2 compatibility endpoints must still render for old bookmarks/regression coverage.
    urls = [
        "/discover",
        "/decision-queue",
        "/decide/NKE",
        "/research/NKE",
        "/research/NKE/financial-flows?year=2025",
        "/flows/NKE",
        "/monitor/NKE",
        "/validate/NKE",
        "/portfolio",
        "/settings",
        "/control",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code, response.data[:300])


def test_financial_flows_page_uses_v312_engine():
    _app, client, _company_id = build_client()
    response = client.get("/research/NKE/financial-flows?year=2025")
    assert response.status_code == 200
    assert b"ENGINE 3.1.12" in response.data
    assert b"Revenue" in response.data
    assert b"Cash Flow" in response.data


def test_control_can_preview_published_friend_and_insider_views():
    app, client, company_id = build_client()
    with app.app_context():
        db.session.add(Publication(company_id=company_id, version=1, visibility="FRIEND", payload={
            "business": "Published business",
            "numbers": "Published numbers",
            "valuation": "Published valuation",
            "catalysts": "Published catalysts",
            "risk": "Published risk",
            "expectations": "Insider expectations",
            "bear_case": "Insider bear case",
            "flows": "Insider flows",
            "monitoring": "Insider monitoring",
            "sources": "Published sources",
        }, is_current=True, model_version="0.0.2"))
        db.session.commit()

    assert client.post("/view-as", data={"role": "FRIEND"}).status_code == 302
    friend = client.get("/company/NKE")
    assert friend.status_code == 200
    assert b"Published business" in friend.data
    assert b"Insider expectations" not in friend.data

    assert client.post("/view-as", data={"role": "INSIDER"}).status_code == 302
    insider = client.get("/company/NKE")
    assert insider.status_code == 200
    assert b"Insider expectations" in insider.data
