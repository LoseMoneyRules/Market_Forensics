from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.core_models import Job
from mfapp.security import encrypt_secret, hash_password


def _make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "0212-discovery",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '0212.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def _seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control0212@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def _stage0():
    return {
        "member_count": 1000, "raw_count": 1200, "excluded_count": 200,
        "excluded_breakdown": {"NON-OPERATING SECURITY": 200},
        "generated_at": "2026-09-19T12:00:00", "cache_hit": True,
    }


def _stage1_row(ticker="QUIET", *, shortable=True, activity=False):
    lanes = ["BROAD_ROTATION"]
    if activity:
        lanes.append("MARKET_ACTIVITY")
    return {
        "ticker": ticker, "name": f"{ticker} Corp", "exchange": "NYSE",
        "price": 50.0, "daily_volume": 2_000_000, "dollar_volume": 100_000_000,
        "move_pct": 0.2, "shortable": shortable, "easy_to_borrow": shortable,
        "stage1_lanes": lanes, "stage1_reasons": ["Broad universe rotation."],
        "snapshot_as_of": "2026-09-19T15:00:00Z",
    }


def _stage1(rows):
    return {
        "rows": rows, "scanned_count": len(rows), "qualified_count": len(rows),
        "broad_rotation_count": sum("BROAD_ROTATION" in r["stage1_lanes"] for r in rows),
        "quiet_broad_count": sum("MARKET_ACTIVITY" not in r["stage1_lanes"] for r in rows),
        "activity_count": sum("MARKET_ACTIVITY" in r["stage1_lanes"] for r in rows),
        "cursor_start": 0, "cursor_end": len(rows), "excluded_breakdown": {},
        "snapshot_requested_count": len(rows), "snapshot_received_count": len(rows),
        "coverage_progress": {
            "universe_size": 1000, "seen_7d": len(rows), "seen_30d": len(rows),
            "pct_7d": round(len(rows) / 10.0, 1), "pct_30d": round(len(rows) / 10.0, 1),
            "estimated_full_rotation_runs": 5,
        },
    }


def _evidence(*, gap=30.0, quality="INTRINSIC", methods=3, side="LONG", signals=True):
    signal = {
        "LONG": {"side": "LONG", "label": "OPERATING LEVERAGE", "detail": "Operating margin +150 bps YoY", "points": 16},
        "SHORT": {"side": "SHORT", "label": "OPERATING DELEVERAGE", "detail": "Operating margin -150 bps YoY", "points": 18},
    }[side]
    return {
        "bear": 35.0, "base": 65.0 if side == "LONG" else 35.0, "bull": 80.0,
        "gap_pct": gap, "quality": quality, "valuation_methods": methods,
        "long_score": 16 if side == "LONG" and signals else 0,
        "short_score": 18 if side == "SHORT" and signals else 0,
        "signals": [signal] if signals else [], "snapshot": {}, "source": "TEST",
        "data_freshness": "2026-09-19T15:00:00",
    }


def _run_scan(monkeypatch, evidence, *, row=None):
    import mfapp.market_discovery as md

    row = row or _stage1_row()
    monkeypatch.setattr(md, "_headers", lambda user_id: {"x": "y"})
    monkeypatch.setattr(md, "stage0_universe", lambda *args, **kwargs: _stage0())
    monkeypatch.setattr(md, "stage1_screen", lambda *args, **kwargs: _stage1([row]))
    monkeypatch.setattr(md, "_active_coverage_tickers", lambda user_id: set())
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {})
    monkeypatch.setattr(md, "enrich_forensic_candidates", lambda *args, **kwargs: ({row["ticker"]: evidence}, {}))
    return md.market_scan(1)


