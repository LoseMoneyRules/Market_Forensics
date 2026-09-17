from __future__ import annotations

from datetime import datetime

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp import data_providers
from mfapp.core_models import Company, Coverage, Job, Security
from mfapp.data_providers import QuoteResult
from mfapp.extensions import db
from mfapp.historical_engine import annual_history_asof
from mfapp.jobs import enqueue_job, run_jobs
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace
from mfapp.valuation_engine import default_cases, evaluate, metrics_from_history, robust_blend


def build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({"TESTING": True, "SECRET_KEY": "013-test", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '013.db'}", "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": True})


def seed(app):
    with app.app_context():
        user = User(email="control013@example.com", display_name="Control", role="CONTROL", password_hash=hash_password("abcdefghijklmnop"), totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True)
        db.session.add(user); db.session.flush()
        company = Company(legal_name="Example Inc", display_name="Example", sector="Consumer Cyclical", industry="Footwear")
        db.session.add(company); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", is_primary=True, active=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id); db.session.commit()
        return user.id, company.id, security.id, coverage.id


def test_intrinsic_value_is_not_market_anchored():
    history = [
        {"fiscal_year": 2023, "revenue": 1000, "net_income": 100, "fcf": 90, "cash": 100, "debt": 50, "shares_outstanding": 100},
        {"fiscal_year": 2024, "revenue": 1100, "net_income": 110, "fcf": 100, "cash": 120, "debt": 50, "shares_outstanding": 100},
        {"fiscal_year": 2025, "revenue": 1200, "net_income": 120, "fcf": 108, "cash": 140, "debt": 50, "shares_outstanding": 100},
    ]
    metrics = metrics_from_history(history)
    policy = default_cases(metrics, "Consumer / Brand")
    at_50 = evaluate(metrics, policy, policy["weights"], 5, current_price=50)
    at_500 = evaluate(metrics, policy, policy["weights"], 5, current_price=500)
    for name in ("BEAR", "BASE", "BULL"):
        assert at_50["scenarios"][name]["fair_value"] == at_500["scenarios"][name]["fair_value"]
    assert at_50["expected_value"] == at_500["expected_value"]
    assert at_50["scenarios"]["BASE"]["gap_pct"] != at_500["scenarios"]["BASE"]["gap_pct"]


def test_missing_share_basis_blocks_targets_instead_of_using_market_price():
    history = [{"fiscal_year": 2025, "revenue": 1000, "net_income": 100, "fcf": 80, "cash": 50, "debt": 20}]
    metrics = metrics_from_history(history)
    policy = default_cases(metrics, "Generic")
    result = evaluate(metrics, policy, policy["weights"], 5, current_price=123.45)
    assert metrics["basis_usable"] is False
    assert all(result["scenarios"][name]["fair_value"] is None for name in ("BEAR", "BASE", "BULL"))


def test_robust_blend_downweights_method_outlier():
    value, weights, flags = robust_blend({"pe": 50, "ev_sales": 52, "fcf_yield": 150}, {"pe": .4, "ev_sales": .25, "fcf_yield": .35})
    assert value is not None
    assert weights["fcf_yield"] < .20
    assert flags


def test_sec_point_in_time_history_excludes_future_filing():
    companyfacts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"fy": 2023, "fp": "FY", "form": "10-K", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "accn": "old", "val": 100},
                            {"fy": 2023, "fp": "FY", "form": "10-K/A", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-06-01", "accn": "future", "val": 150},
                        ]
                    }
                }
            }
        }
    }
    feb = annual_history_asof(companyfacts, datetime.fromisoformat("2024-02-15").date())
    july = annual_history_asof(companyfacts, datetime.fromisoformat("2024-07-01").date())
    assert feb[-1]["revenue"] == 100
    assert july[-1]["revenue"] == 150
    assert feb[-1]["filed_at"] == "2024-02-01"


def test_quote_consensus_rejects_material_provider_disagreement(monkeypatch):
    now = datetime.utcnow()
    monkeypatch.setattr(data_providers, "_alpaca", lambda ticker, uid: QuoteResult(True, "Alpaca", 100, as_of=now, quality="OBSERVED"))
    monkeypatch.setattr(data_providers, "_tiingo", lambda ticker, uid: QuoteResult(True, "Tiingo", 120, as_of=now, quality="OBSERVED"))
    monkeypatch.setattr(data_providers, "_alpha_vantage", lambda ticker, uid: QuoteResult(False, "Alpha", message="off"))
    monkeypatch.setattr(data_providers, "_public_chart", lambda ticker: QuoteResult(True, "Public", 101, as_of=now, quality="PUBLIC_FALLBACK"))
    result = data_providers.fetch_quote("EXM", 1)
    assert result.ok is False
    assert "disagreement" in result.message.lower()


def test_historical_job_runs_through_normal_queue(tmp_path, monkeypatch):
    app = build_app(tmp_path, monkeypatch); user_id, company_id, security_id, coverage_id = seed(app)
    monkeypatch.setattr("mfapp.jobs.run_historical_test", lambda coverage_id, user_id, lookback: {"run_id": 7, "status": "VALIDATED", "sample_size": 5})
    monkeypatch.setattr("mfapp.jobs.prefill_coverage", lambda coverage_id, user_id: {"coverage_id": coverage_id})
    with app.app_context():
        job = enqueue_job("HISTORICAL_TEST", user_id=user_id, company_id=company_id, security_id=security_id, payload={"coverage_id": coverage_id, "lookback_years": 10}, priority=1)
        job_id = job.id
        result = run_jobs(limit=1, user_id=user_id)
        assert result[0]["status"] == "DONE"
        stored = db.session.get(Job, job_id)
        assert stored.status == "DONE"
        assert stored.result["run_id"] == 7
