from __future__ import annotations

import pytest

import mfapp.valuation_engine as ve
from mfapp.valuation_engine import (
    TYPE_PRIORS,
    _altman_z_score,
    calibrate_multiples,
    dcf_value,
    default_cases,
    detect_operating_regime,
    evaluate,
    metrics_from_history,
    scenario_value,
)


def _calibration():
    return {
        "source": "COMPANY_POINT_IN_TIME_5Y_10Y",
        "sample_size": 5,
        "pe": (10.0, 14.0, 18.0),
        "p_sales": (0.4, 0.7, 1.0),
        "ev_sales": (0.5, 0.8, 1.1),
        "ev_ebitda": (5.0, 7.0, 10.0),
        "fcf_yield": (0.12, 0.08, 0.05),
        "p_b": (1.0, 1.5, 2.0),
        "method_stats": {},
    }


def _metrics(**overrides):
    base = {
        "revenue": 1_000.0,
        "gross_profit": 300.0,
        "operating_income": 80.0,
        "net_income": 50.0,
        "fcf": 70.0,
        "ebitda": 110.0,
        "net_debt": 100.0,
        "shares": 10.0,
        "basis_usable": True,
        "revenue_growth": 0.04,
        "gross_margin": 0.30,
        "net_margin": 0.05,
        "operating_margin": 0.08,
        "fcf_margin": 0.07,
        "ebitda_margin": 0.11,
        "capex_to_revenue": 0.04,
        "economic_roic_pct": 12.0,
        "earnings_retention_rate": 0.40,
        "share_growth_rate": 0.01,
        "net_debt_to_ebitda": 0.91,
        "interest_coverage_x": 6.0,
        "fixed_charge_coverage_x": 4.0,
        "liquidation_floor_per_share": 0.0,
        "valuation_policy": {"method_exclusions": [], "ledger": []},
        "company_quality": {"state": "SOUND"},
        "history_stats": {
            "revenue_growth": {"sample_size": 6, "p10": -0.01, "median": 0.04, "p90": 0.09, "std": 0.04},
            "net_margin": {"sample_size": 6, "p10": 0.03, "median": 0.05, "p90": 0.07, "std": 0.015},
            "fcf_margin": {"sample_size": 6, "p10": 0.04, "median": 0.07, "p90": 0.09, "std": 0.02},
            "operating_margin": {"sample_size": 6, "p10": 0.05, "median": 0.08, "p90": 0.10, "std": 0.02},
            "ebitda_margin": {"sample_size": 6, "p10": 0.08, "median": 0.11, "p90": 0.14, "std": 0.02},
            "share_growth": {"sample_size": 6, "p10": -0.01, "median": 0.01, "p90": 0.04, "std": 0.02},
            "economic_roic": {"sample_size": 6, "p10": 0.08, "median": 0.12, "p90": 0.16, "std": 0.03},
            "ccc_days": {"sample_size": 6, "p10": 20.0, "median": 30.0, "p90": 42.0, "std": 8.0},
        },
    }
    base.update(overrides)
    return base


def test_032_integrity_uses_company_p10_p50_p90_not_fixed_type_proxy():
    observations = []
    for fy, price in zip(range(2021, 2026), (10.0, 12.0, 14.0, 16.0, 18.0)):
        observations.append({
            "fiscal_year": fy,
            "price": price,
            "shares": 10.0,
            "revenue": 100.0,
            "net_income": 10.0,
            "fcf": 5.0,
            "ebitda": 20.0,
            "net_debt": 0.0,
            "equity": 80.0,
        })
    result = calibrate_multiples(observations, "Generic")
    assert TYPE_PRIORS == {}
    assert result["source"] == "COMPANY_POINT_IN_TIME_5Y_10Y"
    assert result["method_stats"]["pe"]["horizon"] == "5Y"
    assert result["method_stats"]["pe"]["sample_size"] == 5
    assert result["pe"][0] == pytest.approx(10.8)
    assert result["pe"][1] == pytest.approx(14.0)
    assert result["pe"][2] == pytest.approx(17.2)
    assert result["uses_fixed_type_proxy"] is False


def test_032_integrity_disables_sales_multiples_for_cvs_like_thin_margin_business():
    metrics = _metrics(net_margin=0.02, operating_margin=0.03, ebitda_margin=0.06, ebitda=60.0)
    cases = default_cases(metrics, "Generic", _calibration())
    assert cases["weights"]["p_sales"] == 0.0
    assert cases["weights"]["ev_sales"] == 0.0
    assert cases["weights"]["ev_ebitda"] > 0.0
    assert cases["weights"]["dcf"] > 0.0
    assert "p_sales" in cases["BASE"]["method_exclusions"]
    assert "ev_sales" in cases["BASE"]["method_exclusions"]


