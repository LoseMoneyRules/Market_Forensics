from __future__ import annotations

import math

from mfapp.calculations import financial_metrics
from mfapp.decision_engine import build_research_intelligence
from mfapp.discovery_forensics import _signals, discovery_opportunity
from mfapp.economic_reality import build_economic_reality, has_suppression
from mfapp.company_quality import build_company_quality
from mfapp.valuation_engine import default_cases, evaluate, metrics_from_history
from tools.sync_production_state import sync_current_state


def _cmg_like_snapshot():
    # 2025 CMG-scale economics: the test is intentionally synthetic and provider-free.
    # It protects the classification contract rather than snapshotting a live filing.
    row = {
        "revenue": 11_925_601_000.0,
        "operating_income": 1_700_000_000.0,
        "pretax_income": 1_720_000_000.0,
        "income_tax": 430_000_000.0,
        "cfo": 2_000_000_000.0,
        "capex": 1_100_000_000.0,
        "fcf": 900_000_000.0,
        "cash": 1_000_000_000.0,
        "debt": None,
        "liabilities": 6_163_924_000.0,
        "equity": 3_000_000_000.0,
        "shares_outstanding": 1_360_000_000.0,
        "diluted_shares": 1_360_000_000.0,
    }
    facts = {
        "cash_unrestricted": 1_000_000_000.0,
        "operating_lease_current": 302_380_000.0,
        "operating_lease_noncurrent": 4_773_434_000.0,
        "operating_lease_rou_asset": 4_463_010_000.0,
        "depreciation_amortization": 650_000_000.0,
    }
    snapshot = build_economic_reality(row, facts=facts)
    row["quality"] = {"economic_reality": snapshot}
    return row, snapshot


def test_030_cmg_like_operating_leases_are_not_financial_debt():
    row, economic = _cmg_like_snapshot()
    leases = 302_380_000.0 + 4_773_434_000.0

    assert math.isclose(economic["operating_lease_liability"], leases)
    assert math.isclose(economic["operating_lease_share_of_liabilities_pct"], leases / row["liabilities"] * 100.0)
    assert math.isclose(economic["lease_revenue_productivity_x"], row["revenue"] / leases)
    assert economic["debt_basis"] == "LEASE_HEAVY_NO_FILED_FINANCIAL_DEBT"
    assert economic["gross_economic_debt"] == 0.0
    assert economic["economic_net_debt"] == -1_000_000_000.0
    assert not economic["material_unresolved"]
    assert "RAW_LIABILITY_LEVERAGE" in economic["suppressions"]


def test_030_canonical_valuation_bridge_excludes_operating_lease_liability():
    row, economic = _cmg_like_snapshot()
    metrics = metrics_from_history([{"fiscal_year": 2025, **row}])

    assert metrics["net_debt_basis"] == "LEASE_HEAVY_NO_FILED_FINANCIAL_DEBT"
    assert metrics["net_debt"] == economic["economic_net_debt"]
    assert metrics["net_debt"] == -1_000_000_000.0
    assert metrics["basis_usable"]


def test_030_finance_leases_supplier_finance_and_enterprise_claims_are_in_bridge():
    row = {
        "revenue": 5_000.0, "cash": 500.0, "debt": None,
        "liabilities": 3_000.0, "equity": 2_000.0,
    }
    economic = build_economic_reality(row, facts={
        "cash_unrestricted": 400.0,
        "financial_debt_noncurrent": 1_000.0,
        "finance_lease_noncurrent": 200.0,
        "supplier_finance_current": 150.0,
        "pension_liability": 100.0,
        "noncontrolling_interest": 80.0,
        "preferred_equity": 70.0,
        "contingent_consideration_noncurrent": 50.0,
    })
    assert economic["gross_economic_debt"] == 1_650.0
    assert economic["liquid_offset"] == 400.0
    assert economic["economic_net_debt"] == 1_250.0


def test_030_restricted_cash_does_not_offset_economic_debt():
    economic = build_economic_reality(
        {"revenue": 1_000.0, "cash": 400.0, "debt": 700.0, "liabilities": 900.0, "_debt_source_tag": "LongTermDebt"},
        facts={"restricted_cash_current": 250.0},
    )
    assert economic["unrestricted_cash_for_debt_offset"] == 150.0
    assert economic["economic_net_debt"] == 550.0


