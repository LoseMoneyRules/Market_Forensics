from __future__ import annotations

from datetime import date
from decimal import Decimal
import inspect

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, FinancialPeriod, NormalizedFinancial
from mfapp.extensions import db
from mfapp.positioning import _aggregate_trade_flow


def make_app(tmp_path, monkeypatch, name="035"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "035-surface-coherence",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def test_035_current_row_merges_split_same_period_evidence(tmp_path, monkeypatch):
    from mfapp.current_financials import current_row

    app = make_app(tmp_path, monkeypatch, "same_period_merge")
    with app.app_context():
        db.create_all()
        company = Company(legal_name="Split Evidence Co", display_name="Split Evidence Co")
        db.session.add(company)
        db.session.flush()

        active = FinancialPeriod(
            company_id=company.id, period_type="FY", fiscal_year=2026,
            end_date=date(2026, 5, 31), currency="USD",
        )
        db.session.add(active)
        db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=active.id,
            revenue=Decimal("46398"), gross_profit=Decimal("19911"),
            operating_income=Decimal("3797"), inventory=None,
            source_map={"revenue": {"provider": "SEC"}}, quality={},
        ))

        sibling = FinancialPeriod(
            company_id=company.id, period_type="SUPERSEDED_FY", fiscal_year=2026,
            end_date=date(2026, 5, 31), currency="USD",
        )
        db.session.add(sibling)
        db.session.flush()
        db.session.add(NormalizedFinancial(
            financial_period_id=sibling.id,
            revenue=None, inventory=Decimal("7501"),
            receivables=Decimal("5931"),
            source_map={
                "inventory": {"provider": "SEC", "tag": "InventoryNet"},
                "receivables": {"provider": "SEC"},
            },
            quality={},
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


def test_035_low_direction_coverage_is_visible_but_not_scorable():
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
    assert flow["eligible_volume_pct"] == 20.0
    assert flow["sanity_status"] == "PASS"
    assert flow["observation_usable"] is True
    assert flow["decision_usable"] is False
    assert "DIRECTION_ELIGIBLE_VOLUME_TOO_LOW" in flow["decision_reasons"]


def test_035_recalculate_is_the_research_cache_materialization_boundary():
    from mfapp import jobs

    source = inspect.getsource(jobs._execute)
    recalc = source.split('if kind == "RECALCULATE":', 1)[1].split('if kind == "RESEARCH_PREFILL":', 1)[0]
    assert "refresh_research_cache" in recalc
    assert 'result["research_cache"]' in recalc


def test_035_positioning_refresh_repairs_price_spine_too():
    from mfapp import routes_publish

    source = inspect.getsource(routes_publish.queue_refresh)
    positioning = source.split('if kind == "positioning":', 1)[1]
    assert '"PRICE_HISTORY_REFRESH"' in positioning
    assert '"lookback_years": 2' in positioning