def test_0212_stage0_broad_universe_includes_quiet_non_mover(tmp_path, monkeypatch):
    import mfapp.discovery_universe as du

    app = _make_app(tmp_path, monkeypatch)
    user_id = _seed_control(app)

    class Response:
        status_code = 200
        def json(self):
            return [
                {"symbol": "QUIET", "name": "Quiet Industrial Inc", "exchange": "NYSE", "status": "active", "tradable": True, "class": "us_equity", "shortable": True},
                {"symbol": "ETF1", "name": "Example ETF Fund", "exchange": "NYSE", "status": "active", "tradable": True, "class": "us_equity"},
                {"symbol": "SPAC", "name": "Example Acquisition Corp", "exchange": "NASDAQ", "status": "active", "tradable": True, "class": "us_equity"},
            ]

    monkeypatch.setattr(du.requests, "get", lambda *args, **kwargs: Response())
    with app.app_context():
        calls = Counter()
        result = du.stage0_universe(user_id, {"x": "y"}, [], calls, force_refresh=True)
    assert [row["ticker"] for row in result["members"]] == ["QUIET"]
    assert result["member_count"] == 1
    assert calls["alpaca_assets"] == 1


def test_0212_stage0_excludes_etf_warrant_and_spac_shell():
    from mfapp.discovery_universe import _asset_is_operating_equity

    base = {"exchange": "NYSE", "status": "active", "tradable": True, "class": "us_equity"}
    assert _asset_is_operating_equity({**base, "symbol": "ABC", "name": "ABC Industrial"})[0] is True
    assert _asset_is_operating_equity({**base, "symbol": "SPY", "name": "SPDR S&P 500 ETF Fund"})[0] is False
    assert _asset_is_operating_equity({**base, "symbol": "QQQ", "name": "Invesco QQQ Trust Series 1"})[0] is False
    assert _asset_is_operating_equity({**base, "symbol": "ABC.WS", "name": "ABC Warrant"})[0] is False
    assert _asset_is_operating_equity({**base, "symbol": "ACQ", "name": "Example Acquisition Corp"})[0] is False


def test_0212_stage1_liquidity_uses_completed_prior_bar(monkeypatch):
    import mfapp.discovery_universe as du

    class Response:
        status_code = 200
        def json(self):
            return {
                "QUIET": {
                    "latestTrade": {"p": 50.0, "t": "2026-09-19T15:00:00Z"},
                    "dailyBar": {"c": 50.0, "v": 100_000},
                    "prevDailyBar": {"c": 49.0, "v": 2_000_000},
                }
            }

    monkeypatch.setattr(du.requests, "get", lambda *args, **kwargs: Response())
    calls = Counter()
    result = du._snapshot_map(["QUIET"], {"x": "y"}, [], calls)["QUIET"]
    assert result["daily_volume"] == 2_000_000
    assert result["dollar_volume"] == 98_000_000
    assert result["liquidity_basis"] == "PREVIOUS_COMPLETED_DAILY_BAR"
    assert calls["alpaca_snapshot_batches"] == 1


def test_0212_stage1_has_no_sec_deep_enrichment():
    source = Path("mfapp/discovery_universe.py").read_text()
    assert "from .secdata" not in source
    assert "api/xbrl" not in source.lower()
    assert "SEC_DATA" not in source


