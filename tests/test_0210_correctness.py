from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from mfapp import create_app
from mfapp.core_models import (
    Catalyst, Company, Coverage, Event, HistoricalTestRun, MarketSnapshot,
    ResearchState, RiskPlan, Security, Source, ValuationModel,
)
from mfapp.decision_engine import build_research_intelligence
from mfapp.decision_lenses import build_decision_lenses
from mfapp.discovery_forensics import _local_forensics
from mfapp.extensions import db
from mfapp.models import User
from mfapp.readiness import research_readiness
from mfapp.services import ensure_workspace, valuation_result
from mfapp.validation_policy import state_for_run, validation_payload, validation_state
from mfapp.valuation_engine import evaluate


def make_app(tmp_path, name="0210"):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "0210-release",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_workspace(app, ticker="TST"):
    with app.app_context():
        db.create_all()
        user = User(
            email=f"{ticker.lower()}0210@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash="test",
            totp_secret_enc="test",
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        company = Company(
            legal_name="Correctness Test Co",
            display_name="Correctness Test Co",
            sector="Industrials",
            industry="Machinery",
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
        db.session.add(MarketSnapshot(
            security_id=security.id,
            provider="TEST",
            price=Decimal("100"),
            currency="USD",
            as_of=datetime(2026, 9, 18, 12, 0, 0),
            quality="OBSERVED",
        ))
        db.session.commit()
        return user.id, company.id, security.id, coverage.id


def _seed_management_original(company_id, metric, year, value, period_end=None, filed_at=None):
    period_end = period_end or f"{year}-12-31"
    filed_at = filed_at or f"{year + 1}-02-01"
    db.session.add(Event(
        company_id=company_id,
        event_type="MANAGEMENT_ACTUAL_ORIGINAL",
        title=f"{metric} original actual FY{year}",
        event_date=datetime.fromisoformat(filed_at),
        payload={
            "metric": metric,
            "fiscal_year": year,
            "value": value,
            "period_type": "FY",
            "period_end": period_end,
            "filed_at": filed_at,
            "source_accession": f"orig-{metric}-{year}",
            "source_provider": "SEC_COMPANYFACTS",
            "point_in_time_original": True,
            "actual_version": "1",
        },
    ))
    db.session.commit()


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def _readiness(state="VALIDATED"):
    return {
        "ready_to_validate": True,
        "gates": [],
        "validation": {"state": state, "samples": 5, "reliability": 70.0},
    }


def test_0210_reference_fallback_target_visible_but_cannot_create_edge(tmp_path, monkeypatch):
    app = make_app(tmp_path, "valuation_fail_closed")
    _, company_id, _, coverage_id = seed_workspace(app, "VAL")
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        company = db.session.get(Company, company_id)
        research = ResearchState.query.filter_by(coverage_id=coverage_id).first()
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).first()
        market = MarketSnapshot.query.filter_by(security_id=coverage.security_id).first()
        research.variant_market = "Market expects weak execution."
        research.variant_us = "Evidence can improve faster than expected."
        research.variant_evidence = "Filed operating evidence supports the variant."
        db.session.add(Catalyst(
            coverage_id=coverage_id,
            title="Execution proof",
            direction="POSITIVE",
            status="OPEN",
            evidence="Filed results",
        ))
        db.session.commit()

        monkeypatch.setattr(
            "mfapp.decision_lenses.price_implied_expectations",
            lambda *args, **kwargs: {"classification": "BALANCED", "available": True},
        )
        valuation = {
            "current_price": 100.0,
            "bear": 85.0,
            "base": 140.0,
            "bull": 170.0,
            "expected_value": 133.75,
            "quality": "PROVISIONAL_REFERENCE_FALLBACK",
            "base_quality": "PROVISIONAL_REFERENCE_FALLBACK",
        }
        intelligence = build_research_intelligence(
            [],
            valuation,
            market_price=100.0,
            valuation_quality=valuation["base_quality"],
            readiness=_readiness(),
        )
        lenses = build_decision_lenses(
            coverage=coverage,
            company=company,
            research=research,
            risk=risk,
            model=model,
            market=market,
            valuation=valuation,
            intelligence={**intelligence, "confidence": "HIGH", "warnings": []},
            readiness=_readiness(),
            management={},
            tape={"metrics": {"regime": "ACCUMULATION"}},
        )

        assert valuation["bear"] == 85.0 and valuation["base"] == 140.0 and valuation["bull"] == 170.0
        assert intelligence["base_gap_pct"] == 40.0
        assert not intelligence["valuation_decision_grade"]
        assert not any(row["label"] == "Valuation gap" and row["weight"] != 0 for row in intelligence["signals"])
        assert lenses["value"] == "UNVERIFIED"
        assert lenses["variant"] not in {"POSITIVE EDGE", "NEGATIVE EDGE"}
        assert lenses["research_conclusion"] == "DATA REVIEW"


def test_0210_intrinsic_base_still_drives_value_variant_and_conclusion(tmp_path, monkeypatch):
    app = make_app(tmp_path, "valuation_intrinsic")
    _, company_id, _, coverage_id = seed_workspace(app, "INT")
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        company = db.session.get(Company, company_id)
        research = ResearchState.query.filter_by(coverage_id=coverage_id).first()
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).first()
        market = MarketSnapshot.query.filter_by(security_id=coverage.security_id).first()
        research.variant_market = "Market expects weak execution."
        research.variant_us = "Filed evidence supports recovery."
        research.variant_evidence = "Variant evidence."
        db.session.add(Catalyst(
            coverage_id=coverage_id,
            title="Positive catalyst",
            direction="POSITIVE",
            status="OPEN",
            evidence="Evidence",
        ))
        db.session.commit()
        monkeypatch.setattr(
            "mfapp.decision_lenses.price_implied_expectations",
            lambda *args, **kwargs: {"classification": "BALANCED", "available": True},
        )
        valuation = {
            "current_price": 100.0,
            "bear": 90.0,
            "base": 140.0,
            "bull": 175.0,
            "expected_value": 136.25,
            "quality": "INTRINSIC",
            "base_quality": "INTRINSIC",
        }
        intelligence = build_research_intelligence(
            [],
            valuation,
            market_price=100.0,
            valuation_quality="INTRINSIC",
            readiness=_readiness(),
        )
        lenses = build_decision_lenses(
            coverage=coverage,
            company=company,
            research=research,
            risk=risk,
            model=model,
            market=market,
            valuation=valuation,
            intelligence={**intelligence, "confidence": "HIGH", "warnings": []},
            readiness=_readiness(),
            management={},
            tape={"metrics": {"regime": "ACCUMULATION"}},
        )
        assert intelligence["valuation_decision_grade"]
        assert lenses["value"] == "ATTRACTIVE"
        assert lenses["variant"] == "POSITIVE EDGE"
        assert lenses["research_conclusion"] == "LONG READY"


