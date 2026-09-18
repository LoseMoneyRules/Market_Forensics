from __future__ import annotations

from datetime import datetime, timezone

from mfapp.core_models import Job


def test_025_discovery_requires_fair_value_and_operating_confirmation(monkeypatch):
    import mfapp.market_discovery as md

    class FakeResponse:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload

    def fake_get(url, **kwargs):
        if "most-actives" in url:
            return FakeResponse({"most_actives": [
                {"symbol":"RETO","volume":100_000_000},
                {"symbol":"GOODL","volume":5_000_000},
                {"symbol":"GOODS","volume":5_000_000},
                {"symbol":"NOFAIR","volume":5_000_000},
            ]})
        return FakeResponse({
            "gainers":[{"symbol":"GOODS","percent_change":12.0}],
            "losers":[{"symbol":"GOODL","percent_change":-4.0}],
        })

    monkeypatch.setattr(md, "_headers", lambda user_id: {"x":"y"})
    monkeypatch.setattr(md.requests, "get", fake_get)
    monkeypatch.setattr(md, "_snapshot_map", lambda symbols, headers, errors: {
        "RETO":{"price":.20,"daily_volume":100_000_000,"dollar_volume":20_000_000},
        "GOODL":{"price":25.0,"daily_volume":5_000_000,"dollar_volume":125_000_000},
        "GOODS":{"price":60.0,"daily_volume":5_000_000,"dollar_volume":300_000_000},
        "NOFAIR":{"price":30.0,"daily_volume":5_000_000,"dollar_volume":150_000_000},
    })
    monkeypatch.setattr(md, "_asset_map", lambda symbols, headers, errors: {
        symbol: {"name":symbol+" Corp","status":"active","exchange":"NASDAQ","tradable":True,"shortable":True}
        for symbol in symbols
    })
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {})
    monkeypatch.setattr(md, "enrich_forensic_candidates", lambda user_id, pool, context, errors: {
        "GOODL":{"base":40.0,"gap_pct":60.0,"quality":"INTRINSIC","long_score":38,"short_score":0,
                 "signals":[{"side":"LONG","label":"REVENUE ACCELERATION","detail":"TTM revenue +12.0% YoY","points":14},
                            {"side":"LONG","label":"OPERATING LEVERAGE","detail":"Operating margin +180 bps YoY","points":16}],
                 "snapshot":{},"source":"TEST"},
        "GOODS":{"base":35.0,"gap_pct":-41.7,"quality":"INTRINSIC","long_score":0,"short_score":40,
                 "signals":[{"side":"SHORT","label":"REVENUE DETERIORATION","detail":"TTM revenue -8.0% YoY","points":16},
                            {"side":"SHORT","label":"OPERATING DELEVERAGE","detail":"Operating margin -220 bps YoY","points":18}],
                 "snapshot":{},"source":"TEST"},
    })

    result = md.market_scan(1)
    tickers = {row["ticker"] for row in result["candidates"]}
    assert "RETO" not in tickers
    assert "NOFAIR" not in tickers
    assert tickers == {"GOODL", "GOODS"}
    assert result["contract_version"] == "FORENSIC_FAIR_VALUE_V1"
    assert all(row.get("fair_value") is not None and row.get("forensic_signals") for row in result["candidates"])


def test_025_discovery_can_return_zero_instead_of_filler(monkeypatch):
    import mfapp.market_discovery as md

    class FakeResponse:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload

    monkeypatch.setattr(md, "_headers", lambda user_id: {"x":"y"})
    monkeypatch.setattr(md.requests, "get", lambda url, **kwargs: FakeResponse(
        {"most_actives":[{"symbol":"VALID","volume":5_000_000}]}
        if "most-actives" in url else {"gainers":[],"losers":[]}
    ))
    monkeypatch.setattr(md, "_snapshot_map", lambda symbols, headers, errors: {
        "VALID":{"price":30.0,"daily_volume":5_000_000,"dollar_volume":150_000_000}
    })
    monkeypatch.setattr(md, "_asset_map", lambda symbols, headers, errors: {
        "VALID":{"name":"Valid Corp","status":"active","exchange":"NYSE","tradable":True,"shortable":True}
    })
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {})
    monkeypatch.setattr(md, "enrich_forensic_candidates", lambda user_id, pool, context, errors: {})

    result = md.market_scan(1)
    assert result["candidates"] == []
    assert result["long_count"] == 0 and result["short_count"] == 0
    assert result["excluded_breakdown"]["NO FORENSIC FAIR VALUE / DATA"] == 1


def test_025_discovery_legacy_payload_is_rejected():
    from mfapp.routes import _normalized_market_scan

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = Job(
        job_type="DISCOVERY_SCAN", status="DONE", priority=70, user_id=1,
        payload={}, result={"market_scan":{"candidates":[{"ticker":"RETO","research_side":"SHORT"}]}},
        attempts=1, max_attempts=3, run_after=now, started_at=now, finished_at=now,
    )
    normalized = _normalized_market_scan(job)
    assert normalized["candidates"] == []
    assert normalized["stale_contract"] is True


def test_025_forensic_engine_has_no_reference_price_fallback():
    from pathlib import Path
    source = Path("mfapp/discovery_forensics.py").read_text()
    market = Path("mfapp/market_discovery.py").read_text()
    assert "allow_reference_fallback=False" in source
    assert "valuation_methods" in source
    assert "FORENSIC_EDGE_PCT = 20.0" in source
    assert "A price move alone can NEVER" in market
    assert '"contract_version": "FORENSIC_FAIR_VALUE_V1"' in market
