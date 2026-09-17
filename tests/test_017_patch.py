from pathlib import Path

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User, UserPreference


def test_017_patch_ui_contract():
    base = Path("mfapp/templates/base.html").read_text()
    css = Path("mfapp/static/css/v0171.css").read_text()
    pre = Path("mfapp/static/js/v0171-pre.js").read_text()
    js = Path("mfapp/static/js/v0171.js").read_text()

    assert "v0171.css" in base
    assert "v0171-pre.js" in base and "v0171.js" in base
    assert base.index("v0171-pre.js") < base.index("v016.js") < base.index("v017.js") < base.index("v0171.js")
    assert "number-format-form" not in base
    assert "section is defined and section == 'overview'" in base
    assert "__mf0171OriginalResearchStrip" in pre

    assert "surface/017/overview" in js
    assert "business-evidence-path-0171" in js
    assert "Our view vs market expectation" in js
    assert "--mf-chart-market" in js and "col.market" in js
    assert "localizeUtcTimes" in js
    assert "Uses the email registered on this account" in js
    assert "mf-mobile-menu" in js and "cloneNode" in js
    assert "follow the money, then check the bridge" in js.lower()
    assert "--mf-chart-market" in css
    assert "purple" not in css.lower()


def test_017_patch_alert_email_is_registered_account_email(tmp_path, monkeypatch):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "017-patch-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '017-patch.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })
    with app.app_context():
        db.create_all()
        user = User(
            email="registered@example.com",
            display_name="Registered",
            role="CONTROL",
            password_hash="not-used-in-this-test",
            totp_secret_enc="not-used-in-this-test",
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(UserPreference(user_id=user.id, key="alert_email_017", value={"email": "old-override@example.net"}))
        db.session.commit()
        user_id = user.id

        from mfapp import monitoring_017
        assert monitoring_017.alert_email(user_id) == "registered@example.com"

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"
    response = client.post("/alerts/017/email", json={"email": "attempted-override@example.org"})
    assert response.status_code == 200
    assert response.get_json()["email"] == "registered@example.com"

    with app.app_context():
        from mfapp import monitoring_017
        assert monitoring_017.alert_email(user_id) == "registered@example.com"
