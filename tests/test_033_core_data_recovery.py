from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, DataQualityIssue, FinancialFlow, FinancialPeriod, Job, NormalizedFinancial, RefreshRun, Security, Source
from mfapp.extensions import db
from mfapp.jobs import dismiss_terminal_jobs, enqueue_job, run_jobs
from mfapp.models import User
from mfapp.secdata import (
    DURATION_TAGS,
    _annual_duration,
    _apply_annual_statement_bridges,
    _bridge_fy_end_instants_to_q4,
    _fiscal_quarter_from_end,
    _quarter_duration_values,
    _reconcile_financial_field_issues,
    _validated_sga_operating_bridge,
)


def make_app(tmp_path, monkeypatch, name="033"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "033-core-data-recovery",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def _fact(*, start: str, end: str, value: int, form: str, fp: str, filed: str):
    return {
        "start": start, "end": end, "val": value, "form": form, "fp": fp,
        "filed": filed, "accn": "TEST-" + filed, "fy": int(end[:4]),
    }


def test_033_non_calendar_quarters_come_from_fact_period_not_filing_fp():
    # Intuit-style July year-end. Deliberately wrong fp markers model comparative
    # Companyfacts repeated in later filings; the represented dates are canonical.
    rows = [
        _fact(start="2025-08-01", end="2025-10-31", value=100, form="10-Q", fp="Q3", filed="2025-12-01"),
        _fact(start="2025-11-01", end="2026-01-31", value=110, form="10-Q", fp="Q1", filed="2026-03-01"),
        _fact(start="2026-02-01", end="2026-04-30", value=120, form="10-Q", fp="Q2", filed="2026-06-01"),
        _fact(start="2025-08-01", end="2026-07-31", value=460, form="10-K", fp="FY", filed="2026-09-01"),
    ]
    facts = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rows}}}}}
    annual = _annual_duration(facts, DURATION_TAGS["revenue"], "0731")
    quarters = _quarter_duration_values(facts, DURATION_TAGS["revenue"], annual, fiscal_year_end="0731")

    assert _fiscal_quarter_from_end(rows[0], "0731") == "Q1"
    assert _fiscal_quarter_from_end(rows[1], "0731") == "Q2"
    assert _fiscal_quarter_from_end(rows[2], "0731") == "Q3"
    assert [quarters[(2026, q)]["value"] for q in ("Q1", "Q2", "Q3", "Q4")] == [100, 110, 120, 130]


def test_033_march_year_end_quarters_are_consecutive_for_lpg_shape():
    samples = [
        ({"end": "2025-06-30", "fp": "Q2"}, "Q1"),
        ({"end": "2025-09-30", "fp": "Q3"}, "Q2"),
        ({"end": "2025-12-31", "fp": "Q1"}, "Q3"),
        ({"end": "2026-03-31", "fp": "FY"}, "Q4"),
    ]
    assert [_fiscal_quarter_from_end(row, "0331") for row, _ in samples] == [expected for _, expected in samples]




def _annual_fact(*, start: str, end: str, value: int, tag: str = "Fact"):
    return {
        "start": start, "end": end, "val": value, "form": "10-K", "fp": "FY",
        "filed": "2026-07-24", "accn": "TEST-ANNUAL", "fy": int(end[:4]),
        "tag": tag, "namespace": "us-gaap",
    }