def test_0210_valuation_engine_reference_fallback_is_explicit_and_visible():
    cases = {
        "BEAR": {"probability": .25},
        "BASE": {"probability": .50},
        "BULL": {"probability": .25},
    }
    result = evaluate(
        {"basis_usable": False, "data_warnings": []},
        cases,
        {"pe": 1.0, "ev_sales": 0.0, "fcf_yield": 0.0},
        current_price=100.0,
    )
    assert result["quality"] == "PROVISIONAL_REFERENCE_FALLBACK"
    assert all(result["scenarios"][name]["fair_value"] is not None for name in ("BEAR", "BASE", "BULL"))
    assert result["scenarios"]["BASE"]["quality"] == "REFERENCE_PRICE_FALLBACK"


def test_0210_discovery_rejects_provisional_covered_base(monkeypatch):
    annual = [
        {"fiscal_year": 2024, "revenue": 100.0, "operating_income": 10.0, "net_income": 8.0, "fcf": 7.0, "inventory": 12.0, "receivables": 10.0},
        {"fiscal_year": 2025, "revenue": 110.0, "operating_income": 12.0, "net_income": 9.0, "fcf": 9.0, "inventory": 12.0, "receivables": 10.0},
    ]
    monkeypatch.setattr("mfapp.discovery_forensics.quarterly_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr("mfapp.discovery_forensics.annual_rows", lambda *args, **kwargs: list(reversed(annual)))

    provisional = {
        "company_id": 1,
        "base_gap_pct": 40.0,
        "valuation": {"base": 140.0, "base_quality": "PROVISIONAL_REFERENCE_FALLBACK"},
    }
    intrinsic = {
        "company_id": 1,
        "base_gap_pct": 40.0,
        "valuation": {"base": 140.0, "base_quality": "INTRINSIC"},
    }
    assert _local_forensics(provisional, 100.0, -5.0) is None
    assert _local_forensics(intrinsic, 100.0, -5.0) is not None


def test_0210_management_parser_keeps_incompatible_promises_evidence_only(tmp_path, monkeypatch):
    import mfapp.management_promises as mp

    app = make_app(tmp_path, "management_noncompare")
    _, company_id, _, _ = seed_workspace(app, "MGT")
    cases = [
        "Management expects Q2 2027 revenue growth of 8% to 10%.",
        "Management expects FY2027 adjusted operating margin of 18% to 20%.",
        "Management expects FY2027 free cash flow of $2.0 billion to $2.2 billion.",
    ]
    with app.app_context():
        for sentence in cases:
            parsed = mp.extract_promises(sentence)
            assert len(parsed) == 1
            mp.store_promises(company_id, parsed)
        monkeypatch.setattr(mp, "annual_rows", lambda company_id, limit=20: [{
            "fiscal_year": 2027,
            "period_type": "FY",
            "period_end": "2027-12-31",
            "revenue": 120_000_000_000.0,
            "fcf": 2_100_000_000.0,
            "metrics": {"revenue_growth_pct": 9.0, "operating_margin_pct": 19.0},
        }])
        rows = mp.evaluate_promises(company_id)
        assert len(rows) == 3
        assert {row["status"] for row in rows} == {"EVIDENCE_ONLY"}
        assert all(row["comparability"] == "NON_COMPARABLE" for row in rows)


def test_0210_management_comparable_guidance_scores_and_comparator_year_is_not_target(tmp_path, monkeypatch):
    import mfapp.management_promises as mp

    app = make_app(tmp_path, "management_compare")
    _, company_id, _, _ = seed_workspace(app, "CMP")
    met = "Compared with 2025, management expects FY2027 revenue growth of 8% to 10%."
    miss = "Management expects FY2028 revenue growth of 12% to 14%."
    with app.app_context():
        p1 = mp.extract_promises(met)
        p2 = mp.extract_promises(miss)
        assert p1[0]["target_year"] == 2027
        assert p1[0]["target_period_type"] == "FY"
        mp.store_promises(company_id, p1 + p2)
        _seed_management_original(company_id, "revenue_growth_pct", 2027, 9.0)
        _seed_management_original(company_id, "revenue_growth_pct", 2028, 7.0)
        rows = mp.evaluate_promises(company_id)
        by_year = {row["target_year"]: row for row in rows}
        assert by_year[2027]["status"] == "MET"
        assert by_year[2028]["status"] == "MISS"
        assert by_year[2027]["comparability"] == "COMPARABLE"


def test_0210_management_retroactive_guidance_is_not_scored(tmp_path, monkeypatch):
    import mfapp.management_promises as mp

    app = make_app(tmp_path, "management_retro")
    _, company_id, _, _ = seed_workspace(app, "RET")
    with app.app_context():
        source = Source(
            company_id=company_id,
            provider="SEC",
            source_type="FILING",
            title="RET 10-K 2028-02-15",
            accession_no="000-test",
            published_at=datetime(2028, 2, 15),
            retrieved_at=datetime(2028, 2, 15),
            meta={"form": "10-K"},
        )
        db.session.add(source)
        db.session.commit()
        parsed = mp.extract_promises("Management expects FY2027 revenue growth of 8% to 10%.", source_id=source.id)
        mp.store_promises(company_id, parsed, source_id=source.id)
        _seed_management_original(company_id, "revenue_growth_pct", 2027, 9.0)
        row = mp.evaluate_promises(company_id)[0]
        assert row["status"] == "EVIDENCE_ONLY"
        assert row["comparability_reason"] == "GUIDANCE_PUBLISHED_AFTER_TARGET_PERIOD"
        assert row["source_date"] == "2028-02-15"



def test_0210_management_parser_recovers_declines_eps_and_qualitative_guidance():
    import mfapp.management_promises as mp

    decline = mp.extract_promises("Management expects FY2027 revenue decline of 8% to 10%.")
    assert len(decline) == 1
    assert decline[0]["metric"] == "revenue_growth_pct"
    assert decline[0]["low"] == -10.0
    assert decline[0]["high"] == -8.0

    eps = mp.extract_promises("Management expects FY2027 GAAP diluted EPS of $3.20 to $3.40.")
    assert len(eps) == 1
    assert eps[0]["metric"] == "eps"
    assert eps[0]["unit"] == "USD/share"
    assert eps[0]["comparability"] == "COMPARABLE"

    adjusted = mp.extract_promises("Management expects FY2027 adjusted EPS of $3.20 to $3.40.")
    assert adjusted[0]["comparability"] == "NON_COMPARABLE"
    assert adjusted[0]["comparability_reason"] == "BASIS_NOT_CANONICAL"

    qualitative = mp.extract_promises(
        "Management expects FY2027 revenue to decline in the low-single-digits."
    )
    assert len(qualitative) == 1
    assert qualitative[0]["operator"] == "QUALITATIVE"
    assert qualitative[0]["status"] if "status" in qualitative[0] else True
    assert qualitative[0]["comparability"] == "NON_COMPARABLE"
    assert qualitative[0]["target_text"]


def test_0210_management_original_actual_uses_earliest_public_filing_not_restated_comparative():
    import mfapp.management_promises as mp

    companyfacts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [
                        {"val": 100.0, "start": "2026-01-01", "end": "2026-12-31", "filed": "2027-02-01", "form": "10-K", "fp": "FY", "accn": "orig-2026"},
                        {"val": 110.0, "start": "2027-01-01", "end": "2027-12-31", "filed": "2028-02-01", "form": "10-K", "fp": "FY", "accn": "orig-2027"},
                        {"val": 112.0, "start": "2027-01-01", "end": "2027-12-31", "filed": "2029-02-01", "form": "10-K", "fp": "FY", "accn": "later-restatement"},
                    ]}
                },
                "EarningsPerShareDiluted": {
                    "units": {"USD/shares": [
                        {"val": 3.25, "start": "2027-01-01", "end": "2027-12-31", "filed": "2028-02-01", "form": "10-K", "fp": "FY", "accn": "orig-2027"},
                        {"val": 3.40, "start": "2027-01-01", "end": "2027-12-31", "filed": "2029-02-01", "form": "10-K", "fp": "FY", "accn": "later-restatement"},
                    ]}
                },
            }
        }
    }
    rows = mp.original_actuals_from_companyfacts(companyfacts, "1231")
    revenue = next(row for row in rows if row["metric"] == "revenue" and row["fiscal_year"] == 2027)
    eps = next(row for row in rows if row["metric"] == "eps" and row["fiscal_year"] == 2027)
    growth = next(row for row in rows if row["metric"] == "revenue_growth_pct" and row["fiscal_year"] == 2027)
    assert revenue["value"] == 110.0
    assert revenue["source_accession"] == "orig-2027"
    assert eps["value"] == 3.25
    assert eps["source_accession"] == "orig-2027"
    assert round(growth["value"], 6) == 10.0
    assert growth["point_in_time_original"] is True


