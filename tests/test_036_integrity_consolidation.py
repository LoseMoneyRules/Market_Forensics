from __future__ import annotations

from datetime import date
from decimal import Decimal
import inspect
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, FinancialPeriod, NormalizedFinancial
from mfapp.extensions import db
from mfapp.positioning import FLOW_METHOD_VERSION, _aggregate_trade_flow


def make_app(tmp_path, monkeypatch, name="036"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "036-integrity-consolidation",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def test_036_current_row_coalesces_split_same_period_evidence_with_provenance(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "same_period")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Split Evidence Co", display_name="Split Evidence Co")
        db.session.add(company)
        db.session.flush()

        active = FinancialPeriod(
            company_id=company.id,
            period_type="FY",
            fiscal_year=2026,
            end_date=date(2026, 5, 31),
            currency="USD",
        )
        db.session.add(active)
        db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=active.id,
            revenue=Decimal("46398"),
            gross_profit=Decimal("19911"),
            operating_income=Decimal("3797"),
            inventory=None,
            source_map={"revenue": {"provider": "SEC"}},
            quality={},
        ))

        sibling = FinancialPeriod(
            company_id=company.id,
            period_type="SUPERSEDED_FY",
            fiscal_year=2026,
            end_date=date(2026, 5, 31),
            currency="USD",
        )
        db.session.add(sibling)
        db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=sibling.id,
            inventory=Decimal("7501"),
            receivables=Decimal("5931"),
            source_map={
                "inventory": {"provider": "SEC", "tag": "InventoryNet"},
                "receivables": {"provider": "SEC"},
            },
            quality={"economic_reality": {"facts": {"inventory": 7501.0}}},
        ))
        db.session.commit()

        current = current_row(company.id)
        assert current is not None
        assert current["revenue"] == 46398.0
        assert current["inventory"] == 7501.0
        assert current["receivables"] == 5931.0
        assert current["quality"]["same_period_recovery"] is True
        assert "inventory" in current["quality"]["same_period_recovered_fields"]
        assert current["source_map"]["inventory"]["recovered_from_period_id"] == sibling.id
        assert current["source_map"]["inventory"]["method"] == "SAME_PERIOD_EVIDENCE_COALESCE"
        assert current["quality"]["economic_reality"]["facts"]["inventory"] == 7501.0




def test_036_post_fye_cover_share_instant_does_not_create_next_fiscal_year():
    from mfapp.secdata import _annual_instant

    facts = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2026-07-31",
                                "val": 268000000,
                                "form": "10-K",
                                "fp": "FY",
                                "filed": "2026-09-09",
                                "accn": "TEST-INTU-FYE",
                            },
                            {
                                "end": "2026-08-31",
                                "val": 267236000,
                                "form": "10-K",
                                "fp": "FY",
                                "filed": "2026-09-09",
                                "accn": "TEST-INTU-COVER",
                            },
                        ]
                    }
                }
            }
        }
    }

    rows = _annual_instant(
        facts,
        ["EntityCommonStockSharesOutstanding"],
        namespace="dei",
        fiscal_year_end="0731",
    )
    assert set(rows) == {2026}
    assert rows[2026]["end"] == "2026-07-31"
    assert 2027 not in rows