def test_033_nike_like_statement_resolves_operating_income_without_symbol_rules():
    # Generic retail/manufacturing presentation: Gross Profit + total S&A +
    # pretax/non-operating evidence, but no explicit OperatingIncomeLoss fact.
    gp = _annual_fact(start="2025-06-01", end="2026-05-31", value=19911, tag="GrossProfit")
    sga = _annual_fact(start="2025-06-01", end="2026-05-31", value=16114, tag="SellingGeneralAndAdministrativeExpense")
    pretax = _annual_fact(start="2025-06-01", end="2026-05-31", value=3900, tag="IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
    nonop = _annual_fact(start="2025-06-01", end="2026-05-31", value=103, tag="NonoperatingIncomeExpense")
    duration = {
        field: {} for field in DURATION_TAGS
    }
    duration["gross_profit"][2026] = gp
    duration["pretax_income"][2026] = pretax

    _apply_annual_statement_bridges(
        duration,
        sga={2026: sga},
        nonoperating_total={2026: nonop},
        nonoperating_components={},
    )

    assert duration["operating_expenses"][2026]["val"] == 16114
    assert duration["operating_income"][2026]["val"] == 3797
    from mfapp.calculations import financial_metrics
    metrics = financial_metrics(
        {"revenue": 46398, "gross_profit": 19911, "operating_expenses": 16114, "operating_income": 3797},
        {},
    )
    assert round(metrics["operating_margin_pct"], 2) == 8.18


def test_033_sga_is_not_blindly_treated_as_total_opex():
    # A company with separate R&D could have SGA that is only one operating-cost
    # component. If the candidate operating income does not reconcile through
    # independent non-operating evidence, leave it unresolved.
    gp = _annual_fact(start="2025-01-01", end="2025-12-31", value=800, tag="GrossProfit")
    sga = _annual_fact(start="2025-01-01", end="2025-12-31", value=300, tag="SellingGeneralAndAdministrativeExpense")
    pretax = _annual_fact(start="2025-01-01", end="2025-12-31", value=350, tag="IncomeBeforeTax")
    nonop = _annual_fact(start="2025-01-01", end="2025-12-31", value=10, tag="NonoperatingIncomeExpense")

    expense, operating = _validated_sga_operating_bridge(gp, sga, pretax, nonop, [])
    assert expense is None
    assert operating is None

    # Even a coincidentally equal non-operating magnitude cannot justify using SGA
    # as total OpEx when the implied bridge is structurally too large.
    coincidental = _annual_fact(start="2025-01-01", end="2025-12-31", value=150, tag="NonoperatingIncomeExpense")
    expense, operating = _validated_sga_operating_bridge(gp, sga, pretax, coincidental, [])
    assert expense is None
    assert operating is None


def test_033_direct_operating_expenses_produce_exact_operating_income():
    gp = _annual_fact(start="2025-01-01", end="2025-12-31", value=500, tag="GrossProfit")
    opex = _annual_fact(start="2025-01-01", end="2025-12-31", value=300, tag="OperatingExpenses")
    duration = {field: {} for field in DURATION_TAGS}
    duration["gross_profit"][2025] = gp
    duration["operating_expenses"][2025] = opex

    _apply_annual_statement_bridges(
        duration, sga={}, nonoperating_total={}, nonoperating_components={}
    )
    assert duration["operating_income"][2025]["val"] == 200
    assert duration["operating_income"][2025]["_mf_derived_method"] == "GROSS_PROFIT_MINUS_OPERATING_EXPENSES"


def test_033_weighted_average_shares_are_reconstructed_by_quarter_not_ytd_proxy():
    rows = [
        _fact(start="2026-01-01", end="2026-03-31", value=100, form="10-Q", fp="Q1", filed="2026-05-01"),
        _fact(start="2026-01-01", end="2026-06-30", value=105, form="10-Q", fp="Q2", filed="2026-08-01"),
        _fact(start="2026-01-01", end="2026-09-30", value=110, form="10-Q", fp="Q3", filed="2026-11-01"),
        _fact(start="2026-01-01", end="2026-12-31", value=115, form="10-K", fp="FY", filed="2027-02-01"),
    ]
    facts = {"facts": {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": rows}}}}}
    annual = _annual_duration(facts, DURATION_TAGS["diluted_shares"], "1231")
    quarters = _quarter_duration_values(
        facts, DURATION_TAGS["diluted_shares"], annual,
        shares_metric=True, fiscal_year_end="1231",
    )

    assert quarters[(2026, "Q1")]["value"] == Decimal("100")
    # YTD averages must be converted back to incremental quarter averages.
    assert 109 < float(quarters[(2026, "Q2")]["value"]) < 111
    assert 119 < float(quarters[(2026, "Q3")]["value"]) < 121
    assert 129 < float(quarters[(2026, "Q4")]["value"]) < 131
    assert quarters[(2026, "Q4")]["method"] == "FY_MINUS_9M_WEIGHTED_AVERAGE"




def test_033_sec_inline_extension_parser_recovers_custom_whole_entity_facts(monkeypatch):
    from mfapp import sec_inline_facts

    html = """
    <html><body>
      <xbrli:context id="D2026"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:startDate>2025-06-01</xbrli:startDate><xbrli:endDate>2026-05-31</xbrli:endDate></xbrli:period>
      </xbrli:context>
      <xbrli:context id="I2026"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:instant>2026-05-31</xbrli:instant></xbrli:period>
      </xbrli:context>
      <xbrli:context id="SEG"><xbrli:entity><xbrli:identifier>1</xbrli:identifier>
        <xbrli:segment><xbrldi:explicitMember dimension="acme:RegionAxis">acme:USMember</xbrldi:explicitMember></xbrli:segment>
        </xbrli:entity><xbrli:period><xbrli:instant>2026-05-31</xbrli:instant></xbrli:period>
      </xbrli:context>
      <ix:nonFraction name="acme:CustomRevenue" contextRef="D2026" unitRef="USD" scale="6">46,398</ix:nonFraction>
      <ix:nonFraction name="acme:CustomInventory" contextRef="I2026" unitRef="USD" scale="6">7,501</ix:nonFraction>
      <ix:nonFraction name="acme:CustomInventory" contextRef="SEG" unitRef="USD" scale="6">99</ix:nonFraction>
    </body></html>
    """
    labels = """<?xml version="1.0"?>
    <link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
      <link:labelLink xlink:type="extended">
        <link:loc xlink:type="locator" xlink:href="acme.xsd#acme_CustomRevenue" xlink:label="loc_rev"/>
        <link:loc xlink:type="locator" xlink:href="acme.xsd#acme_CustomInventory" xlink:label="loc_inv"/>
        <link:label xlink:type="resource" xlink:label="lab_rev" xlink:role="http://www.xbrl.org/2003/role/label">Total revenues</link:label>
        <link:label xlink:type="resource" xlink:label="lab_inv" xlink:role="http://www.xbrl.org/2003/role/label">Inventories</link:label>
        <link:labelArc xlink:type="arc" xlink:from="loc_rev" xlink:to="lab_rev"/>
        <link:labelArc xlink:type="arc" xlink:from="loc_inv" xlink:to="lab_inv"/>
      </link:labelLink>
    </link:linkbase>"""

    class FakeResponse:
        def __init__(self, *, text="", payload=None):
            self.text = text
            self._payload = payload or {}
        def json(self):
            return self._payload

    def fake_get(url, user_agent, timeout=15):
        if url.endswith("/index.json"):
            return FakeResponse(payload={"directory": {"item": [{"name": "acme_lab.xml"}]}})
        if url.endswith("/acme_lab.xml"):
            return FakeResponse(text=labels)
        if url.endswith("/acme.htm"):
            return FakeResponse(text=html)
        return None

    monkeypatch.setattr(sec_inline_facts, "_get", fake_get)
    concepts = sec_inline_facts.extract_extension_concepts(
        cik="1",
        accession="0000000001-26-000001",
        primary_document="acme.htm",
        form="10-K",
        filed="2026-07-24",
        user_agent="Test test@example.com",
        label_aliases={"revenue": {"total revenues"}, "inventory": {"inventories"}},
    )

    revenue = concepts["CustomRevenue"]["units"]["USD"]
    inventory = concepts["CustomInventory"]["units"]["USD"]
    assert len(revenue) == 1
    assert len(inventory) == 1  # dimensional/segment fact is rejected
    assert revenue[0]["val"] == Decimal("46398000000")
    assert inventory[0]["val"] == Decimal("7501000000")
    assert revenue[0]["_mf_filing_extension"] is True


def test_033_nike_like_companyfacts_flow_resolves_operating_income_and_inventory_end_to_end(tmp_path, monkeypatch):
    from mfapp.secdata import refresh_company_fundamentals

    app = make_app(tmp_path, monkeypatch, "nike_like")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Retail Co", display_name="Retail Co")
        security = Security(company=company, ticker="RETL", exchange="NYSE")
        db.session.add_all([company, security]); db.session.commit()

        def duration_node(label, tag, value):
            return {
                "label": label,
                "units": {"USD": [{
                    "start": "2025-06-01", "end": "2026-05-31", "val": value,
                    "form": "10-K", "fp": "FY", "filed": "2026-07-24",
                    "accn": f"TEST-{tag}", "fy": 2026,
                }]}
            }

        facts = {
            "entityName": "Retail Co",
            "facts": {"us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": duration_node("Revenues", "rev", 46398),
                "CostOfRevenue": duration_node("Cost of sales", "cogs", 26487),
                "GrossProfit": duration_node("Gross profit", "gp", 19911),
                "SellingGeneralAndAdministrativeExpense": duration_node("Selling and administrative expense", "sga", 16114),
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": duration_node("Income before income taxes", "pretax", 3900),
                "NonoperatingIncomeExpense": duration_node("Nonoperating income expense", "nonop", 103),
                "IncomeTaxExpenseBenefit": duration_node("Income tax expense", "tax", 792),
                "NetIncomeLoss": duration_node("Net income", "net", 3108),
                "InventoryNet": {
                    "label": "Inventories",
                    "units": {"USD": [{
                        "end": "2026-05-31", "val": 7501, "form": "10-K", "fp": "FY",
                        "filed": "2026-07-24", "accn": "TEST-inventory", "fy": 2026,
                    }]}
                },
            }}
        }
        monkeypatch.setattr("mfapp.secdata._ua", lambda user_id: "Research test research@example.com")
        monkeypatch.setattr("mfapp.secdata._ticker_meta", lambda ticker, ua: {
            "cik": "0000000001", "name": "Retail Co", "sic": "3021",
            "sic_description": "Rubber and plastics footwear", "fiscal_year_end": "0531",
        })
        monkeypatch.setattr("mfapp.secdata._json", lambda url, ua: facts)

        result = refresh_company_fundamentals(company, security, 1)
        period = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY", fiscal_year=2026).first()
        row = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()

        assert result["annual_saved"] == 1
        assert row.inventory == Decimal("7501")
        assert row.operating_expenses == Decimal("16114")
        assert row.operating_income == Decimal("3797")
        assert row.source_map["operating_income"]["method"] == "VALIDATED_GROSS_PROFIT_MINUS_SGA"
        from mfapp.calculations import financial_metrics
        assert round(financial_metrics({
            "revenue": row.revenue,
            "gross_profit": row.gross_profit,
            "operating_expenses": row.operating_expenses,
            "operating_income": row.operating_income,
        })["operating_margin_pct"], 2) == 8.18


def test_033_missing_applicable_inventory_becomes_explicit_data_quality_issue(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "field_integrity")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Continuity Co", display_name="Continuity Co")
        db.session.add(company); db.session.flush()
        fy = FinancialPeriod(company_id=company.id, period_type="FY", fiscal_year=2025, end_date=date(2025, 12, 31))
        q1 = FinancialPeriod(company_id=company.id, period_type="Q1", fiscal_year=2026, end_date=date(2026, 3, 31))
        db.session.add_all([fy, q1]); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=fy.id, revenue=Decimal("1000"), inventory=Decimal("200"),
            operating_income=Decimal("100"), source_map={}, quality={},
        ))
        db.session.add(NormalizedFinancial(
            financial_period_id=q1.id, revenue=Decimal("260"), inventory=None,
            operating_income=Decimal("30"), source_map={}, quality={},
        ))
        db.session.commit()

        audit = _reconcile_financial_field_issues(company)
        db.session.flush()
        assert "inventory" in audit["missing_expected_fields"]
        issue = DataQualityIssue.query.filter_by(
            company_id=company.id,
            object_type="financial_basis",
            object_id=str(q1.id),
            code="MISSING_EXPECTED_INVENTORY",
            status="OPEN",
        ).first()
        assert issue is not None


