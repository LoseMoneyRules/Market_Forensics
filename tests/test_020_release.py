from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.alert_engine import alert_email as account_alert_email
from mfapp.calculations import CALCULATION_VERSION, financial_metrics
from mfapp.core_models import Company, Coverage, MarketSnapshot, MonitoringRule, Security
from mfapp.decision_engine import build_research_intelligence
from mfapp.discovery_engine import classify_coverage
from mfapp.extensions import db
from mfapp.models import User, UserPreference
from mfapp.readiness import research_readiness
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


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


def test_020_health_identity_and_calculation_version(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert app.config["VERSION"] == "0.2.0"
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == "0.2.0"
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
    }
    assert expected <= routes

    base = Path("mfapp/templates/base.html").read_text()
    assert "css/app.css" in base and "js/app.js" in base and "js/theme.js" in base
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
    flows = Path("mfapp/templates/financial_flows.html").read_text()

    assert "MutationObserver" not in app_js
    assert "cloneNode" not in app_js
    assert "aria-expanded" in app_js and "mf-mobile-menu" in app_js and "mf-mobile-backdrop" in app_js
    assert "data-mf-chart=\"valuation\"" in Path("mfapp/templates/valuation.html").read_text()
    assert "#7f8a94" in app_js
    assert "READY TO VALIDATE" in company
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
