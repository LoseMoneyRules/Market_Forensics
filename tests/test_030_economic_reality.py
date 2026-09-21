from __future__ import annotations

import math

from mfapp.calculations import financial_metrics
from mfapp.decision_engine import build_research_intelligence
from mfapp.discovery_forensics import _signals, discovery_opportunity
from mfapp.economic_reality import build_economic_reality, has_suppression
from mfapp.valuation_engine import metrics_from_history


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