def test_033_fy_end_inventory_bridges_exactly_to_synthetic_q4(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "q4_inventory")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Inventory Co", display_name="Inventory Co")
        db.session.add(company); db.session.flush()
        fy = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2026,
            end_date=date(2026, 5, 31), currency="USD",
        )
        q4 = FinancialPeriod(
            company_id=company.id, period_type="Q4", fiscal_year=2026,
            end_date=date(2026, 5, 31), currency="USD",
        )
        db.session.add_all([fy, q4]); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=fy.id,
            revenue=Decimal("46398"), inventory=Decimal("7501"),
            source_map={"inventory": {"provider": "SEC", "tag": "InventoryNet"}},
            quality={},
        ))
        db.session.add(NormalizedFinancial(
            financial_period_id=q4.id,
            revenue=Decimal("11000"), inventory=None,
            source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))
        db.session.commit()

        assert _bridge_fy_end_instants_to_q4(company) >= 1
        db.session.flush()
        q4_row = NormalizedFinancial.query.filter_by(financial_period_id=q4.id).first()
        assert q4_row.inventory == Decimal("7501")
        assert q4_row.source_map["inventory"]["method"] == "FY_END_INSTANT_BRIDGE"
        assert q4_row.source_map["inventory"]["bridge_from_period_id"] == fy.id