def test_0210_management_scan_reads_8k_exhibit_and_old_zero_marker_does_not_block(tmp_path, monkeypatch):
    import mfapp.jobs as jobs
    import mfapp.management_promises as mp

    app = make_app(tmp_path, "management_exhibit_scan")
    user_id, company_id, security_id, coverage_id = seed_workspace(app, "EXH")

    class FakeResponse:
        def __init__(self, status_code=200, text="", payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload or {}
        def json(self):
            return self._payload

    with app.app_context():
        company = db.session.get(Company, company_id)
        security = db.session.get(Security, security_id)
        source = Source(
            company_id=company_id,
            provider="SEC",
            source_type="FILING",
            title="EXH 8-K 2026-09-18",
            url="https://www.sec.gov/primary.htm",
            accession_no="0000000000-26-000001",
            published_at=datetime(2026, 9, 18),
            retrieved_at=datetime(2026, 9, 18),
            meta={"form": "8-K"},
        )
        db.session.add(source)
        db.session.flush()
        db.session.add(Event(
            company_id=company_id,
            source_id=source.id,
            event_type="MANAGEMENT_GUIDANCE_SCAN",
            title="legacy empty scan",
            event_date=datetime(2026, 9, 18),
            payload={"form": "8-K", "accession_no": source.accession_no, "promises_found": 0, "promises_stored": 0},
        ))
        db.session.commit()

        monkeypatch.setattr(jobs, "sec_ticker_meta", lambda ticker, ua: {
            "cik": "0000000123", "fiscal_year_end": "1231"
        })
        submissions = {
            "filings": {"recent": {
                "form": ["8-K"],
                "accessionNumber": ["0000000000-26-000001"],
                "filingDate": ["2026-09-18"],
                "primaryDocument": ["primary.htm"],
            }}
        }
        monkeypatch.setattr(jobs, "sec_json", lambda url, ua: {"facts": {}} if "companyfacts" in url else submissions)
        monkeypatch.setattr(jobs, "sec_user_agent", lambda uid: "Market Forensics test@example.com")

        def fake_get(url, **kwargs):
            if url.endswith("/index.json"):
                return FakeResponse(payload={"directory": {"item": [
                    {"name": "primary.htm"},
                    {"name": "ex991.htm"},
                ]}})
            if url.endswith("/primary.htm"):
                return FakeResponse(text="<html><body>Item 2.02 Results of Operations.</body></html>")
            if url.endswith("/ex991.htm"):
                return FakeResponse(text="<html><body>Management expects FY2027 revenue growth of 8% to 10%.</body></html>")
            return FakeResponse(status_code=404)

        monkeypatch.setattr(jobs.requests, "get", fake_get)

        result = jobs._management_scan(company, security, user_id, 10)
        assert result["scan_version"] == mp.MANAGEMENT_SCAN_VERSION
        assert result["guidance_filings_scanned"] == 1
        assert result["documents_scanned"] == 2
        assert result["promises_found"] == 1
        assert result["promises_stored"] == 1

        promises = Event.query.filter_by(company_id=company_id, event_type="MANAGEMENT_PROMISE").all()
        assert len(promises) == 1
        assert (promises[0].payload or {}).get("document_name") == "ex991.htm"

        skipped = jobs._management_scan(company, security, user_id, 10)
        assert skipped["guidance_filings_scanned"] == 0
        assert skipped["skipped_current_version"] == 1

        forced = jobs._management_scan(company, security, user_id, 10, force=True)
        assert forced["guidance_filings_scanned"] == 1
        assert forced["promises_stored"] == 0


def test_0210_management_only_original_actual_can_produce_met_miss(tmp_path, monkeypatch):
    import mfapp.management_promises as mp

    app = make_app(tmp_path, "management_original_gate")
    _, company_id, _, _ = seed_workspace(app, "OAC")
    with app.app_context():
        promise = mp.extract_promises("Management expects FY2027 revenue growth of 8% to 10%.")
        mp.store_promises(company_id, promise)
        monkeypatch.setattr(mp, "annual_rows", lambda company_id, limit=20: [{
            "fiscal_year": 2027,
            "period_type": "FY",
            "period_end": "2027-12-31",
            "metrics": {"revenue_growth_pct": 9.0},
        }])
        first = mp.evaluate_promises(company_id)[0]
        assert first["status"] == "EVIDENCE_ONLY"
        assert first["comparability_reason"] == "ORIGINAL_ACTUAL_NOT_VERIFIED"

        _seed_management_original(company_id, "revenue_growth_pct", 2027, 9.0)
        second = mp.evaluate_promises(company_id)[0]
        assert second["status"] == "MET"
        assert second["actual_provenance"]["point_in_time_original"] is True


def test_0210_validation_policy_is_single_and_conservative():
    assert validation_state(exists=False) == "NOT RUN"
    assert validation_payload(None)["state"] == "NOT RUN"
    assert validation_payload(None)["status"] == "NOT RUN"
    from mfapp.routes import _fallback_readiness
    assert _fallback_readiness()["validation"]["state"] == "NOT RUN"
    assert _fallback_readiness()["validation"]["status"] == "NOT RUN"
    assert validation_state(exists=True, sample_size=3, reliability=90) == "LIMITED"
    assert validation_state(exists=True, sample_size=5, reliability=64.99) == "LIMITED"
    assert validation_state(exists=True, sample_size=5, reliability=65) == "VALIDATED"
    assert validation_state(exists=True, sample_size=5, reliability=39.9) == "REVIEW"


def test_0210_old_run_status_is_canonicalized_in_readiness_and_validate_page(tmp_path):
    app = make_app(tmp_path, "validation_e2e")
    user_id, _, _, coverage_id = seed_workspace(app, "RUN")
    with app.app_context():
        run = HistoricalTestRun(
            coverage_id=coverage_id,
            user_id=user_id,
            status="VALIDATED",
            lookback_years=10,
            sample_size=3,
            reliability_score=Decimal("90"),
            finished_at=datetime(2026, 9, 18, 12, 0, 0),
        )
        db.session.add(run)
        db.session.commit()
        assert state_for_run(run) == "LIMITED"
        readiness = research_readiness(db.session.get(Coverage, coverage_id))
        assert readiness["validation"]["state"] == "LIMITED"
        assert readiness["validation"]["status"] == "LIMITED"
        assert readiness["validation"]["policy"]["min_valid_samples"] == 5
        assert readiness["validation"]["policy"]["validated_reliability"] == 65.0

    client = app.test_client()
    login(client, user_id)
    response = client.get("/company/RUN/validate")
    assert response.status_code == 200
    assert b"LIMITED" in response.data


def test_0210_stored_base_quality_survives_valuation_result(tmp_path):
    app = make_app(tmp_path, "quality_provenance")
    _, _, _, coverage_id = seed_workspace(app, "QLT")
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).first()
        rows = {row.name.upper(): row for row in model.scenarios}
        for name, value in (("BEAR", 80), ("BASE", 125), ("BULL", 160)):
            rows[name].equity_value_per_share = Decimal(str(value))
            rows[name].outputs = {"fair_value": value, "quality": "REFERENCE_PRICE_FALLBACK"}
        model.assumptions = dict(model.assumptions or {}) | {
            "latest_engine_result": {"quality": "PROVISIONAL_REFERENCE_FALLBACK"}
        }
        db.session.commit()
        result = valuation_result(coverage)
        assert result["base"] == 125.0
        assert result["base_quality"] == "PROVISIONAL_REFERENCE_FALLBACK"
        assert result["decision_grade"] is False


