from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Job, ResearchGateApproval, ResearchState, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "024-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '024.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control024@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(
            company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD",
            validation_source="TEST", active=True, is_primary=True,
        )
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
        research.thesis = "Evidence-backed thesis"
        research.counter_evidence = "Explicit counter evidence"
        research.variant_us = "Variant perception"
        db.session.commit()
        return user.id, company.id, security.id, coverage.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_024_process_readiness_full_approve_reopen_cycle_has_no_recalc(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, coverage_id = seed_control(app)
    client = app.test_client(); login_control(client, uid)

    with app.app_context():
        before = Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count()

    approved = client.post("/company/EXM/readiness/overview", headers={"Accept": "application/json"}, data={"action": "approve"})
    assert approved.status_code == 200
    assert "APPROVED" in approved.get_json()["html"] and "Reopen" in approved.get_json()["html"]

    reopened = client.post("/company/EXM/readiness/overview", headers={"Accept": "application/json"}, data={"action": "revoke"})
    assert reopened.status_code == 200
    assert "PENDING APPROVAL" in reopened.get_json()["html"] and "Approve" in reopened.get_json()["html"]

    approved_again = client.post("/company/EXM/readiness/overview", headers={"Accept": "application/json"}, data={"action": "approve"})
    assert approved_again.status_code == 200 and "APPROVED" in approved_again.get_json()["html"]

    with app.app_context():
        after = Job.query.filter_by(user_id=uid, job_type="RECALCULATE").count()
        assert after == before
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage_id, gate_key="overview").first() is not None


