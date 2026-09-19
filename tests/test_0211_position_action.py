from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, MarketSnapshot, PortfolioRiskPlan, Position, PositionProfile,
    Security, Snapshot,
)
from mfapp.extensions import db
from mfapp.models import User
from mfapp.position_action import decide_position_action
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import publication_payload


def _decision(**overrides):
    data = {
        "has_position": False,
        "side": "LONG",
        "research_attached": True,
        "research_conclusion": "LONG READY",
        "validation_state": "VALIDATED",
        "value_state": "ATTRACTIVE",
        "variant_state": "POSITIVE EDGE",
        "path_state": "SUPPORTIVE",
        "model_confidence": "STRONG",
        "thesis_control": "CONTROLLED",
        "invalidation_triggered": False,
        "portfolio_weight_pct": None,
        "max_position_pct": 10.0,
        "suggested_position_pct": 8.0,
        "entry_conditions": "Confirm filed evidence.",
        "add_conditions": "Add only after the next filed KPI confirms the thesis.",
        "exit_conditions": "Exit on locked thesis invalidation.",
    }
    data.update(overrides)
    return decide_position_action(**data)


def test_0211_no_position_long_ready_is_candidate_not_order():
    out = _decision()
    assert out["action"] == "BUY CANDIDATE"
    assert out["rule"] == "NO_POSITION_LONG_READY"
    assert "not an order" in out["blocker"].lower()


def test_0211_no_position_research_incomplete_waits():
    out = _decision(research_conclusion="RESEARCH INCOMPLETE")
    assert out["action"] == "WAIT"
    assert out["rule"] == "RESEARCH_INCOMPLETE"


def test_0211_long_ready_with_risk_room_is_add_on_evidence():
    out = _decision(
        has_position=True,
        side="LONG",
        portfolio_weight_pct=4.0,
        max_position_pct=10.0,
        suggested_position_pct=8.0,
    )
    assert out["action"] == "ADD ON EVIDENCE"
    assert out["rule"] == "LONG_READY_ADD_ON_EVIDENCE"


def test_0211_long_over_cap_reduces_even_when_research_long_ready():
    out = _decision(
        has_position=True,
        side="LONG",
        portfolio_weight_pct=12.0,
        max_position_pct=10.0,
        suggested_position_pct=15.0,
    )
    assert out["action"] == "REDUCE"
    assert out["rule"] == "RISK_BREACH_REDUCE_LONG"


def test_0211_long_locked_thesis_invalidation_forces_exit():
    out = _decision(
        has_position=True,
        side="LONG",
        portfolio_weight_pct=4.0,
        invalidation_triggered=True,
    )
    assert out["action"] == "EXIT / SELL"
    assert out["rule"] == "LOCKED_INVALIDATION_EXIT"


def test_0211_short_ready_with_room_is_add_short_on_evidence():
    out = _decision(
        has_position=True,
        side="SHORT",
        research_conclusion="SHORT READY",
        value_state="EXPENSIVE",
        variant_state="NEGATIVE EDGE",
        path_state="HOSTILE",
        portfolio_weight_pct=3.0,
        suggested_position_pct=7.0,
    )
    assert out["action"] == "ADD SHORT ON EVIDENCE"
    assert out["rule"] == "SHORT_READY_ADD_ON_EVIDENCE"


def test_0211_short_risk_breach_reduces_short():
    out = _decision(
        has_position=True,
        side="SHORT",
        research_conclusion="SHORT READY",
        value_state="EXPENSIVE",
        variant_state="NEGATIVE EDGE",
        path_state="HOSTILE",
        portfolio_weight_pct=11.0,
        max_position_pct=10.0,
        suggested_position_pct=12.0,
    )
    assert out["action"] == "REDUCE SHORT"
    assert out["rule"] == "RISK_BREACH_REDUCE_SHORT"


def test_0211_portfolio_only_security_keeps_limited_action():
    out = _decision(
        has_position=True,
        side="LONG",
        research_attached=False,
        research_conclusion="RESEARCH INCOMPLETE",
        portfolio_weight_pct=4.0,
    )
    assert out["action"] == "HOLD / WAIT"
    assert out["rule"] == "PORTFOLIO_ONLY"
    assert "research" in out["blocker"].lower()