def test_0210_discovery_manual_override_is_not_intrinsic_candidate(monkeypatch):
    annual = [
        {"fiscal_year": 2024, "revenue": 100.0, "operating_income": 10.0, "net_income": 8.0, "fcf": 7.0, "inventory": 12.0, "receivables": 10.0},
        {"fiscal_year": 2025, "revenue": 110.0, "operating_income": 12.0, "net_income": 9.0, "fcf": 9.0, "inventory": 12.0, "receivables": 10.0},
    ]
    monkeypatch.setattr("mfapp.discovery_forensics.quarterly_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr("mfapp.discovery_forensics.annual_rows", lambda *args, **kwargs: list(reversed(annual)))
    context = {
        "company_id": 1,
        "base_gap_pct": 40.0,
        "valuation": {"base": 140.0, "base_quality": "MANUAL_OVERRIDE"},
    }
    assert _local_forensics(context, 100.0, -5.0) is None


def test_0210_discovery_labels_require_intrinsic_quality():
    from mfapp.discovery_engine import classify_coverage

    readiness = {"done": 13, "total": 13}
    common = {
        "base_gap_pct": 35.0,
        "bias": "LONG",
        "stance": "ATTRACTIVE",
        "confidence": "HIGH",
        "negatives": 0,
        "positives": 3,
    }
    assert "LONG DISLOCATION" not in classify_coverage(
        {**common, "valuation_base_quality": "MANUAL_OVERRIDE"},
        readiness,
    )
    labels = classify_coverage(
        {**common, "valuation_base_quality": "INTRINSIC"},
        readiness,
    )
    assert "LONG DISLOCATION" in labels


def test_0210_legacy_cache_missing_quality_is_normalized_fail_closed(tmp_path):
    from mfapp.research_cache import cache_event_type, latest_research_cache

    app = make_app(tmp_path, "legacy_cache_quality")
    _, company_id, _, coverage_id = seed_workspace(app, "OLD")
    with app.app_context():
        db.session.add(Event(
            company_id=company_id,
            event_type=cache_event_type(coverage_id),
            title="OLD research cache",
            event_date=datetime(2026, 9, 18, 12, 0, 0),
            payload={
                "valuation": {"current_price": 100.0, "base": 140.0},
                "intelligence": {
                    "base_gap_pct": 40.0,
                    "bias": "LONG",
                    "stance": "ATTRACTIVE",
                    "confidence": "HIGH",
                    "negatives": 0,
                    "positives": 3,
                },
                "readiness": {"done": 13, "total": 13},
                "discovery_labels": ["QUALITY AT DISCOUNT", "LONG DISLOCATION"],
            },
        ))
        db.session.commit()
        cache = latest_research_cache(coverage_id, company_id)
        assert cache is not None
        assert "LONG DISLOCATION" not in cache["discovery_labels"]
        assert "QUALITY AT DISCOUNT" not in cache["discovery_labels"]



def test_0210_valuation_model_save_queues_research_cache_refresh(tmp_path):
    from mfapp.core_models import Job
    from mfapp.research_cache import cache_event_type

    app = make_app(tmp_path, "valuation_save_recalc")
    user_id, company_id, _, coverage_id = seed_workspace(app, "SAVE")
    with app.app_context():
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).first()
        cache_time = datetime.now() + timedelta(seconds=5)
        db.session.add(Event(
            company_id=company_id,
            event_type=cache_event_type(coverage_id),
            title="SAVE current research cache",
            event_date=cache_time,
            payload={
                "valuation": {
                    "current_price": 100.0, "bear": 85.0, "base": 120.0, "bull": 150.0,
                    "base_quality": "INTRINSIC", "quality": "INTRINSIC",
                },
                "readiness": {"ready_to_validate": False, "gates": [], "validation": {"state": "NOT RUN"}},
                "intelligence": {"valuation_base_quality": "INTRINSIC"},
                "decision_lenses": {"value": "FAIR", "variant": "POSSIBLE", "research_conclusion": "RESEARCH INCOMPLETE", "rows": []},
            },
        ))
        model.updated_at = cache_time - timedelta(seconds=1)
        db.session.commit()
        assert Job.query.filter_by(user_id=user_id, job_type="RECALCULATE").count() == 0

    client = app.test_client()
    login(client, user_id)
    response = client.post("/company/SAVE/valuation/model", data={
        "company_type": "Industrial",
        "current_shares": "1000000",
        "share_basis_verified": "1",
        "weight_pe": "0.40",
        "weight_ev_sales": "0.25",
        "weight_fcf_yield": "0.35",
        "horizon_years": "5",
    })
    assert response.status_code == 302

    with app.app_context():
        jobs = Job.query.filter_by(user_id=user_id, job_type="RECALCULATE").all()
        assert len(jobs) == 1
        assert (jobs[0].payload or {}).get("coverage_id") == coverage_id


