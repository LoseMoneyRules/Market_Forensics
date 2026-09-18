from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Event, FinancialPeriod, NormalizedFinancial, Security
from mfapp.extensions import db
from mfapp.financial_flow_engine import build_income_statement_flow
from mfapp.decision_support import tape_context_metrics
from mfapp.current_financials import numbers_completeness
from mfapp.models import User
from mfapp.routes import SECTIONS
from mfapp.secdata import (
    _AV_BALANCE_FIELDS, _AV_CASH_FIELDS, _AV_INCOME_FIELDS, _compose_debt,
    _finish_normalized, _semantic_tag_groups,
)
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace
from mfapp.research_cache import cache_event_type
from mfapp.triangulation_engine import apply_peer_valuation_overlay


def make_app(tmp_path, monkeypatch, name="028"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "028-release",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control_workspace(app, ticker="EXM"):
    with app.app_context():
        db.create_all()
        user = User(
            email=f"{ticker.lower()}028@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        company = Company(
            legal_name="Example Industrial Co",
            display_name="Example Industrial Co",
            sector="Industrials",
            industry="Industrial Machinery",
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
        db.session.commit()
        return user.id


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_028_reports_do_not_use_wsgi_file_wrapper(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "report_wrapper")
    uid = seed_control_workspace(app)
    client = app.test_client()
    login(client, uid)

    def hostile_wrapper(*args, **kwargs):
        raise AssertionError("wsgi.file_wrapper must not be used for in-memory reports")

    for url, prefix in (
        ("/company/EXM/report/pdf?mode=full", b"%PDF"),
        ("/company/EXM/report/docx?mode=full", b"PK"),
    ):
        response = client.get(url, environ_overrides={"wsgi.file_wrapper": hostile_wrapper})
        assert response.status_code == 200
        assert response.data.startswith(prefix)
        assert "attachment;" in response.headers.get("Content-Disposition", "")
        assert response.headers.get("Cache-Control") == "no-store, max-age=0"


def test_028_safe_report_fallback_handles_real_fundamentals(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "report_fundamentals")
    uid = seed_control_workspace(app)
    with app.app_context():
        company = Company.query.filter_by(display_name="Example Industrial Co").first()
        period = FinancialPeriod(
            company_id=company.id,
            period_type="FY",
            fiscal_year=2025,
            end_date=date(2025, 12, 31),
            currency="USD",
        )
        db.session.add(period)
        db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=period.id,
            revenue=Decimal("1000"),
            cogs=Decimal("600"),
            gross_profit=Decimal("400"),
            operating_income=Decimal("250"),
            pretax_income=Decimal("220"),
            income_tax=Decimal("50"),
            net_income=Decimal("170"),
            cfo=Decimal("210"),
            capex=Decimal("40"),
            fcf=Decimal("170"),
            receivables=Decimal("120"),
            inventory=Decimal("80"),
            debt=Decimal("100"),
            cash=Decimal("40"),
            equity=Decimal("500"),
        ))
        db.session.commit()

    client = app.test_client()
    login(client, uid)

    def fail_rich(*args, **kwargs):
        raise RuntimeError("force rich renderer failure")

    def emergency_must_not_run(*args, **kwargs):
        raise AssertionError("safe renderer fallback should handle populated fundamentals")

    monkeypatch.setattr("mfapp.reporting.render_pdf", fail_rich)
    monkeypatch.setattr("mfapp.reporting.render_docx", fail_rich)
    monkeypatch.setattr("mfapp.routes_publish.emergency_research_report_stream", emergency_must_not_run)

    pdf = client.get("/company/EXM/report/pdf?mode=full")
    docx = client.get("/company/EXM/report/docx?mode=full")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert docx.status_code == 200 and docx.data.startswith(b"PK")


