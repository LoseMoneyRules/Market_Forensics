import os
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import Company, Publication, User
from mfapp.security import encrypt_secret, hash_password


def make_app(tmp_path):
    os.environ["MF_V312_APP_DIR"] = str(tmp_path / "engine")
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-key-v004",
    })
    with app.app_context():
        db.create_all()
    return app


def add_user(app, role="CONTROL", email="control-v004@example.com"):
    with app.app_context():
        user = User(
            email=email,
            display_name=role.title(),
            role=role,
            password_hash=hash_password("temporary-test-value"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def login_session(client, user_id, view_as="CONTROL"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = view_as


def fake_state(ticker="NKE"):
    return {
        "ticker": ticker,
        "snap": {"meta": [{"company_name": "NIKE Test"}]},
        "bars": [],
        "fund_rows": [],
        "bar": {"close": 40.0},
        "scores": {"Net Tape": 52.0, "Confidence": 61.0, "Short Pressure": 45.0, "Institutional Flow": 55.0},
        "metrics": {"revenue": 50_000_000_000.0, "fcf": 4_000_000_000.0, "roic": .18, "fcf_margin": .08, "valuation_share_basis_status": "VERIFIED", "valuation_share_source": "SEC_CURRENT", "valuation_share_confidence": "HIGH"},
        "assumptions": {"horizon_years": 5, "weights": {"pe": .4, "ev_sales": .3, "fcf_yield": .3}, "bear": {}, "base": {}, "bull": {}},
        "scenarios": {
            "bear": {"fair_value": 30.0, "pe": 31.0, "ev_sales": 29.0, "fcf_yield": 30.0, "dcf": 28.0, "prob": .25, "flags": []},
            "base": {"fair_value": 50.0, "pe": 52.0, "ev_sales": 49.0, "fcf_yield": 50.0, "dcf": 47.0, "prob": .50, "flags": []},
            "bull": {"fair_value": 70.0, "pe": 72.0, "ev_sales": 68.0, "fcf_yield": 70.0, "dcf": 65.0, "prob": .25, "flags": []},
        },
        "expected_value": 50.0,
        "base_value": 50.0,
        "price": 40.0,
        "display_price": 40.0,
        "model_price": 40.0,
        "upside": .25,
        "coverage": {"status": "RESEARCH", "portfolio_state": "LONG"},
        "gates": [{"Gate": "Business quality", "Status": "PASS", "Reason": "Synthetic render gate"}],
        "gate_counts": {"PASS": 1, "WATCH": 0, "FAIL": 0, "INCOMPLETE": 15},
        "gate_label": "RESEARCH INCOMPLETE",
        "kpis": [],
        "kpi_eval": [],
        "audit": {"counts": {"PASS": 5, "WARN": 1, "FAIL": 0}},
        "valuation_confidence": {"label": "MEDIUM", "score": 65},
        "valuation_regime": {},
        "forensics": {"positives": [], "negatives": [], "data_alerts": []},
        "management": {"label": "LOW DATA", "score": 50, "scorable": 0, "met": 0, "missed": 0, "rows": []},
        "quality_validation": {"label": "SOLID", "score": 70, "checks": []},
        "forecast_assumptions": {"years": 5, "bear": {}, "base": {}, "bull": {}},
        "forecast_scenarios": {},
        "price_reconciliation": {},
        "variant_case": {},
        "triangulation": {},
        "research": {},
        "risk": {},
        "portfolio": {"side": "LONG", "shares": 508.0, "avg_cost": 48.0, "notes": "PRIVATE"},
        "validation": {},
        "share_basis_evidence": {},
        "share_basis_override": {},
        "opportunity": {"setup": "SOLID + ATTRACTIVE", "focus": "LONG WATCH", "reason": "synthetic", "next_action": "Research", "long_score": 68, "short_score": 32, "quality_score": 75},
        "financial_flow_years": [2025],
        "financial_flows_by_year": {"2025": {"income": {"ok": False, "reason": "Synthetic", "rows": []}, "cash": {"ok": False, "reason": "Synthetic", "rows": []}}},
        "income_flow": {"ok": False, "reason": "Synthetic", "rows": []},
        "cash_flow": {"ok": False, "reason": "Synthetic", "rows": []},
        "sources": [],
        "refresh_runs": [],
        "short_interest": [],
        "flow_rows": [],
        "short_volume": [],
    }


def test_control_preview_is_always_reversible(tmp_path):
    app = make_app(tmp_path)
    control_id = add_user(app)
    client = app.test_client()
    login_session(client, control_id)

    r = client.get("/preview/FRIEND", follow_redirects=False)
    assert r.status_code == 302
    with client.session_transaction() as s:
        assert s["view_as"] == "FRIEND"
        assert s["user_id"] == control_id
    assert client.get("/").status_code == 200
    assert client.get("/control").status_code == 404

    r = client.get("/control-mode", follow_redirects=False)
    assert r.status_code == 302 and "/workstation" in r.location
    with client.session_transaction() as s:
        assert s["view_as"] == "CONTROL"

    r = client.get("/preview/INSIDER", follow_redirects=False)
    assert r.status_code == 302
    with client.session_transaction() as s:
        assert s["view_as"] == "INSIDER"
    assert client.get("/control-mode", follow_redirects=False).status_code == 302
    with client.session_transaction() as s:
        assert s["view_as"] == "CONTROL"

    with app.app_context():
        assert db.session.get(User, control_id).role == "CONTROL"


def test_non_control_cannot_use_control_recovery(tmp_path):
    app = make_app(tmp_path)
    friend_id = add_user(app, role="FRIEND", email="friend-v004@example.com")
    client = app.test_client()
    login_session(client, friend_id, "FRIEND")
    assert client.get("/control-mode").status_code == 403
    assert client.get("/preview/CONTROL").status_code == 403


def test_full_engine_is_materialized_exactly():
    engine = Path(__file__).resolve().parents[1] / "market_forensics"
    files = sorted(p.name for p in engine.glob("*.py"))
    assert len(files) == 27
    assert "workstation.py" in files
    assert "valuation.py" in files
    assert "forensics.py" in files
    assert "service.py" in files
    import market_forensics
    assert market_forensics.__version__ == "3.1.12"


def test_original_financial_flows_handle_nonstandard_filings():
    from market_forensics.financial_flows import cash_flow_flow, income_statement_flow

    ups_style = income_statement_flow({"revenue": 100.0, "gross_profit": None, "operating_income": 12.0, "pretax": 10.0, "tax": 2.0, "net_income": 8.0})
    assert ups_style["ok"] is True
    assert "Gross profit" not in ups_style["labels"]
    assert "Operating income" in ups_style["labels"]

    cash_deficit = cash_flow_flow({"net_income": -4.0, "cfo": -3.0, "capex": 5.0, "buybacks": 0.0, "dividends": 0.0})
    assert cash_deficit["ok"] is True
    assert "Cash used in operations" in cash_deficit["labels"]


def test_all_full_workstation_sections_render(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    control_id = add_user(app)
    client = app.test_client()
    login_session(client, control_id)

    import mfapp.full312_routes as routes
    monkeypatch.setattr(routes, "load_state", lambda ticker: fake_state(ticker))

    sections = ["decide", "research", "fundamentals", "financial-flows", "management", "valuation", "tape", "monitoring", "validate", "risk", "portfolio", "sources"]
    for section in sections:
        r = client.get(f"/workstation/NKE/{section}")
        assert r.status_code == 200, (section, r.data[:500])
        assert b"3.1.12 FULL" in r.data


def test_published_snapshot_never_contains_private_position(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    control_id = add_user(app)
    client = app.test_client()
    login_session(client, control_id)

    import mfapp.full312_routes as routes
    monkeypatch.setattr(routes, "load_state", lambda ticker: fake_state(ticker))

    r = client.post("/workstation/NKE/publish", data={"visibility": "FRIEND"}, follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        company = Company.query.filter_by(ticker="NKE").one()
        pub = Publication.query.filter_by(company_id=company.id, is_current=True).one()
        assert pub.model_version == "0.0.4 / V3.1.12 FULL"
        assert "portfolio" not in pub.payload
        assert "shares" not in pub.payload
        assert "avg_cost" not in pub.payload
        assert "PRIVATE" not in str(pub.payload)

    client.get("/preview/FRIEND")
    r = client.get("/company/NKE")
    assert r.status_code == 200
    assert b"PRIVATE" not in r.data
    assert client.get("/control-mode", follow_redirects=False).status_code == 302