def test_0210_stale_intrinsic_cache_is_immediately_fail_closed_and_requeued(tmp_path, monkeypatch):
    from mfapp.core_models import Job
    from mfapp.research_cache import cache_event_type
    from mfapp.routes import _cached_coverage_rows

    app = make_app(tmp_path, "stale_cache_guard")
    user_id, company_id, _, coverage_id = seed_workspace(app, "STALE")
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        model = ValuationModel.query.filter_by(coverage_id=coverage_id, is_active=True).first()
        old_time = datetime(2026, 1, 1, 12, 0, 0)
        db.session.add(Event(
            company_id=company_id,
            event_type=cache_event_type(coverage_id),
            title="STALE research cache",
            event_date=old_time,
            payload={
                "valuation": {
                    "current_price": 100.0, "bear": 90.0, "base": 140.0, "bull": 175.0,
                    "base_quality": "INTRINSIC", "quality": "INTRINSIC", "decision_grade": True,
                },
                "readiness": {
                    "ready_to_validate": True,
                    "done": 13, "total": 13, "gates": [],
                    "validation": {"state": "VALIDATED", "samples": 5, "reliability": 75.0},
                },
                "intelligence": {
                    "valuation_base_quality": "INTRINSIC",
                    "valuation_decision_grade": True,
                    "base_gap_pct": 40.0,
                    "warnings": [],
                },
                "decision_lenses": {
                    "value": "ATTRACTIVE",
                    "variant": "POSITIVE EDGE",
                    "research_conclusion": "LONG READY",
                    "rows": [
                        {"key": "value", "state": "ATTRACTIVE"},
                        {"key": "variant", "state": "POSITIVE EDGE"},
                    ],
                },
            },
        ))
        model.updated_at = datetime(2026, 9, 18, 12, 0, 0)
        coverage.updated_at = datetime(2026, 9, 18, 12, 0, 0)
        db.session.commit()

        rows, _ = _cached_coverage_rows(user_id)
        row = next(item for item in rows if item["coverage"].id == coverage_id)
        assert row["valuation"]["base"] == 140.0
        assert row["valuation"]["base_quality"] == "DATA_WARNING"
        assert row["valuation"]["decision_grade"] is False
        assert row["decision_lenses"]["value"] == "UNVERIFIED"
        assert row["decision_lenses"]["variant"] == "DEFINED · UNPROVEN"
        assert row["decision_lenses"]["research_conclusion"] == "DATA REVIEW"
        assert "LONG DISLOCATION" not in row["discovery_labels"]
        assert "QUALITY AT DISCOUNT" not in row["discovery_labels"]

        from mfapp.market_discovery import _coverage_context_map
        discovery_context = _coverage_context_map(user_id, {"STALE"})["STALE"]
        assert discovery_context["valuation"]["base"] == 140.0
        assert discovery_context["valuation"]["base_quality"] == "DATA_WARNING"
        assert discovery_context["valuation"]["decision_grade"] is False
        assert "LONG DISLOCATION" not in discovery_context["discovery_labels"]
        assert "QUALITY AT DISCOUNT" not in discovery_context["discovery_labels"]

        monkeypatch.setattr("mfapp.jobs.market_scan", lambda user_id: {"contract_version": "FORENSIC_FAIR_VALUE_V1", "candidates": []})
        from mfapp.jobs import _discovery
        ranked = _discovery(user_id)["ranked"]
        stale_rank = next(item for item in ranked if item["ticker"] == "STALE")
        assert stale_rank["base_gap_pct"] == 40.0
        assert stale_rank["score"] == 65.0  # 13 approved gates; stale/non-intrinsic gap contributes zero.

        assert Job.query.filter_by(user_id=user_id, job_type="RECALCULATE").count() == 1

    client = app.test_client()
    login(client, user_id)
    response = client.get("/company/STALE/overview")
    assert response.status_code == 200
    assert b"DATA REVIEW" in response.data


