from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, Event, Expectation, FinancialFlow, FinancialPeriod, HistoricalPrice,
    MarketSnapshot, NormalizedFinancial, ResearchGateApproval, Security,
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
        for year in range(2016, 2026):
            step = year - 2016
            rev = 700 + step * 60
            cogs = 300 + step * 22
            op = 120 + step * 15
            ni = 80 + step * 10
            cfo = 140 + step * 15
            capex = 40 + step * 3
            shares = 108 - step
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
    assert "10Y history coverage" in fhtml
    assert "10Y ANNUAL HISTORY GAP" not in fhtml

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



def test_032_secondary_fundamentals_can_restore_missing_fiscal_years(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "av_history_backfill")
    with app.app_context():
        db.create_all()
        company = Company(
            legal_name="Backfill Co", display_name="Backfill Co",
            sector="Industrials", industry="Machinery",
        )
        db.session.add(company); db.session.flush()
        security = Security(
            company_id=company.id, ticker="BFIL", exchange="NYSE", currency="USD",
            validation_source="TEST", active=True, is_primary=True,
        )
        db.session.add(security); db.session.commit()

        years = list(range(2016, 2026))
        income = {"annualReports": [{
            "fiscalDateEnding": f"{year}-12-31", "reportedCurrency": "USD",
            "totalRevenue": str(1000 + (year - 2016) * 100),
            "costOfRevenue": str(400 + (year - 2016) * 40),
            "grossProfit": str(600 + (year - 2016) * 60),
            "operatingIncome": str(180 + (year - 2016) * 20),
            "incomeBeforeTax": str(160 + (year - 2016) * 18),
            "incomeTaxExpense": str(32 + (year - 2016) * 4),
            "netIncome": str(128 + (year - 2016) * 14),
        } for year in years], "quarterlyReports": []}
        balance = {"annualReports": [{
            "fiscalDateEnding": f"{year}-12-31", "reportedCurrency": "USD",
            "cashAndCashEquivalentsAtCarryingValue": str(150 + year - 2016),
            "currentNetReceivables": "120", "inventory": "80",
            "currentAccountsPayable": "90", "totalAssets": "1800",
            "totalLiabilities": "700", "totalShareholderEquity": "1100",
            "shortLongTermDebtTotal": "200",
        } for year in years], "quarterlyReports": []}
        cash = {"annualReports": [{
            "fiscalDateEnding": f"{year}-12-31", "reportedCurrency": "USD",
            "operatingCashflow": str(220 + (year - 2016) * 20),
            "capitalExpenditures": str(60 + (year - 2016) * 2),
        } for year in years], "quarterlyReports": []}
        payloads = {
            "INCOME_STATEMENT": income,
            "BALANCE_SHEET": balance,
            "CASH_FLOW": cash,
        }

        monkeypatch.setattr("mfapp.secdata.get_secret", lambda *_args, **_kwargs: "configured")
        monkeypatch.setattr(
            "mfapp.secdata._alpha_vantage_statement",
            lambda function, _ticker, _user_id: payloads[function],
        )

        from mfapp.secdata import _alpha_vantage_fill_missing
        from mfapp.current_financials import annual_history_coverage, annual_rows

        result = _alpha_vantage_fill_missing(company, security, 1)
        db.session.commit()

        assert result["periods_created"] == 10
        assert result["years_backfilled"] == list(range(2025, 2015, -1))
        coverage = annual_history_coverage(company.id)
        assert coverage["complete"] is True
        assert coverage["missing_years"] == []
        rows = annual_rows(company.id, 16)
        assert len(rows) == 10
        assert all(row["revenue"] is not None for row in rows)
        assert all(row["fcf"] is not None for row in rows)


def test_032_all_core_authenticated_pages_and_apis_render(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "core_surfaces")
    uid, coverage_id = seed_full_research(app)
    client = app.test_client(); login(client, uid)

    pages = [
        "/", "/discovery", "/portfolio", "/portfolio/TST", "/publications",
        "/settings", "/control", "/trace", "/settings/notifications",
        "/alerts/email", f"/alerts/subscription/{coverage_id}",
    ]
    for path in pages:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, (path, response.status_code, response.data[:1000])

    json_paths = [
        "/jobs/status",
        "/company/TST/price/live",
        "/company/TST/price/history/live",
        "/company/TST/api/surface/overview",
        "/company/TST/api/surface/business",
        "/company/TST/api/surface/expectations",
        "/company/TST/api/surface/valuation",
        "/company/TST/api/surface/financial-flows",
        "/company/TST/api/surface/tape",
        "/company/TST/api/surface/monitoring",
        "/company/TST/api/surface/audit",
        "/company/TST/api/surface-detail/overview",
        "/company/TST/api/surface-detail/expectations",
        "/company/TST/api/surface-detail/valuation",
        "/company/TST/api/surface-detail/financial-flows",
        "/company/TST/api/surface-detail/tape",
        "/company/TST/api/surface-detail/monitoring",
        "/company/TST/api/surface-detail/audit",
    ]
    for path in json_paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.data[:1000])
        assert response.is_json, path

    history_payload = client.get("/company/TST/price/history/live").get_json()
    assert history_payload["cache"]["target_years"] == 10

    pdf = client.get("/company/TST/report/pdf?mode=executive")
    assert pdf.status_code == 200
    assert pdf.data.startswith(b"%PDF")
    docx = client.get("/company/TST/report/docx?mode=full")
    assert docx.status_code == 200
    assert docx.data.startswith(b"PK")