def test_032_integrity_auto_scenarios_can_never_invert(monkeypatch):
    monkeypatch.setattr(ve, "MONTE_CARLO_DRAWS", 500)
    metrics = _metrics()
    defaults = default_cases(metrics, "Generic", _calibration())
    result = evaluate(
        metrics,
        {name: defaults[name] for name in ("BEAR", "BASE", "BULL")},
        defaults["weights"],
        defaults["horizon_years"],
        current_price=50.0,
        allow_reference_fallback=False,
    )
    bear = result["scenarios"]["BEAR"]["fair_value"]
    base = result["scenarios"]["BASE"]["fair_value"]
    bull = result["scenarios"]["BULL"]["fair_value"]
    assert bear <= base <= bull
    assert result["monte_carlo"]["available"] is True
    assert result["monte_carlo"]["p10"] <= result["monte_carlo"]["p50"] <= result["monte_carlo"]["p90"]


def test_032_integrity_sbc_is_dilution_not_double_cash_penalty():
    rows = [
        {
            "fiscal_year": 2023, "period_end": "2023-12-31", "revenue": 100.0, "net_income": 10.0,
            "operating_income": 15.0, "fcf": 12.0, "cfo": 15.0, "capex": 3.0,
            "shares_outstanding": 10.0, "diluted_shares": 10.0, "cash": 20.0, "debt": 0.0,
            "quality": {"economic_reality": {"economic_net_debt": -20.0, "debt_basis": "TEST", "material_unresolved": False, "sbc_to_revenue_pct": 10.0}},
        },
        {
            "fiscal_year": 2024, "period_end": "2024-12-31", "revenue": 110.0, "net_income": 11.0,
            "operating_income": 16.0, "fcf": 13.2, "cfo": 16.2, "capex": 3.0,
            "shares_outstanding": 10.5, "diluted_shares": 10.5, "cash": 22.0, "debt": 0.0,
            "quality": {"economic_reality": {"economic_net_debt": -22.0, "debt_basis": "TEST", "material_unresolved": False, "sbc_to_revenue_pct": 10.0}},
        },
        {
            "fiscal_year": 2025, "period_end": "2025-12-31", "revenue": 121.0, "net_income": 12.1,
            "operating_income": 17.0, "fcf": 14.52, "cfo": 17.52, "capex": 3.0,
            "shares_outstanding": 11.0, "diluted_shares": 11.0, "cash": 24.0, "debt": 0.0,
            "quality": {"economic_reality": {"economic_net_debt": -24.0, "debt_basis": "TEST", "material_unresolved": False, "sbc_to_revenue_pct": 10.0}},
        },
    ]
    metrics = metrics_from_history(rows)
    assert metrics["fcf_margin"] == pytest.approx(0.12)
    assert metrics["fcf_margin_basis"] == "REPORTED_FCF_WITH_DILUTION_DENOMINATOR"
    assert metrics["share_growth_rate"] > 0.0
    no_dilution = dcf_value(100, .10, .12, .10, .025, 5, 10, 0.0)
    with_dilution = dcf_value(100, .10, .12, .10, .025, 5, 10, .05)
    assert with_dilution < no_dilution


def test_032_integrity_conservative_net_cash_floor_bounds_bear():
    metrics = _metrics(liquidation_floor_per_share=30.0, net_debt=-400.0)
    assumptions = {
        "growth": 0.0, "fcf_margin": 0.01, "equity_discount_rate": .18, "terminal_growth": 0.0,
        "share_growth": 0.0, "scenario_multiplier": .65, "liquidation_floor": 30.0,
    }
    row = scenario_value(metrics, assumptions, {"dcf": 1.0}, 5, allow_reference_fallback=False)
    assert row["fair_value"] >= 30.0
    assert any("liquidation floor" in flag.lower() for flag in row["flags"])


def test_032_integrity_cyclical_business_normalizes_and_deemphasizes_pe():
    metrics = _metrics()
    metrics["history_stats"]["revenue_growth"]["std"] = .20
    metrics["history_stats"]["operating_margin"]["std"] = .08
    cases = default_cases(metrics, "Semiconductor / AI", _calibration())
    assert cases["life_cycle"] == "CYCLICAL"
    assert cases["weights"]["pe"] == pytest.approx(.25)
    assert cases["weights"]["ev_ebitda"] == pytest.approx(1.75)