def test_033_empty_quarter_shells_do_not_hide_valid_annual_current_basis(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "ttm_fallback")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Fiscal Co", display_name="Fiscal Co")
        db.session.add(company); db.session.flush()

        annual = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2025,
            start_date=date(2024, 8, 1), end_date=date(2025, 7, 31), currency="USD",
        )
        db.session.add(annual); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=annual.id,
            revenue=Decimal("500"), operating_income=Decimal("80"),
            net_income=Decimal("60"), cfo=Decimal("75"), capex=Decimal("20"),
            fcf=Decimal("55"), source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))

        for period_type, fiscal_year, end_date in (
            ("Q4", 2025, date(2025, 7, 31)),
            ("Q1", 2026, date(2025, 10, 31)),
            ("Q2", 2026, date(2026, 1, 31)),
            ("Q3", 2026, date(2026, 4, 30)),
        ):
            period = FinancialPeriod(
                company_id=company.id, period_type=period_type, fiscal_year=fiscal_year,
                end_date=end_date, currency="USD",
            )
            db.session.add(period); db.session.flush()
            db.session.add(NormalizedFinancial(
                financial_period_id=period.id, source_map={}, quality={},
            ))
        db.session.commit()

        current = current_row(company.id)
        assert current is not None
        assert current["period_type"] == "FY"
        assert current["comparison_basis"] == "FY_FALLBACK"
        assert current["revenue"] == 500.0

        from mfapp.current_financials import forecast_rows
        forecasts = forecast_rows(company.id, None, 3)
        assert len(forecasts) == 3
        assert all(row["revenue"] is not None for row in forecasts)


