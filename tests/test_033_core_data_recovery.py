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
    _fiscal_year_from_end,
    _quarter_duration_values,
    _reconcile_financial_field_issues,
    _upsert_period,
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



def test_033_companyfacts_gap_is_filled_by_generic_filing_extension_pipeline(tmp_path, monkeypatch):
    from mfapp.secdata import refresh_company_fundamentals

    app = make_app(tmp_path, monkeypatch, "extension_pipeline")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Extension Co", display_name="Extension Co")
        security = Security(company=company, ticker="EXTN", exchange="NYSE")
        db.session.add_all([company, security]); db.session.commit()

        def duration_node(label, value):
            return {
                "label": label,
                "units": {"USD": [{
                    "start": "2025-01-01", "end": "2025-12-31", "val": value,
                    "form": "10-K", "fp": "FY", "filed": "2026-02-20",
                    "accn": "STD", "fy": 2025,
                }]}
            }

        facts = {
            "entityName": "Extension Co",
            "facts": {"us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": duration_node("Revenues", 1000),
                "CostOfRevenue": duration_node("Cost of revenue", 500),
                "GrossProfit": duration_node("Gross profit", 500),
                "OperatingIncomeLoss": duration_node("Operating income", 180),
                "NetIncomeLoss": duration_node("Net income", 120),
            }}
        }

        def inject_extension(company_arg, companyfacts, meta, ua):
            companyfacts.setdefault("facts", {})["filing-extension"] = {
                "CustomInventory": {
                    "label": "Inventories",
                    "units": {"USD": [{
                        "end": "2025-12-31", "val": Decimal("240"), "form": "10-K", "fp": "FY",
                        "filed": "2026-02-20", "accn": "EXT", "tag": "CustomInventory",
                        "namespace": "ext", "_mf_filing_extension": True,
                        "_mf_source_id": None,
                        "_mf_derived_method": "FILING_EXTENSION_LABEL_FALLBACK",
                    }]}
                }
            }
            return {"attempted": True, "filings_scanned": 1, "concepts_added": 1, "facts_added": 1}

        monkeypatch.setattr("mfapp.secdata._ua", lambda user_id: "Research test research@example.com")
        monkeypatch.setattr("mfapp.secdata._ticker_meta", lambda ticker, ua: {
            "cik": "0000000002", "name": "Extension Co", "sic": "3990",
            "sic_description": "Manufacturing", "fiscal_year_end": "1231",
        })
        monkeypatch.setattr("mfapp.secdata._json", lambda url, ua: facts)
        monkeypatch.setattr("mfapp.secdata._augment_companyfacts_with_recent_filing_extensions", inject_extension)

        result = refresh_company_fundamentals(company, security, 1)
        period = FinancialPeriod.query.filter_by(company_id=company.id, period_type="FY", fiscal_year=2025).first()
        row = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()

        assert result["filing_extension_fallback"]["facts_added"] == 1
        assert row.inventory == Decimal("240")
        assert row.source_map["inventory"]["method"] == "FILING_EXTENSION_LABEL_FALLBACK"


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
        assert SEC_NORMALIZER_VERSION == "0.3.3-production-data-truth-r3"


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


def test_033_week_based_fiscal_year_end_does_not_create_fake_apple_history_holes():
    # Apple-style 52/53-week years can close a few days after the SEC's current
    # nominal fiscalYearEnd MMDD. Those are still the represented fiscal year,
    # not the following one.
    rows = [
        _fact(start="2016-09-25", end="2017-09-30", value=229234, form="10-K", fp="FY", filed="2017-11-03"),
        _fact(start="2017-10-01", end="2018-09-29", value=265595, form="10-K", fp="FY", filed="2018-11-05"),
        _fact(start="2022-09-25", end="2023-09-30", value=383285, form="10-K", fp="FY", filed="2023-11-03"),
        _fact(start="2023-10-01", end="2024-09-28", value=391035, form="10-K", fp="FY", filed="2024-11-01"),
    ]
    facts = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rows}}}}}
    annual = _annual_duration(facts, DURATION_TAGS["revenue"], "0927")

    assert _fiscal_year_from_end(rows[0], "0927") == 2017
    assert _fiscal_year_from_end(rows[2], "0927") == 2023
    assert set(annual) == {2017, 2018, 2023, 2024}
    assert annual[2017]["val"] == 229234
    assert annual[2023]["val"] == 383285