def test_0210_management_requires_explicit_full_year_and_interim_wins(tmp_path, monkeypatch):
    import mfapp.management_promises as mp

    app = make_app(tmp_path, "management_period_guard")
    _, company_id, _, _ = seed_workspace(app, "PRD")
    with app.app_context():
        quarterly = mp.extract_promises("Management expects Q2 FY2027 revenue growth of 8% to 10%.")
        plain_year = mp.extract_promises("Management expects 2028 revenue growth of 12% to 14%.")
        explicit_fy = mp.extract_promises("Management expects FY2029 revenue growth of 6% to 8%.")

        assert quarterly[0]["target_period_type"] == "INTERIM"
        assert quarterly[0]["comparability"] == "NON_COMPARABLE"
        assert plain_year[0]["target_period_type"] == "UNRESOLVED"
        assert plain_year[0]["comparability"] == "NON_COMPARABLE"
        assert explicit_fy[0]["target_period_type"] == "FY"
        assert explicit_fy[0]["comparability"] == "COMPARABLE"

        mp.store_promises(company_id, quarterly + plain_year + explicit_fy)
        _seed_management_original(company_id, "revenue_growth_pct", 2029, 7.0)
        rows = mp.evaluate_promises(company_id)
        by_year = {row["target_year"]: row for row in rows}
        assert by_year[2027]["status"] == "EVIDENCE_ONLY"
        assert by_year[2028]["status"] == "EVIDENCE_ONLY"
        assert by_year[2029]["status"] == "MET"


def test_0210_management_reports_use_target_period_not_forced_fy_label():
    from pathlib import Path

    source = Path("mfapp/reporting.py").read_text()
    assert '["Period","Metric","Promise","Actual","Status"]' in source
    assert 'row.get("target_period")' in source
