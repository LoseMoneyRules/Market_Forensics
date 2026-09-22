from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import mfapp.historical_engine as he
from mfapp.core_models import Company
from mfapp.valuation_engine import calibrate_multiples, detect_structural_regime, metrics_from_history
from mfapp.valuation_forensics import _similarity


def _annual_row(
    fy: int,
    *,
    revenue: float,
    gross_margin: float,
    operating_margin: float,
    fcf_margin: float,
    capex_ratio: float,
    net_debt_ratio: float,
) -> dict:
    operating_income = revenue * operating_margin
    fcf = revenue * fcf_margin
    capex = revenue * capex_ratio
    net_debt = revenue * net_debt_ratio
    return {
        "fiscal_year": fy,
        "period_type": "FY",
        "period_end": f"{fy}-12-31",
        "filed_at": f"{fy + 1}-02-15",
        "revenue": revenue,
        "gross_profit": revenue * gross_margin,
        "operating_income": operating_income,
        "net_income": revenue * max(.01, operating_margin * .65),
        "fcf": fcf,
        "cfo": fcf + capex,
        "capex": capex,
        "cash": max(0.0, -net_debt),
        "debt": max(0.0, net_debt),
        "diluted_shares": 100.0,
        "shares_outstanding": 100.0,
        "equity": revenue * .45,
        "assets": revenue * .85,
        "liabilities": revenue * .40,
        "quality": {
            "economic_reality": {
                "economic_net_debt": net_debt,
                "debt_basis": "TEST",
                "material_unresolved": False,
                "depreciation_amortization": revenue * .02,
                "economic_roic_pct": 12.0 if fy < 2022 else 24.0,
            }
        },
    }


def test_structural_break_excludes_old_business_regime_from_multiple_calibration():
    history = []
    for fy in range(2018, 2022):
        history.append(_annual_row(
            fy, revenue=100 + (fy - 2018) * 5, gross_margin=.30,
            operating_margin=.08, fcf_margin=.06, capex_ratio=.07, net_debt_ratio=.35,
        ))
    for fy in range(2022, 2026):
        history.append(_annual_row(
            fy, revenue=190 + (fy - 2022) * 20, gross_margin=.68,
            operating_margin=.23, fcf_margin=.18, capex_ratio=.02, net_debt_ratio=-.05,
        ))

    regime = detect_structural_regime(history)
    assert regime["state"] == "HIGH_CONFIDENCE_BREAK"
    assert regime["history_filter_applies"] is True
    assert regime["regime_start_fiscal_year"] == 2022

    observations = []
    for row in history:
        fy = row["fiscal_year"]
        # Old business traded at a structurally different earnings multiple.
        pe = 35.0 if fy < 2022 else float(10 + (fy - 2022))
        net_income = row["net_income"]
        shares = row["diluted_shares"]
        price = pe * net_income / shares
        observations.append({
            "fiscal_year": fy,
            "price": price,
            "shares": shares,
            "revenue": row["revenue"],
            "net_income": net_income,
            "fcf": row["fcf"],
            "ebitda": row["operating_income"] + row["quality"]["economic_reality"]["depreciation_amortization"],
            "equity": row["equity"],
            "net_debt": row["quality"]["economic_reality"]["economic_net_debt"],
        })

    calibrated = calibrate_multiples(observations, "Generic", regime)
    assert calibrated["regime_filter_applied"] is True
    assert calibrated["discarded_pre_regime_count"] == 4
    assert calibrated["method_stats"]["pe"]["sample_size"] == 4
    assert calibrated["pe"][1] == pytest.approx(11.5)
    assert calibrated["pe"][1] < 15.0


def test_single_dimension_swing_does_not_erase_history():
    history = []
    for fy in range(2018, 2026):
        history.append(_annual_row(
            fy,
            revenue=100 + (fy - 2018) * 5,
            gross_margin=.42,
            operating_margin=.08 if fy < 2024 else .16,
            fcf_margin=.08,
            capex_ratio=.04,
            net_debt_ratio=.10,
        ))
    regime = detect_structural_regime(history)
    assert regime["history_filter_applies"] is False