def test_030_deferred_revenue_is_not_debt_and_customer_financing_is_context():
    economic = build_economic_reality(
        {"revenue": 2_000.0, "cash": 300.0, "debt": 0.0, "liabilities": 1_000.0, "_debt_source_tag": "LongTermDebt"},
        facts={"cash_unrestricted": 300.0, "deferred_revenue_current": 450.0},
    )
    assert economic["deferred_revenue"] == 450.0
    assert economic["gross_economic_debt"] == 0.0
    assert economic["economic_net_debt"] == -300.0
    assert any(flag["code"] == "CUSTOMER_FINANCING_MATERIAL" for flag in economic["flags"])


def test_030_growth_capex_proxy_prevents_automatic_fcf_short():
    current = {
        "revenue": 1_000.0, "operating_income": 100.0, "pretax_income": 90.0, "income_tax": 20.0,
        "net_income": 70.0, "cfo": 120.0, "capex": 200.0, "fcf": -80.0,
        "cash": 50.0, "debt": 0.0, "liabilities": 200.0, "equity": 500.0,
        "_debt_source_tag": "LongTermDebt",
    }
    economic = build_economic_reality(current, facts={
        "cash_unrestricted": 50.0,
        "depreciation_amortization": 60.0,
    })
    current["quality"] = {"economic_reality": economic}
    metrics = financial_metrics(current, {"revenue": 950.0, "net_income": 65.0})
    current["metrics"] = metrics

    result = build_research_intelligence(
        [current],
        {"base": 120.0, "expected_value": 120.0, "current_price": 100.0, "base_quality": "INTRINSIC"},
        market_price=100.0,
        readiness={"gates": [], "validation": {"state": "VALIDATED"}, "ready_to_validate": True},
    )
    assert economic["growth_capex_proxy"] == 140.0
    assert economic["owner_cash_proxy"] == 60.0
    assert has_suppression(economic, "NEGATIVE_FCF_AUTOMATIC")
    assert any(signal["label"] == "Growth reinvestment" and signal["weight"] == 0 for signal in result["signals"])
    assert not any(signal["label"] == "Cash conversion" and signal["weight"] < 0 for signal in result["signals"])


def test_030_material_sbc_removes_unadjusted_positive_cash_conversion_score():
    current = {
        "revenue": 1_000.0, "net_income": 100.0, "cfo": 220.0, "capex": 100.0, "fcf": 120.0,
        "cash": 50.0, "debt": 0.0, "liabilities": 100.0, "equity": 500.0,
        "_debt_source_tag": "LongTermDebt",
    }
    economic = build_economic_reality(current, facts={
        "cash_unrestricted": 50.0, "share_based_compensation": 80.0,
    })
    current["quality"] = {"economic_reality": economic}
    current["metrics"] = financial_metrics(current, {})

    result = build_research_intelligence(
        [current],
        {"base": 120.0, "expected_value": 120.0, "current_price": 100.0, "base_quality": "INTRINSIC"},
        market_price=100.0,
        readiness={"gates": [], "validation": {"state": "VALIDATED"}, "ready_to_validate": True},
    )
    assert economic["fcf_after_sbc"] == 40.0
    assert any(signal["label"] == "Cash conversion after SBC" and signal["weight"] == 0 for signal in result["signals"])


def test_030_financial_sector_disables_generic_industrial_rules():
    economic = build_economic_reality(
        {"revenue": 1_000.0, "cash": 100.0, "debt": 500.0, "liabilities": 5_000.0, "_debt_source_tag": "LongTermDebt"},
        facts={"cash_unrestricted": 100.0},
        company_type="Commercial Bank",
    )
    for code in (
        "GENERIC_LEVERAGE_SCORE", "GENERIC_WORKING_CAPITAL_SCORE",
        "NEGATIVE_FCF_AUTOMATIC", "FCF_MARGIN_EROSION_AUTOMATIC",
    ):
        assert code in economic["suppressions"]


def test_030_discovery_caps_material_accounting_uncertainty_at_watch():
    result = {
        "gap_pct": -35.0,
        "quality": "INTRINSIC",
        "valuation_methods": 3,
        "long_score": 0,
        "short_score": 50,
        "economic_reality": {"quality": "LOW", "material_unresolved": True},
    }
    opportunity = discovery_opportunity(result)
    assert opportunity is not None
    assert opportunity["side"] == "SHORT"
    assert opportunity["priority"] == "WATCH"
    assert opportunity["operating_state"] == "ACCOUNTING REVIEW"


