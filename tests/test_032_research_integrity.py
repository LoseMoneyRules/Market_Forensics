from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, Expectation, FinancialFlow, FinancialPeriod, MarketSnapshot,
    NormalizedFinancial, ResearchGateApproval, Security,
)
from mfapp.extensions import db
from mfapp.financial_flow_engine import build_cash_flow, build_income_statement_flow
from mfapp.models import User
from mfapp.readiness import research_readiness
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch, name="032"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "032-research-integrity",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def _normalized(period, *, revenue, cogs, op_income, net_income, cfo, capex, shares):
    gross_profit = revenue - cogs
    return NormalizedFinancial(
        financial_period_id=period.id,
        revenue=Decimal(str(revenue)),
        cogs=Decimal(str(cogs)),
        gross_profit=Decimal(str(gross_profit)),
        operating_expenses=Decimal(str(gross_profit - op_income)),
        operating_income=Decimal(str(op_income)),
        pretax_income=Decimal(str(net_income / 0.79)),
        income_tax=Decimal(str((net_income / 0.79) - net_income)),
        net_income=Decimal(str(net_income)),
        cfo=Decimal(str(cfo)),
        capex=Decimal(str(capex)),
        fcf=Decimal(str(cfo - capex)),
        buybacks=Decimal("25"),
        dividends=Decimal("10"),
        diluted_shares=Decimal(str(shares)),
        shares_outstanding=Decimal(str(shares)),
        cash=Decimal("350"),
        debt=Decimal("120"),
        receivables=Decimal("210"),
        inventory=Decimal("95"),
        payables=Decimal("140"),
        assets=Decimal("2100"),
        liabilities=Decimal("850"),
        equity=Decimal("1250"),
        source_map={
            "revenue": "TEST", "cogs": "TEST", "gross_profit": "TEST",
            "operating_income": "TEST", "net_income": "TEST", "cfo": "TEST",
            "capex": "TEST", "fcf": "TEST", "cash": "TEST", "debt": "TEST",
            "receivables": "TEST", "inventory": "TEST", "payables": "TEST",
            "assets": "TEST", "liabilities": "TEST", "equity": "TEST",
            "shares_outstanding": "TEST", "diluted_shares": "TEST",
        },
        quality={},
        calculation_version="0.3.2-test",
    )