def test_metrics_use_current_regime_operating_history_after_high_confidence_break():
    history = []
    for fy in range(2018, 2022):
        history.append(_annual_row(
            fy, revenue=100, gross_margin=.28, operating_margin=.06,
            fcf_margin=.04, capex_ratio=.08, net_debt_ratio=.40,
        ))
    for fy in range(2022, 2026):
        history.append(_annual_row(
            fy, revenue=200, gross_margin=.70, operating_margin=.24,
            fcf_margin=.19, capex_ratio=.02, net_debt_ratio=-.05,
        ))
    metrics = metrics_from_history(history)
    assert metrics["structural_regime"]["history_filter_applies"] is True
    assert metrics["valuation_history_start_fiscal_year"] == 2022
    assert metrics["valuation_history_years"] == 4
    assert metrics["operating_margin"] == pytest.approx(.24)


def test_validate_point_in_time_calibration_has_live_engine_fields_and_current_anchor(monkeypatch):
    history = [
        {
            "fiscal_year": 2025,
            "period_type": "FY",
            "filed_at": "2026-02-15",
            "period_end": "2025-12-31",
            "revenue": 1000.0,
            "operating_income": 120.0,
            "net_income": 80.0,
            "fcf": 90.0,
            "diluted_shares": 100.0,
            "shares_outstanding": 100.0,
            "equity": 500.0,
            "quality": {
                "economic_reality": {
                    "economic_net_debt": 150.0,
                    "depreciation_amortization": 30.0,
                    "material_unresolved": False,
                }
            },
        }
    ]
    monkeypatch.setattr(
        he,
        "price_on_or_after",
        lambda *args, **kwargs: SimpleNamespace(close_raw=20.0, trade_date=date(2026, 2, 15)),
    )
    rows = he._calibration_observations(
        1, history, date(2026, 2, 15), provider="TEST"
    )
    assert len(rows) == 1
    assert rows[0]["fiscal_year"] == 2025
    assert rows[0]["ebitda"] == pytest.approx(150.0)
    assert rows[0]["equity"] == pytest.approx(500.0)
    assert rows[0]["anchor_date"] == "2026-02-15"


def test_validate_contract_replays_decision_grade_canonical_engine_only():
    source = Path("mfapp/historical_engine.py").read_text()
    assert "allow_reference_fallback=False" in source
    assert "detect_structural_regime" in source
    assert "independent_method_count" in source
    assert 'base_quality == "INTRINSIC"' in source
    assert '"LIMITED_EVIDENCE"' in source
    assert '"valuation_parity"' in source
    assert '"structural_regime_uses_future_data": False' in source
    assert '"calibration_includes_current_anchor": True' in source


def test_peer_similarity_requires_business_fit_not_just_matching_numbers():
    target_company = Company(
        legal_name="Target", display_name="Target", sector="Consumer", industry="Footwear", country="US"
    )
    unrelated_company = Company(
        legal_name="Unrelated", display_name="Unrelated", sector="Technology", industry="Software", country="US"
    )
    true_peer_company = Company(
        legal_name="Peer", display_name="Peer", sector="Consumer", industry="Footwear", country="US"
    )
    target = {
        "market_cap": 10_000.0,
        "revenue_growth_pct": 8.0,
        "operating_margin_pct": 14.0,
        "fcf_margin_pct": 10.0,
        "roic_pct": 18.0,
        "net_debt_to_fcf": .5,
        "capex_to_revenue_pct": 3.0,
    }
    same_numbers = dict(target)
    unrelated = _similarity(
        target, same_numbers, target_company, unrelated_company, "3149", "7372"
    )
    assert unrelated["economic_score"] >= 90.0
    assert unrelated["business_verified"] is False
    assert unrelated["tier"] == "NOT COMPARABLE"

    true_peer = _similarity(
        target, same_numbers, target_company, true_peer_company, "3149", "3149"
    )
    assert true_peer["business_verified"] is True
    assert true_peer["tier"] == "CLOSE PEER"


def test_peer_universe_is_independent_but_unverified_names_cannot_set_value():
    discovery = Path("mfapp/discovery_market_fundamentals.py").read_text()
    forensics = Path("mfapp/valuation_forensics.py").read_text()
    template = Path("mfapp/templates/_valuation_forensics.html").read_text()

    assert 'PEER_UNIVERSE_CACHE_KEY = "discovery_peer_universe_v1"' in discovery
    assert '"CANDIDATE_ONLY_UNTIL_BUSINESS_TAXONOMY_VERIFIED"' in discovery
    assert "Coverage membership is never a peer criterion" in template
    assert "Full-market peer candidates — not used in valuation" in template
    assert '"used_in_peer_valuation": False' in forensics
    assert 'row.get("business_verified")' in forensics
    assert '"VERIFIED_BUSINESS_PEERS_PLUS_FULL_MARKET_ECONOMIC_CANDIDATES"' in forensics