def test_024_command_center_fits_desktop_and_scrolls_only_narrow():
    dashboard = Path("mfapp/templates/dashboard.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    assert '<div class="table-card coverage-card">' in dashboard
    assert ".coverage-card{overflow:visible}" in css
    assert ".coverage-table{width:100%;min-width:0;table-layout:auto" in css
    assert ".coverage-table th{white-space:nowrap" in css
    assert ".coverage-table td{padding:10px 6px" in css
    assert ".coverage-table{width:max-content" not in css
    assert "@media(max-width:1100px){.coverage-card{overflow-x:auto}.coverage-table{min-width:1080px}" in css
    assert ".coverage-table th:last-child,.coverage-table td:last-child{width:1%;white-space:nowrap" in css


def test_024_alpaca_raw_history_survives_split_adjustment_failure(monkeypatch):
    import mfapp.historical_data as hd

    calls = []
    def fake_pages(ticker, user_id, start, end, adjustment):
        calls.append(adjustment)
        if adjustment == "split":
            raise RuntimeError("split endpoint failed")
        return [
            {"t": "2026-09-16T00:00:00Z", "c": 220.0, "v": 10_000_000},
            {"t": "2026-09-17T00:00:00Z", "c": 222.0, "v": 11_000_000},
        ]

    monkeypatch.setattr(hd, "_alpaca_pages", fake_pages)
    rows = hd._alpaca_history("AAPL", 1, hd.date(2026, 9, 1), hd.date(2026, 9, 18))
    assert calls == ["raw", "split"]
    assert len(rows) == 2
    assert rows[0]["close_raw"] == rows[0]["close_split_adjusted"] == 220.0


def test_024_discovery_filters_penny_short_and_builds_prioritized_long_short(monkeypatch):
    import mfapp.market_discovery as md

    class FakeResponse:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload

    def fake_get(url, **kwargs):
        if "most-actives" in url:
            return FakeResponse({"most_actives": [
                {"symbol": "PENNY", "volume": 80_000_000},
                {"symbol": "LONGY", "volume": 5_000_000},
                {"symbol": "SHORTY", "volume": 4_000_000},
            ]})
        return FakeResponse({
            "gainers": [
                {"symbol": "PENNY", "percent_change": 35.0},
                {"symbol": "SHORTY", "percent_change": 15.0},
            ],
            "losers": [{"symbol": "LONGY", "percent_change": -13.0}],
        })

    monkeypatch.setattr(md, "_headers", lambda user_id: {"x": "y"})
    monkeypatch.setattr(md.requests, "get", fake_get)
    monkeypatch.setattr(md, "_snapshot_map", lambda symbols, headers, errors: {
        "PENNY": {"price": .21, "daily_volume": 80_000_000, "dollar_volume": 16_800_000},
        "LONGY": {"price": 25.0, "daily_volume": 5_000_000, "dollar_volume": 125_000_000},
        "SHORTY": {"price": 50.0, "daily_volume": 4_000_000, "dollar_volume": 200_000_000},
    })
    monkeypatch.setattr(md, "_coverage_context_map", lambda user_id, symbols: {})

    result = md.market_scan(1)
    tickers = {row["ticker"] for row in result["candidates"]}
    assert "PENNY" not in tickers
    assert result["excluded_breakdown"]["LOW PRICE"] == 1
    assert result["long_candidates"][0]["ticker"] == "LONGY"
    assert result["short_candidates"][0]["ticker"] == "SHORTY"
    assert result["long_candidates"][0]["priority"] == "P2"
    assert result["short_candidates"][0]["priority"] == "P2"


def test_024_discovery_ui_is_two_column_and_priority_first():
    html = Path("mfapp/templates/discovery.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    assert "discovery-side-grid" in html
    assert "LONG RADAR" in html and "SHORT RADAR" in html
    assert "priority-badge priority-" in html
    assert "Filtered universe audit" in html
    assert ".discovery-side-grid{display:grid;grid-template-columns:1fr 1fr" in css


def test_024_financial_flows_have_intrinsic_compact_geometry_and_no_engine_label():
    css = Path("mfapp/static/css/app.css").read_text()
    js = Path("mfapp/static/js/flows.js").read_text()
    assert ".flow-canvas{min-height:0" in css
    assert ".flow-svg{display:block;width:auto;height:auto;max-width:none;min-height:0;margin:0}" in css
    assert "const colGap=180,nodeW=150,nodeH=52,top=14,side=10,rowGap=12;" in js
    assert "width,height,role:'img'" in js
    assert "calculation_version" not in js
    assert " · engine " not in js


def test_024_company_header_is_one_literal_component_everywhere():
    paths = (
        "mfapp/templates/company_section.html",
        "mfapp/templates/valuation.html",
        "mfapp/templates/financial_flows.html",
        "mfapp/templates/validate.html",
        "mfapp/templates/portfolio_security.html",
    )
    for path in paths:
        text = Path(path).read_text()
        assert text.count('{% include "_company_header.html" %}') == 1
        assert "company_header_context" not in text
        assert "company_header_states" not in text
        assert "data-live-price" not in text
    canonical = Path("mfapp/templates/_company_header.html").read_text()
    for token in ("security.ticker", "security.exchange", "company.display_name", "coverage.research_state", "coverage.status", "readiness.done", "data-live-price", "market.provider", "market.as_of"):
        assert token in canonical


def test_024_settings_is_only_visible_version_surface():
    settings = Path("mfapp/templates/settings.html").read_text()
    assert "v{{ mf_version }}" in settings

    for path in Path("mfapp/templates").glob("*.html"):
        if path.name == "settings.html":
            continue
        text = path.read_text()
        assert "trace_build" not in text, path
        assert "calculation_version" not in text, path
        assert not __import__("re").search(r"\b0\.\d+\.\d+\b", text), path

    for path in (Path("mfapp/static/js/flows.js"), Path("mfapp/static/js/app.js"), Path("mfapp/static/css/app.css"), Path("mfapp/reporting.py")):
        text = path.read_text()
        assert not __import__("re").search(r"\b0\.\d+\.\d+\b", text), path


def test_024_reports_are_cache_only_and_safe_when_rich_rendering_fails(monkeypatch):
    import mfapp.reporting as reporting

    source = Path("mfapp/reporting.py").read_text()
    assert "automatic_triangulation(" not in source
    assert "tape_series(" not in source

    data = {
        "branding": {"title": "Market Forensics", "footer": "Lose Money Rules"},
        "ticker": "EXM", "company": "Example", "action": "DATA REVIEW", "stance": "WATCH",
        "confidence": "UNVALIDATED", "market_price": 40, "bear": 30, "base": 50, "bull": 70,
        "base_gap_pct": 25, "decision_lenses": [], "thesis": "", "counter_evidence": "",
        "variant_market": "", "variant_us": "", "supporting": [], "opposing": [], "mode": "executive",
        "implied_expectations": {},
    }
    monkeypatch.setattr(reporting, "render_pdf", lambda data: (_ for _ in ()).throw(RuntimeError("rich PDF failed")))
    monkeypatch.setattr(reporting, "render_docx", lambda data: (_ for _ in ()).throw(RuntimeError("rich Word failed")))
    assert reporting.render_pdf_safe(data).getvalue().startswith(b"%PDF-")
    assert reporting.render_docx_safe(data).getvalue().startswith(b"PK")


def test_024_report_routes_return_documents_without_release_in_filename(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, _ = seed_control(app)
    client = app.test_client(); login_control(client, uid)

    executive = client.get("/company/EXM/report/pdf?mode=executive")
    full = client.get("/company/EXM/report/pdf?mode=full")
    word = client.get("/company/EXM/report/docx?mode=full")
    assert executive.status_code == 200 and executive.data.startswith(b"%PDF-")
    assert full.status_code == 200 and full.data.startswith(b"%PDF-")
    assert word.status_code == 200 and word.data.startswith(b"PK")
    for response in (executive, full, word):
        assert "0.2." not in response.headers.get("Content-Disposition", "")


def test_024_price_history_live_contract_is_visible_and_non_disruptive():
    valuation = Path("mfapp/templates/valuation.html").read_text()
    js = Path("mfapp/static/js/app.js").read_text()
    routes = Path("mfapp/workspace_routes.py").read_text()
    assert "data-price-history-state" in valuation
    assert "PRICE HISTORY FAILED" in valuation
    assert "/price/history/live" in routes
    assert "pollPriceHistory" in js
    assert "valuationHistoryCanvas.dataset.history=JSON.stringify(payload.rows)" in js
    assert "valuationChart(valuationHistoryCanvas)" in js


def test_024_release_identity_and_deep_clean_rules():
    assert Path("VERSION").read_text().strip() == "0.2.4"
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert "**State-Version: 0.2.4**" in state
    assert "Permanent clean-release rule" in state
    assert "Settings is the only user-facing" in state
    assert "Approve → Reopen → Approve" in state
    assert "desktop layout problems" in state