def test_036_legacy_instant_only_fy_shell_cannot_become_current_basis(tmp_path, monkeypatch):
    from mfapp.current_financials import annual_rows, current_row

    app = make_app(tmp_path, monkeypatch, "phantom_fy")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="July Software Co", display_name="July Software Co")
        db.session.add(company)
        db.session.flush()

        prior = FinancialPeriod(
            company_id=company.id,
            period_type="FY",
            fiscal_year=2025,
            end_date=date(2025, 7, 31),
            currency="USD",
        )
        real = FinancialPeriod(
            company_id=company.id,
            period_type="FY",
            fiscal_year=2026,
            end_date=date(2026, 7, 31),
            currency="USD",
        )
        phantom = FinancialPeriod(
            company_id=company.id,
            period_type="FY",
            fiscal_year=2027,
            end_date=date(2026, 8, 31),
            currency="USD",
        )
        db.session.add_all([prior, real, phantom])
        db.session.flush()

        db.session.add_all([
            NormalizedFinancial(
                financial_period_id=prior.id,
                revenue=Decimal("18831"),
                operating_income=Decimal("4923"),
                net_income=Decimal("3869"),
                cfo=Decimal("6207"),
                capex=Decimal("84"),
                fcf=Decimal("6123"),
                source_map={"revenue": {"provider": "SEC"}},
                quality={},
            ),
            NormalizedFinancial(
                financial_period_id=real.id,
                revenue=Decimal("21448"),
                operating_income=Decimal("5884"),
                net_income=Decimal("4566"),
                cfo=Decimal("8838"),
                capex=Decimal("175"),
                fcf=Decimal("8663"),
                cash=Decimal("4705"),
                assets=Decimal("40000"),
                liabilities=Decimal("26000"),
                equity=Decimal("14000"),
                diluted_shares=Decimal("277"),
                source_map={"revenue": {"provider": "SEC"}},
                quality={},
            ),
            NormalizedFinancial(
                financial_period_id=phantom.id,
                shares_outstanding=Decimal("267236000"),
                source_map={
                    "shares_outstanding": {
                        "provider": "SEC",
                        "namespace": "dei",
                        "tag": "EntityCommonStockSharesOutstanding",
                    }
                },
                quality={},
            ),
        ])
        db.session.commit()

        rows = annual_rows(company.id, 5)
        assert [row["fiscal_year"] for row in rows] == [2026, 2025]

        current = current_row(company.id)
        assert current is not None
        assert current["fiscal_year"] == 2026
        assert current["period_end"] == "2026-07-31"
        assert current["revenue"] == 21448.0
        assert current["operating_income"] == 5884.0
        assert current["cfo"] == 8838.0
        assert current["metrics"]["revenue_growth_pct"] is not None

def test_036_sparse_newer_ttm_cannot_hide_complete_fy_current_basis(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "sparse_newer_ttm")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="July Software Co", display_name="July Software Co")
        db.session.add(company)
        db.session.flush()

        prior = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2024,
            end_date=date(2024, 7, 31), currency="USD",
        )
        current_fy = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2025,
            end_date=date(2025, 7, 31), currency="USD",
        )
        db.session.add_all([prior, current_fy])
        db.session.flush()
        db.session.add_all([
            NormalizedFinancial(
                financial_period_id=prior.id,
                revenue=Decimal("16000"), gross_profit=Decimal("12600"),
                operating_income=Decimal("3600"), pretax_income=Decimal("3400"),
                income_tax=Decimal("700"), net_income=Decimal("2700"),
                cfo=Decimal("4200"), capex=Decimal("500"), fcf=Decimal("3700"),
                cash=Decimal("3000"), debt=Decimal("6500"),
                receivables=Decimal("1200"), assets=Decimal("30000"),
                liabilities=Decimal("19000"), equity=Decimal("11000"),
                shares_outstanding=Decimal("280"), diluted_shares=Decimal("285"),
                source_map={"revenue": {"provider": "SEC"}}, quality={},
            ),
            NormalizedFinancial(
                financial_period_id=current_fy.id,
                revenue=Decimal("18800"), gross_profit=Decimal("14900"),
                operating_income=Decimal("4700"), pretax_income=Decimal("4450"),
                income_tax=Decimal("900"), net_income=Decimal("3550"),
                cfo=Decimal("5400"), capex=Decimal("650"), fcf=Decimal("4750"),
                cash=Decimal("4100"), debt=Decimal("7200"),
                receivables=Decimal("1450"), assets=Decimal("34000"),
                liabilities=Decimal("21000"), equity=Decimal("13000"),
                shares_outstanding=Decimal("282"), diluted_shares=Decimal("287"),
                source_map={"revenue": {"provider": "SEC"}}, quality={},
            ),
        ])

        # Reproduce the INTU-like failure mode: four consecutive quarters are
        # sufficient to synthesize a newer TTM because Revenue exists, but the
        # quarterly normalization is too sparse to support the rest of the
        # current decision surface. The complete FY must remain canonical.
        for period_type, fiscal_year, end_date, revenue in (
            ("Q4", 2025, date(2025, 7, 31), "4800"),
            ("Q1", 2026, date(2025, 10, 31), "5000"),
            ("Q2", 2026, date(2026, 1, 31), "5200"),
            ("Q3", 2026, date(2026, 4, 30), "5400"),
        ):
            period = FinancialPeriod(
                company_id=company.id, period_type=period_type, fiscal_year=fiscal_year,
                end_date=end_date, currency="USD",
            )
            db.session.add(period)
            db.session.flush()
            db.session.add(NormalizedFinancial(
                financial_period_id=period.id,
                revenue=Decimal(revenue),
                source_map={"revenue": {"provider": "SEC"}},
                quality={},
            ))
        db.session.commit()

        current = current_row(company.id)
        assert current is not None
        assert current["period_type"] == "FY"
        assert current["period_end"] == "2025-07-31"
        assert current["comparison_basis"] == "LATEST_COMPLETE_FY_TTM_WITHHELD"
        assert current["revenue"] == 18800.0
        assert current["gross_profit"] == 14900.0
        assert current["operating_income"] == 4700.0
        assert current["fcf"] == 4750.0
        assert current["assets"] == 34000.0
        assert current["metrics"]["revenue_growth_pct"] == 17.5
        assert current["quality"]["newer_ttm_withheld"] is True
        assert current["quality"]["withheld_ttm_period_end"] == "2026-04-30"
        assert "gross_profit" in current["quality"]["withheld_ttm_missing_fields"]
        assert "cfo" in current["quality"]["withheld_ttm_missing_fields"]