def test_030_discovery_short_signals_honor_economic_suppressions():
    snapshot = {
        "revenue_growth_pct": 3.0,
        "operating_margin_delta_bps": -250.0,
        "fcf_margin_delta_bps": -400.0,
        "inventory_vs_revenue_pp": 30.0,
        "receivables_vs_revenue_pp": 30.0,
        "fcf_conversion": 0.2,
        "economic_reality": {
            "suppressions": [
                "REPORTED_MARGIN_DETERIORATION_AUTOMATIC",
                "FCF_MARGIN_EROSION_AUTOMATIC",
                "GENERIC_WORKING_CAPITAL_SCORE",
                "NEGATIVE_FCF_AUTOMATIC",
            ]
        },
    }
    signals, _long_score, short_score = _signals(snapshot, None)
    assert short_score == 0
    assert not [row for row in signals if row["side"] == "SHORT"]



def _quality_row(year, revenue, operating_income, net_income, cfo, capex, cash, debt, equity, shares, *, receivables=100.0, extra_facts=None):
    row = {
        "fiscal_year": year,
        "period_end": f"{year}-12-31",
        "revenue": float(revenue),
        "operating_income": float(operating_income),
        "pretax_income": float(net_income) / 0.8 if net_income is not None else None,
        "income_tax": (float(net_income) / 0.8) * 0.2 if net_income is not None else None,
        "net_income": float(net_income) if net_income is not None else None,
        "cfo": float(cfo),
        "capex": float(capex),
        "fcf": float(cfo) - float(capex),
        "cash": float(cash),
        "debt": float(debt),
        "liabilities": float(debt) + 300.0,
        "equity": float(equity),
        "receivables": float(receivables),
        "inventory": 80.0,
        "payables": 70.0,
        "shares_outstanding": float(shares),
        "diluted_shares": float(shares),
        "buybacks": 0.0,
        "dividends": 0.0,
        "_debt_source_tag": "LongTermDebt",
    }
    facts = {"cash_unrestricted": float(cash), **(extra_facts or {})}
    row["quality"] = {"economic_reality": build_economic_reality(row, facts=facts)}
    return row


def test_030_company_quality_strong_does_not_create_hidden_valuation_premium():
    history = [
        _quality_row(2022, 1000, 180, 140, 180, 40, 100, 0, 500, 100),
        _quality_row(2023, 1100, 200, 155, 195, 42, 110, 0, 520, 99),
        _quality_row(2024, 1210, 225, 175, 215, 45, 125, 0, 545, 98),
        _quality_row(2025, 1331, 255, 200, 245, 48, 145, 0, 575, 96),
    ]
    quality = build_company_quality(history, "Consumer / Brand")
    assert quality["state"] == "STRONG"
    assert quality["red_flag_count"] == 0
    policy = quality["valuation_policy"]
    assert policy["positive_quality_uplift"] is False
    assert policy["risk_premium_bps"] == 0
    assert policy["growth_haircut_bps"] == 0
    assert policy["terminal_growth_haircut_bps"] == 0
    assert policy["bear_probability_shift_pts"] == 0


def test_030_company_quality_fragile_creates_bounded_downside_policy():
    history = [
        _quality_row(2022, 1200, 100, 60, 50, 120, 100, 900, 450, 100, receivables=100, extra_facts={"interest_expense": 80, "operating_lease_cost": 25}),
        _quality_row(2023, 1100, 40, 20, 20, 130, 80, 950, 400, 108, receivables=120, extra_facts={"interest_expense": 90, "operating_lease_cost": 25}),
        _quality_row(2024, 950, -20, -30, 10, 140, 70, 1000, 350, 116, receivables=150, extra_facts={"interest_expense": 100, "operating_lease_cost": 25}),
        _quality_row(2025, 800, -60, -80, 5, 150, 60, 1050, 300, 125, receivables=230, extra_facts={"interest_expense": 110, "operating_lease_cost": 25}),
    ]
    quality = build_company_quality(history, "Consumer / Brand")
    assert quality["state"] == "FRAGILE"
    assert quality["red_flag_count"] >= 2
    assert any(flag["severity"] == "RED" for flag in quality["alarm_bells"])
    policy = quality["valuation_policy"]
    assert 0 < policy["risk_premium_bps"] <= 300
    assert 0 < policy["growth_haircut_bps"] <= 300
    assert 0 <= policy["terminal_growth_haircut_bps"] <= 100
    assert policy["bear_probability_shift_pts"] == 10