def test_033_current_basis_prefers_newer_or_same_date_fy_over_stale_ttm(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "freshest_current_basis")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Fresh Filing Co", display_name="Fresh Filing Co")
        db.session.add(company); db.session.flush()

        fy = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2026,
            start_date=date(2025, 6, 1), end_date=date(2026, 5, 31), currency="USD",
        )
        db.session.add(fy); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=fy.id,
            revenue=Decimal("46398"), gross_profit=Decimal("19911"),
            operating_income=Decimal("3797"), inventory=Decimal("7501"),
            net_income=Decimal("3108"), cfo=Decimal("3500"), capex=Decimal("900"),
            fcf=Decimal("2600"), source_map={"inventory": {"provider": "SEC"}}, quality={},
        ))

        # A complete same-date TTM is reconstructable, but its Q4 instant can be
        # thinner than the audited FY balance sheet. FY and TTM are economically
        # the same duration at year-end, so the richer filed FY must be current.
        for period_type, end_date, revenue in (
            ("Q1", date(2025, 8, 31), "11000"),
            ("Q2", date(2025, 11, 30), "11200"),
            ("Q3", date(2026, 2, 28), "11800"),
            ("Q4", date(2026, 5, 31), "12398"),
        ):
            period = FinancialPeriod(
                company_id=company.id, period_type=period_type, fiscal_year=2026,
                end_date=end_date, currency="USD",
            )
            db.session.add(period); db.session.flush()
            db.session.add(NormalizedFinancial(
                financial_period_id=period.id,
                revenue=Decimal(revenue), inventory=None,
                source_map={"revenue": {"provider": "SEC"}}, quality={},
            ))
        db.session.commit()

        current = current_row(company.id)
        assert current is not None
        assert current["period_type"] == "FY"
        assert current["period_end"] == "2026-05-31"
        assert current["revenue"] == 46398.0
        assert current["inventory"] == 7501.0
        assert current["metrics"]["operating_margin_pct"] is not None
        assert current["comparison_basis"] == "LATEST_FILED_FY"


def test_033_newer_fy_beats_older_ttm_after_new_10k(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "new_10k_basis")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="July Software Co", display_name="July Software Co")
        db.session.add(company); db.session.flush()

        fy = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2026,
            start_date=date(2025, 8, 1), end_date=date(2026, 7, 31), currency="USD",
        )
        db.session.add(fy); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=fy.id,
            revenue=Decimal("21448"), operating_income=Decimal("5884"),
            net_income=Decimal("4566"), cfo=Decimal("7000"), capex=Decimal("1200"),
            fcf=Decimal("5800"), source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))

        # Latest reconstructable TTM ends at Q3 because Q4 has not been derived.
        for period_type, fiscal_year, end_date, revenue in (
            ("Q4", 2025, date(2025, 7, 31), "4500"),
            ("Q1", 2026, date(2025, 10, 31), "4800"),
            ("Q2", 2026, date(2026, 1, 31), "5000"),
            ("Q3", 2026, date(2026, 4, 30), "5200"),
        ):
            period = FinancialPeriod(
                company_id=company.id, period_type=period_type, fiscal_year=fiscal_year,
                end_date=end_date, currency="USD",
            )
            db.session.add(period); db.session.flush()
            db.session.add(NormalizedFinancial(
                financial_period_id=period.id, revenue=Decimal(revenue),
                source_map={"revenue": {"provider": "SEC"}}, quality={},
            ))
        db.session.commit()

        current = current_row(company.id)
        assert current is not None
        assert current["period_type"] == "FY"
        assert current["period_end"] == "2026-07-31"
        assert current["revenue"] == 21448.0
        assert round(current["metrics"]["operating_margin_pct"], 2) == round(5884 / 21448 * 100, 2)


def test_033_user_facing_templates_never_hardcode_release_versions():
    import re
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "mfapp" / "templates"
    offenders = []
    for template in templates.glob("*.html"):
        for line_no, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\b0\.\d+\.\d+\b", line):
                offenders.append(f"{template.name}:{line_no}:{line.strip()}")
    assert offenders == [], "User-facing templates must not hardcode product/release versions: " + " | ".join(offenders)


def test_033_parser_revision_repairs_same_end_date_fiscal_label_in_place(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "period_relabel_in_place")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Week Calendar Co", display_name="Week Calendar Co")
        db.session.add(company); db.session.flush()
        source = Source(
            company_id=company.id, provider="SEC", source_type="COMPANYFACTS",
            title="test", url="https://example.test", retrieved_at=datetime.utcnow(),
            content_hash="test-period-relabel", meta={},
        )
        db.session.add(source); db.session.flush()

        # Simulate the old bug: Sep 30, 2023 was incorrectly labeled FY2024.
        stale = FinancialPeriod(
            company_id=company.id, source_id=source.id, period_type="FY",
            fiscal_year=2024, end_date=date(2023, 9, 30), currency="USD",
        )
        db.session.add(stale); db.session.flush()
        stale_id = stale.id
        db.session.commit()

        repaired = _upsert_period(
            company, source, period_type="FY", fiscal_year=2023,
            end_date=date(2023, 9, 30),
            anchor={"end": "2023-09-30", "filed": "2023-11-03", "accn": "TEST-AAPL-2023"},
        )
        db.session.flush()

        assert repaired.id == stale_id
        assert repaired.fiscal_year == 2023
        assert FinancialPeriod.query.filter_by(
            company_id=company.id, period_type="FY", end_date=date(2023, 9, 30)
        ).count() == 1



