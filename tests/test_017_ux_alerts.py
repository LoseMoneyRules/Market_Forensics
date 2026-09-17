from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, MonitoringRule, Publication, Security, Snapshot
from mfapp.extensions import db
from mfapp.models import User
from mfapp.monitoring_017 import (
    alert_subscription,
    save_alert_catalog,
    save_alert_subscription,
    subscription_catalog_for_user,
)
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MF_SMTP_HOST", raising=False)
    monkeypatch.delenv("MF_SMTP_FROM", raising=False)
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "017-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '017.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_workspace(app):
    with app.app_context():
        db.create_all()
        control = User(
            email="control@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        friend = User(
            email="friend@example.com", display_name="Friend", role="FRIEND",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co", sector="Industrials", industry="Machinery")
        db.session.add_all([control, friend, company]); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", active=True, is_primary=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=control.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, control.id)
        snapshot = Snapshot(coverage_id=coverage.id, version=1, snapshot_type="DECISION", payload={}, calculation_version="0.1.6", created_by=control.id)
        db.session.add(snapshot); db.session.flush()
        db.session.add(Publication(
            coverage_id=coverage.id, snapshot_id=snapshot.id, version=1, visibility="FRIEND",
            title="EXM Research", slug="exm-research", payload={"views": {"FRIEND": {}, "INSIDER": {}}},
            published_by=control.id,
        ))
        private_rule = MonitoringRule(
            coverage_id=coverage.id, name="Private invalidation", metric="price", operator="<",
            threshold_value=80, unit="USD", severity="FAIL", locked_pre_investment=True, created_by=control.id,
        )
        public_rule = MonitoringRule(
            coverage_id=coverage.id, name="Revenue warning", metric="revenue_growth_pct", operator="<",
            threshold_value=0, unit="%", severity="WATCH", created_by=control.id,
        )
        db.session.add_all([private_rule, public_rule]); db.session.commit()
        return control.id, friend.id, coverage.id, private_rule.id, public_rule.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_017_control_private_subscription_is_independent_from_member_catalog(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    control_id, friend_id, coverage_id, private_rule_id, public_rule_id = seed_workspace(app)
    with app.app_context():
        catalog = save_alert_catalog(
            coverage_id,
            rule_ids=[public_rule_id],
            system_alerts=["filing"],
            actor_user_id=control_id,
        )
        assert catalog["rule_ids"] == [public_rule_id]

        control_sub = save_alert_subscription(
            control_id, coverage_id,
            rule_ids=[private_rule_id, public_rule_id], system_alerts=["filing", "valuation"],
            email_enabled=True, in_app_enabled=True, actor_user_id=control_id,
        )
        assert set(control_sub["rule_ids"]) == {private_rule_id, public_rule_id}
        assert set(control_sub["system_alerts"]) == {"filing", "valuation"}

        friend_sub = save_alert_subscription(
            friend_id, coverage_id,
            rule_ids=[private_rule_id, public_rule_id], system_alerts=["filing", "valuation"],
            email_enabled=True, in_app_enabled=True, actor_user_id=friend_id,
        )
        assert friend_sub["rule_ids"] == [public_rule_id]
        assert friend_sub["system_alerts"] == ["filing"]
        assert alert_subscription(friend_id, coverage_id)["rule_ids"] == [public_rule_id]

        visible = subscription_catalog_for_user(friend_id, coverage_id)
        assert [row["id"] for row in visible["rules"]] == [public_rule_id]
        assert [row["key"] for row in visible["system_alerts"]] == ["filing"]


def test_017_locked_alert_rule_cannot_be_edited_or_removed(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    control_id, _, _, private_rule_id, public_rule_id = seed_workspace(app)
    client = app.test_client(); login_control(client, control_id)

    locked_edit = client.patch(f"/company/EXM/alerts/017/rule/{private_rule_id}", json={"threshold": 70})
    assert locked_edit.status_code == 409
    locked_delete = client.delete(f"/company/EXM/alerts/017/rule/{private_rule_id}")
    assert locked_delete.status_code == 409

    edited = client.patch(f"/company/EXM/alerts/017/rule/{public_rule_id}", json={
        "name": "Revenue deterioration", "metric": "revenue_growth_pct", "operator": "<",
        "threshold": -2.5, "unit": "%", "severity": "FAIL",
    })
    assert edited.status_code == 200
    assert edited.get_json()["rule"]["name"] == "Revenue deterioration"

    removed = client.delete(f"/company/EXM/alerts/017/rule/{public_rule_id}")
    assert removed.status_code == 200
    with app.app_context():
        assert db.session.get(MonitoringRule, public_rule_id).is_active is False
        assert db.session.get(MonitoringRule, private_rule_id).is_active is True


def test_017_release_routes_and_surfaces_are_registered(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert app.config["VERSION"] == "0.1.7"
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/company/<ticker>/surface/017/<section>" in routes
    assert "/alerts/017/subscription/<int:coverage_id>" in routes
    assert "/company/<ticker>/alerts/017/catalog" in routes
    assert "/company/<ticker>/alerts/017/rule/<int:rule_id>" in routes
    assert "/company/<ticker>/monitoring/evaluate-017" in routes


def test_017_ui_contract_matches_refactor_specification():
    base = Path("mfapp/templates/base.html").read_text()
    css = Path("mfapp/static/css/v017.css").read_text()
    js = Path("mfapp/static/js/v017.js").read_text()
    routes = Path("mfapp/routes_017.py").read_text()
    published = Path("mfapp/templates/published.html").read_text()
    pytest_ini = Path("pytest.ini").read_text()

    assert "v017.css" in base and "v017.js" in base
    assert "--mf-body-size:14px" in css
    assert ".button.primary" in css and "font-weight:400!important" in css
    assert ".company-tabs a" in css and ".navitem" in css
    assert "Evidence path · MICRO / MACRO / for / against / invalidation" in js
    assert "0.1.7 Evidence path" not in js
    assert "movePublicationToReadiness" in js
    assert "Strategic divergences & macro context" in js
    assert "evidence review/approval still required" not in js
    assert "Price vs reported Short Interest" in js
    assert "Daily short activity" in js
    assert "20-day average" in js
    assert "data-theme" in js and "chartRenders" in js
    assert "What is bullish, bearish or unchanged" not in js
    assert "Evidence improves the long/bull case" not in js
    assert "Members can choose" in js and "Notify me" in js
    assert "outstanding_gates" not in routes and "outstanding_count" not in routes
    assert 'data-mf-alert-coverage="{{ publication.coverage_id }}"' in published
    assert "Immutable version" not in published
    assert "test_017_*.py" in pytest_ini
    assert "purple" not in css.lower()
