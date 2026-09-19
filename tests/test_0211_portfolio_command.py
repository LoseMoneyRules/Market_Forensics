from __future__ import annotations

from datetime import datetime, timezone

from mfapp import create_app
from mfapp.core_models import Company, Coverage, MonitoringHistory, MonitoringRule, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.portfolio_engine import build_needs_action, merge_action_history, position_capacity
from mfapp.position_action import (
    decide_position_action,
    monitoring_condition_state,
    save_position_condition_links,
)


def _decision(**overrides):
    data = {
        "has_position": True,
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
        "portfolio_weight_pct": 4.0,
        "max_position_pct": 10.0,
        "suggested_position_pct": 8.0,
        "entry_conditions": "Enter on evidence.",
        "add_conditions": "Add after KPI confirmation.",
        "trim_conditions": "Trim if execution weakens.",
        "exit_conditions": "Exit if the Portfolio exit rule confirms.",
    }
    data.update(overrides)
    return decide_position_action(**data)


def test_0211_linked_add_evidence_must_confirm_before_add():
    pending = _decision(
        add_evidence_required=True,
        add_evidence_confirmed=False,
        add_evidence_status="WATCH",
    )
    assert pending["action"] == "HOLD"
    assert pending["rule"] == "LONG_READY_HOLD"
    assert "linked add evidence" in pending["blocker"].lower()

    confirmed = _decision(
        add_evidence_required=True,
        add_evidence_confirmed=True,
        add_evidence_status="OK",
    )
    assert confirmed["action"] == "ADD ON EVIDENCE"
    assert confirmed["rule"] == "LONG_READY_ADD_ON_EVIDENCE"


def test_0211_confirmed_trim_and_exit_have_conservative_precedence():
    trimmed = _decision(trim_condition_confirmed=True, trim_condition_status="FAIL")
    assert trimmed["action"] == "REDUCE"
    assert trimmed["rule"] == "PORTFOLIO_TRIM_CONDITION_LONG"

    exited = _decision(
        trim_condition_confirmed=True,
        exit_condition_confirmed=True,
        exit_condition_status="FAIL",
    )
    assert exited["action"] == "EXIT / SELL"
    assert exited["rule"] == "PORTFOLIO_EXIT_CONDITION_SELL"

    invalidated = _decision(
        invalidation_triggered=True,
        exit_condition_confirmed=True,
        exit_condition_status="FAIL",
    )
    assert invalidated["rule"] == "LOCKED_INVALIDATION_EXIT"


def test_0211_position_capacity_is_reference_translation_not_action_input():
    capacity = position_capacity(
        current_price=50,
        shares=100,
        portfolio_value=100000,
        current_weight_pct=5.0,
        sizing={"suggested_position_pct": 8.0},
    )
    assert capacity["target_weight_pct"] == 8.0
    assert capacity["headroom_pp"] == 3.0
    assert capacity["current_value"] == 5000
    assert capacity["target_value"] == 8000
    assert capacity["delta_value"] == 3000
    assert capacity["target_shares"] == 160
    assert capacity["delta_shares"] == 60


def test_0211_action_history_only_records_meaningful_transitions():
    previous = {
        "1": {
            "security_id": 1,
            "ticker": "AAA",
            "action": "HOLD",
            "rule": "LONG_READY_HOLD",
            "research_conclusion": "LONG READY",
        }
    }
    current = {
        "1": {
            "security_id": 1,
            "ticker": "AAA",
            "action": "ADD ON EVIDENCE",
            "rule": "LONG_READY_ADD_ON_EVIDENCE",
            "research_conclusion": "LONG READY",
            "why_now": "Evidence confirmed.",
        }
    }
    history = merge_action_history(previous, current, [], changed_at="2026-09-19T10:00:00")
    assert len(history) == 1
    assert history[0]["from_action"] == "HOLD"
    assert history[0]["to_action"] == "ADD ON EVIDENCE"
    assert history[0]["trigger"] == "Evidence confirmed."
    assert merge_action_history(current, current, history, changed_at="2026-09-19T11:00:00") == history


def test_0211_needs_action_prioritizes_exit_reduce_and_ready_holds():
    actions = {
        "1": {"security_id": 1, "ticker": "EXIT", "action": "EXIT / SELL", "research_conclusion": "LONG READY", "why_now": "Exit.", "blocker": ""},
        "2": {"security_id": 2, "ticker": "RED", "action": "REDUCE", "research_conclusion": "LONG READY", "why_now": "Risk.", "blocker": ""},
        "3": {"security_id": 3, "ticker": "HLD", "action": "HOLD", "research_conclusion": "LONG READY", "why_now": "Waiting.", "blocker": "Evidence pending."},
        "4": {"security_id": 4, "ticker": "NO", "action": "HOLD / WAIT", "research_conclusion": "LONG WATCH", "why_now": "Watch.", "blocker": ""},
    }
    rows = build_needs_action(actions)
    assert [row["ticker"] for row in rows] == ["EXIT", "RED", "HLD"]


def test_0211_monitoring_links_are_private_explicit_and_status_aware(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "0211-portfolio-command",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'portfolio-command.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })
    with app.app_context():
        db.create_all()
        user = User(
            email="portfolio-command@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash="test",
            totp_secret_enc="test",
            is_active=True,
        )
        company = Company(legal_name="Evidence Co", display_name="Evidence Co")
        db.session.add_all([user, company])
        db.session.flush()
        security = Security(company_id=company.id, ticker="EVD", exchange="NYSE", currency="USD", active=True, is_primary=True)
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="READY")
        db.session.add(coverage)
        db.session.flush()

        add_rule = MonitoringRule(
            coverage_id=coverage.id,
            name="KPI confirmation",
            metric="revenue_growth",
            operator=">",
            threshold_value=3,
            severity="WATCH",
            created_by=user.id,
        )
        trim_rule = MonitoringRule(
            coverage_id=coverage.id,
            name="Execution deterioration",
            metric="operating_margin",
            operator="<",
            threshold_value=10,
            severity="FAIL",
            created_by=user.id,
        )
        db.session.add_all([add_rule, trim_rule])
        db.session.flush()
        db.session.add_all([
            MonitoringHistory(
                rule_id=add_rule.id,
                observed_value=4,
                status="OK",
                note="Confirmed",
                observed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            MonitoringHistory(
                rule_id=trim_rule.id,
                observed_value=9,
                status="FAIL",
                note="Triggered",
                observed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        ])
        save_position_condition_links(
            user_id=user.id,
            security_id=security.id,
            coverage_id=coverage.id,
            conditions={
                "ADD": {"rule_id": add_rule.id, "confirm_on": "OK"},
                "TRIM": {"rule_id": trim_rule.id, "confirm_on": "TRIGGERED"},
                "EXIT": {"rule_id": None, "confirm_on": "TRIGGERED"},
            },
        )
        db.session.commit()

        state = monitoring_condition_state(
            user_id=user.id,
            security_id=security.id,
            coverage_id=coverage.id,
        )
        assert state["conditions"]["ADD"]["confirmed"] is True
        assert state["conditions"]["ADD"]["latest_status"] == "OK"
        assert state["conditions"]["TRIM"]["confirmed"] is True
        assert state["conditions"]["TRIM"]["latest_status"] == "FAIL"
        assert state["conditions"]["EXIT"]["rule_id"] is None
        assert state["conditions"]["EXIT"]["confirmed"] is False
