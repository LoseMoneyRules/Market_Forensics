from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.alert_engine import alert_email as account_alert_email
from mfapp.calculations import CALCULATION_VERSION, financial_metrics
from mfapp.core_models import Catalyst, Company, Coverage, Event, HistoricalPrice, Job, MarketSnapshot, MonitoringRule, Position, RiskPlan, Security
from mfapp.decision_engine import build_research_intelligence
from mfapp.discovery_engine import classify_coverage
from mfapp.extensions import db
from mfapp.models import User, UserPreference
from mfapp.readiness import research_readiness
from mfapp.security import encrypt_secret, hash_password
from mfapp.reporting import get_report_branding, render_discovery_pdf, research_report_data, set_report_branding
from mfapp.services import create_snapshot, ensure_workspace, publication_payload


def make_app(tmp_path, monkeypatch, *, auto_migrate=False):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MF_SMTP_HOST", raising=False)
    monkeypatch.delenv("MF_SMTP_FROM", raising=False)
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "020-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '020.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": auto_migrate,
    })


def seed_workspace(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        company = Company(
            legal_name="Example Co",
            display_name="Example Co",
            sector="Industrials",
            industry="Machinery",
        )
        db.session.add_all([user, company])
        db.session.flush()
        security = Security(
            company_id=company.id,
            ticker="EXM",
            exchange="NYSE",
            currency="USD",
            validation_source="TEST",
            active=True,
            is_primary=True,
        )
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(
            user_id=user.id,
            security_id=security.id,
            status="RESEARCH",
            research_state="UNDER_REVIEW",
        )
        db.session.add(coverage)
        db.session.flush()
        ensure_workspace(coverage, user.id)
        db.session.add(MarketSnapshot(
            security_id=security.id,
            provider="TEST",
            price=Decimal("40"),
            currency="USD",
            as_of=datetime.now(timezone.utc).replace(tzinfo=None),
            quality="OBSERVED",
            payload={},
        ))
        db.session.commit()
        return user.id, company.id, security.id, coverage.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def seed_fast_cache(app, coverage_id, company_id, *, conclusion="LONG WATCH"):
    from mfapp.research_cache import cache_event_type
    with app.app_context():
        db.session.add(Event(
            company_id=company_id,
            event_type=cache_event_type(coverage_id),
            title="EXM research cache",
            event_date=datetime.now(timezone.utc).replace(tzinfo=None),
            payload={
                "coverage_id": coverage_id,
                "ticker": "EXM",
                "valuation": {"current_price": 40, "bear": 30, "base": 55, "bull": 70, "expected_value": 53},
                "readiness": {
                    "done": 0, "evidence_ready": 0, "total": 13, "gates": [],
                    "ready_to_validate": False,
                    "validation": {"state": "NOT RUN", "run_id": None, "status": None, "samples": 0, "reliability": None},
                    "bias_flags": [],
                },
                "intelligence": {
                    "action": "WAIT", "stance": "WATCH", "bias": "NEUTRAL", "confidence": "MEDIUM",
                    "score": 0.5, "positives": 1, "negatives": 0, "warnings": [], "blockers": [],
                    "signals": [], "top_signals": [], "supporting_evidence": [], "opposing_evidence": [],
                    "base_gap_pct": 37.5, "validation_state": "NOT RUN", "buy_threshold": 2.5, "sell_threshold": -2.5,
                },
                "decision_lenses": {
                    "rows": [], "business": "MIXED", "value": "ATTRACTIVE", "expectations": "BALANCED",
                    "variant": "POSSIBLE", "path": "UNCLEAR", "model_confidence": "UNVALIDATED",
                    "thesis_control": "UNRESOLVED", "research_conclusion": conclusion,
                    "implied_expectations": {"available": False, "classification": "UNAVAILABLE", "drivers": []},
                },
                "brief": {
                    "price": 40, "bear": 30, "base": 55, "bull": 70, "base_gap_pct": 37.5,
                    "confidence": "MEDIUM", "horizon_years": 5, "target_year": date.today().year + 5, "reasons": [],
                },
                "synthesis": {
                    "why_now": ["Cached why now"], "why_not_yet": ["Cached why not yet"],
                    "what_changes": ["Cached change"], "what_kills": ["Cached kill"],
                    "micro_for": [], "micro_against": [], "macro": [], "invalidation": "", "next": ["Cached next"],
                },
                "triangulation": {"available": False, "reason": "cached", "peers": [], "comparisons": [], "signals": [], "method": "CACHE", "sic": "", "sic_description": ""},
                "management": {"score": 60, "coverage_pct": 50, "components": []},
                "management_accountability": [],
                "management_promises": [],
                "tape": {"months": 12, "market": [], "short_interest": [], "short_volume": [], "positioning": {}, "metrics": {"regime": "MIXED", "confidence": "MEDIUM"}},
                "tape_metrics": {"regime": "MIXED", "confidence": "MEDIUM"},
                "discovery_labels": ["LONG DISLOCATION"],
            },
        ))
        db.session.commit()


def test_020_health_identity_and_calculation_version(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    release_version = Path("VERSION").read_text().strip()
    assert app.config["VERSION"] == release_version
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == release_version
    assert response.get_json()["architecture"] == "web-native"
    assert CALCULATION_VERSION == "0.2.0"
    metrics = financial_metrics({"revenue": 110, "fcf": 12}, {"revenue": 100, "fcf": 10})
    assert metrics["calculation_version"] == "0.2.0"


def test_020_existing_control_identity_survives_bootstrap(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, _ = seed_workspace(app)
    with app.app_context():
        user = db.session.get(User, uid)
        old_hash, old_totp = user.password_hash, user.totp_secret_enc
        from mfapp.schema import bootstrap_schema
        bootstrap_schema(migrate_legacy=True)
        user = db.session.get(User, uid)
        assert user.password_hash == old_hash
        assert user.totp_secret_enc == old_totp
        assert user.role == "CONTROL"


def test_020_canonical_routes_and_runtime_assets(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/company/<ticker>/valuation",
        "/company/<ticker>/financial-flows",
        "/company/<ticker>/validate",
        "/company/<ticker>/validate/run",
        "/company/<ticker>/readiness/<gate_key>",
        "/portfolio/<ticker>",
        "/alerts/email",
        "/alerts/subscription/<int:coverage_id>",
        "/company/<ticker>/alerts/rule/<int:rule_id>",
        "/company/<ticker>/tape/borrow-fee",
        "/discovery/report/pdf",
        "/settings/report-branding",
    }
    assert expected <= routes

    base = Path("mfapp/templates/base.html").read_text()
    assert "css/app.css" in base and "js/app.js" in base
    assert "js/theme.js" not in base
    for legacy in ("v012.css", "v013.css", "v014.css", "v015.css", "v016.css", "v017.css", "v0171.css",
                   "v015.js", "v016.js", "v017.js", "v0171-pre.js", "v0171.js"):
        assert legacy not in base


def test_020_key_control_pages_render_without_dom_reconstruction(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, _ = seed_workspace(app)
    client = app.test_client()
    login_control(client, uid)
    for path in (
        "/",
        "/discovery",
        "/company/EXM/overview",
        "/company/EXM/business",
        "/company/EXM/expectations",
        "/company/EXM/valuation",
        "/company/EXM/financial-flows",
        "/company/EXM/validate",
        "/portfolio",
        "/portfolio/EXM",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.data[:500])


def test_020_decision_engine_does_not_buy_on_price_gap_alone():
    result = build_research_intelligence(
        [{
            "metrics": {
                "revenue_growth_pct": 0.0,
                "receivables_growth_pct": 25.5,
            },
            "fcf": None,
            "net_income": None,
        }],
        {"base": 91.6, "expected_value": 80},
        market_price=40,
        valuation_quality="INTRINSIC",
        data_quality_issues=0,
        readiness={"gates": [], "ready_to_validate": False, "validation": {"state": "NOT RUN"}},
    )
    assert result["base_gap_pct"] > 100
    assert result["score"] < result["buy_threshold"]
    assert result["action"] == "WAIT"
    assert result["stance"] in {"WATCH", "NO EDGE"}
    assert any(row["label"] == "Receivables divergence" for row in result["opposing_evidence"])


def test_020_readiness_is_research_only_and_validation_is_separate(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    _, _, _, coverage_id = seed_workspace(app)
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        readiness = research_readiness(coverage)
        keys = {gate["key"] for gate in readiness["gates"]}
        assert "historical-test" not in keys
        assert "validate" not in keys
        assert "position" not in keys
        assert "risk" not in keys
        assert "validation" in readiness
        assert readiness["validation"]["state"] == "NOT RUN"


def test_020_account_email_is_alert_source_and_locked_rules_are_immutable(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, coverage_id = seed_workspace(app)
    with app.app_context():
        db.session.add(UserPreference(user_id=uid, key="alert_email_017", value={"email": "old-override@example.net"}))
        rule = MonitoringRule(
            coverage_id=coverage_id,
            name="Locked invalidation",
            metric="price",
            operator="<",
            threshold_value=Decimal("30"),
            unit="USD",
            severity="FAIL",
            locked_pre_investment=True,
            created_by=uid,
        )
        db.session.add(rule)
        db.session.commit()
        rule_id = rule.id
        assert account_alert_email(uid) == "control@example.com"

    client = app.test_client()
    login_control(client, uid)
    email = client.post("/alerts/email", json={"email": "attempted-override@example.org"})
    assert email.status_code == 200
    assert email.get_json()["email"] == "control@example.com"
    assert client.patch(f"/company/EXM/alerts/rule/{rule_id}", json={"threshold": 20}).status_code == 409
    assert client.delete(f"/company/EXM/alerts/rule/{rule_id}").status_code == 409


def test_020_semantic_preference_migration_preserves_existing_choices(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, coverage_id = seed_workspace(app)
    with app.app_context():
        db.session.add_all([
            UserPreference(user_id=uid, key="notifications_016", value={"email_enabled": False, "cooldown_hours": 48}),
            UserPreference(user_id=uid, key=f"alert_subscription_017_{coverage_id}", value={"rule_ids": [], "system_alerts": ["filing"], "email_enabled": True}),
        ])
        db.session.commit()
        from mfapp.upgrade_020 import migrate_semantic_preferences
        result = migrate_semantic_preferences()
        assert result["status"] == "applied"
        assert UserPreference.query.filter_by(user_id=uid, key="notifications").first().value["cooldown_hours"] == 48
        assert UserPreference.query.filter_by(user_id=uid, key=f"alert_subscription_{coverage_id}").first().value["system_alerts"] == ["filing"]


def test_020_ui_contract_matches_clean_architecture():
    base = Path("mfapp/templates/base.html").read_text()
    app_js = Path("mfapp/static/js/app.js").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    company = Path("mfapp/templates/company_section.html").read_text()
    process_readiness = Path("mfapp/templates/_process_readiness.html").read_text()
    flows = Path("mfapp/templates/financial_flows.html").read_text()

    assert "MutationObserver" not in app_js
    assert "cloneNode" not in app_js
    assert "aria-expanded" in app_js and "mf-mobile-menu" in app_js and "mf-mobile-backdrop" in app_js
    assert "data-mf-chart=\"valuation\"" in Path("mfapp/templates/valuation.html").read_text()
    assert "#7f8a94" in app_js
    assert "READY TO VALIDATE" in process_readiness
    assert "EXTERNAL TRIANGULATION" in company
    assert "5Y OPERATING PATH" in company
    assert "MACHINE READ" in company
    assert "Our view vs market expectation" not in company
    assert "HOW TO READ IT" not in flows
    assert "Follow the money, then check the bridge" not in flows
    assert "purple" not in css.lower()
    assert "Lose Money Rules" in base


def test_020_discovery_lenses_are_evidence_navigation_not_action():
    labels = classify_coverage(
        {
            "base_gap_pct": 40,
            "bias": "LONG",
            "stance": "WATCH",
            "confidence": "HIGH",
            "negatives": 0,
            "positives": 3,
        },
        {"done": 8, "total": 12},
    )
    assert "QUALITY AT DISCOUNT" in labels
    assert "LONG DISLOCATION" in labels
    assert all(label not in {"BUY", "SELL"} for label in labels)


def test_020_coverage_manage_archives_without_deleting_research(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, coverage_id = seed_workspace(app)
    client = app.test_client(); login_control(client, uid)
    response = client.post("/coverage/EXM/manage", data={"action": "archive"}, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        assert coverage is not None
        assert coverage.status == "ARCHIVED"
        assert coverage.research_state == "ARCHIVED"
        assert coverage.research is not None
        assert coverage.valuation_models


def test_020_private_report_and_publication_boundaries(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, security_id, coverage_id = seed_workspace(app)
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        risk.max_loss_pct = Decimal("4.25")
        risk.max_position_pct = Decimal("12.5")
        risk.entry_conditions = "PRIVATE_ENTRY_CONDITION"
        db.session.add(Position(
            user_id=uid,
            security_id=security_id,
            shares=Decimal("321.5"),
            avg_cost=Decimal("27.75"),
            currency="USD",
            notes="PRIVATE_POSITION_NOTE",
        ))
        coverage.research.thesis = "Public-facing research thesis"
        coverage.research.business = "Business evidence"
        coverage.research.risk_summary = "Research invalidation summary"
        db.session.commit()

        from mfapp.routes import _ctx
        from flask import g
        with app.test_request_context("/company/EXM/overview"):
            g.user = db.session.get(User, uid)
            ctx = _ctx("EXM")
            report = research_report_data(ctx, mode="full")
            report_text = str(report)
            assert "PRIVATE_ENTRY_CONDITION" not in report_text
            assert "PRIVATE_POSITION_NOTE" not in report_text
            assert "321.5" not in report_text
            assert "27.75" not in report_text

        snapshot = create_snapshot(coverage, uid, decision_context={
            "research_conclusion": "LONG WATCH",
            "lenses": [{"label": "VALUE", "state": "ATTRACTIVE"}],
        })
        db.session.add(snapshot); db.session.commit()
        published = publication_payload(snapshot, "INSIDER")
        published_text = str(published)
        assert "PRIVATE_ENTRY_CONDITION" not in published_text
        assert "PRIVATE_POSITION_NOTE" not in published_text
        assert "'position':" not in published_text
        assert "max_loss_pct" not in published_text
        assert "max_position_pct" not in published_text
        assert published["decision"]["research_conclusion"] == "LONG WATCH"

    client = app.test_client(); login_control(client, uid)
    pdf = client.get("/company/EXM/report/pdf?mode=executive")
    full_pdf = client.get("/company/EXM/report/pdf?mode=full")
    docx = client.get("/company/EXM/report/docx?mode=full")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf" and len(pdf.data) > 500
    assert full_pdf.status_code == 200 and full_pdf.mimetype == "application/pdf" and len(full_pdf.data) > 500
    assert docx.status_code == 200 and "openxmlformats" in docx.mimetype and len(docx.data) > 1000


def test_020_friend_cannot_access_control_report_or_portfolio(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    _, _, _, _ = seed_workspace(app)
    with app.app_context():
        friend = User(
            email="friend@example.com", display_name="Friend", role="FRIEND",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        db.session.add(friend); db.session.commit(); friend_id = friend.id
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = friend_id
        session["view_as"] = "FRIEND"
    assert client.get("/portfolio").status_code == 403
    assert client.get("/portfolio/EXM").status_code == 403
    assert client.get("/company/EXM/report/pdf").status_code == 403


def test_020_overview_contains_local_depth_without_separate_decide_page():
    company = Path("mfapp/templates/company_section.html").read_text()
    process_readiness = Path("mfapp/templates/_process_readiness.html").read_text()
    routes = Path("mfapp/routes.py").read_text()
    assert "DECISION MAP" in company
    assert "WHY NOW" in company
    assert "WHY NOT YET" in company
    assert "WHAT CHANGES THE DECISION" in company
    assert "WHAT KILLS THE THESIS" in company
    assert "Process readiness" in process_readiness
    assert '("overview", "Overview")' in routes
    assert "decide" not in {key for key in ("decide",) if f'("{key}",' in routes}


def test_020_price_implied_expectations_has_three_transparent_drivers(monkeypatch):
    import mfapp.expectations_engine as engine
    monkeypatch.setattr(engine, "current_row", lambda company_id: {
        "revenue": 1_000_000_000,
        "diluted_shares": 100_000_000,
        "metrics": {"revenue_growth_pct": 5.0, "net_margin_pct": 10.0},
    })
    model = SimpleNamespace(scenarios=[
        SimpleNamespace(name="BASE", inputs={"growth": 0.05, "net_margin": 0.10, "pe": 20.0}),
    ])
    base_price = 1_000_000_000 * (1.05 ** 5) * .10 * 20 / 100_000_000
    result = engine.price_implied_expectations(1, model, base_price)
    assert result["available"] is True
    assert result["classification"] == "BALANCED"
    assert {row["key"] for row in result["drivers"]} == {"revenue_cagr", "net_margin_y5", "exit_pe_y5"}
    for row in result["drivers"]:
        assert row["market_implied"] is not None
        assert row["base"] is not None
        assert row["read"] == "NEAR BASE"


def test_020_mature_decision_lenses_restore_local_research_logic(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, coverage_id = seed_workspace(app)
    import mfapp.decision_lenses as lenses_mod
    monkeypatch.setattr(lenses_mod, "price_implied_expectations", lambda *args, **kwargs: {
        "available": True, "classification": "BALANCED", "drivers": [], "demand_score": 0,
    })
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        research = coverage.research
        risk = coverage.risk_plan
        research.variant_market = "Market expects stagnation."
        research.variant_us = "We expect an operating recovery."
        research.variant_evidence = "Margins and cash conversion are improving."
        research.business = "Durable installed base and repeat service demand."
        risk.thesis_invalidation = "Operating margin below 5% for two filings."
        risk.invalidation_locked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.add(Catalyst(coverage_id=coverage.id, title="Product reset", direction="POSITIVE", status="OPEN"))
        db.session.add(MonitoringRule(
            coverage_id=coverage.id, name="Margin invalidation", metric="operating_margin_pct",
            operator="<", threshold_value=Decimal("5"), unit="%", severity="FAIL",
            locked_pre_investment=True, created_by=uid,
        ))
        db.session.commit()
        result = lenses_mod.build_decision_lenses(
            coverage=coverage,
            company=coverage.security.company,
            research=research,
            risk=risk,
            model=coverage.valuation_models[0],
            market=coverage.security.market_snapshots[-1],
            valuation={"base": 60, "current_price": 40},
            intelligence={"base_gap_pct": 50, "positives": 4, "negatives": 1, "confidence": "HIGH", "warnings": []},
            readiness={"ready_to_validate": True, "gates": [{"key": "business", "evidence_ready": True}], "validation": {"state": "VALIDATED"}},
            management={"score": 75},
            tape={"metrics": {"regime": "SUPPORTIVE"}},
        )
        assert [row["label"] for row in result["rows"]] == [
            "BUSINESS", "VALUE", "EXPECTATIONS", "VARIANT", "PATH", "MODEL CONFIDENCE", "THESIS CONTROL"
        ]
        assert result["business"] == "GOOD"
        assert result["value"] == "ATTRACTIVE"
        assert result["expectations"] == "BALANCED"
        assert result["variant"] == "POSITIVE EDGE"
        assert result["path"] == "SUPPORTIVE"
        assert result["model_confidence"] == "STRONG"
        assert result["thesis_control"] == "CONTROLLED"
        assert result["research_conclusion"] == "LONG READY"


def test_020_automatic_triangulation_uses_exact_sic_and_peer_medians(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, _, _ = seed_workspace(app)
    import mfapp.triangulation_engine as tri
    with app.app_context():
        p1 = Company(legal_name="Peer One", display_name="Peer One", industry="Machinery")
        p2 = Company(legal_name="Peer Two", display_name="Peer Two", industry="Machinery")
        db.session.add_all([p1, p2]); db.session.flush()
        db.session.add_all([
            Security(company_id=p1.id, ticker="P1", exchange="NYSE", active=True, is_primary=True, validation_source="TEST"),
            Security(company_id=p2.id, ticker="P2", exchange="NYSE", active=True, is_primary=True, validation_source="TEST"),
        ])
        db.session.commit()
        meta = {
            company_id: {"sic": "3561", "sic_description": "Machinery"},
            p1.id: {"sic": "3561", "sic_description": "Machinery"},
            p2.id: {"sic": "3561", "sic_description": "Machinery"},
        }
        metrics = {
            company_id: {"company_id": company_id, "ticker": "EXM", "revenue_growth_pct": 15, "operating_margin_pct": 18, "fcf_margin_pct": 12,
                         "inventory_to_revenue_pct": 10, "receivables_to_revenue_pct": 11, "asset_turnover": 1.5, "roic_pct": 20,
                         "share_change_pct": -2, "pe": 14, "ev_sales": 1.7, "fcf_yield_pct": 8, "market_cap": 10_000},
            p1.id: {"company_id": p1.id, "ticker": "P1", "revenue_growth_pct": 5, "operating_margin_pct": 10, "fcf_margin_pct": 6,
                    "inventory_to_revenue_pct": 14, "receivables_to_revenue_pct": 15, "asset_turnover": 1.0, "roic_pct": 10,
                    "share_change_pct": 2, "pe": 20, "ev_sales": 2.5, "fcf_yield_pct": 4, "market_cap": 9_000},
            p2.id: {"company_id": p2.id, "ticker": "P2", "revenue_growth_pct": 7, "operating_margin_pct": 12, "fcf_margin_pct": 7,
                    "inventory_to_revenue_pct": 13, "receivables_to_revenue_pct": 14, "asset_turnover": 1.1, "roic_pct": 12,
                    "share_change_pct": 1, "pe": 22, "ev_sales": 2.7, "fcf_yield_pct": 5, "market_cap": 11_000},
        }
        monkeypatch.setattr(tri, "_sec_meta", lambda cid: meta.get(cid, {}))
        monkeypatch.setattr(tri, "_metric_row", lambda company, user_id=None: metrics.get(company.id))
        result = tri.automatic_triangulation(company_id, uid)
        assert result["available"] is True
        assert result["method"] == "EXACT SIC"
        assert len(result["peers"]) == 2
        assert any(row["label"] == "Revenue growth" and row["state"] == "STRENGTH" for row in result["signals"])
        assert {"ROIC", "P/E", "EV / Sales"} <= {row["label"] for row in result["comparisons"]}
        assert any(row["state"] == "RELATIVE VALUE + QUALITY" for row in result["signals"])


def test_020_management_promises_parse_and_score_met_miss(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    _, company_id, _, _ = seed_workspace(app)
    import mfapp.management_promises as mp
    sentence = "Management expects FY2027 revenue growth of 8% to 10% as capacity normalizes."
    parsed = mp.extract_promises(sentence)
    assert len(parsed) == 1
    assert parsed[0]["metric"] == "revenue_growth_pct"
    assert parsed[0]["target_year"] == 2027
    assert parsed[0]["low"] == 8
    assert parsed[0]["high"] == 10
    with app.app_context():
        mp.store_promises(company_id, parsed)
        db.session.add(Event(
            company_id=company_id,
            event_type="MANAGEMENT_ACTUAL_ORIGINAL",
            title="revenue_growth_pct original actual FY2027",
            event_date=datetime(2028, 2, 1),
            payload={
                "metric": "revenue_growth_pct",
                "fiscal_year": 2027,
                "value": 9.0,
                "period_type": "FY",
                "period_end": "2027-12-31",
                "filed_at": "2028-02-01",
                "source_accession": "original-2027",
                "source_provider": "SEC_COMPANYFACTS",
                "point_in_time_original": True,
                "actual_version": "1",
            },
        ))
        db.session.commit()
        rows = mp.evaluate_promises(company_id)
        assert rows[0]["actual"] == 9.0
        assert rows[0]["status"] == "MET"
        assert rows[0]["actual_provenance"]["point_in_time_original"] is True


def test_020_tape_reads_options_borrow_turnover_and_resilience(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    _, company_id, security_id, _ = seed_workspace(app)
    from mfapp.decision_support import tape_series
    with app.app_context():
        start = date.today() - timedelta(days=80)
        for i in range(70):
            db.session.add(HistoricalPrice(
                security_id=security_id,
                trade_date=start + timedelta(days=i),
                close_raw=Decimal(str(50 + i * .2)),
                close_split_adjusted=Decimal(str(50 + i * .2)),
                volume=Decimal(str(1_000_000 + i * 10_000)),
                provider="TEST",
            ))
        db.session.add(Event(
            company_id=company_id,
            event_type="ALPACA_POSITIONING",
            title="positioning",
            event_date=datetime.now(timezone.utc).replace(tzinfo=None),
            payload={
                "borrow": {"borrow_status": "easy_to_borrow", "shortable": True},
                "options": {"put_open_interest": 7000, "call_open_interest": 10000, "put_call_oi": .70},
                "locate": {},
            },
        ))
        db.session.add(Event(
            company_id=company_id,
            event_type="BORROW_FEE_OBSERVATION",
            title="borrow fee",
            event_date=datetime.now(timezone.utc).replace(tzinfo=None),
            payload={"annualized_fee_pct": 3.25, "source": "TEST BROKER"},
        ))
        db.session.commit()
        security = db.session.get(Security, security_id)
        tape = tape_series(security, 6)
        metrics = tape["metrics"]
        assert metrics["put_call_oi"] == pytest.approx(.70)
        assert metrics["borrow_status"] == "easy_to_borrow"
        assert metrics["borrow_fee_pct"] == pytest.approx(3.25)
        assert metrics["borrow_fee_source"] == "TEST BROKER"
        assert metrics["turnover_ratio_20d"] is not None
        assert metrics["price_resilience"] is not None
        assert metrics["rank_score"] is not None


def test_020_market_wide_discovery_screen_is_only_a_funnel_for_forensic_value():
    market = Path("mfapp/market_discovery.py").read_text()
    universe = Path("mfapp/discovery_universe.py").read_text()
    forensic = Path("mfapp/discovery_forensics.py").read_text()
    assert "stage0_universe" in market
    assert "stage1_screen" in market
    assert "FULL_MARKET_MISPRICING_DISCOVERY_V5" in market
    assert "FORENSIC_ENRICH_LIMIT = 52" in forensic
    assert "FORENSIC_WATCH_EDGE_PCT = 12.0" in forensic
    assert "discovery_opportunity" in forensic
    assert "allow_reference_fallback=False" in forensic
    assert "from .secdata" not in universe and "api/xbrl" not in universe.lower()
    assert "fill_quota" in market
    assert "_market_mispricing_hypothesis" in market
    assert '"stage15_liquidity_tiebreak_only": True' in market
    assert '"stage15_price_move_ranked": False' in market


def test_020_report_branding_is_persisted_and_rejects_non_https_logo(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, _ = seed_workspace(app)
    with app.app_context():
        value = set_report_branding(
            uid, title="Market Forensics", prepared_by="Lose Money Rules",
            footer="Lose Money Rules", logo_url="https://example.com/logo.png",
        )
        assert value["prepared_by"] == "Lose Money Rules"
        assert get_report_branding(uid)["logo_url"] == "https://example.com/logo.png"
        with pytest.raises(ValueError):
            set_report_branding(uid, title="MF", prepared_by="", footer="LMR", logo_url="http://127.0.0.1/logo.png")


def test_020_complete_parity_surfaces_and_canonical_conclusion_contract():
    company = Path("mfapp/templates/company_section.html").read_text()
    base = Path("mfapp/templates/base.html").read_text()
    dashboard = Path("mfapp/templates/dashboard.html").read_text()
    portfolio = Path("mfapp/templates/portfolio_security.html").read_text()
    discovery = Path("mfapp/templates/discovery.html").read_text()
    settings = Path("mfapp/templates/settings.html").read_text()

    for label in ("BUSINESS", "VALUE", "EXPECTATIONS", "VARIANT", "PATH", "MODEL CONFIDENCE", "THESIS CONTROL"):
        assert label in Path("mfapp/decision_lenses.py").read_text()
    assert "PRICE-IMPLIED EXPECTATIONS" in company
    assert "AUTOMATIC TRIANGULATION" in company
    assert "PROMISES VS ACTUALS" in company
    assert "Put / Call OI" in company
    assert "Price resilience" in company
    assert "BROAD UNIVERSE DISCOVERY" in discovery
    assert "REPORT BRANDING" in settings
    assert "{{ intelligence.action }}" not in base
    assert "{{ row.intelligence.action }}" not in dashboard
    assert "{{ intelligence.action }}" not in portfolio
    assert "decision_lenses.research_conclusion" in base
    assert "row.decision_lenses.research_conclusion" in dashboard
    assert "brief.action" not in company
    assert "Research action" not in company
    assert "Expected Value" in company
    assert "EXCEPTIONS FIRST" in company
    assert "Freshness" in dashboard
    assert "Next action" in dashboard
    providers = Path("mfapp/data_providers.py").read_text()
    assert "return [_alpaca(ticker, user_id), _tiingo(ticker, user_id), _alpha_vantage(ticker, user_id), _public_chart(ticker)]" in providers
    assert "last-good cache" in providers


def test_020_discovery_landscape_pdf_and_full_refresh_contract(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, _, _, _ = seed_workspace(app)
    scan = {
        "universe_source": "TEST",
        "candidates": [
            {"ticker": "AAA", "scan_score": 82.5, "move_pct": 12.0, "activity_rank": 1, "lenses": ["HIGH ACTIVITY", "PRICE DISLOCATION"]},
            {"ticker": "BBB", "scan_score": 75.0, "move_pct": -10.0, "activity_rank": 2, "lenses": ["POTENTIAL SHORT"]},
        ],
    }
    pdf = render_discovery_pdf(scan, {"title": "Market Forensics", "footer": "Lose Money Rules", "logo_url": ""})
    assert len(pdf.getvalue()) > 700
    jobs = Path("mfapp/jobs.py").read_text()
    assert '("POSITIONING_REFRESH", 75)' in jobs
    assert '("MANAGEMENT_SCAN", 80)' in jobs
    assert "MANAGEMENT_GUIDANCE_SCAN" in jobs


def test_020_borrow_fee_route_stores_sourced_context(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, _, _ = seed_workspace(app)
    client = app.test_client(); login_control(client, uid)
    response = client.post("/company/EXM/tape/borrow-fee", data={
        "annualized_fee_pct": "4.75",
        "source": "Prime broker",
        "note": "Observed at market open",
    }, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        event = Event.query.filter_by(company_id=company_id, event_type="BORROW_FEE_OBSERVATION").order_by(Event.id.desc()).first()
        assert event is not None
        assert float(event.payload["annualized_fee_pct"]) == pytest.approx(4.75)
        assert event.payload["source"] == "Prime broker"


def test_020_reports_expose_full_pdf_and_discovery_report_actions():
    company = Path("mfapp/templates/company_section.html").read_text()
    discovery = Path("mfapp/templates/discovery.html").read_text()
    assert "Full PDF" in company
    assert "Full Word" in company
    assert "Landscape PDF" in discovery


def test_020_cached_navigation_never_runs_heavy_research_engines(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, _, coverage_id = seed_workspace(app)
    seed_fast_cache(app, coverage_id, company_id)

    import mfapp.routes as routes
    def bomb(*args, **kwargs):
        raise AssertionError("heavy analytical engine executed during normal GET navigation")

    for name in (
        "_research_readiness", "_intelligence", "management_engine", "tape_series",
        "automatic_triangulation", "build_synthesis", "price_implied_expectations", "valuation_result",
    ):
        monkeypatch.setattr(routes, name, bomb)

    def network_bomb(*args, **kwargs):
        raise AssertionError("normal GET navigation must not call external providers")

    monkeypatch.setattr("requests.sessions.Session.request", network_bomb)

    client = app.test_client()
    login_control(client, uid)
    for path in (
        "/", "/discovery", "/company/EXM/overview", "/company/EXM/business",
        "/company/EXM/fundamentals", "/company/EXM/expectations",
        "/company/EXM/management", "/company/EXM/tape",
        "/company/EXM/financial-flows", "/company/EXM/validate",
        "/portfolio", "/settings",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.data[:400])


def test_020_dashboard_query_count_is_bounded_with_cache(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, _, coverage_id = seed_workspace(app)
    seed_fast_cache(app, coverage_id, company_id)

    from sqlalchemy import event as sa_event
    count = {"n": 0}
    with app.app_context():
        engine = db.engine

    def before_cursor(*args, **kwargs):
        count["n"] += 1

    sa_event.listen(engine, "before_cursor_execute", before_cursor)
    try:
        client = app.test_client()
        login_control(client, uid)
        response = client.get("/")
        assert response.status_code == 200
        assert count["n"] <= 18, count
    finally:
        sa_event.remove(engine, "before_cursor_execute", before_cursor)


def test_020_browser_observes_jobs_and_kicks_detached_executor_without_blocking_page_work():
    js = Path("mfapp/static/js/app.js").read_text()
    routes = Path("mfapp/routes_publish.py").read_text()
    manage = Path("manage.py").read_text()
    assert "Page requests stay fast" in js
    assert "last_finished_id" in js
    assert "window.location.reload()" in js
    assert "fetch('/jobs/pump'" in js
    assert "subprocess.Popen" in routes
    assert "start_new_session=True" in routes
    assert '"run-jobs"' in routes and '"--user-id"' in routes
    assert "run_jobs(limit=1" not in routes
    assert "job-executor.lock" in manage
    assert "LOCK_EX | fcntl.LOCK_NB" in manage


def test_020_reporting_dependencies_are_optional_at_startup():
    reporting = Path("mfapp/reporting.py").read_text()
    requirements = Path("requirements.txt").read_text()
    optional = Path("requirements-reporting.txt").read_text()
    assert "from docx import Document" not in reporting.split("_load_report_libs", 1)[0]
    assert "from reportlab" not in reporting.split("_load_report_libs", 1)[0]
    assert "-r requirements-reporting.txt" in requirements
    assert "python-docx" in optional and "reportlab" in optional


def test_020_recalculate_job_builds_research_cache_end_to_end(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_workspace(app)
    with app.app_context():
        from mfapp.jobs import enqueue_job, run_jobs
        from mfapp.research_cache import latest_research_cache
        job = enqueue_job(
            "RECALCULATE",
            user_id=uid,
            company_id=company_id,
            security_id=security_id,
            payload={"coverage_id": coverage_id},
            priority=10,
        )
        result = run_jobs(limit=1, user_id=uid)
        assert result and result[0]["job_id"] == job.id
        assert result[0]["status"] == "DONE", result
        cache = latest_research_cache(coverage_id, company_id)
        assert cache is not None
        assert "valuation" in cache
        assert "readiness" in cache
        assert "decision_lenses" in cache
        assert "synthesis" in cache
        assert "tape" in cache
        assert "triangulation" in cache


def test_020_portfolio_get_does_not_compute_historical_correlations(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_workspace(app)
    seed_fast_cache(app, coverage_id, company_id)
    with app.app_context():
        db.session.add(Position(
            user_id=uid, security_id=security_id, shares=Decimal("10"),
            avg_cost=Decimal("35"), currency="USD", notes=""
        ))
        db.session.commit()

    import mfapp.portfolio_engine as pe
    monkeypatch.setattr(pe, "_return_series", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("historical correlation work executed during Portfolio GET")
    ))
    client = app.test_client()
    login_control(client, uid)
    response = client.get("/portfolio")
    assert response.status_code == 200


def test_020_mobile_navigation_has_single_working_controller_and_css_contract():
    js = Path("mfapp/static/js/app.js").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    base = Path("mfapp/templates/base.html").read_text()
    assert js.count("const menuButton = document.getElementById('mf-mobile-menu')") == 1
    assert "menuButton?.addEventListener('click'" in js
    assert "backdrop?.addEventListener('click', closeMobile)" in js
    assert "event.key === 'Escape'" in js
    assert "window.innerWidth > 900" in js
    assert 'id="mf-mobile-menu"' in base
    assert 'id="mf-mobile-backdrop"' in base
    assert 'id="mf-primary-nav"' in base
    assert "body.nav-open .navrail" in css
    assert "body.nav-open .mobile-backdrop" in css
    assert "z-index:90" in css


def test_020_current_price_refresh_contract_is_five_minutes():
    workspace = Path("mfapp/workspace_routes.py").read_text()
    js = Path("mfapp/static/js/app.js").read_text()
    assert "timedelta(minutes=5)" in workspace
    assert '"MARKET_REFRESH"' in workspace
    assert "5 * 60 * 1000" in js
    assert "/price/refresh" in js


def test_020_job_pump_spawns_detached_executor_without_running_inline(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_workspace(app)
    with app.app_context():
        from mfapp.jobs import enqueue_job
        job = enqueue_job(
            "RECALCULATE",
            user_id=uid,
            company_id=company_id,
            security_id=security_id,
            payload={"coverage_id": coverage_id},
            priority=10,
        )
        job_id = job.id

    import mfapp.routes_publish as publish_routes
    spawned = {}
    def fake_spawn(user_id):
        spawned["user_id"] = user_id
        return 43210
    monkeypatch.setattr(publish_routes, "_spawn_job_runner", fake_spawn)

    client = app.test_client()
    login_control(client, uid)
    response = client.post("/jobs/pump", headers={"Accept": "application/json"})
    assert response.status_code == 202
    payload = response.get_json()
    assert payload["spawned"] is True
    assert payload["pid"] == 43210
    assert spawned["user_id"] == uid

    with app.app_context():
        queued = db.session.get(Job, job_id)
        assert queued.status == "QUEUED"
        assert queued.started_at is None


def test_020_discovery_scan_is_two_stage_and_hard_bounded():
    discovery = Path("mfapp/market_discovery.py").read_text()
    forensic = Path("mfapp/discovery_forensics.py").read_text()
    jobs = Path("mfapp/jobs.py").read_text()
    assert "_coverage_context_map" in discovery and "latest_cache_map" in discovery
    assert "enrich_forensic_candidates" in discovery
    assert "default_cases" in forensic and "evaluate" in forensic
    assert "FORENSIC_ENRICH_PER_SIDE" in forensic
    assert "return 300 if str(job_type).upper() == \"DISCOVERY_SCAN\"" in jobs
    assert "_execute_with_deadline(job)" in jobs


def test_current_state_matches_version():
    version = Path("VERSION").read_text().strip()
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert f"**State-Version: {version}**" in state
    assert "READ THIS FIRST" in state
    assert "single source of truth" in state.lower()