def seed_full_research(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control032@example.com", display_name="Control", role="CONTROL",
            password_hash="unused-test-hash", totp_secret_enc="unused-test-totp", is_active=True,
        )
        company = Company(
            legal_name="Integrity Test Co", display_name="Integrity Test Co",
            sector="Technology", industry="Application Software",
        )
        db.session.add_all([user, company]); db.session.flush()
        security = Security(
            company_id=company.id, ticker="TST", exchange="NASDAQ", currency="USD",
            validation_source="TEST", active=True, is_primary=True,
        )
        db.session.add(security); db.session.flush()
        coverage = Coverage(
            user_id=user.id, security_id=security.id, status="RESEARCH",
            research_state="UNDER_REVIEW",
        )
        db.session.add(coverage); db.session.flush()
        research, risk, _investment, model = ensure_workspace(coverage, user.id)

        research.thesis = "Integrity thesis"
        research.counter_evidence = "Integrity counter evidence"
        research.variant_market = "Market expects slower normalization."
        research.variant_us = "Filed cash conversion is stronger than implied."
        research.variant_evidence = "Stored filing evidence."
        research.business = "Evidence-backed software business."
        research.numbers = "ALL-FUNDAMENTALS-MARKER"
        research.expectations = "ALL-EXPECTATIONS-MARKER"
        research.flows_summary = "ALL-FLOWS-MARKER"
        risk.thesis_invalidation = "Revenue and cash conversion miss the locked threshold."
        risk.invalidation_locked_at = datetime(2025, 1, 1)

        db.session.add(MarketSnapshot(
            security_id=security.id, provider="TEST", price=Decimal("42.00"),
            currency="USD", as_of=datetime(2026, 9, 21, 18, 0, 0), quality="OBSERVED", payload={},
        ))

        period_rows = []
        for year, rev, cogs, op, ni, cfo, capex, shares in (
            (2024, 1000, 420, 220, 150, 240, 60, 100),
            (2025, 1250, 500, 300, 205, 330, 80, 98),
        ):
            period = FinancialPeriod(
                company_id=company.id, period_type="FY", fiscal_year=year,
                start_date=date(year, 1, 1), end_date=date(year, 12, 31),
                filed_at=date(year + 1, 2, 15), accession_no=f"TEST-{year}",
                currency="USD", created_at=datetime(year + 1, 2, 15),
            )
            db.session.add(period); db.session.flush()
            normalized = _normalized(
                period, revenue=rev, cogs=cogs, op_income=op,
                net_income=ni, cfo=cfo, capex=capex, shares=shares,
            )
            db.session.add(normalized)
            period_rows.append((period, normalized))

        latest_period, latest_normalized = period_rows[-1]
        latest = {
            "period_label": "FY2025", "fiscal_year": 2025,
            "revenue": float(latest_normalized.revenue),
            "cogs": float(latest_normalized.cogs),
            "gross_profit": float(latest_normalized.gross_profit),
            "operating_expenses": float(latest_normalized.operating_expenses),
            "operating_income": float(latest_normalized.operating_income),
            "pretax_income": float(latest_normalized.pretax_income),
            "income_tax": float(latest_normalized.income_tax),
            "net_income": float(latest_normalized.net_income),
            "cfo": float(latest_normalized.cfo),
            "capex": float(latest_normalized.capex),
            "fcf": float(latest_normalized.fcf),
            "buybacks": float(latest_normalized.buybacks),
            "dividends": float(latest_normalized.dividends),
        }
        db.session.add_all([
            FinancialFlow(
                financial_period_id=latest_period.id, flow_type="INCOME_STATEMENT",
                payload=build_income_statement_flow(latest), calculation_version="0.3.2-test",
            ),
            FinancialFlow(
                financial_period_id=latest_period.id, flow_type="CASH_FLOW",
                payload=build_cash_flow(latest), calculation_version="0.3.2-test",
            ),
            Expectation(
                coverage_id=coverage.id, metric="Revenue CAGR Marker", period_label="FY2030E",
                market_value=Decimal("0.06"), internal_value=Decimal("0.09"),
                unit="%", confidence="HIGH", notes="Stored expectation evidence.",
            ),
        ])

        scenarios = {row.name.upper(): row for row in model.scenarios}
        for name, growth, margin, pe, value in (
            ("BEAR", 0.02, 0.14, 18, 32),
            ("BASE", 0.08, 0.18, 24, 52),
            ("BULL", 0.12, 0.21, 28, 68),
        ):
            scenarios[name].inputs = {
                "growth": growth, "net_margin": margin, "fcf_margin": margin - 0.01, "pe": pe,
            }
            scenarios[name].equity_value_per_share = Decimal(str(value))

        db.session.add(ResearchGateApproval(
            coverage_id=coverage.id, gate_key="fundamentals", approved_by=user.id,
            approved_at=datetime(2025, 1, 1), evidence_hash="legacy-approved-basis",
        ))
        db.session.commit()
        return user.id, coverage.id


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_032_core_research_pages_render_populated_stored_evidence(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "populated")
    uid, _coverage_id = seed_full_research(app)
    client = app.test_client(); login(client, uid)

    fundamentals = client.get("/company/TST/fundamentals")
    assert fundamentals.status_code == 200, fundamentals.data[:1000]
    fhtml = fundamentals.get_data(as_text=True)
    assert "ALL-FUNDAMENTALS-MARKER" in fhtml
    assert "FY2025" in fhtml
    assert "No normalized financials stored yet." not in fhtml
    assert "COMPLETE FILED HISTORY" in fhtml

    expectations = client.get("/company/TST/expectations")
    assert expectations.status_code == 200, expectations.data[:1000]
    ehtml = expectations.get_data(as_text=True)
    assert "ALL-EXPECTATIONS-MARKER" in ehtml
    assert "Revenue CAGR Marker" in ehtml
    assert "PRICE-IMPLIED EXPECTATIONS" in ehtml
    assert "FY2026E" in ehtml

    flows = client.get("/company/TST/financial-flows")
    assert flows.status_code == 200, flows.data[:1000]
    flow_html = flows.get_data(as_text=True)
    assert "ALL-FLOWS-MARKER" in flow_html
    assert "Income Statement" in flow_html and "Cash Flow" in flow_html
    assert "No calculated flow stored" not in flow_html
    assert "FY2025" in flow_html