def test_030_company_quality_policy_is_embedded_in_auto_bear_base_bull():
    history = [
        _quality_row(2022, 1200, 100, 60, 50, 120, 100, 900, 450, 100, receivables=100, extra_facts={"interest_expense": 80, "operating_lease_cost": 25}),
        _quality_row(2023, 1100, 40, 20, 20, 130, 80, 950, 400, 108, receivables=120, extra_facts={"interest_expense": 90, "operating_lease_cost": 25}),
        _quality_row(2024, 950, -20, -30, 10, 140, 70, 1000, 350, 116, receivables=150, extra_facts={"interest_expense": 100, "operating_lease_cost": 25}),
        _quality_row(2025, 800, -60, -80, 5, 150, 60, 1050, 300, 125, receivables=230, extra_facts={"interest_expense": 110, "operating_lease_cost": 25}),
    ]
    metrics = metrics_from_history(history, company_type="Consumer / Brand")
    policy = metrics["valuation_policy"]
    cases = default_cases(metrics, "Consumer / Brand")
    assert cases["BASE"]["equity_discount_rate"] == 0.10 + policy["risk_premium_bps"] / 10000.0
    assert cases["BEAR"]["probability"] > 0.25
    assert cases["BULL"]["probability"] < 0.25
    assert cases["BASE"]["terminal_growth"] <= 0.025
    assert cases["BASE"]["growth"] <= (metrics["revenue_growth"] or 0.0)


def test_030_method_exclusions_cannot_be_reenabled_by_saved_weights():
    metrics = {
        "revenue": 1_000.0,
        "net_income": 100.0,
        "fcf": 100.0,
        "net_debt": 200.0,
        "shares": 100.0,
        "basis_usable": True,
        "revenue_growth": 0.05,
        "net_margin": 0.10,
        "fcf_margin": 0.10,
        "valuation_policy": {
            "method_exclusions": ["pe", "ev_sales"],
            "ledger": [],
        },
        "company_quality": {"state": "UNRESOLVED"},
    }
    cases = default_cases(metrics, "Generic")
    result = evaluate(
        metrics,
        {name: cases[name] for name in ("BEAR", "BASE", "BULL")},
        {"pe": 0.8, "ev_sales": 0.1, "fcf_yield": 0.1},
        current_price=10.0,
        allow_reference_fallback=False,
    )
    assert result["effective_input_weights"]["pe"] == 0.0
    assert result["effective_input_weights"]["ev_sales"] == 0.0
    assert result["effective_input_weights"]["fcf_yield"] == 0.1
    assert result["method_exclusions"] == ["ev_sales", "pe"]


def test_030_valuation_ledger_distinguishes_lease_classification_from_price_penalty():
    row, _economic = _cmg_like_snapshot()
    metrics = metrics_from_history([{"fiscal_year": 2025, "period_end": "2025-12-31", **row}], company_type="Consumer / Brand")
    ledger = list((metrics.get("valuation_policy") or {}).get("ledger") or [])
    lease = next(item for item in ledger if item["item"] == "Operating leases")
    assert lease["effect"] == "CLASSIFICATION_AND_RISK"
    assert "Excluded from financial net debt" in lease["impact"]
    assert metrics["net_debt"] == -1_000_000_000.0


def test_030_production_state_sync_changes_only_canonical_production_line():
    source = (
        "# Market Forensics — CURRENT STATE\n\n"
        "**State-Version: 0.3.0**  \n"
        "**Production:** 0.2.13 old state  \n"
        "**Historical note:** production once was 0.2.11.\n"
    )
    updated = sync_current_state(
        source,
        version="0.3.0",
        source_sha="abcdef1234567890",
        run_id="12345",
        run_number="77",
        deployed_at="2026-09-21T14:00:00Z",
    )
    assert "**Production:** 0.3.0 on Namecheap" in updated
    assert "manual deploy run " + chr(96) + "12345" + chr(96) + " / deploy #77" in updated
    assert chr(96) + "abcdef1234567890" + chr(96) in updated
    assert "**Historical note:** production once was 0.2.11." in updated
    assert updated.count("**Production:**") == 1