def test_033_recalculate_rebuilds_financial_flows_from_valid_annual_basis(tmp_path, monkeypatch):
    from mfapp.jobs import recalculate_company

    app = make_app(tmp_path, monkeypatch, "flows")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Flow Co", display_name="Flow Co")
        db.session.add(company); db.session.flush()
        period = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2025,
            start_date=date(2025, 1, 1), end_date=date(2025, 12, 31), currency="USD",
        )
        db.session.add(period); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=period.id,
            revenue=Decimal("1000"), cogs=Decimal("450"), gross_profit=Decimal("550"),
            operating_expenses=Decimal("300"), operating_income=Decimal("250"),
            pretax_income=Decimal("220"), income_tax=Decimal("45"), net_income=Decimal("175"),
            cfo=Decimal("240"), capex=Decimal("70"), fcf=Decimal("170"),
            source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))
        db.session.commit()

        result = recalculate_company(company.id)
        assert result["periods"] == 1
        rows = FinancialFlow.query.filter_by(financial_period_id=period.id).all()
        assert {row.flow_type for row in rows} == {"INCOME_STATEMENT", "CASH_FLOW"}
        income = next(row for row in rows if row.flow_type == "INCOME_STATEMENT")
        assert (income.payload or {}).get("bridge_steps")
        assert (income.payload or {}).get("nodes")