def test_032_integrity_financial_reit_generic_model_fails_closed():
    cases = default_cases(_metrics(), "Financial / REIT", _calibration())
    assert all(value == 0.0 for value in cases["weights"].values())
    assert cases["life_cycle"] == "SECTOR_SPECIFIC"
    assert any("sector-specific" in note.lower() for note in cases["BASE"]["integrity_notes"])


def test_032_integrity_altman_is_tail_risk_guard_when_inputs_exist():
    metrics = _metrics(
        assets=1000.0,
        liabilities=900.0,
        current_assets=100.0,
        current_liabilities=400.0,
        retained_earnings=-200.0,
        operating_income=20.0,
        revenue=500.0,
        shares=10.0,
        current_price=5.0,
    )
    altman = _altman_z_score(metrics, "Industrial")
    assert altman["available"] is True
    assert altman["state"] == "DISTRESS"
    cases = default_cases(metrics, "Industrial", _calibration())
    assert cases["BASE"]["solvency_state"] == "DISTRESS"
    assert cases["BEAR"]["scenario_multiplier"] <= .65


def test_032_integrity_freshness_shield_widens_simulation(monkeypatch):
    monkeypatch.setattr(ve, "MONTE_CARLO_DRAWS", 400)
    metrics = _metrics(market_move_since_filing_pct=-35.0, pb_deviation_from_history_pct=-40.0)
    defaults = default_cases(metrics, "Generic", _calibration())
    result = evaluate(
        metrics,
        {name: defaults[name] for name in ("BEAR", "BASE", "BULL")},
        defaults["weights"],
        5,
        current_price=50.0,
        allow_reference_fallback=False,
    )
    assert result["monte_carlo"]["freshness_scale"] == pytest.approx(1.25)
    assert any("DATA DESYNCHRONIZATION SHIELD" in warning for warning in result["warnings"])



def test_032_integrity_cash_flow_variants_do_not_count_as_two_independent_methods():
    metrics = _metrics()
    assumptions = {
        "growth": .04,
        "net_margin": .05,
        "fcf_margin": .07,
        "ebitda_margin": .11,
        "share_growth": .01,
        "target_fcf_yield": .08,
        "equity_discount_rate": .10,
        "terminal_growth": .025,
        "scenario_multiplier": 1.0,
        "liquidation_floor": None,
    }
    row = scenario_value(
        metrics,
        assumptions,
        {"fcf_yield": 1.0, "dcf": 1.0},
        5,
        allow_reference_fallback=False,
    )
    assert row["method_count"] == 2
    assert row["independent_method_count"] == 1
    assert row["independent_method_families"] == ["CASH_FLOW"]
    assert row["quality"] == "INTRINSIC_SINGLE_METHOD"


def test_032_integrity_cvs_like_revenue_scale_cannot_manufacture_triple_digit_value(monkeypatch):
    monkeypatch.setattr(ve, "MONTE_CARLO_DRAWS", 600)
    metrics = _metrics(
        revenue=380_000.0,
        gross_profit=55_000.0,
        operating_income=13_300.0,
        net_income=5_700.0,
        fcf=11_400.0,
        ebitda=20_000.0,
        net_debt=80_000.0,
        shares=1_300.0,
        revenue_growth=.04,
        gross_margin=.145,
        net_margin=.015,
        operating_margin=.035,
        fcf_margin=.03,
        ebitda_margin=.0526,
        capex_to_revenue=.02,
        share_growth_rate=0.0,
        net_debt_to_ebitda=4.0,
    )
    metrics["history_stats"]["revenue_growth"] = {"sample_size": 6, "p10": -.02, "median": .03, "p90": .07, "std": .035}
    metrics["history_stats"]["net_margin"] = {"sample_size": 6, "p10": .01, "median": .016, "p90": .022, "std": .005}
    metrics["history_stats"]["fcf_margin"] = {"sample_size": 6, "p10": .02, "median": .03, "p90": .04, "std": .008}
    metrics["history_stats"]["operating_margin"] = {"sample_size": 6, "p10": .02, "median": .035, "p90": .05, "std": .01}
    metrics["history_stats"]["ebitda_margin"] = {"sample_size": 6, "p10": .04, "median": .055, "p90": .07, "std": .01}
    calibration = {
        "source": "COMPANY_POINT_IN_TIME_5Y_10Y",
        "sample_size": 6,
        "pe": (7.0, 10.0, 14.0),
        "p_sales": (.15, .30, .55),
        "ev_sales": (.35, .55, .85),
        "ev_ebitda": (4.5, 7.0, 10.0),
        "fcf_yield": (.12, .09, .06),
        "p_b": (1.0, 1.5, 2.0),
        "method_stats": {},
    }
    defaults = default_cases(metrics, "Generic", calibration)
    assert defaults["weights"]["p_sales"] == 0.0
    assert defaults["weights"]["ev_sales"] == 0.0
    result = evaluate(
        metrics,
        {name: defaults[name] for name in ("BEAR", "BASE", "BULL")},
        defaults["weights"],
        defaults["horizon_years"],
        current_price=87.59,
        allow_reference_fallback=False,
    )
    bear = result["scenarios"]["BEAR"]["fair_value"]
    base = result["scenarios"]["BASE"]["fair_value"]
    bull = result["scenarios"]["BULL"]["fair_value"]
    assert bear <= base <= bull
    assert base < 160.0
    assert result["scenarios"]["BASE"]["independent_method_count"] >= 2