def test_036_low_direction_coverage_is_visible_but_not_scorable():
    rows = [
        {"p": 100.00, "s": 10, "c": [], "t": "2026-09-21T14:00:00Z", "x": "N"},
        {"p": 100.10, "s": 10, "c": [], "t": "2026-09-21T14:00:01Z", "x": "N"},
        {"p": 100.20, "s": 80, "c": ["I"], "t": "2026-09-21T14:00:02Z", "x": "N"},
    ]
    flow = _aggregate_trade_flow(date(2026, 9, 21), {
        "rows": rows,
        "feed": "sip",
        "feed_scope": "CONSOLIDATED_SIP",
        "complete": True,
        "sampled": False,
        "reference_bar": {"volume": 100, "reference_price": 100.0},
        "errors": [],
    })
    assert flow["method_version"] == FLOW_METHOD_VERSION
    assert flow["eligible_volume_pct"] == 20.0
    assert flow["sanity_status"] == "PASS"
    assert flow["observation_usable"] is True
    assert flow["decision_usable"] is False
    assert "DIRECTION_ELIGIBLE_VOLUME_TOO_LOW" in flow["decision_reasons"]


def test_036_recalculate_is_single_research_cache_materialization_boundary():
    from mfapp import jobs, research_cache

    recalc = inspect.getsource(jobs.recalculate_company)
    execute = inspect.getsource(jobs._execute)
    cache_source = inspect.getsource(research_cache)

    assert recalc.count("refresh_research_cache") == 1
    assert "patch_research_cache_tape" not in execute
    assert "_publish_tape_after_evidence" not in execute
    assert "patch_research_cache_tape" not in cache_source
    assert 'result["recalculate_job_id"] = _queue_recalculate_after_evidence' in execute


def test_036_positioning_refresh_repairs_price_spine_and_tape_get_stays_db_only():
    from mfapp import routes, routes_publish

    refresh_source = inspect.getsource(routes_publish.queue_refresh)
    positioning = refresh_source.split('if kind == "positioning":', 1)[1]
    assert '"PRICE_HISTORY_REFRESH"' in positioning
    assert '"lookback_years": 2' in positioning

    display_source = inspect.getsource(routes._materialized_tape_for_display)
    assert "tape_series(security, 12)" in display_source
    assert "latest_evidence" in display_source
    assert "requests." not in display_source
    assert "provider_status" not in display_source


def test_036_recovered_financial_source_label_remains_visible():
    template = Path("mfapp/templates/company_section.html").read_text()
    assert "value.get('source') or value.get('provider')" in template
    assert "SAME_PERIOD_EVIDENCE_COALESCE" in Path("mfapp/current_financials.py").read_text()


def test_036_release_metadata_is_single_current_version():
    version = Path("VERSION").read_text().strip()
    current = Path("docs/CURRENT_STATE.md").read_text()
    how = Path("docs/HOW_MARKET_FORENSICS_WORKS.md").read_text()

    assert version == "0.3.6"
    assert "**State-Version: 0.3.6**" in current
    assert "**Current product line:** 0.3.6" in how
    assert "PR #70" in current and "superseded" in current.lower()
    production_lines = [line for line in current.splitlines() if line.startswith("**Production:**")]
    assert len(production_lines) == 1

    deploy_workflow = Path(".github/workflows/deploy-namecheap.yml").read_text()
    assert "expected exactly one top-level Production line" in deploy_workflow
