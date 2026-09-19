from __future__ import annotations

from datetime import datetime, timezone

from mfapp.core_models import Job


def _stage0():
    return {
        "member_count": 4, "raw_count": 4, "excluded_count": 0, "excluded_breakdown": {},
        "generated_at": "2026-09-19T12:00:00", "cache_hit": True,
    }


def _stage1(rows):
    return {
        "rows": rows, "scanned_count": len(rows), "qualified_count": len(rows),
        "broad_rotation_count": len(rows), "quiet_broad_count": len(rows),
        "activity_count": 0, "cursor_start": 0, "cursor_end": len(rows),
        "excluded_breakdown": {},
    }


def test_025_discovery_requires_fair_value_and_operating_confirmation(monkeypatch):
    import mfapp.market_discovery as md

    monkeypatch.setattr(md, "_headers", lambda user_id: {"x": "y"})
    monkeypatch.setattr(md, "stage0_universe", lambda *args, **kwargs: _stage0())
    rows = [
        {"ticker": "RETO", "name": "Reto Corp", "exchange": "NASDAQ", "price": .20, "daily_volume": 100_000_000, "dollar_volume": 20_000_000, "move_pct": 0, "shortable": True, "easy_to_borrow": True, "stage1_lanes": ["BROAD_ROTATION"]},
        {"ticker": "GOODL", "name": "Good Long", "exchange": "NASDAQ", "price": 25.0, "daily_volume": 5_000_000, "dollar_volume": 125_000_000, "move_pct": -1, "shortable": True, "easy_to_borrow": True, "stage1_lanes": ["BROAD_ROTATION"]},
        {"ticker": "GOODS", "name": "Good Short", "exchange": "NASDAQ", "price": 60.0, "daily_volume": 5_000_000, "dollar_volume": 300_000_000, "move_pct": 1, "shortable": True, "easy_to_borrow": True, "stage1_lanes": ["BROAD_ROTATION"]},
        {"ticker": "NOFAIR", "name": "No Fair", "exchange": "NASDAQ", "price": 30.0, "daily_volume": 5_000_000, "dollar_volume": 150_000_000, "move_pct": 0, "shortable": True, "easy_to_borrow": True, "stage1_lanes": ["BROAD_ROTATION"]},
    ]
    monkeypatch.setattr(md, "stage1_screen", lambda *args, **kwargs: _stage1(rows))
    monkeypatch.setattr(md, "_active_coverage_tickers", lambda user_id: set())
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {})
    monkeypatch.setattr(md, "enrich_forensic_candidates", lambda *args, **kwargs: ({
        "GOODL": {"bear": 20.0, "base": 40.0, "bull": 50.0, "gap_pct": 60.0, "quality": "INTRINSIC", "valuation_methods": 3, "long_score": 38, "short_score": 0,
                  "signals": [{"side": "LONG", "label": "REVENUE ACCELERATION", "detail": "TTM revenue +12.0% YoY", "points": 14},
                              {"side": "LONG", "label": "OPERATING LEVERAGE", "detail": "Operating margin +180 bps YoY", "points": 16}],
                  "snapshot": {}, "source": "TEST"},
        "GOODS": {"bear": 25.0, "base": 35.0, "bull": 45.0, "gap_pct": -41.7, "quality": "INTRINSIC", "valuation_methods": 3, "long_score": 0, "short_score": 40,
                  "signals": [{"side": "SHORT", "label": "REVENUE DETERIORATION", "detail": "TTM revenue -8.0% YoY", "points": 16},
                              {"side": "SHORT", "label": "OPERATING DELEVERAGE", "detail": "Operating margin -220 bps YoY", "points": 18}],
                  "snapshot": {}, "source": "TEST"},
    }, {}))

    result = md.market_scan(1)
    tickers = {row["ticker"] for row in result["candidates"]}
    assert "RETO" not in tickers
    assert "NOFAIR" not in tickers
    assert tickers == {"GOODL", "GOODS"}
    assert result["contract_version"] == "BROAD_FORENSIC_DISCOVERY_V2"
    assert all(row.get("fair_value") is not None and row.get("forensic_signals") for row in result["candidates"])


def test_025_discovery_can_return_zero_instead_of_filler(monkeypatch):
    import mfapp.market_discovery as md

    monkeypatch.setattr(md, "_headers", lambda user_id: {"x": "y"})
    monkeypatch.setattr(md, "stage0_universe", lambda *args, **kwargs: _stage0())
    rows = [{"ticker": "VALID", "name": "Valid Corp", "exchange": "NYSE", "price": 30.0, "daily_volume": 5_000_000, "dollar_volume": 150_000_000, "move_pct": 0, "shortable": True, "easy_to_borrow": True, "stage1_lanes": ["BROAD_ROTATION"]}]
    monkeypatch.setattr(md, "stage1_screen", lambda *args, **kwargs: _stage1(rows))
    monkeypatch.setattr(md, "_active_coverage_tickers", lambda user_id: set())
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {})
    monkeypatch.setattr(md, "enrich_forensic_candidates", lambda *args, **kwargs: ({}, {"TTM INVALID / INCOMPLETE": 1}))

    result = md.market_scan(1)
    assert result["candidates"] == []
    assert result["long_count"] == 0 and result["short_count"] == 0
    assert result["contract_version"] == "BROAD_FORENSIC_DISCOVERY_V2"


def test_025_discovery_legacy_payload_is_rejected():
    from mfapp.routes import _normalized_market_scan

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = Job(
        job_type="DISCOVERY_SCAN", status="DONE", priority=70, user_id=1,
        payload={}, result={"market_scan": {"contract_version": "FORENSIC_FAIR_VALUE_V1", "candidates": [{"ticker": "RETO", "research_side": "SHORT"}]}},
        attempts=1, max_attempts=3, run_after=now, started_at=now, finished_at=now,
    )
    normalized = _normalized_market_scan(job)
    assert normalized["candidates"] == []
    assert normalized["stale_contract"] is True


def test_025_forensic_engine_has_no_reference_price_fallback():
    from pathlib import Path
    source = Path("mfapp/discovery_forensics.py").read_text()
    market = Path("mfapp/market_discovery.py").read_text()
    universe = Path("mfapp/discovery_universe.py").read_text()
    assert "allow_reference_fallback=False" in source
    assert "valuation_methods" in source
    assert "FORENSIC_EDGE_PCT = 20.0" in source
    assert "stage0_universe" in market and "stage1_screen" in market
    assert '"contract_version": CONTRACT_VERSION' in market
    assert "from .secdata" not in universe and "api/xbrl" not in universe.lower() and "SEC_DATA" not in universe