def test_028_polish_contract_keeps_expectations_forward_and_ui_readable():
    kpis = Path("mfapp/templates/_research_kpis.html").read_text()
    template = Path("mfapp/templates/company_section.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    readiness = Path("mfapp/templates/_process_readiness.html").read_text()
    reporting = Path("mfapp/reporting.py").read_text()

    assert "{% elif section == 'expectations' %}" not in kpis
    assert "research-strip-summary .signal-dot{font-weight:400!important}" in css
    assert ".evidence-score-compact strong{font-size:20px" in css
    assert "tone-{{ signal.tone }}" in template
    assert "Tape posture" in template and "Directional pressure" in template and "Next confirmation" in template
    assert "HOW MONITORING WORKS" in template
    assert "Suggested cadence and triggers only" in template
    assert "leverage_display" in template
    assert readiness.index("READY TO VALIDATE.") < readiness.index("publish-readiness")
    assert readiness.index("READY TO VALIDATE.") > readiness.index("{% endfor %}")
    assert "Research intelligence" in reporting
    assert "Valuation map" in reporting
    assert "Thesis / variant perception" in reporting
    assert "Current fundamentals" in reporting
    assert Path("VERSION").read_text().strip() == "0.2.8"


def test_028_tape_context_turns_scores_into_wait_long_short_or_lateral():
    lateral = tape_context_metrics({
        "long_demand": 58, "bear_pressure": 53, "price_resilience": 52,
        "battle_intensity": 45, "confidence": "MEDIUM", "regime": "MIXED",
    })
    assert lateral["pressure_direction"] == "LATERAL"
    assert lateral["posture"] == "WAIT FOR CONFIRMATION"

    long = tape_context_metrics({
        "long_demand": 75, "bear_pressure": 48, "price_resilience": 62,
        "battle_intensity": 42, "confidence": "HIGH", "regime": "SUPPORTIVE",
    })
    assert long["pressure_direction"] == "LONG"
    assert long["posture"] == "SUPPORTIVE TAPE"

    short = tape_context_metrics({
        "long_demand": 35, "bear_pressure": 66, "price_resilience": 39,
        "battle_intensity": 50, "confidence": "HIGH", "regime": "HOSTILE",
    })
    assert short["pressure_direction"] == "SHORT"
    assert short["posture"] == "HOSTILE TAPE"


def test_028_second_polish_layout_contract():
    dashboard = Path("mfapp/templates/dashboard.html").read_text()
    fundamentals = Path("mfapp/templates/company_section.html").read_text()
    validate = Path("mfapp/templates/validate.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()

    assert dashboard.count("command-table-note") == 1
    assert dashboard.index("coverage-table") < dashboard.index("command-table-note") < dashboard.index("command-refresh-actions-bottom")
    assert "process-ready-only" in dashboard
    assert "{% if row.readiness.ready_to_validate %}" in dashboard
    assert validate.index('_research_tabs.html') < validate.index('validation-run-bar')
    assert "fundamentals-current-strip" in fundamentals
    assert fundamentals.index("Analyst notes") < fundamentals.index("DATA COMPLETENESS")
    assert "ANALYSIS READY" in fundamentals and "missing_derived_metrics" in fundamentals
    assert ".tape-context-card{border-top:" not in css
    assert ".tape-context-card{padding:10px 12px!important" in css
    assert Path("VERSION").read_text().strip() == "0.2.8"


def test_028_debt_components_recover_nike_style_total_debt():
    direct = {"tag": "LongTermDebt", "val": "7030"}
    current = {"tag": "LongTermDebtCurrent", "val": "999"}
    noncurrent = {"tag": "LongTermDebtNoncurrent", "val": "7030"}
    short_term = {"tag": "ShortTermBorrowings", "val": "0"}

    value, records, method = _compose_debt(direct, current, noncurrent, short_term)
    assert value == Decimal("8029")
    assert method == "SUM_CURRENT_NONCURRENT_DEBT"
    assert {row["tag"] for row in records} == {
        "LongTermDebtCurrent", "LongTermDebtNoncurrent", "ShortTermBorrowings",
    }

    combined = {"tag": "DebtLongtermAndShorttermCombinedAmount", "val": "8500"}
    value, records, method = _compose_debt(combined, current, noncurrent, short_term)
    assert value == Decimal("8500")
    assert method == "DIRECT_COMBINED_DEBT"
    assert records == [combined]

    value, records, method = _compose_debt(direct, None, None, None)
    assert value is None
    assert records == []
    assert method == ""


def test_028_exact_filing_label_fallback_can_find_extension_taxonomy():
    companyfacts = {
        "facts": {
            "nke": {
                "InventoriesCustom": {"label": "Inventories", "units": {}},
                "CostOfSalesCustom": {"label": "Cost of sales", "units": {}},
                "SegmentInventories": {"label": "Inventories by geographic area", "units": {}},
            },
            "us-gaap": {
                "InventoryNet": {"label": "Inventory, Net", "units": {}},
            },
        }
    }
    inventory = _semantic_tag_groups(companyfacts, "inventory")
    cogs = _semantic_tag_groups(companyfacts, "cogs")
    assert "InventoriesCustom" in inventory["nke"]
    assert "SegmentInventories" not in inventory["nke"]
    assert "CostOfSalesCustom" in cogs["nke"]


def test_028_data_completeness_flags_historical_field_that_disappears(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "completeness")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Gap Co", display_name="Gap Co")
        db.session.add(company); db.session.flush()

        fy = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2025,
            end_date=date(2025, 5, 31), currency="USD",
        )
        db.session.add(fy); db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=fy.id,
            revenue=Decimal("1000"), cogs=Decimal("600"), gross_profit=Decimal("400"),
            operating_income=Decimal("150"), pretax_income=Decimal("140"), income_tax=Decimal("30"),
            net_income=Decimal("110"), cfo=Decimal("160"), capex=Decimal("40"), fcf=Decimal("120"),
            inventory=Decimal("100"), receivables=Decimal("90"), payables=Decimal("70"),
            cash=Decimal("80"), debt=Decimal("200"), equity=Decimal("500"),
        ))

        quarter_specs = [
            ("Q1", date(2026, 8, 31)), ("Q2", date(2026, 11, 30)),
            ("Q3", date(2027, 2, 28)), ("Q4", date(2027, 5, 31)),
        ]
        for idx, (period_type, end_date) in enumerate(quarter_specs, start=1):
            p = FinancialPeriod(
                company_id=company.id, period_type=period_type, fiscal_year=2027,
                end_date=end_date, currency="USD",
            )
            db.session.add(p); db.session.flush()
            db.session.add(NormalizedFinancial(
                financial_period_id=p.id,
                revenue=Decimal("260"), cogs=Decimal("150"), gross_profit=Decimal("110"),
                operating_income=Decimal("40"), pretax_income=Decimal("38"), income_tax=Decimal("8"),
                net_income=Decimal("30"), cfo=Decimal("42"), capex=Decimal("10"), fcf=Decimal("32"),
                inventory=None if period_type == "Q4" else Decimal(str(100 + idx)),
                receivables=Decimal("95"), payables=Decimal("72"),
                cash=Decimal("85"), debt=Decimal("195"), equity=Decimal("510"),
            ))
        db.session.commit()

        completeness = numbers_completeness(company.id)
        assert completeness["ttm_ready"] is True
        assert "inventory" in completeness["missing_continuity_fields"]
        assert "DIO" in completeness["missing_derived_metrics"]
        assert "Inventory / Revenue" in completeness["missing_derived_metrics"]
        assert completeness["analysis_ready"] is False