def test_032_price_history_core_defaults_are_ten_years():
    from pathlib import Path

    files = {
        path: Path(path).read_text()
        for path in (
            "mfapp/routes.py", "mfapp/routes_edit.py", "mfapp/routes_publish.py",
            "mfapp/research_routes.py", "mfapp/jobs.py",
        )
    }
    combined = "\n".join(files.values())
    assert '"lookback_years": 10' in combined
    assert 'payload["lookback_years"] = 10' in combined
    assert 'get("lookback_years") or 10' in files["mfapp/jobs.py"]
    assert '"lookback_years": 3' not in combined
    assert 'payload["lookback_years"] = 3' not in combined



def test_032_missing_fiscal_year_is_visible_and_blocks_fundamentals_readiness(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "missing_fy")
    uid, coverage_id = seed_full_research(app)
    with app.app_context():
        gap = FinancialPeriod.query.filter_by(company_id=1, period_type="FY", fiscal_year=2021).first()
        assert gap is not None
        NormalizedFinancial.query.filter_by(financial_period_id=gap.id).delete()
        FinancialFlow.query.filter_by(financial_period_id=gap.id).delete()
        db.session.delete(gap)
        db.session.commit()

        from mfapp.current_financials import annual_history_coverage
        coverage = db.session.get(Coverage, coverage_id)
        hist = annual_history_coverage(coverage.security.company_id if hasattr(coverage, "security") else db.session.get(Security, coverage.security_id).company_id)
        assert 2021 in hist["missing_years"]
        readiness = research_readiness(coverage)
        fundamentals = next(row for row in readiness["gates"] if row["key"] == "fundamentals")
        assert fundamentals["evidence_ready"] is False
        assert 2021 in fundamentals["evidence"]["annual_missing_years"]

        from mfapp.report_contract import _fundamentals
        report_history, _current = _fundamentals(db.session.get(Security, coverage.security_id).company_id)
        assert len(report_history) == 10
        report_gap = next(row for row in report_history if row["period"] == "FY2021")
        assert report_gap["missing_year"] is True
        assert report_gap["history_status"] == "MISSING"

    client = app.test_client(); login(client, uid)
    response = client.get("/company/TST/fundamentals")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "FY2021" in html
    assert "NOT STORED" in html
    assert "10Y ANNUAL HISTORY GAP" in html



def test_032_price_history_falls_through_partial_provider_to_full_ten_year_source(monkeypatch):
    from mfapp.historical_data import fetch_history

    today = date.today()
    requested_start = today - timedelta(days=366 * 10 + 45)

    def rows(start, end, step_days, provider):
        out = []
        day = start
        while day <= end:
            out.append({
                "trade_date": day,
                "provider": provider,
                "close_raw": 100.0,
                "close_split_adjusted": 100.0,
                "split_basis_factor": 1.0,
                "volume": 1_000_000,
                "quality": "TEST",
                "payload": {},
            })
            day += timedelta(days=step_days)
        return out

    partial = rows(today - timedelta(days=366 * 3), today, 3, "Alpaca IEX historical")
    full = rows(requested_start, today, 3, "Tiingo historical")

    monkeypatch.setattr("mfapp.historical_data._alpaca_history", lambda *_args: partial)
    monkeypatch.setattr("mfapp.historical_data._tiingo_history", lambda *_args: full)
    monkeypatch.setattr(
        "mfapp.historical_data._public_history",
        lambda *_args: (_ for _ in ()).throw(AssertionError("public fallback should not be needed")),
    )

    result, errors = fetch_history("TST", 1, 10)
    assert result
    assert result[0]["provider"] == "Tiingo historical"
    assert (result[-1]["trade_date"] - result[0]["trade_date"]).days >= 3650
    assert any("Alpaca: partial historical span" in item for item in errors)


