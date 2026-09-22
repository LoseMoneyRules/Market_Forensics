from __future__ import annotations

from pathlib import Path

from mfapp.core_models import Company
from mfapp.valuation_forensics import _similarity


def test_0322_peer_requires_structural_match_not_same_sector_plus_similar_numbers():
    target_company = Company(
        legal_name="Target",
        display_name="Target",
        sector="Consumer",
        industry="Pharmacy Retail",
        country="US",
    )
    unrelated_company = Company(
        legal_name="Unrelated",
        display_name="Unrelated",
        sector="Consumer",
        industry="Footwear",
        country="US",
    )
    target = {
        "market_cap": 50_000,
        "revenue_growth_pct": 5,
        "operating_margin_pct": 6,
        "fcf_margin_pct": 5,
        "roic_pct": 10,
        "net_debt_to_fcf": 2.0,
        "capex_to_revenue_pct": 2.0,
    }
    result = _similarity(
        target,
        dict(target),
        target_company,
        unrelated_company,
        "5912",
        "3140",
    )
    assert result["structural_match"] is False
    assert result["tier"] in {"REFERENCE ONLY", "NOT COMPARABLE"}


def test_0322_peer_engine_never_promotes_research_database_presence_into_peer_median():
    source = Path("mfapp/valuation_forensics.py").read_text()
    assert "structural_match" in source
    assert "SIC division or same industry required" in source
    assert "eligible_candidates" in source
    assert "reference_candidates" in source
    assert "Fewer than three structurally comparable" in source
    assert "unrelated researched names are never promoted" in source


def test_0322_validate_is_same_engine_and_excludes_non_decision_grade_snapshots():
    source = Path("mfapp/historical_engine.py").read_text()
    assert "from .valuation_engine import ENGINE_VERSION" in source
    assert "allow_reference_fallback=False" in source
    assert 'base_output.get("independent_method_count")' in source
    assert '"MODEL_LIMITED"' in source
    assert '"regime_detection_point_in_time": True' in source
    assert '"ebitda": ebitda' in source
    assert '"equity": row.get("equity")' in source


def test_0322_engine_version_invalidates_pre_regime_cache():
    engine = Path("mfapp/valuation_engine.py").read_text()
    forensics = Path("mfapp/valuation_forensics.py").read_text()
    cache = Path("mfapp/research_cache.py").read_text()
    assert 'ENGINE_VERSION = "0.3.2-integrity-v2"' in engine
    assert 'ENGINE_VERSION = "0.3.2-integrity-v2"' in forensics
    assert "saved_engine != VALUATION_ENGINE_VERSION" in cache
