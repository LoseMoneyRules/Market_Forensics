from pathlib import Path

from mfapp.discovery_market_fundamentals import _screen_one
from mfapp.market_discovery import _select_stage2_finalists


def _row(ticker: str, screen: dict):
    return {
        "ticker": ticker,
        "name": ticker,
        "price": 20.0,
        "daily_volume": 1_000_000,
        "dollar_volume": 20_000_000,
        "move_pct": 0.0,
        "shortable": True,
        "easy_to_borrow": True,
        "stage1_lanes": ["FULL_UNIVERSE"],
        "fundamental_screen": screen,
    }


def test_unknown_stage2_never_uses_market_only_selection():
    blind = _row("BLIND", {
        "status": "MISSING",
        "eligible": False,
        "side": "NEUTRAL",
        "strength": 0,
        "signals": [],
    })
    assert _select_stage2_finalists([blind], {}, limit=20) == []


def test_full_market_fundamental_screen_is_directional_and_auditable():
    row = {"ticker": "ABC", "price": 20.0}
    facts = {
        "cik": "0000000123",
        "latest_filed": "2026-08-01",
        "current": {
            "revenue": 120.0,
            "operating_income": 18.0,
            "cfo": 20.0,
            "capex": 5.0,
            "inventory": 10.0,
            "receivables": 12.0,
            "shares": 10.0,
        },
        "prior": {
            "revenue": 100.0,
            "operating_income": 10.0,
            "cfo": 12.0,
            "capex": 5.0,
            "inventory": 12.0,
            "receivables": 14.0,
            "shares": 10.0,
        },
        "annual": {
            "revenue": 400.0,
            "net_income": 25.0,
            "cfo": 50.0,
            "capex": 10.0,
        },
    }
    screen = _screen_one(row, facts)
    assert screen["status"] == "READY"
    assert screen["eligible"] is True
    assert screen["side"] == "LONG"
    assert screen["strength"] >= 3
    assert any(item["label"] == "OPERATING LEVERAGE" for item in screen["signals"])
    assert any(item["label"] == "FCF YIELD" for item in screen["signals"])


def test_full_market_contract_is_explicit_in_code_and_ui():
    market = Path("mfapp/market_discovery.py").read_text()
    universe = Path("mfapp/discovery_universe.py").read_text()
    template = Path("mfapp/templates/discovery.html").read_text()
    assert 'CONTRACT_VERSION = "FULL_MARKET_FORENSIC_DISCOVERY_V4"' in market
    assert '"full_universe_each_run": True' in market
    assert '"unknown_stage2_requires_marketwide_fundamental_screen": True' in market
    assert "There is no rotating cursor" in universe
    assert "FULL MARKET" in template
    assert "no rotating slice" in template


def test_stage2_balances_marketwide_long_and_short_prescreen():
    long_screen = {
        "status": "READY",
        "eligible": True,
        "side": "LONG",
        "strength": 8,
        "signals": [{"side": "LONG", "label": "FCF YIELD", "detail": "cheap", "points": 3}],
    }
    short_screen = {
        "status": "READY",
        "eligible": True,
        "side": "SHORT",
        "strength": 8,
        "signals": [{"side": "SHORT", "label": "MARGIN PRESSURE", "detail": "deteriorating", "points": 3}],
    }
    rows = [
        *[_row(f"L{i}", long_screen) for i in range(20)],
        *[_row(f"S{i}", short_screen) for i in range(20)],
    ]
    selected = _select_stage2_finalists(rows, {}, limit=20)
    assert len(selected) == 20
    assert sum(row["fundamental_screen"]["side"] == "LONG" for row in selected) == 10
    assert sum(row["fundamental_screen"]["side"] == "SHORT" for row in selected) == 10