def test_033_failed_job_rolls_back_partial_business_writes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "atomic")
    with app.app_context():
        db.create_all()
        user = User(
            email="control033@example.com", display_name="Control", role="CONTROL",
            password_hash="unused-test-hash", totp_secret_enc="unused-test-totp", is_active=True,
        )
        db.session.add(user); db.session.commit()
        job = enqueue_job("RECALCULATE", user_id=user.id, company_id=999, payload={}, priority=1)
        job.max_attempts = 1
        db.session.commit()

        def broken_execute(_job):
            db.session.add(Company(legal_name="PARTIAL WRITE", display_name="PARTIAL WRITE"))
            db.session.flush()
            raise RuntimeError("simulated core failure")

        monkeypatch.setattr("mfapp.jobs._execute_with_deadline", broken_execute)
        result = run_jobs(limit=1, user_id=user.id)

        assert result[0]["status"] == "FAILED"
        assert Company.query.filter_by(legal_name="PARTIAL WRITE").first() is None
        assert db.session.get(Job, job.id).status == "FAILED"
        assert RefreshRun.query.filter_by(job_id=job.id, status="FAILED").count() == 1



def test_033_refresh_stale_reingests_coverage_with_old_financial_normalizer(tmp_path, monkeypatch):
    from mfapp.jobs import _stale
    from mfapp.secdata import SEC_NORMALIZER_VERSION

    app = make_app(tmp_path, monkeypatch, "stale_normalizer")
    with app.app_context():
        db.create_all()
        user = User(
            email="control-stale@example.com", display_name="Control", role="CONTROL",
            password_hash="unused-test-hash", totp_secret_enc="unused-test-totp", is_active=True,
        )
        company = Company(legal_name="Stale Co", display_name="Stale Co")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(company_id=company.id, ticker="STAL", exchange="NYSE")
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH")
        db.session.add(coverage)
        db.session.add(Source(
            company_id=company.id, provider="SEC", source_type="COMPANYFACTS",
            title="old facts", url="https://data.sec.gov/old",
            meta={"normalizer_version": "0.3.3"},
        ))
        db.session.add(RefreshRun(
            company_id=company.id, security_id=security.id,
            refresh_type="SEC_INGEST", status="DONE",
            started_at=datetime.now(), finished_at=datetime.now(),
        ))
        db.session.commit()

        monkeypatch.setattr("mfapp.jobs.provider_status", lambda user_id: {"sec": True})
        result = _stale(user.id)
        sec_job = Job.query.filter_by(
            user_id=user.id, company_id=company.id, security_id=security.id,
            job_type="SEC_INGEST", status="QUEUED",
        ).first()
        assert result["coverage_scanned"] == 1
        assert sec_job is not None
        assert SEC_NORMALIZER_VERSION == "0.3.3-financial-completeness"


def test_033_terminal_job_cleanup_preserves_row_and_marks_dismissed(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "dismiss")
    with app.app_context():
        db.create_all()
        user = User(
            email="control033b@example.com", display_name="Control", role="CONTROL",
            password_hash="unused-test-hash", totp_secret_enc="unused-test-totp", is_active=True,
        )
        db.session.add(user); db.session.flush()
        failed = Job(job_type="SEC_INGEST", status="FAILED", user_id=user.id, payload={}, result={}, error_message="provider failure")
        cancelled = Job(job_type="RECALCULATE", status="CANCELLED", user_id=user.id, payload={}, result={}, error_message="cancelled")
        done = Job(job_type="MARKET_REFRESH", status="DONE", user_id=user.id, payload={}, result={})
        db.session.add_all([failed, cancelled, done]); db.session.commit()
        ids = (failed.id, cancelled.id, done.id)

        assert dismiss_terminal_jobs(user.id) == 2
        assert db.session.get(Job, ids[0]).status == "DISMISSED"
        assert db.session.get(Job, ids[1]).status == "DISMISSED"
        assert db.session.get(Job, ids[2]).status == "DONE"
        assert (db.session.get(Job, ids[0]).result or {}).get("dismissed", {}).get("previous_status") == "FAILED"


def test_033_settings_consolidates_duplicate_job_views():
    from pathlib import Path

    template = Path("mfapp/templates/settings.html").read_text()
    assert "DATA OPERATIONS" in template
    assert "Background jobs & execution history" in template
    assert "Clear failed/cancelled" in template
    assert "data-coverage-panel" in template
    assert '<p class="eyebrow">BACKGROUND WORK</p>' not in template
    assert '<p class="eyebrow">DATA INGESTION</p>' not in template