def test_033_live_read_prefers_rich_correct_row_over_newer_sparse_duplicate(tmp_path, monkeypatch):
    from mfapp.current_financials import annual_rows, current_row

    app = make_app(tmp_path, monkeypatch, "legacy_duplicate_read")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Legacy Duplicate Co", display_name="Legacy Duplicate Co")
        db.session.add(company); db.session.flush()

        correct = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2026,
            end_date=date(2026, 5, 31), filed_at=date(2026, 7, 24), currency="USD",
        )
        db.session.add(correct); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=correct.id,
            revenue=Decimal("46398"), gross_profit=Decimal("19911"),
            operating_income=Decimal("3797"), inventory=Decimal("7501"),
            net_income=Decimal("3108"), cfo=Decimal("3500"), capex=Decimal("900"),
            fcf=Decimal("2600"),
            source_map={
                "revenue": {"provider": "SEC"},
                "operating_income": {"provider": "SEC"},
                "inventory": {"provider": "SEC"},
            },
            quality={},
        ))

        # Simulate production legacy pollution: same represented date, wrong FY,
        # newer DB id, and a much thinner normalized shell.
        stale = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2027,
            end_date=date(2026, 5, 31), filed_at=date(2026, 7, 24), currency="USD",
        )
        db.session.add(stale); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=stale.id,
            revenue=None, inventory=None, source_map={}, quality={},
        ))
        db.session.commit()

        rows = annual_rows(company.id, 5)
        assert len(rows) == 1
        assert rows[0]["period_id"] == correct.id
        assert rows[0]["fiscal_year"] == 2026
        assert rows[0]["revenue"] == 46398.0
        assert rows[0]["inventory"] == 7501.0
        assert round(rows[0]["metrics"]["operating_margin_pct"], 2) == 8.18

        current = current_row(company.id)
        assert current["period_id"] == correct.id
        assert current["inventory"] == 7501.0

        audit = _reconcile_financial_field_issues(company)
        assert "inventory" not in audit["missing_expected_fields"]

        from mfapp.secdata import _reconcile_annual_history_issues
        history_audit = _reconcile_annual_history_issues(company, target_years=1)
        assert history_audit["years"] == [2026]

        from mfapp.services import financial_rows
        published_rows = financial_rows(company.id, 5)
        assert len(published_rows) == 1
        assert published_rows[0]["period_id"] == correct.id
        assert published_rows[0]["inventory"] == 7501.0


def test_033_exact_correct_period_quarantines_existing_same_end_stale_sibling(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "quarantine_duplicate_identity")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Quarantine Co", display_name="Quarantine Co")
        db.session.add(company); db.session.flush()
        source = Source(
            company_id=company.id, provider="SEC", source_type="COMPANYFACTS",
            title="test", url="https://example.test", retrieved_at=datetime.utcnow(),
            content_hash="quarantine-test", meta={},
        )
        db.session.add(source); db.session.flush()

        correct = FinancialPeriod(
            company_id=company.id, source_id=source.id, period_type="FY",
            fiscal_year=2023, end_date=date(2023, 9, 30), currency="USD",
        )
        db.session.add(correct); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=correct.id, revenue=Decimal("383285"),
            source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))

        stale = FinancialPeriod(
            company_id=company.id, source_id=source.id, period_type="FY",
            fiscal_year=2024, end_date=date(2023, 9, 30), currency="USD",
        )
        db.session.add(stale); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=stale.id, revenue=None, source_map={}, quality={},
        ))
        db.session.commit()

        chosen = _upsert_period(
            company, source, period_type="FY", fiscal_year=2023,
            end_date=date(2023, 9, 30),
            anchor={"end": "2023-09-30", "filed": "2023-11-03", "accn": "AAPL-2023"},
        )
        db.session.flush()

        assert chosen.id == correct.id
        stale = db.session.get(FinancialPeriod, stale.id)
        assert stale.period_type == "SUPERSEDED_FY"
        assert stale.normalized.quality["period_identity_state"] == "SUPERSEDED"
        assert FinancialPeriod.query.filter_by(
            company_id=company.id, period_type="FY", end_date=date(2023, 9, 30)
        ).count() == 1


def test_033_research_basis_ignores_legacy_duplicate_identity(tmp_path, monkeypatch):
    from mfapp.research_basis import latest_financial_basis

    app = make_app(tmp_path, monkeypatch, "canonical_research_basis")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Basis Co", display_name="Basis Co")
        db.session.add(company); db.session.flush()

        correct = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2026,
            end_date=date(2026, 7, 31), filed_at=date(2026, 9, 1), currency="USD",
        )
        db.session.add(correct); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=correct.id, revenue=Decimal("21448"),
            operating_income=Decimal("5884"), source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))
        stale = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2027,
            end_date=date(2026, 7, 31), filed_at=date(2026, 9, 1), currency="USD",
        )
        db.session.add(stale); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=stale.id, source_map={}, quality={},
        ))
        db.session.commit()

        basis = latest_financial_basis(company.id)
        assert basis["available"] is True
        assert basis["period_id"] == correct.id
        assert basis["fiscal_year"] == 2026
        assert basis["period_end"] == "2026-07-31"