def test_028_readiness_links_and_coverage_alpha_sort_contract():
    readiness = Path("mfapp/templates/_process_readiness.html").read_text()
    routes = Path("mfapp/routes.py").read_text()
    css = Path("mfapp/static/css/app.css").read_text()

    assert "class=\"gate-link\"" in readiness
    assert "section=gate.key" in readiness
    assert 'rows.sort(key=lambda row: str(row["security"].ticker or "").upper())' in routes
    assert ".gate-link{color:var(--navy);font-weight:400" in css
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert "use as little bold as possible" in state
    assert Path("VERSION").read_text().strip() == "0.2.8"


def test_028_business_renders_with_partial_peer_overlay_cache(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "business_partial_peer")
    uid = seed_control_workspace(app, ticker="ORCL")
    with app.app_context():
        coverage = Coverage.query.join(Security, Coverage.security_id == Security.id).filter(Security.ticker == "ORCL").first()
        security = db.session.get(Security, coverage.security_id)
        company = db.session.get(Company, security.company_id)
        db.session.add(Event(
            company_id=company.id,
            event_type=cache_event_type(coverage.id),
            title="ORCL research cache",
            event_date=datetime.now(timezone.utc).replace(tzinfo=None),
            payload={
                "valuation": {
                    "current_price": 170.0,
                    "bear": 130.0,
                    "base": 190.0,
                    "bull": 230.0,
                    "expected_value": 185.0,
                    # Reproduce the pre-fix non-applied shape that omitted intrinsic_base.
                    "peer_overlay": {
                        "applied": False,
                        "reason": "Insufficient peer valuation evidence.",
                        "eligible": False,
                        "estimate": None,
                        "peer_count": 3,
                        "method_count": 1,
                    },
                },
                "triangulation": {
                    "available": True,
                    "reason": "",
                    "method": "SIC DIVISION",
                    "sic": "7372",
                    "sic_description": "Prepackaged Software",
                    "peers": [{"ticker": "PEER", "name": "Peer Co", "match": "SIC DIVISION"}],
                    "comparisons": [],
                    "signals": [],
                    "peer_value_crosscheck": {
                        "eligible": False,
                        "estimate": None,
                        "peer_count": 3,
                        "method_count": 1,
                    },
                },
            },
        ))
        db.session.commit()

    client = app.test_client()
    login(client, uid)
    response = client.get("/company/ORCL/business")
    assert response.status_code == 200, response.data[:1000]
    assert b"Peer fair-value cross-check" in response.data
    assert b"Intrinsic Base" in response.data


