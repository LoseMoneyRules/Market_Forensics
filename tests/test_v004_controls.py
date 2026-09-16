import os
from types import SimpleNamespace

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db as web_db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def make_app(tmp_path):
    os.environ["MF_V312_APP_DIR"] = str(tmp_path / "engine-controls")
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-v004-controls",
    })
    with app.app_context():
        web_db.create_all()
        user = User(
            email="control-controls@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("temporary-test-value"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        web_db.session.add(user)
        web_db.session.commit()
        uid = user.id
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["view_as"] = "CONTROL"
    return app, client, uid


def blocked_state(ticker="NKE"):
    return {
        "ticker": ticker,
        "metrics": {
            "valuation_share_basis_usable": False,
            "valuation_share_basis_status": "DATA REVIEW",
            "valuation_basis_issue": "Current share denominator is not verified on current split basis.",
            "valuation_share_raw": 100_000_000,
            "current_price_date": "2026-09-16",
        },
        "coverage": {"company_type": "Generic"},
        "gates": [{"key": "business_quality", "gate": "Business quality", "status": "INCOMPLETE", "auto_status": "INCOMPLETE", "manual": False, "notes": ""}],
        "peer_links": [], "bear_case_items": [], "kpis": [], "events": [], "snapshot_changes": [],
        "short_interest": [], "next_event": {},
    }


def test_deep_controls_page_renders_and_is_hidden_in_preview(tmp_path, monkeypatch):
    app, client, _uid = make_app(tmp_path)
    import mfapp.full312_controls as controls
    monkeypatch.setattr(controls, "load_state", lambda ticker: blocked_state(ticker))

    r = client.get("/workstation/NKE/controls")
    assert r.status_code == 200
    assert b"Deep Controls" in r.data
    assert b"SHARE BASIS RECOVERY" in r.data
    assert b"Bear Case ledger" in r.data
    assert b"OFFICIAL SHORT INTEREST" in r.data

    client.get("/preview/FRIEND")
    assert client.get("/workstation/NKE/controls").status_code == 404
    assert client.get("/control-mode", follow_redirects=False).status_code == 302
    assert client.get("/workstation/NKE/controls").status_code == 200


def test_share_basis_recovery_and_clear_use_original_engine_db(tmp_path, monkeypatch):
    _app, client, _uid = make_app(tmp_path)
    import mfapp.full312_controls as controls
    monkeypatch.setattr(controls, "load_state", lambda ticker: blocked_state(ticker))

    r = client.post("/workstation/NKE/controls/share-basis", data={
        "mode": "USER_CURRENT_BASIS",
        "current_basis_shares": "123456789",
        "basis_date": "2026-09-16",
        "source_note": "Latest filing cover page plus corporate actions verified through quote date",
        "confirmed": "1",
    }, follow_redirects=False)
    assert r.status_code == 302

    from market_forensics import db
    row = db.share_basis_override("NKE")
    assert row is not None
    assert float(row["current_basis_shares"]) == 123456789
    assert row["mode"] == "USER_CURRENT_BASIS"

    r = client.post("/workstation/NKE/controls/share-basis/clear", follow_redirects=False)
    assert r.status_code == 302
    assert db.share_basis_override("NKE") is None


def test_gate_override_bear_case_and_event_freeze_persist(tmp_path, monkeypatch):
    _app, client, _uid = make_app(tmp_path)
    import mfapp.full312_controls as controls
    monkeypatch.setattr(controls, "load_state", lambda ticker: blocked_state(ticker))
    from market_forensics import db, decision

    r = client.post("/workstation/NKE/controls/gate", data={"gate_key": "business_quality", "status": "WATCH", "notes": "Manual evidence under review"})
    assert r.status_code == 302
    assert db.gate_overrides("NKE")["business_quality"]["manual_status"] == "WATCH"

    decision.seed_bear_case("NKE")
    item = db.query("SELECT * FROM bear_case_items WHERE ticker=? ORDER BY item_key LIMIT 1", ("NKE",))[0]
    r = client.post("/workstation/NKE/controls/bear-case", data={
        "item_key": item["item_key"], "evidence_for": "Demand weakens", "evidence_against": "Orders stable", "severity": "4", "status": "WATCH",
    })
    assert r.status_code == 302
    saved = db.query("SELECT * FROM bear_case_items WHERE ticker=? AND item_key=?", ("NKE", item["item_key"]))[0]
    assert saved["evidence_for"] == "Demand weakens"
    assert int(saved["severity"]) == 4

    frozen = {
        "event_date": "2026-10-01", "event_type": "Earnings", "title": "Q1 earnings",
        "thesis_expectation": "Revenue stabilization", "bear_case": "Guide down", "base_case": "Inline", "bull_case": "Beat",
        "add_condition": "Margin beats", "reduce_condition": "Inventory worsens", "exit_condition": "Thesis invalidated",
    }
    r = client.post("/workstation/NKE/controls/event", data=frozen)
    assert r.status_code == 302
    event = db.query("SELECT * FROM events WHERE ticker=?", ("NKE",))[0]
    frozen_before = {k: event[k] for k in ("thesis_expectation", "bear_case", "base_case", "bull_case", "add_condition", "reduce_condition", "exit_condition")}

    r = client.post("/workstation/NKE/controls/event/close", data={"event_id": event["id"], "thesis_state": "WEAKER", "post_result": "Margins missed"})
    assert r.status_code == 302
    closed = db.query("SELECT * FROM events WHERE id=?", (event["id"],))[0]
    assert closed["status"] == "DONE"
    assert closed["thesis_state"] == "WEAKER"
    assert {k: closed[k] for k in frozen_before} == frozen_before


def test_monitoring_history_and_short_interest_no_lookahead(tmp_path):
    _app, client, _uid = make_app(tmp_path)
    from market_forensics import db

    stamp = db.now_utc()
    db.execute(
        """INSERT INTO monitoring_kpis(ticker,name,category,metric_key,source_type,unit,manual_value,operator,threshold,linked_to,importance,locked,notes,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("NKE", "Channel check", "Custom", "", "MANUAL", "score", 1.0, ">=", 0.0, "THESIS", 3, 1, "Locked threshold", stamp, stamp),
    )
    kpi = db.query("SELECT * FROM monitoring_kpis WHERE ticker=?", ("NKE",))[0]
    r = client.post("/workstation/NKE/controls/monitoring/manual", data={"kpi_id": kpi["id"], "manual_value": "2.5"})
    assert r.status_code == 302
    assert float(db.query("SELECT manual_value FROM monitoring_kpis WHERE id=?", (kpi["id"],))[0]["manual_value"]) == 2.5
    hist = db.query("SELECT * FROM monitoring_history WHERE kpi_id=?", (kpi["id"],))
    assert len(hist) == 1 and float(hist[0]["value"]) == 2.5
    # Threshold is immutable in this route even when the rule is locked.
    assert float(db.query("SELECT threshold FROM monitoring_kpis WHERE id=?", (kpi["id"],))[0]["threshold"]) == 0.0

    r = client.post("/workstation/NKE/controls/short-interest", data={
        "settlement_date": "2026-08-31", "publication_date": "2026-09-10", "shares_short": "75000000", "days_to_cover": "3.2",
    })
    assert r.status_code == 302
    si = db.query("SELECT * FROM short_interest WHERE ticker=?", ("NKE",))[0]
    assert si["settlement_date"] == "2026-08-31"
    assert si["publication_date"] == "2026-09-10"
    assert float(si["shares_short"]) == 75000000

    # Reject impossible publication-before-settlement records.
    r = client.post("/workstation/NKE/controls/short-interest", data={
        "settlement_date": "2026-09-15", "publication_date": "2026-09-10", "shares_short": "1",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert len(db.query("SELECT * FROM short_interest WHERE ticker=?", ("NKE",))) == 1


def test_peer_validation_uses_original_symbol_validator(tmp_path, monkeypatch):
    _app, client, _uid = make_app(tmp_path)
    from market_forensics import db, symbols
    monkeypatch.setattr(symbols, "validate_ticker", lambda ticker: SimpleNamespace(valid=True, ticker="UPS", name="United Parcel Service", message="", source="test"))
    r = client.post("/workstation/NKE/controls/peer", data={"peer_ticker": "UPS", "relation_type": "PEER", "notes": "Logistics comparator"})
    assert r.status_code == 302
    peers = db.peer_links("NKE")
    assert len(peers) == 1 and peers[0]["peer_ticker"] == "UPS"

    r = client.post("/workstation/NKE/controls/peer/delete", data={"peer_ticker": "UPS", "relation_type": "PEER"})
    assert r.status_code == 302
    assert db.peer_links("NKE") == []
