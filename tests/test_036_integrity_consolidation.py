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
