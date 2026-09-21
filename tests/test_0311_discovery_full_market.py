from pathlib import Path

from mfapp.discovery_market_fundamentals import _screen_one
from mfapp.market_discovery import _market_mispricing_hypothesis, _select_stage2_finalists


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
    assert 'CONTRACT_VERSION = "FULL_MARKET_MISPRICING_DISCOVERY_V5"' in market
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
        "metrics": {
            "revenue_yoy_pct": 8.0, "operating_margin_pct": 12.0,
            "operating_margin_change_pp": 2.0, "fcf_margin_pct": 10.0,
            "inventory_growth_pct": 0.0, "receivables_growth_pct": 0.0,
            "pe_proxy": 14.0, "ps_proxy": 1.5, "fcf_yield_pct": 8.0,
        },
    }
    short_screen = {
        "status": "READY",
        "eligible": True,
        "side": "SHORT",
        "strength": 8,
        "signals": [{"side": "SHORT", "label": "MARGIN PRESSURE", "detail": "deteriorating", "points": 3}],
        "metrics": {
            "revenue_yoy_pct": -10.0, "operating_margin_pct": 3.0,
            "operating_margin_change_pp": -3.0, "fcf_margin_pct": -2.0,
            "inventory_growth_pct": 8.0, "receivables_growth_pct": 10.0,
            "pe_proxy": 50.0, "ps_proxy": 8.0, "fcf_yield_pct": -1.0,
        },
    }
    rows = [
        *[_row(f"L{i}", long_screen) for i in range(20)],
        *[_row(f"S{i}", short_screen) for i in range(20)],
    ]
    selected = _select_stage2_finalists(rows, {}, limit=20)
    assert len(selected) == 20
    assert sum(row["fundamental_screen"]["side"] == "LONG" for row in selected) == 10
    assert sum(row["fundamental_screen"]["side"] == "SHORT" for row in selected) == 10


def test_stage15_requires_price_to_operating_tension_not_generic_strength():
    generic = _row("GENERIC", {
        "status": "READY", "eligible": True, "side": "LONG", "strength": 9,
        "signals": [{"side": "LONG", "label": "REVENUE GROWTH", "detail": "growth", "points": 3}],
        "metrics": {
            "revenue_yoy_pct": 12.0, "operating_margin_pct": 15.0,
            "operating_margin_change_pp": 2.0, "fcf_margin_pct": 8.0,
            "inventory_growth_pct": 4.0, "receivables_growth_pct": 5.0,
            "pe_proxy": 32.0, "ps_proxy": 6.0, "fcf_yield_pct": 3.5,
        },
    })
    generic["dollar_volume"] = 500_000_000
    mispriced = _row("MISPRICED", {
        "status": "READY", "eligible": True, "side": "LONG", "strength": 5,
        "signals": [{"side": "LONG", "label": "OPERATING LEVERAGE", "detail": "margin up", "points": 3}],
        "metrics": {
            "revenue_yoy_pct": 8.0, "operating_margin_pct": 14.0,
            "operating_margin_change_pp": 2.5, "fcf_margin_pct": 11.0,
            "inventory_growth_pct": -2.0, "receivables_growth_pct": 0.0,
            "pe_proxy": 13.0, "ps_proxy": 1.4, "fcf_yield_pct": 9.0,
        },
    })
    assert _market_mispricing_hypothesis(generic)["eligible"] is False
    hypothesis = _market_mispricing_hypothesis(mispriced)
    assert hypothesis["eligible"] is True
    assert hypothesis["side"] == "LONG"
    selected = _select_stage2_finalists([generic, mispriced], {}, limit=1)
    assert [row["ticker"] for row in selected] == ["MISPRICED"]


def test_stage15_short_hypothesis_requires_expensive_and_deteriorating():
    row = _row("SHORTMIS", {
        "status": "READY", "eligible": True, "side": "SHORT", "strength": 8,
        "signals": [],
        "metrics": {
            "revenue_yoy_pct": -12.0, "operating_margin_pct": 2.0,
            "operating_margin_change_pp": -3.5, "fcf_margin_pct": -4.0,
            "inventory_growth_pct": 12.0, "receivables_growth_pct": 14.0,
            "pe_proxy": 55.0, "ps_proxy": 9.0, "fcf_yield_pct": -2.0,
        },
    })
    hypothesis = _market_mispricing_hypothesis(row)
    assert hypothesis["eligible"] is True
    assert hypothesis["side"] == "SHORT"
    assert hypothesis["valuation_points"] >= 2
    assert hypothesis["operating_points"] >= 2


def test_stage2_ranking_uses_liquidity_only_after_evidence():
    weaker = _row("LIQUID", {
        "status": "READY", "eligible": True, "side": "LONG", "strength": 6,
        "signals": [],
        "metrics": {
            "revenue_yoy_pct": 3.0, "operating_margin_pct": 8.0,
            "operating_margin_change_pp": 1.0, "fcf_margin_pct": 6.0,
            "inventory_growth_pct": 2.0, "receivables_growth_pct": 2.0,
            "pe_proxy": 18.0, "ps_proxy": 1.8, "fcf_yield_pct": 5.5,
        },
    })
    weaker["dollar_volume"] = 900_000_000
    stronger = _row("EVIDENCE", {
        "status": "READY", "eligible": True, "side": "LONG", "strength": 6,
        "signals": [],
        "metrics": {
            "revenue_yoy_pct": 10.0, "operating_margin_pct": 14.0,
            "operating_margin_change_pp": 3.0, "fcf_margin_pct": 12.0,
            "inventory_growth_pct": -2.0, "receivables_growth_pct": 0.0,
            "pe_proxy": 12.0, "ps_proxy": 1.2, "fcf_yield_pct": 10.0,
        },
    })
    stronger["dollar_volume"] = 20_000_000
    selected = _select_stage2_finalists([weaker, stronger], {}, limit=1)
    assert [row["ticker"] for row in selected] == ["EVIDENCE"]