def test_0211_add_is_not_a_direct_price_or_pnl_rule():
    params = inspect.signature(decide_position_action).parameters
    assert "current_price" not in params
    assert "avg_cost" not in params
    assert "pnl" not in params
    hold = _decision(
        has_position=True,
        side="LONG",
        research_conclusion="LONG WATCH",
        portfolio_weight_pct=3.0,
        suggested_position_pct=8.0,
    )
    assert hold["action"] == "HOLD / WAIT"


def _make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "0211-position-action",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '0211.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def _seed_control(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control0211@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        company = Company(legal_name="Provider Free Co", display_name="Provider Free Co")
        db.session.add(company)
        db.session.flush()
        security = Security(
            company_id=company.id, ticker="PFG", exchange="NYSE", currency="USD",
            active=True, is_primary=True,
        )
        db.session.add(security)
        db.session.flush()
        db.session.add(Position(user_id=user.id, security_id=security.id, shares=10, avg_cost=50, currency="USD"))
        db.session.add(PositionProfile(user_id=user.id, security_id=security.id, side="LONG", tags=""))
        db.session.add(PortfolioRiskPlan(
            user_id=user.id, security_id=security.id, risk_budget_pct=Decimal("0.75"),
            sizing_reference_price=None, event_liquidity_haircut_pct=Decimal("5"),
            max_position_pct=Decimal("100"), updated_by=user.id,
        ))
        db.session.add(MarketSnapshot(
            security_id=security.id, provider="TEST", price=Decimal("50"), currency="USD",
            as_of=datetime.now(timezone.utc).replace(tzinfo=None), quality="OBSERVED", payload={},
        ))
        db.session.commit()
        return user.id, security.id


def _login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_0211_normal_portfolio_get_is_provider_free(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    user_id, _ = _seed_control(app)
    client = app.test_client()
    _login(client, user_id)

    def fail_network(*args, **kwargs):
        raise AssertionError("normal GET must not call a provider/network")

    monkeypatch.setattr("requests.sessions.Session.request", fail_network)
    response = client.get("/portfolio/PFG")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "POSITION ACTION" in html
    assert "HOLD / WAIT" in html
    assert "ADD ON EVIDENCE, NOT ON PRICE" in html


def test_0211_position_action_never_enters_publication_member_payload(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    user_id, _ = _seed_control(app)
    with app.app_context():
        snapshot = Snapshot(
            coverage_id=999,
            version=1,
            snapshot_type="DECISION",
            created_by=user_id,
            payload={
                "security": {"ticker": "PFG"},
                "company": {"name": "Provider Free Co"},
                "research": {"thesis": "Public-safe thesis", "risk_summary": "Public-safe research risk"},
                "research_state": "READY",
                "valuation": {"base": 60},
                "decision": {"research_conclusion": "LONG READY"},
                "market": {"price": 50},
                "sources": [],
                "position_action": {"action": "ADD ON EVIDENCE", "next_confirmation": "PRIVATE ACTION MARKER"},
                "position": {"shares": 123, "avg_cost": 44},
                "portfolio_risk": {"max_position_pct": 10, "risk_budget_pct": .75},
                "sizing": {"suggested_position_pct": 4.2},
            },
        )
        for visibility in ("FRIEND", "INSIDER"):
            payload = publication_payload(snapshot, visibility)
            text = repr(payload)
            assert "position_action" not in payload
            assert "position" not in payload
            assert "portfolio_risk" not in payload
            assert "sizing" not in payload
            assert "PRIVATE ACTION MARKER" not in text
            assert "123" not in text


def test_0211_version_and_contract_are_synced():
    version = open("VERSION", encoding="utf-8").read().strip()
    assert tuple(int(part) for part in version.split(".")) >= (0, 2, 12)
    current = open("docs/CURRENT_STATE.md", encoding="utf-8").read()
    how = open("docs/HOW_MARKET_FORENSICS_WORKS.md", encoding="utf-8").read()
    parity = open("docs/LOCAL_WEB_PARITY_0_2_11.md", encoding="utf-8").read()
    assert f"**State-Version: {version}**" in current
    assert f"**Current product line:** {version}" in how
    assert "Research judges the security. Portfolio decides what the existing position allows you to do." in how
    assert "ADD ON EVIDENCE, NOT ON PRICE." in parity
