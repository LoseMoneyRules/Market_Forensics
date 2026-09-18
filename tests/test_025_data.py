from __future__ import annotations

from pathlib import Path


def test_025_sec_comparative_periods_use_economic_end_date():
    from mfapp.secdata import _annual_duration, _fiscal_year_from_end

    assert _fiscal_year_from_end({"end": "2024-12-31", "fy": 2026}, "1231") == 2024
    assert _fiscal_year_from_end({"end": "2025-04-30", "fy": 2025}, "0131") == 2026

    facts = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        {"form":"10-K","fp":"FY","fy":2025,"start":"2024-01-01","end":"2024-12-31","filed":"2025-02-01","accn":"a","val":100},
        {"form":"10-K","fp":"FY","fy":2026,"start":"2024-01-01","end":"2024-12-31","filed":"2026-02-01","accn":"b","val":101},
        {"form":"10-K","fp":"FY","fy":2026,"start":"2025-01-01","end":"2025-12-31","filed":"2026-02-01","accn":"c","val":120},
    ]}}}}}
    rows = _annual_duration(facts, ["RevenueFromContractWithCustomerExcludingAssessedTax"], "1231")
    assert set(rows) == {2024, 2025}
    assert rows[2024]["val"] == 101
    assert rows[2025]["val"] == 120


def test_025_ttm_requires_four_consecutive_fiscal_quarters():
    from mfapp.current_financials import _aggregate_quarters

    def q(fy, fp, end, revenue):
        return {
            "fiscal_year": fy, "period_type": fp, "period_end": end,
            "period_ids": [fy * 10], "revenue": revenue, "gross_profit": revenue * .4,
            "operating_income": revenue * .15, "net_income": revenue * .10,
            "cfo": revenue * .12, "capex": revenue * .02, "fcf": revenue * .10,
            "cash": 50, "debt": 20, "inventory": 15, "receivables": 20,
            "payables": 10, "shares_outstanding": 10, "diluted_shares": 10,
        }

    valid = [
        q(2025, "Q2", "2025-03-31", 100),
        q(2025, "Q3", "2025-06-30", 110),
        q(2025, "Q4", "2025-09-30", 120),
        q(2026, "Q1", "2025-12-31", 130),
    ]
    out = _aggregate_quarters(list(reversed(valid)), "TTM")
    assert out is not None and out["revenue"] == 460

    missing = [
        q(2025, "Q1", "2024-12-31", 90),
        q(2025, "Q2", "2025-03-31", 100),
        q(2025, "Q4", "2025-09-30", 120),
        q(2026, "Q1", "2025-12-31", 130),
    ]
    assert _aggregate_quarters(list(reversed(missing)), "TTM") is None


def test_025_fundamentals_are_quarter_auditable_and_mixed_chart():
    template = Path("mfapp/templates/company_section.html").read_text()
    routes = Path("mfapp/routes.py").read_text()
    js = Path("mfapp/static/js/app.js").read_text()
    current = Path("mfapp/current_financials.py").read_text()

    assert "quarterly_financials" in routes and "numbers_completeness" in routes
    assert "FILING QUARTERS" in template
    assert "DATA COMPLETENESS" in template
    assert "numbers_scale_series|tojson" in template
    assert "function revenueFcfChart" in js
    assert "Revenue · columns" in js and "FCF · line" in js
    assert "fillRect" in js
    assert "_quarter_sequence_value" in current
    assert "SAME_QUARTER_PRIOR_YEAR" in current


def test_025_release_handoff_has_numbers_correctness_rules():
    version = Path("VERSION").read_text().strip()
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert version == "0.2.7"
    assert "**State-Version: 0.2.7**" in state
    assert "four distinct fiscally consecutive quarters" in state
    assert "Companyfacts repeats comparative periods" in state