def test_032_integrity_structural_regime_excludes_old_business_history():
    rows = [
        {"fiscal_year": 2018, "period_end": "2018-12-31", "revenue": 100.0, "gross_profit": 55.0, "operating_income": 20.0, "fcf": 15.0, "capex": 4.0, "diluted_shares": 10.0},
        {"fiscal_year": 2019, "period_end": "2019-12-31", "revenue": 105.0, "gross_profit": 57.0, "operating_income": 21.0, "fcf": 16.0, "capex": 4.0, "diluted_shares": 10.0},
        {"fiscal_year": 2020, "period_end": "2020-12-31", "revenue": 110.0, "gross_profit": 59.0, "operating_income": 22.0, "fcf": 16.0, "capex": 4.0, "diluted_shares": 10.0},
        # Persistent transformed business: much larger revenue base, lower margin,
        # higher capital intensity and a materially different share base.
        {"fiscal_year": 2021, "period_end": "2021-12-31", "revenue": 210.0, "gross_profit": 63.0, "operating_income": 17.0, "fcf": 12.0, "capex": 18.0, "diluted_shares": 14.0},
        {"fiscal_year": 2022, "period_end": "2022-12-31", "revenue": 225.0, "gross_profit": 67.0, "operating_income": 18.0, "fcf": 13.0, "capex": 19.0, "diluted_shares": 14.2},
        {"fiscal_year": 2023, "period_end": "2023-12-31", "revenue": 240.0, "gross_profit": 72.0, "operating_income": 20.0, "fcf": 14.0, "capex": 20.0, "diluted_shares": 14.4},
        {"fiscal_year": 2024, "period_end": "2024-12-31", "revenue": 255.0, "gross_profit": 76.0, "operating_income": 21.0, "fcf": 15.0, "capex": 21.0, "diluted_shares": 14.5},
    ]
    regime = detect_operating_regime(rows)
    assert regime["detected"] is True
    assert regime["start_fiscal_year"] == 2021
    assert regime["excluded_years"] == 3
    assert len(regime["signals"]) >= 2

    observations = [
        {
            "fiscal_year": fy,
            "price": price,
            "shares": 10.0,
            "revenue": 100.0,
            "net_income": 10.0,
            "fcf": 8.0,
            "ebitda": 15.0,
            "net_debt": 0.0,
            "equity": 80.0,
        }
        for fy, price in zip(range(2018, 2025), (40, 42, 45, 12, 13, 14, 15))
    ]
    calibration = calibrate_multiples(observations, "Generic", regime["start_fiscal_year"])
    assert calibration["pre_regime_observations_excluded"] == 3
    assert calibration["method_stats"]["pe"]["sample_size"] == 4
    assert calibration["pe"][1] < 20.0


def test_032_integrity_validate_uses_same_fail_closed_engine_contract():
    from pathlib import Path
    source = Path("mfapp/historical_engine.py").read_text()
    assert "allow_reference_fallback=False" in source
    assert 'base_output.get("independent_method_count")' in source
    assert '"MODEL_LIMITED"' in source
    assert '"regime_detection_point_in_time": True' in source
    assert '"ebitda": ebitda' in source
    assert '"equity": row.get("equity")' in source