def test_032_current_financial_coalesces_complementary_same_period_evidence(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "coalesced_period")
    with app.app_context():
        db.create_all()
        company = Company(
            legal_name="Coalesced Evidence Co", display_name="Coalesced Evidence Co",
            sector="Consumer", industry="Footwear",
        )
        db.session.add(company); db.session.flush()

        prior = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2024,
            start_date=date(2023, 6, 1), end_date=date(2024, 5, 31),
            filed_at=date(2024, 7, 20), accession_no="TEST-2024", currency="USD",
        )
        active = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2025,
            start_date=date(2024, 6, 1), end_date=date(2025, 5, 31),
            filed_at=date(2025, 7, 20), accession_no="TEST-2025-A", currency="USD",
        )
        legacy = FinancialPeriod(
            company_id=company.id, period_type="SUPERSEDED_FY", fiscal_year=2025,
            start_date=date(2024, 6, 1), end_date=date(2025, 5, 31),
            filed_at=date(2025, 7, 20), accession_no="TEST-2025-B", currency="USD",
        )
        db.session.add_all([prior, active, legacy]); db.session.flush()

        prior_row = _normalized(prior, revenue=1000, cogs=600, op_income=140, net_income=100, cfo=180, capex=60, shares=100)
        active_row = _normalized(active, revenue=1100, cogs=640, op_income=160, net_income=115, cfo=200, capex=65, shares=99)
        legacy_row = _normalized(legacy, revenue=1100, cogs=640, op_income=160, net_income=115, cfo=200, capex=65, shares=99)

        # Reproduce the production failure mode: the active identity has the
        # duration statement, while an audit-preserved sibling retains Inventory.
        active_row.inventory = None
        active_row.source_map = dict(active_row.source_map or {}) | {"inventory": None}
        legacy_row.revenue = None
        legacy_row.source_map = dict(legacy_row.source_map or {}) | {"revenue": None}
        legacy_row.inventory = Decimal("125")
        db.session.add_all([prior_row, active_row, legacy_row])
        db.session.commit()

        from mfapp.current_financials import current_row
        from mfapp.secdata import _reconcile_financial_field_issues

        current = current_row(company.id)
        assert current is not None
        assert current["revenue"] == 1100.0
        assert current["inventory"] == 125.0
        assert abs(float(current["metrics"]["revenue_growth_pct"]) - 10.0) < 1e-9
        assert current["quality"]["canonical_read_coalesced"] is True
        assert len(current["quality"]["canonical_read_period_ids"]) == 2

        issues = _reconcile_financial_field_issues(company)
        assert "inventory" not in issues["missing_expected_fields"]


def test_032_tape_display_recovers_from_stored_evidence_without_provider_call(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "tape_display_recovery")
    uid, coverage_id = seed_full_research(app)

    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        security = db.session.get(Security, coverage.security_id)
        day = date(2026, 9, 18)
        db.session.add_all([
            HistoricalPrice(
                security_id=security.id, trade_date=date(2026, 9, 17),
                provider="TEST", close_raw=Decimal("100"), close_split_adjusted=Decimal("100"),
                volume=Decimal("1000000"), quality="OBSERVED", payload={},
            ),
            HistoricalPrice(
                security_id=security.id, trade_date=day,
                provider="TEST", close_raw=Decimal("101"), close_split_adjusted=Decimal("101"),
                volume=Decimal("1200000"), quality="OBSERVED", payload={},
            ),
            Event(
                company_id=security.company_id,
                event_type="ALPACA_POSITIONING",
                title="stored positioning",
                event_date=datetime(2026, 9, 18, 20, 0, 0),
                payload={
                    "flow": {
                        "rows": [{
                            "date": day.isoformat(),
                            "method_version": __import__("mfapp.positioning", fromlist=["FLOW_METHOD_VERSION"]).FLOW_METHOD_VERSION,
                            "sanity_status": "PASS",
                            "observation_usable": True,
                            "decision_usable": False,
                            "decision_reasons": ["PARTIAL_SAMPLE_CONTEXT_ONLY"],
                            "coverage_status": "SAMPLED",
                            "feed": "sip",
                            "feed_scope": "CONSOLIDATED_SIP",
                            "source_status": "PARTIAL_SAMPLED",
                            "classification_method": "REGULAR_SESSION_RECONCILED_TICK_RULE_PROXY",
                            "flow_confidence_pct": 40.0,
                            "sample_volume_pct": 20.0,
                            "eligible_volume_pct": 95.0,
                            "reference_volume": 1200000,
                            "large_threshold": 100000.0,
                            "very_large_threshold": 250000.0,
                            "whale_threshold": 500000.0,
                            "large_buy": 2000000.0,
                            "large_sell": 1000000.0,
                            "net_large": 1000000.0,
                            "very_large_buy": 900000.0,
                            "very_large_sell": 400000.0,
                            "net_very_large": 500000.0,
                            "whale_buy": 600000.0,
                            "whale_sell": 100000.0,
                            "net_whale": 500000.0,
                            "net_large_ratio": 5.0,
                            "net_whale_ratio": 2.5,
                            "large_share_pct": 20.0,
                            "whale_share_pct": 5.0,
                        }]
                    }
                },
            ),
        ])
        db.session.commit()

        from mfapp.routes import _materialized_tape_for_display

        tape = _materialized_tape_for_display(security, {})
        assert len(tape["daily_market"]) == 2
        assert len(tape["institutional_flow"]) == 1
        assert tape["metrics"]["large_buy"] == 2000000.0
        assert tape["metrics"]["net_whale"] == 500000.0
        assert tape["metrics"]["flow_observation_usable"] is True