def test_0212_stage2_reuses_canonical_valuation_engine(monkeypatch):
    import mfapp.discovery_forensics as df

    called = {}
    monkeypatch.setattr(df, "metrics_from_history", lambda annual: {"revenue": 100, "shares": 10, "basis_usable": True, "net_debt": 0})
    monkeypatch.setattr(df, "default_cases", lambda metrics, company_type: {
        "BEAR": {"probability": .25}, "BASE": {"probability": .5}, "BULL": {"probability": .25},
        "weights": {"pe": 1, "ev_sales": 1, "fcf_yield": 1}, "horizon_years": 5,
    })
    def fake_evaluate(metrics, cases, weights, years, current_price=None, **kwargs):
        called.update(kwargs)
        return {
            "quality": "INTRINSIC", "warnings": [],
            "scenarios": {
                "BEAR": {"fair_value": 40, "pe": 40, "ev_sales": 40, "fcf_yield": 40},
                "BASE": {"fair_value": 60, "pe": 60, "ev_sales": 60, "fcf_yield": 60},
                "BULL": {"fair_value": 80, "pe": 80, "ev_sales": 80, "fcf_yield": 80},
            },
        }
    monkeypatch.setattr(df, "evaluate", fake_evaluate)
    out = df._valuation_from_history(
        [{"fiscal_year": 2025, "revenue": 100}],
        {"revenue": 110, "net_income": 10, "fcf": 12, "cash": 5, "debt": 0, "shares_outstanding": 10, "operating_income": 15},
        {"revenue": 100}, 50.0,
    )
    assert called["allow_reference_fallback"] is False
    assert out["base"] == 60
    assert out["valuation_methods"] == 3


def test_0212_provisional_reference_valuation_is_watch_not_qualified(monkeypatch):
    result = _run_scan(monkeypatch, _evidence(quality="PROVISIONAL_REFERENCE_FALLBACK"))
    assert result["long_count"] == 0
    assert result["watch_count"] == 1
    assert result["watch_candidates"][0]["priority"] == "WATCH"
    assert "needs verification" in result["watch_candidates"][0]["priority_reason"].lower()


def test_0212_less_than_two_valuation_methods_is_watch_not_qualified(monkeypatch):
    result = _run_scan(monkeypatch, _evidence(methods=1))
    assert result["long_count"] == 0
    assert result["watch_count"] == 1
    assert result["watch_candidates"][0]["valuation_methods"] == 1


def test_0212_long_funnel_distinguishes_p1_p2_and_watch(monkeypatch):
    emerging = _run_scan(monkeypatch, _evidence(gap=19.9))
    assert emerging["watch_count"] == 1
    assert emerging["watch_candidates"][0]["priority"] == "WATCH"

    no_signal_below_edge = _run_scan(monkeypatch, _evidence(gap=19.9, signals=False))
    assert no_signal_below_edge["candidates"] == []

    valuation_lead = _run_scan(monkeypatch, _evidence(gap=25.0, signals=False))
    assert valuation_lead["long_count"] == 1
    assert valuation_lead["long_candidates"][0]["priority"] == "P2"

    strong = _run_scan(monkeypatch, _evidence(gap=25.0))
    assert strong["long_count"] == 1
    assert strong["long_candidates"][0]["priority"] == "P1"


def test_0212_short_actionability_downgrades_to_watch_instead_of_disappearing(monkeypatch):
    short = _evidence(gap=-25.0, side="SHORT")
    not_shortable = _run_scan(monkeypatch, short, row=_stage1_row("SHORTX", shortable=False))
    assert not_shortable["short_count"] == 0
    assert not_shortable["watch_count"] == 1
    assert not_shortable["watch_candidates"][0]["direction"] == "SHORT"

    good = _run_scan(monkeypatch, short, row=_stage1_row("SHORTY", shortable=True))
    assert good["short_count"] == 1
    assert good["short_candidates"][0]["priority"] == "P1"


def test_0212_zero_candidates_is_valid_run(monkeypatch):
    result = _run_scan(monkeypatch, _evidence(gap=0.0))
    assert result["configured"] is True
    assert result["candidate_count"] == 0
    assert result["long_count"] == 0
    assert result["short_count"] == 0


def test_0212_discovery_never_auto_promotes_to_coverage():
    market = Path("mfapp/market_discovery.py").read_text()
    universe = Path("mfapp/discovery_universe.py").read_text()
    template = Path("mfapp/templates/discovery.html").read_text()
    assert "Coverage(" not in market
    assert "Coverage(" not in universe
    assert "ensure_security_from_validation" not in market
    assert ">Promote<" in template