def test_028_non_applied_peer_overlay_keeps_stable_schema():
    result = apply_peer_valuation_overlay(
        {"base": 190.0, "bear": 130.0, "bull": 230.0, "expected_value": 185.0, "current_price": 170.0},
        {"peer_value_crosscheck": {"eligible": False, "estimate": None, "peer_count": 3, "method_count": 1}},
    )
    overlay = result["peer_overlay"]
    assert overlay["applied"] is False
    assert overlay["weight"] == 0.0
    assert overlay["intrinsic_base"] == 190.0
    assert overlay["forensic_base"] == 190.0
    assert overlay["peer_estimate"] is None


def test_028_income_statement_is_sequential_revenue_to_net_waterfall():
    flow = build_income_statement_flow({
        "period_label": "FY2025",
        "revenue": 1000,
        "cogs": 600,
        "gross_profit": 400,
        "operating_expenses": 150,
        "operating_income": 250,
        "pretax_income": 220,
        "income_tax": 50,
        "net_income": 170,
    })
    assert flow["presentation"] == "WATERFALL"
    steps = flow["bridge_steps"]
    assert [row["label"] for row in steps] == [
        "Revenue",
        "COGS / Cost of Revenue",
        "Gross Profit",
        "Operating Expenses / Costs",
        "Operating Income",
        "Other / Interest",
        "Pre-Tax Income",
        "Income Tax",
        "Net Income",
    ]
    assert steps[0]["result"] == 1000
    assert steps[1]["value"] == -600
    assert steps[3]["value"] == -150
    assert steps[5]["value"] == -30
    assert steps[7]["value"] == -50
    assert steps[-1]["result"] == 170


def test_028_sec_normalization_recovers_gross_profit_from_direct_cogs():
    row = NormalizedFinancial(
        revenue=Decimal("1000"),
        cogs=Decimal("600"),
        gross_profit=None,
        operating_income=Decimal("250"),
    )
    source_map = {"revenue": {"method": "DIRECT_FY"}, "cogs": {"method": "DIRECT_FY"}}
    _finish_normalized(row, source_map, period_type="FY")
    assert row.gross_profit == Decimal("400")
    assert row.operating_expenses == Decimal("150")
    assert source_map["gross_profit"]["method"] == "REVENUE_MINUS_COGS"


def test_028_optional_fundamental_fallback_covers_all_three_statements():
    assert _AV_INCOME_FIELDS["gross_profit"] == ("grossProfit",)
    assert "equity" in _AV_BALANCE_FIELDS
    assert "cash" in _AV_BALANCE_FIELDS
    assert _AV_CASH_FIELDS["cfo"] == ("operatingCashflow",)
    assert _AV_CASH_FIELDS["capex"] == ("capitalExpenditures",)


def test_028_evidence_is_consolidated_at_bottom_before_reports():
    template = Path("mfapp/templates/company_section.html").read_text()
    evidence = template.index('class="panel evidence-summary-panel evidence-consolidated"')
    reports = template.index('class="panel export-research"')
    thesis = template.index("Thesis & variant perception")
    assert thesis < evidence < reports
    assert "For / against / signals" in template
    assert "Evidence signals</h2>" not in template
    assert "expectations-basis-strip" not in template


def test_028_validate_is_after_audit_and_links_to_real_validate_route():
    keys = [key for key, _ in SECTIONS]
    assert keys[-2:] == ["audit", "validate"]
    tabs = Path("mfapp/templates/_research_tabs.html").read_text()
    template = Path("mfapp/templates/company_section.html").read_text()
    assert "web.validate_company" in tabs
    assert "web.validate_company" in template


def test_028_deploy_excludes_vendor_trees_from_normal_mirrors():
    workflow = Path(".github/workflows/deploy-namecheap.yml").read_text()
    assert "rm -rf .release-payload/mfapp/_reporting_vendor .release-payload/mfapp/_vendor" in workflow
    assert workflow.count("--exclude='^_reporting_vendor(/|$)'") >= 4
    assert workflow.count("--exclude='^_vendor(/|$)'") >= 4
    assert "MF_REPORTING_VENDOR_UPLOAD" in workflow