def test_032_all_company_research_navigation_survives_realistic_data(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "all_routes")
    uid, _coverage_id = seed_full_research(app)
    client = app.test_client(); login(client, uid)

    paths = [
        "/company/TST/overview", "/company/TST/business", "/company/TST/fundamentals",
        "/company/TST/expectations", "/company/TST/valuation", "/company/TST/bear-case",
        "/company/TST/catalysts", "/company/TST/financial-flows", "/company/TST/management",
        "/company/TST/tape", "/company/TST/monitoring", "/company/TST/journal",
        "/company/TST/audit", "/company/TST/validate",
    ]
    for path in paths:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, (path, response.status_code, response.data[:1000])


def test_032_readiness_failure_never_hides_stored_data(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "degraded")
    uid, _coverage_id = seed_full_research(app)
    client = app.test_client(); login(client, uid)

    def broken_readiness(_coverage):
        raise TypeError("simulated readiness metadata incompatibility")

    monkeypatch.setattr("mfapp.routes.research_readiness", broken_readiness)

    for path, marker in (
        ("/company/TST/fundamentals", "ALL-FUNDAMENTALS-MARKER"),
        ("/company/TST/expectations", "ALL-EXPECTATIONS-MARKER"),
        ("/company/TST/financial-flows", "ALL-FLOWS-MARKER"),
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.data[:1000])
        html = response.get_data(as_text=True)
        assert marker in html
        assert "RESEARCH CONTROL TEMPORARILY UNAVAILABLE" in html


def test_032_financial_basis_review_accepts_aware_and_naive_timestamps(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "timezone")
    _uid, coverage_id = seed_full_research(app)

    with app.app_context():
        monkeypatch.setattr(
            "mfapp.readiness.latest_financial_basis",
            lambda _company_id: {
                "available": True,
                "materialized_at": "2026-09-21T19:55:32+00:00",
                "period_type": "FY", "fiscal_year": 2025, "period_end": "2025-12-31",
                "filed_at": "2026-02-15", "accession_no": "TEST-2025",
            },
        )
        coverage = db.session.get(Coverage, coverage_id)
        result = research_readiness(coverage)
        fundamentals = next(row for row in result["gates"] if row["key"] == "fundamentals")
        assert fundamentals["financial_review_required"] is True
        assert fundamentals["status"] == "REVIEW REQUIRED"


def test_032_degraded_control_blocks_cached_decision():
    from mfapp.routes import _fail_closed_degraded_control

    intelligence, lenses = _fail_closed_degraded_control(
        {"degraded": True},
        {"action": "BUY", "stance": "POSITIVE", "confidence": "HIGH", "warnings": [], "blockers": []},
        {
            "research_conclusion": "BUY", "model_confidence": "HIGH", "thesis_control": "LOCKED",
            "rows": [
                {"key": "model_confidence", "state": "HIGH"},
                {"key": "thesis_control", "state": "LOCKED"},
            ],
        },
    )
    assert intelligence["action"] == "WAIT"
    assert intelligence["stance"] == "DATA REVIEW"
    assert intelligence["confidence"] == "LOW"
    assert intelligence["blockers"]
    assert lenses["research_conclusion"] == "DATA REVIEW"
    assert lenses["model_confidence"] == "UNVALIDATED"
    assert lenses["thesis_control"] == "CONTROL UNAVAILABLE"
