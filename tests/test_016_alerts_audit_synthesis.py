from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Alert, Company, Coverage, MarketSnapshot, MonitoringRule, Security
from mfapp.extensions import db
from mfapp.flows_016 import build_income_statement_flow
from mfapp.models import AuditEvent, User
from mfapp.monitoring_016 import evaluate_coverage, save_notification_preferences
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MF_SMTP_HOST", raising=False)
    monkeypatch.delenv("MF_SMTP_FROM", raising=False)
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "016-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '016.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control_workspace(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co", sector="Industrials", industry="Machinery")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", active=True, is_primary=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        ids = (user.id, company.id, security.id, coverage.id); db.session.commit()
        return ids


def test_016_income_flow_preserves_revenue_to_net_when_gross_profit_is_missing():
    flow = build_income_statement_flow({
        "period_label": "FY2025",
        "revenue": 100,
        "gross_profit": None,
        "cogs": None,
        "operating_expenses": None,
        "operating_income": 20,
        "pretax_income": 18,
        "income_tax": 3,
        "net_income": 15,
    })
    assert flow["calculation_version"] == "0.1.6"
    assert flow["statement_chain"] == ["Revenue", "Operating Income", "Pre-Tax Income", "Net Income"]
    values = {row["label"]: row["value"] for row in flow["nodes"]}
    assert values["Revenue"] == 100
    assert values["Operating Income"] == 20
    assert values["Net Income"] == 15
    assert any(edge["target"] == "Operating Costs / Expenses" for edge in flow["edges"])


def test_016_monitoring_creates_one_alert_then_dedupes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch); user_id, _, security_id, coverage_id = seed_control_workspace(app)
    with app.app_context():
        db.session.add(MarketSnapshot(
            security_id=security_id, provider="TEST", price=Decimal("120"), currency="USD",
            as_of=datetime.now(timezone.utc).replace(tzinfo=None), quality="OBSERVED", payload={},
        ))
        rule = MonitoringRule(
            coverage_id=coverage_id, name="Price invalidation", metric="price", operator=">",
            threshold_value=Decimal("100"), unit="USD", severity="FAIL", locked_pre_investment=True,
            created_by=user_id,
        )
        db.session.add(rule); db.session.commit()
        save_notification_preferences(user_id, {
            "email_enabled": False, "in_app_enabled": True, "cooldown_hours": 24,
            "filing_lead_days": 7, "minimum_severity": "INFO",
        })
        first = evaluate_coverage(coverage_id, user_id)
        second = evaluate_coverage(coverage_id, user_id)
        assert first["created_alerts"] == 1
        assert second["created_alerts"] == 0
        assert Alert.query.filter_by(coverage_id=coverage_id).count() == 1
        alert = Alert.query.filter_by(coverage_id=coverage_id).first()
        assert alert.rule_id == rule.id
        assert alert.severity == "FAIL"
        assert AuditEvent.query.filter_by(action="monitoring.trigger", object_id=str(rule.id)).count() == 1


def test_016_locked_rule_requires_numeric_threshold_in_route(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch); user_id, _, _, _ = seed_control_workspace(app)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"
    response = client.post("/company/EXM/monitoring", data={
        "name": "Locked narrative only", "metric": "revenue_growth_pct", "operator": "NOTE",
        "threshold_text": "bad", "locked_pre_investment": "1",
    }, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        assert MonitoringRule.query.filter_by(name="Locked narrative only").count() == 0


def test_016_release_assets_and_routes_are_registered(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert app.config["VERSION"] == "0.1.6"
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/company/<ticker>/surface/016/<section>" in rules
    assert "/company/<ticker>/monitoring/evaluate-016" in rules
    assert "/settings/notifications/016" in rules
    assert "/company/<ticker>/financial-flows/recalculate-016" in rules

    base = Path("mfapp/templates/base.html").read_text()
    css = Path("mfapp/static/css/v016.css").read_text()
    js = Path("mfapp/static/js/v016.js").read_text()
    flows = Path("mfapp/static/js/flows.js").read_text()
    manage = Path("manage.py").read_text()
    assert "v016.css" in base and "v016.js" in base
    assert ".decision-brief{display:none!important}" in css
    assert "PRICE" in js and "FAIR VALUE" in js and "WHY" in js and "WHEN" in js
    assert "Price vs FINRA short-volume share" in js
    assert "Zero baseline" in js
    assert "state-bullish" in css and "state-bearish" in css
    assert "flow-chain-016" in flows and "meta.value" in flows
    assert "_run_monitoring_for_controls" in manage
    assert "purple" not in css.lower()
