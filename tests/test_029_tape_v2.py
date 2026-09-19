from __future__ import annotations

from pathlib import Path

from mfapp.positioning import _aggregate_trade_flow
from mfapp.tape_engine import path_regime, score_tape_day


def test_029_tape_engine_restores_rank_and_distribution_logic():
    row = score_tape_day(
        return_pct=-3.0,
        volume_ratio=1.5,
        close_location=12.0,
        short_pct=58.0,
        net_large_ratio=-5.0,
        net_whale_ratio=-2.5,
        flow_confidence=90.0,
        si_change_pct=9.0,
        put_call_oi=1.35,
        hard_to_borrow=True,
        has_market=True,
        has_short_volume=True,
        has_flow=True,
        has_positioning=True,
    )
    assert row["rank"] in {"D", "F"}
    assert row["forensic_regime"] in {"DISTRIBUTION", "DISTRIBUTION / BEARS CONTROL"}
    assert path_regime(row["forensic_regime"]) == "HOSTILE"
    assert row["short_pressure"] > row["long_demand"]


def test_029_tape_engine_distinguishes_absorption_from_large_flow():
    absorbed = score_tape_day(
        return_pct=1.4,
        volume_ratio=1.4,
        close_location=88.0,
        short_pct=60.0,
        net_large_ratio=6.0,
        net_whale_ratio=3.0,
        flow_confidence=95.0,
        has_market=True,
        has_short_volume=True,
        has_flow=True,
        has_positioning=False,
    )
    failing = score_tape_day(
        return_pct=-3.0,
        volume_ratio=1.4,
        close_location=8.0,
        short_pct=60.0,
        net_large_ratio=6.0,
        net_whale_ratio=3.0,
        flow_confidence=95.0,
        has_market=True,
        has_short_volume=True,
        has_flow=True,
        has_positioning=False,
    )
    assert absorbed["institutional_flow"] == failing["institutional_flow"]
    assert absorbed["absorption"] > failing["absorption"]
    assert absorbed["price_resilience"] > failing["price_resilience"]
    assert absorbed["net_tape"] > failing["net_tape"]


def test_029_missing_flow_stays_low_data():
    row = score_tape_day(
        return_pct=0.5,
        volume_ratio=1.0,
        close_location=60.0,
        has_market=True,
        has_short_volume=False,
        has_flow=False,
        has_positioning=False,
    )
    assert row["forensic_regime"] == "LOW DATA"
    assert row["data_confidence"] < 55


def test_029_large_whale_flow_uses_adaptive_thresholds_and_proxy_language():
    trades = []
    price = 100.0
    for idx in range(20):
        price += 0.01 if idx % 2 == 0 else -0.005
        trades.append({"p": price, "s": 50 + idx, "t": f"2026-09-17T14:{idx:02d}:00Z", "i": idx})
    trades.extend([
        {"p": 101.0, "s": 6_000, "t": "2026-09-17T15:00:00Z", "i": 100},
        {"p": 100.5, "s": 7_000, "t": "2026-09-17T15:01:00Z", "i": 101},
        {"p": 101.2, "s": 12_000, "t": "2026-09-17T15:02:00Z", "i": 102},
    ])
    row = _aggregate_trade_flow(
        __import__("datetime").date(2026, 9, 17),
        {"rows": trades, "feed": "sip", "feed_scope": "CONSOLIDATED_SIP", "complete": True, "sampled": False, "errors": []},
    )
    assert row["large_threshold"] >= 100_000
    assert row["very_large_threshold"] >= 250_000
    assert row["whale_threshold"] >= 500_000
    assert row["classification_method"] == "TICK_RULE_PROXY"
    assert row["feed_scope"] == "CONSOLIDATED_SIP"
    assert row["trade_rows"] == len(trades)


def test_029_tape_surface_restores_local_chart_contract():
    template = Path("mfapp/templates/company_section.html").read_text()
    js = Path("mfapp/static/js/app.js").read_text()
    operating = Path("docs/HOW_MARKET_FORENSICS_WORKS.md").read_text()

    for chart in (
        "tape-price-flow", "tape-volume", "tape-price-short", "tape-short",
        "tape-flow", "tape-cumulative-flow", "tape-scores", "tape-whale", "tape-ats",
    ):
        assert f'data-mf-chart="{chart}"' in template
        assert chart in js

    assert "LARGE / WHALE POSITIONING" in template
    assert "WHAT CHANGED?" in template
    assert "WHAT WOULD CHANGE THE REGIME?" in template
    assert "Large / Whale is a trade-size proxy" in template
    assert "Tape Engine V2" in operating
    assert tuple(int(part) for part in Path("VERSION").read_text().strip().split(".")) >= (0, 2, 9)


def test_029_tape_v2_preserves_028_tape_surface():
    template = Path("mfapp/templates/company_section.html").read_text()

    for label in (
        "MACHINE READ",
        "Tape posture",
        "Directional pressure",
        "Next confirmation",
        "Borrow status",
        "Borrow fee",
        "Put / Call OI",
        "Turnover impulse",
        "Short interest",
        "SI change",
        "Days to cover",
        "Daily short vol · 20d",
        "Settlement",
        "Flow interpretation",
        "Add sourced borrow-fee observation",
    ):
        assert label in template

    assert 'data-mf-chart="tape-price-short"' in template
    assert 'data-mf-chart="tape-short"' in template
    assert "Historical market series not stored yet" in template
    assert "<th>Settlement</th><th>Short interest</th><th>Days to cover</th>" in template


def test_029_old_tape_cache_is_forward_compatible():
    from mfapp.routes import _cached_tape_for_months

    old = {
        "months": 12,
        "market": [{"date": "2026-09-12", "price": 100.0, "volume": 1_000_000}],
        "short_interest": [],
        "short_volume": [],
        "positioning": {},
        "metrics": {
            "absorption": 55.0,
            "price_resilience": 52.0,
            "long_demand": 51.0,
            "bear_pressure": 49.0,
            "battle_intensity": 40.0,
            "net_tape": 20.0,
            "rank_score": 45.0,
            "regime": "MIXED",
            "confidence": "MEDIUM",
        },
    }
    upgraded = _cached_tape_for_months(old, 12)
    assert upgraded["metrics"]["rank"] == "—"
    assert upgraded["metrics"]["forensic_regime"] == "LOW DATA"
    assert upgraded["institutional_flow"] == []
    assert upgraded["ats"] == []
    assert upgraded["tape_daily"] == []


def test_029_tape_v2_is_wired_to_background_evidence_not_get_navigation():
    jobs = Path("mfapp/jobs.py").read_text()
    positioning = Path("mfapp/positioning.py").read_text()
    finra = Path("mfapp/finra.py").read_text()
    routes = Path("mfapp/routes.py").read_text()
    cache = Path("mfapp/research_cache.py").read_text()

    assert 'event_type="ALPACA_POSITIONING"' in jobs
    assert 'refresh_positioning_bundle(security.ticker, job.user_id)' in jobs
    assert 'event_type="FINRA_ATS_SERIES"' in jobs
    assert 'out["flow"] = refresh_institutional_flow(symbol, user_id)' in positioning
    assert '"weekly_otc"' in finra and "weekly_otc_summary" in finra

    tape_route = routes.split("elif section == \"tape\":", 1)[1].split("elif section == \"monitoring\":", 1)[0]
    assert "_cached_tape_for_months" in tape_route
    assert "tape_series(" not in tape_route
    assert "tape = tape_series(security, 12)" in cache