def test_0212_normal_discovery_get_is_provider_free(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    user_id = _seed_control(app)
    client = app.test_client()
    _login(client, user_id)

    def fail_network(*args, **kwargs):
        raise AssertionError("normal Discovery GET must not call providers")

    monkeypatch.setattr("requests.sessions.Session.request", fail_network)
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "BROAD UNIVERSE DISCOVERY" in response.get_data(as_text=True)


def test_0212_stage1_all_lanes_are_hard_bounded(tmp_path, monkeypatch):
    import mfapp.discovery_universe as du

    app = _make_app(tmp_path, monkeypatch)
    user_id = _seed_control(app)
    members = []
    for prefix, count in (("R", 240), ("A", 200), ("C", 120)):
        for idx in range(count):
            members.append({
                "ticker": f"{prefix}{idx:03d}", "name": f"{prefix}{idx:03d} Corp",
                "exchange": "NYSE", "shortable": True, "easy_to_borrow": True,
            })
    activity = {
        f"A{idx:03d}": {"ticker": f"A{idx:03d}", "activity_rank": idx + 1, "move_pct": 1.0, "activity_sources": ["MOST_ACTIVE"]}
        for idx in range(200)
    }
    captured = {}

    monkeypatch.setattr(du, "_activity_pool", lambda *args, **kwargs: activity)
    def fake_snapshots(symbols, *args, **kwargs):
        captured["symbols"] = list(symbols)
        return {
            ticker: {"price": 20.0, "daily_volume": 3_000_000, "dollar_volume": 60_000_000, "move_pct": 0.0, "as_of": "2026-09-19T15:00:00Z"}
            for ticker in symbols
        }
    monkeypatch.setattr(du, "_snapshot_map", fake_snapshots)

    with app.app_context():
        result = du.stage1_screen(
            user_id, {"x": "y"},
            {"members": members, "generated_at": "2026-09-19T12:00:00"},
            [], Counter(), known_tickers={f"C{idx:03d}" for idx in range(120)},
        )

    assert len(captured["symbols"]) == 480
    assert result["scanned_count"] == 480
    assert result["batch_size"] == 360
    assert result["activity_limit"] == 160
    assert result["coverage_limit"] == 80


def test_0212_stage1_does_not_advance_checkpoint_when_snapshots_fail(tmp_path, monkeypatch):
    import mfapp.discovery_universe as du

    app = _make_app(tmp_path, monkeypatch)
    user_id = _seed_control(app)
    members = [
        {"ticker": f"R{idx:03d}", "name": f"R{idx:03d} Corp", "exchange": "NYSE", "shortable": True}
        for idx in range(20)
    ]
    monkeypatch.setattr(du, "_activity_pool", lambda *args, **kwargs: {})
    monkeypatch.setattr(du, "_snapshot_map", lambda *args, **kwargs: {})

    with app.app_context():
        result = du.stage1_screen(
            user_id, {"x": "y"}, {"members": members, "generated_at": "2026-09-19T12:00:00"},
            [], Counter(), known_tickers=set(),
        )
    assert result["cursor_start"] == 0
    assert result["cursor_end"] == 0
    assert result["qualified_count"] == 0


def test_0212_stage2_and_snapshot_work_are_hard_bounded():
    import mfapp.discovery_forensics as df
    import mfapp.discovery_universe as du
    import mfapp.market_discovery as md

    assert df.FORENSIC_ENRICH_LIMIT == 10
    assert du.STAGE1_BATCH_SIZE == 360
    assert du.STAGE1_ACTIVITY_LIMIT == 160
    assert du.STAGE1_COVERAGE_LIMIT == 80
    assert du.SNAPSHOT_CHUNK_SIZE == 60
    rows = [_stage1_row(f"Q{i:03d}") for i in range(100)]
    selected = md._select_stage2_finalists(rows, {}, limit=df.FORENSIC_ENRICH_LIMIT)
    assert len(selected) == 10
    # Max normal run: 1 asset refresh + 2 screeners + ceil((360+160+80)/60)
    # snapshots + 1 SEC map + 2 calls per 10 unknown finalists.
    assert 1 + 2 + 10 + 1 + (2 * df.FORENSIC_ENRICH_LIMIT) <= 34
    assert md.MIN_DOLLAR_VOLUME == 15_000_000.0
    assert md.MIN_DAILY_VOLUME == 200_000.0


def test_0212_ttm_requires_four_coherent_fiscal_quarters():
    from mfapp.discovery_forensics import _ttm

    base = datetime(2025, 3, 31)
    rows = []
    for idx in range(4):
        end = (base + timedelta(days=91 * idx)).date().isoformat()
        rows.append({"period_end": end, "revenue": 10, "gross_profit": 5, "operating_income": 2, "net_income": 1, "cfo": 2, "capex": 1, "fcf": 1})
    assert _ttm(rows) is not None
    assert _ttm(rows[:3]) is None
    broken = list(rows)
    broken[-1] = {**broken[-1], "period_end": "2027-12-31"}
    assert _ttm(broken) is None


def test_0212_final_ranking_is_auditable_not_hidden_composite():
    source = Path("mfapp/market_discovery.py").read_text()
    template = Path("mfapp/templates/discovery.html").read_text()
    reporting = Path("mfapp/reporting.py").read_text()
    assert '"ranking_basis"' in source
    assert "scan_score" not in source
    assert "forensic score" not in template.lower()
    discovery_report = reporting.split("def render_discovery_pdf(", 1)[1].split("__all__", 1)[0]
    assert "scan_score" not in discovery_report
    for field in ("Bear", "Base", "Bull", "Gap", "methods", "Invalidation", "Freshness"):
        assert field in template



def test_0212_tracks_recent_broad_universe_coverage(tmp_path, monkeypatch):
    import mfapp.discovery_universe as du

    app = _make_app(tmp_path, monkeypatch)
    user_id = _seed_control(app)
    members = [
        {"ticker": f"U{idx:03d}", "name": f"Universe {idx}", "exchange": "NYSE", "shortable": True}
        for idx in range(600)
    ]
    monkeypatch.setattr(du, "_activity_pool", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        du, "_snapshot_map",
        lambda symbols, *args, **kwargs: {
            ticker: {
                "price": 25.0, "daily_volume": 3_000_000, "dollar_volume": 75_000_000,
                "move_pct": 0.0, "as_of": "2026-09-19T15:00:00Z",
                "liquidity_basis": "PREVIOUS_COMPLETED_DAILY_BAR",
            }
            for ticker in symbols
        },
    )

    with app.app_context():
        first = du.stage1_screen(
            user_id, {"x": "y"},
            {"members": members, "generated_at": "2026-09-19T12:00:00"},
            [], Counter(), known_tickers=set(),
        )
        second = du.stage1_screen(
            user_id, {"x": "y"},
            {"members": members, "generated_at": "2026-09-19T12:00:00"},
            [], Counter(), known_tickers=set(),
        )

    assert first["coverage_progress"]["seen_7d"] == 360
    assert first["coverage_progress"]["pct_7d"] == 60.0
    assert second["coverage_progress"]["seen_7d"] == 600
    assert second["coverage_progress"]["pct_7d"] == 100.0
    assert second["coverage_progress"]["estimated_full_rotation_runs"] == 2


def test_0212_universe_health_detects_collapse_and_snapshot_failure():
    from mfapp.market_discovery import _discovery_health

    health = _discovery_health(
        {"member_count": 600, "previous_member_count": 1000, "stale_cache": False},
        {
            "snapshot_requested_count": 100, "snapshot_received_count": 50,
            "excluded_breakdown": {"PRICE UNKNOWN": 10},
        },
    )
    assert health["status"] == "CRITICAL"
    assert any(">30% drop" in flag for flag in health["flags"])
    assert any("50/100" in flag for flag in health["flags"])


def test_0212_basis_guard_flags_share_discontinuity_and_short_history():
    from mfapp.discovery_forensics import _basis_review_flags

    flags = _basis_review_flags(
        {"shares_outstanding": 200},
        {"shares_outstanding": 100},
        annual=[{"fiscal_year": 2025}, {"fiscal_year": 2024}],
    )
    assert any("Share-count basis changed" in flag for flag in flags)
    assert any("Short filed history" in flag for flag in flags)


def test_0212_corporate_action_review_never_becomes_candidate(monkeypatch):
    evidence = _evidence(gap=35.0)
    evidence["corporate_action_review"] = ["Share-count basis changed +100% YoY; basis needs review."]
    result = _run_scan(monkeypatch, evidence)
    assert result["candidates"] == []
    assert result["excluded_breakdown"]["CORPORATE ACTION / BASIS REVIEW"] >= 1
    assert any(
        row["ticker"] == "QUIET" and row["reason"] == "CORPORATE ACTION / BASIS REVIEW"
        for row in result["rejection_log"]
    )


def test_0212_scan_cadence_builds_breadth_then_becomes_weekly():
    from mfapp.market_discovery import _scan_cadence

    health = {"status": "OK"}
    early = _scan_cadence(health, {"pct_30d": 10.0})
    mature = _scan_cadence(health, {"pct_30d": 50.0})
    retry = _scan_cadence({"status": "WARN"}, {"pct_30d": 50.0})
    critical = _scan_cadence({"status": "CRITICAL"}, {"pct_30d": 50.0})
    assert early["recommended_interval_days"] == 3
    assert mature["recommended_interval_days"] == 7
    assert retry["recommended_interval_days"] == 3
    assert critical["recommended_interval_days"] == 1


def test_0212_discovery_ui_exposes_health_rejections_freshness_and_provenance():
    template = Path("mfapp/templates/discovery.html").read_text()
    routes = Path("mfapp/routes.py").read_text()
    for text in (
        "Universe health", "7d breadth", "30d breadth", "Investigated but rejected",
        "Emerging / verification-needed leads", "Discovery finds research leads; Validation remains stricter",
        "Freshness · Market:", 'name="origin" value="discovery"',
    ):
        assert text in template
    assert "_discovery_promotion_provenance" in routes
    assert '"discovery_provenance"' in routes



def test_0212_discovery_page_renders_sparse_stage2_rejection(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    user_id = _seed_control(app)
    with app.app_context():
        job = Job(
            job_type="DISCOVERY_SCAN",
            status="DONE",
            priority=70,
            user_id=user_id,
            payload={},
            result={
                "market_scan": {
                    "contract_version": "BROAD_FORENSIC_DISCOVERY_V3",
                    "configured": True,
                    "candidates": [],
                    "errors": [],
                    "stage0_count": 4000,
                    "stage1_scanned_count": 240,
                    "stage1_qualified_count": 100,
                    "stage2_selected_count": 8,
                    "stage2_enriched_count": 0,
                    "provider_calls": {},
                    "provider_call_total": 0,
                    "guardrails": {},
                    "coverage_progress": {},
                    "universe_health": {"status": "OK", "flags": []},
                    "scan_cadence": {},
                    "rejection_log": [{
                        "ticker": "ABC",
                        "stage": "STAGE2 ENRICHMENT",
                        "reason": "SEC TICKER UNRESOLVED",
                        "detail": "",
                    }],
                }
            },
            finished_at=datetime(2026, 9, 19, 18, 45, 0),
        )
        db.session.add(job)
        db.session.commit()

    client = app.test_client()
    _login(client, user_id)
    response = client.get("/discovery")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Investigated but rejected" in body
    assert "SEC TICKER UNRESOLVED" in body
    assert "Something went wrong" not in body
