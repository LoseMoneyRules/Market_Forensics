from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.autofill import AUTO_MARKER, prefill_coverage
from mfapp.core_models import Company, Coverage, FinancialPeriod, MarketSnapshot, NormalizedFinancial, Security, ValuationModel
from mfapp.data_providers import provider_status, set_secret
from mfapp.extensions import db
from mfapp.finra import daily_short_volume
from mfapp.formatting import format_number
from mfapp.models import User, UserPreference
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({"TESTING": True,"SECRET_KEY": "012-test","SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'v012.db'}","WTF_CSRF_ENABLED": False,"AUTO_MIGRATE": True})


def seed_company(app):
    with app.app_context():
        user = User(email="control012@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("abcdefghijklmnop"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.flush()
        company = Company(legal_name="Example Corp", display_name="Example", sector="Consumer", industry="Footwear")
        db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", is_primary=True, active=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        values = [
            (2023, 10_000_000_000, 900_000_000, 800_000_000, 1_000_000_000),
            (2024, 10_800_000_000, 1_020_000_000, 920_000_000, 1_000_000_000),
            (2025, 11_600_000_000, 1_150_000_000, 1_050_000_000, 1_000_000_000),
        ]
        for fy, revenue, net_income, fcf, shares in values:
            period = FinancialPeriod(company_id=company.id, period_type="FY", fiscal_year=fy, start_date=date(fy - 1, 7, 1), end_date=date(fy, 6, 30), currency="USD")
            db.session.add(period); db.session.flush()
            db.session.add(NormalizedFinancial(financial_period_id=period.id, revenue=Decimal(revenue), gross_profit=Decimal(revenue) * Decimal("0.44"), operating_income=Decimal(revenue) * Decimal("0.12"), net_income=Decimal(net_income), cfo=Decimal(fcf) + Decimal(300_000_000), capex=Decimal(300_000_000), fcf=Decimal(fcf), inventory=Decimal(2_000_000_000 + (fy - 2023) * 50_000_000), receivables=Decimal(1_100_000_000), payables=Decimal(900_000_000), cash=Decimal(2_000_000_000), debt=Decimal(1_200_000_000), equity=Decimal(5_000_000_000), diluted_shares=Decimal(shares), shares_outstanding=Decimal(shares), source_map={}, quality={"test": True}))
        db.session.add(MarketSnapshot(security_id=security.id, provider="TEST", price=Decimal("37.50"), currency="USD", as_of=datetime(2026, 9, 17, 12, 0), quality="OBSERVED", payload={}))
        db.session.commit(); return user.id, coverage.id


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id; session["view_as"] = "CONTROL"


def test_number_formats_are_display_only():
    assert format_number(12_345_678, "FULL") == "12,345,678"
    assert format_number(12_345_678, "M") == "12.3M"
    assert format_number(12_345_678_901, "B") == "12.3B"
    assert format_number(12_345_678_901, "AUTO") == "12.3B"


def test_display_preference_persists_per_user(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id, _ = seed_company(app); client = app.test_client(); login(client, user_id)
    response = client.post("/settings/display", data={"number_format": "B"}, follow_redirects=False); assert response.status_code in (302, 303)
    with app.app_context():
        row = UserPreference.query.filter_by(user_id=user_id, key="number_format").first(); assert row is not None; assert row.value["mode"] == "B"


def test_autofill_builds_coherent_scenarios_and_preserves_manual_override(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id, coverage_id = seed_company(app)
    with app.app_context():
        result = prefill_coverage(coverage_id, user_id)
        assert set(result["scenario_updates"]) == {"BEAR", "BASE", "BULL"}
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).first(); scenarios = {row.name: row for row in model.scenarios}
        bear = float(scenarios["BEAR"].equity_value_per_share); base = float(scenarios["BASE"].equity_value_per_share); bull = float(scenarios["BULL"].equity_value_per_share)
        assert 0 < bear <= base <= bull
        assert scenarios["BASE"].inputs["auto_prefill"] is True
        assert model.method == "MULTI_METHOD_INTRINSIC"
        assert model.assumptions["auto_draft"]["current_price_role"] == "COMPARISON_ONLY_NOT_AN_INPUT_TO_INTRINSIC_VALUE"
        assert model.coverage.research.numbers.startswith(AUTO_MARKER)
        scenarios["BASE"].equity_value_per_share = Decimal("77.77"); scenarios["BASE"].inputs = {"manual_value": 77.77}; db.session.commit()
        second = prefill_coverage(coverage_id, user_id); db.session.refresh(scenarios["BASE"])
        assert float(scenarios["BASE"].equity_value_per_share) == 77.77
        assert "BASE" not in second["scenario_updates"]


def test_finra_public_daily_requires_no_credential_and_api_status_is_optional(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id, _ = seed_company(app)
    with app.app_context():
        status = provider_status(user_id); assert status["finra"] is True; assert status["finra_api"] is False
        set_secret(user_id, "finra_client_id", "public-client"); set_secret(user_id, "finra_client_secret", "public-secret"); db.session.commit(); assert provider_status(user_id)["finra_api"] is True
    class Response:
        status_code = 200
        text = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20260917|EXM|300|20|1000|Q\n"
    monkeypatch.setattr("mfapp.finra.requests.get", lambda *args, **kwargs: Response())
    rows = daily_short_volume("EXM", lookback_days=1); assert rows; assert rows[-1]["short_volume"] == 300.0; assert rows[-1]["short_pct"] == 0.32
