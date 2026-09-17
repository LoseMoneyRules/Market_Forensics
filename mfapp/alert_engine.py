from __future__ import annotations

import os
import re
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any

from .core_models import (
    Alert,
    Coverage,
    DataQualityIssue,
    MonitoringHistory,
    MonitoringRule,
    Publication,
    Security,
)
from .extensions import db
from .models import AuditEvent, User, UserPreference
from .monitoring_engine import NUMERIC_OPERATORS, _compare, _metric_map, _next_filing_estimate, _num, _recent_alert
from .services import can_view_publication

EMAIL_PREF_KEY = "alert_email_017"
SUBSCRIPTION_PREFIX = "alert_subscription_017_"
CATALOG_PREFIX = "alert_catalog_017_"
SYSTEM_ALERTS = {
    "data_quality": "Data-quality failures",
    "filing": "Earnings / filing window",
    "valuation": "Valuation-zone events",
}
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _subscription_key(coverage_id: int) -> str:
    return f"{SUBSCRIPTION_PREFIX}{coverage_id}"


def _catalog_key(coverage_id: int) -> str:
    return f"{CATALOG_PREFIX}{coverage_id}"


def _clean_ints(values) -> list[int]:
    out = []
    for raw in values or []:
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _clean_system(values) -> list[str]:
    return sorted({str(value) for value in (values or []) if str(value) in SYSTEM_ALERTS})


def _active_rule_ids(coverage_id: int) -> set[int]:
    return {
        row.id
        for row in MonitoringRule.query.filter_by(coverage_id=coverage_id, is_active=True).all()
    }


def alert_email(user_id: int) -> str:
    """Account registration email is the only alert-email source of truth."""
    user = db.session.get(User, user_id)
    return str(user.email if user else "").strip().lower()


def save_alert_email(user_id: int, email: str, actor_user_id: int) -> str:
    """Compatibility no-op: notification email cannot diverge from the account email."""
    value = alert_email(user_id)
    db.session.add(AuditEvent(
        actor_user_id=actor_user_id,
        action="alerts.email.account_source",
        object_type="user",
        object_id=str(user_id),
        meta={"email_domain": value.rsplit("@", 1)[-1] if "@" in value else ""},
    ))
    db.session.commit()
    return value


def alert_subscription(user_id: int, coverage_id: int) -> dict[str, Any]:
    row = UserPreference.query.filter_by(user_id=user_id, key=_subscription_key(coverage_id)).first()
    value = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
    return {
        "rule_ids": _clean_ints(value.get("rule_ids")),
        "system_alerts": _clean_system(value.get("system_alerts")),
        "email_enabled": bool(value.get("email_enabled", True)),
        "in_app_enabled": bool(value.get("in_app_enabled", True)),
    }


def save_alert_subscription(
    user_id: int,
    coverage_id: int,
    *,
    rule_ids: list[int],
    system_alerts: list[str],
    email_enabled: bool,
    in_app_enabled: bool,
    actor_user_id: int,
) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if not coverage:
        raise ValueError("Coverage not found.")

    requested_rules = set(_clean_ints(rule_ids))
    requested_system = set(_clean_system(system_alerts))
    if user_id == coverage.user_id:
        # CONTROL can subscribe to private rules without exposing them to members.
        allowed_rules = _active_rule_ids(coverage_id)
        allowed_system = set(SYSTEM_ALERTS)
    else:
        catalog = alert_catalog(coverage_id)
        allowed_rules = set(catalog["rule_ids"])
        allowed_system = set(catalog["system_alerts"])

    value = {
        "rule_ids": sorted(requested_rules.intersection(allowed_rules)),
        "system_alerts": sorted(requested_system.intersection(allowed_system)),
        "email_enabled": bool(email_enabled),
        "in_app_enabled": bool(in_app_enabled),
    }
    row = UserPreference.query.filter_by(user_id=user_id, key=_subscription_key(coverage_id)).first()
    if row is None:
        row = UserPreference(user_id=user_id, key=_subscription_key(coverage_id), value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.add(AuditEvent(
        actor_user_id=actor_user_id,
        action="alerts.subscription.save",
        object_type="coverage",
        object_id=str(coverage_id),
        meta={"recipient_user_id": user_id, **value},
    ))
    db.session.commit()
    return value


def alert_catalog(coverage_id: int) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if not coverage:
        return {"rule_ids": [], "system_alerts": []}
    row = UserPreference.query.filter_by(user_id=coverage.user_id, key=_catalog_key(coverage_id)).first()
    value = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
    systems = value.get("system_alerts") if "system_alerts" in value else list(SYSTEM_ALERTS)
    return {
        "rule_ids": sorted(set(_clean_ints(value.get("rule_ids"))).intersection(_active_rule_ids(coverage_id))),
        "system_alerts": _clean_system(systems),
    }


def save_alert_catalog(
    coverage_id: int,
    *,
    rule_ids: list[int],
    system_alerts: list[str],
    actor_user_id: int,
) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if not coverage or coverage.user_id != actor_user_id:
        raise PermissionError("Only the CONTROL owner can define the alert catalog.")
    value = {
        "rule_ids": sorted(set(_clean_ints(rule_ids)).intersection(_active_rule_ids(coverage_id))),
        "system_alerts": _clean_system(system_alerts),
    }
    row = UserPreference.query.filter_by(user_id=coverage.user_id, key=_catalog_key(coverage_id)).first()
    if row is None:
        row = UserPreference(user_id=coverage.user_id, key=_catalog_key(coverage_id), value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.add(AuditEvent(
        actor_user_id=actor_user_id,
        action="alerts.catalog.save",
        object_type="coverage",
        object_id=str(coverage_id),
        meta=value,
    ))
    db.session.commit()
    return value


def _publication_access(user: User, coverage: Coverage) -> bool:
    if str(user.role or "").upper() == "CONTROL" and coverage.user_id == user.id:
        return True
    role = str(user.role or "FRIEND").upper()
    rows = Publication.query.filter_by(coverage_id=coverage.id).filter(
        Publication.revoked_at.is_(None)
    ).order_by(Publication.published_at.desc()).all()
    return any(can_view_publication(row, role) for row in rows)


def subscription_catalog_for_user(user_id: int, coverage_id: int) -> dict[str, Any]:
    user = db.session.get(User, user_id)
    coverage = db.session.get(Coverage, coverage_id)
    if not user or not coverage or not user.is_active or not _publication_access(user, coverage):
        raise PermissionError("Published research access is required for these alerts.")
    catalog = alert_catalog(coverage_id)
    subscription = alert_subscription(user_id, coverage_id)
    if user_id == coverage.user_id:
        visible_rule_ids = _active_rule_ids(coverage_id)
        visible_system = list(SYSTEM_ALERTS)
    else:
        visible_rule_ids = set(catalog["rule_ids"])
        visible_system = catalog["system_alerts"]
    rules = MonitoringRule.query.filter(
        MonitoringRule.coverage_id == coverage_id,
        MonitoringRule.is_active.is_(True),
        MonitoringRule.id.in_(visible_rule_ids or [-1]),
    ).order_by(MonitoringRule.id.asc()).all()
    return {
        "coverage_id": coverage_id,
        "email": alert_email(user_id),
        "subscription": subscription,
        "rules": [
            {
                "id": row.id,
                "name": row.name,
                "severity": row.severity,
                "trigger": _rule_trigger(row),
                "selected": row.id in subscription["rule_ids"],
                "member_available": row.id in catalog["rule_ids"],
            }
            for row in rules
        ],
        "system_alerts": [
            {
                "key": key,
                "label": SYSTEM_ALERTS[key],
                "selected": key in subscription["system_alerts"],
                "member_available": key in catalog["system_alerts"],
            }
            for key in visible_system
        ],
        "smtp_ready": _smtp_ready(),
    }


def _rule_trigger(rule: MonitoringRule) -> str:
    if rule.operator in NUMERIC_OPERATORS and rule.threshold_value is not None:
        value = f"{float(rule.threshold_value):g}"
        unit = f" {rule.unit}" if rule.unit else ""
        return f"{rule.metric} {rule.operator} {value}{unit}".strip()
    return str(rule.threshold_text or rule.metric or "Manual evidence trigger").strip()


def _smtp_ready() -> bool:
    return bool(os.environ.get("MF_SMTP_HOST", "").strip() and os.environ.get("MF_SMTP_FROM", "").strip())


def _send_email(email: str, security: Security, alert: Alert) -> tuple[bool, str]:
    if not _smtp_ready():
        return False, "SMTP_NOT_CONFIGURED"
    host = os.environ.get("MF_SMTP_HOST", "").strip()
    port = int(os.environ.get("MF_SMTP_PORT", "587") or 587)
    username = os.environ.get("MF_SMTP_USER", "").strip()
    password = os.environ.get("MF_SMTP_PASSWORD", "")
    sender = os.environ.get("MF_SMTP_FROM", "").strip()
    use_ssl = os.environ.get("MF_SMTP_SSL", "0") == "1"
    use_starttls = os.environ.get("MF_SMTP_STARTTLS", "1") == "1"
    msg = EmailMessage()
    msg["Subject"] = f"[Market Forensics] {security.ticker} · {alert.title}"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        f"Market Forensics monitoring alert\n\nTicker: {security.ticker}\nSeverity: {alert.severity}\n"
        f"Alert: {alert.title}\n\n{alert.body}\n\nADD ON EVIDENCE, NOT ON PRICE."
    )
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if use_starttls:
                    smtp.starttls()
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
        return True, "SENT"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _trigger_selected(subscription: dict[str, Any], rule_id: int | None, trigger_kind: str) -> bool:
    if rule_id is not None:
        return rule_id in subscription["rule_ids"]
    return trigger_kind in subscription["system_alerts"]


def _recipient_users(coverage: Coverage) -> list[User]:
    users: dict[int, User] = {}
    owner = db.session.get(User, coverage.user_id)
    if owner and owner.is_active:
        users[owner.id] = owner
    for pref in UserPreference.query.filter_by(key=_subscription_key(coverage.id)).all():
        user = db.session.get(User, pref.user_id)
        if user and user.is_active and _publication_access(user, coverage):
            users[user.id] = user
    return list(users.values())


def _emit_recipient(
    *,
    user: User,
    coverage: Coverage,
    security: Security,
    severity: str,
    title: str,
    body: str,
    rule_id: int | None,
    trigger_kind: str,
    trigger_meta: dict[str, Any],
    cooldown_hours: int,
    in_app_enabled: bool,
    email_enabled: bool,
) -> dict[str, Any]:
    recent = _recent_alert(user.id, coverage.id, rule_id, title, cooldown_hours)
    if recent:
        return {"created": False, "reason": "DEDUPED", "alert_id": recent.id, "user_id": user.id}
    alert = Alert(
        user_id=user.id,
        coverage_id=coverage.id,
        rule_id=rule_id,
        severity=str(severity or "WATCH").upper(),
        title=title[:220],
        body=body,
        is_read=not in_app_enabled,
    )
    db.session.add(alert)
    db.session.flush()
    db.session.add(AuditEvent(
        actor_user_id=coverage.user_id,
        action="monitoring.trigger",
        object_type="monitoring_rule" if rule_id else "coverage",
        object_id=str(rule_id or coverage.id),
        meta={
            "alert_id": alert.id,
            "recipient_user_id": user.id,
            "ticker": security.ticker,
            "severity": alert.severity,
            "trigger_kind": trigger_kind,
            **trigger_meta,
        },
    ))
    db.session.commit()

    email_status = "DISABLED"
    email = alert_email(user.id)
    if email_enabled and email:
        sent, email_status = _send_email(email, security, alert)
        db.session.add(AuditEvent(
            actor_user_id=coverage.user_id,
            action="notification.email.sent" if sent else "notification.email.failed",
            object_type="alert",
            object_id=str(alert.id),
            meta={
                "status": email_status,
                "trigger_kind": trigger_kind,
                "recipient_user_id": user.id,
                "recipient_domain": email.rsplit("@", 1)[-1],
            },
        ))
        db.session.commit()
    return {"created": True, "alert_id": alert.id, "email": email_status, "user_id": user.id}


def _emit_trigger(
    *,
    coverage: Coverage,
    security: Security,
    severity: str,
    title: str,
    body: str,
    rule_id: int | None,
    trigger_kind: str,
    trigger_meta: dict[str, Any],
    cooldown_hours: int = 24,
) -> list[dict[str, Any]]:
    catalog = alert_catalog(coverage.id)
    out = []
    for user in _recipient_users(coverage):
        subscription = alert_subscription(user.id, coverage.id)
        selected = _trigger_selected(subscription, rule_id, trigger_kind)
        is_owner = user.id == coverage.user_id
        if not is_owner and not selected:
            continue
        offered = (rule_id in catalog["rule_ids"]) if rule_id is not None else (trigger_kind in catalog["system_alerts"])
        # CONTROL subscriptions are independent of the member-facing catalog.
        email_enabled = bool(selected and subscription["email_enabled"] and (is_owner or offered))
        in_app_enabled = bool(subscription["in_app_enabled"] if selected else is_owner)
        out.append(_emit_recipient(
            user=user,
            coverage=coverage,
            security=security,
            severity=severity,
            title=title,
            body=body,
            rule_id=rule_id,
            trigger_kind=trigger_kind,
            trigger_meta=trigger_meta,
            cooldown_hours=cooldown_hours,
            in_app_enabled=in_app_enabled,
            email_enabled=email_enabled,
        ))
    return out


def evaluate_coverage(coverage_id: int, owner_user_id: int) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    owner = db.session.get(User, owner_user_id)
    if not coverage or not owner or coverage.user_id != owner.id:
        return {"coverage_id": coverage_id, "evaluated": False, "reason": "NOT_FOUND_OR_NOT_OWNER"}
    security = db.session.get(Security, coverage.security_id)
    if not security:
        return {"coverage_id": coverage_id, "evaluated": False, "reason": "SECURITY_NOT_FOUND"}
    metrics = _metric_map(coverage)
    deliveries: list[dict[str, Any]] = []

    for rule in MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True).order_by(MonitoringRule.id).all():
        operator = str(rule.operator or "").strip()
        threshold = _num(rule.threshold_value)
        observed = metrics.get(str(rule.metric or "").strip().lower())
        if operator not in NUMERIC_OPERATORS or threshold is None or observed is None:
            continue
        triggered = _compare(observed, operator, threshold)
        last = MonitoringHistory.query.filter_by(rule_id=rule.id).order_by(MonitoringHistory.observed_at.desc()).first()
        status = "FAIL" if triggered else "OK"
        if not last or str(last.status or "").upper() != status:
            db.session.add(MonitoringHistory(
                rule_id=rule.id,
                observed_value=observed,
                status=status,
                note="Automatic monitoring evaluation",
            ))
            db.session.commit()
        if triggered:
            deliveries.extend(_emit_trigger(
                coverage=coverage,
                security=security,
                severity=rule.severity or "WATCH",
                title=f"Threshold triggered · {rule.name}",
                body=f"Observed {rule.metric} = {observed:g}{(' ' + rule.unit) if rule.unit else ''}; trigger {operator} {threshold:g}{(' ' + rule.unit) if rule.unit else ''}.",
                rule_id=rule.id,
                trigger_kind="rule",
                trigger_meta={
                    "metric": rule.metric,
                    "operator": operator,
                    "threshold": threshold,
                    "observed": observed,
                    "locked_pre_investment": bool(rule.locked_pre_investment),
                },
            ))

    failures = DataQualityIssue.query.filter_by(company_id=security.company_id, status="OPEN").filter(
        DataQualityIssue.severity.in_(["FAIL", "CRITICAL"])
    ).all()
    if failures:
        deliveries.extend(_emit_trigger(
            coverage=coverage,
            security=security,
            severity="FAIL",
            title="Data-quality failure requires review",
            body=f"{len(failures)} open FAIL/CRITICAL data-quality issue(s).",
            rule_id=None,
            trigger_kind="data_quality",
            trigger_meta={"open_failures": len(failures)},
        ))

    filing_date = _next_filing_estimate(security.company_id)
    if filing_date is not None:
        days = (filing_date - date.today()).days
        if 0 <= days <= 7:
            deliveries.extend(_emit_trigger(
                coverage=coverage,
                security=security,
                severity="INFO",
                title="Estimated filing window approaching",
                body=f"Estimated next SEC filing window is {filing_date.isoformat()} ({days} day(s)). Refresh fundamentals when the filing lands.",
                rule_id=None,
                trigger_kind="filing",
                trigger_meta={"estimated_filing_date": filing_date.isoformat(), "days": days},
                cooldown_hours=72,
            ))

    price, bear, bull = (metrics.get(k) for k in ("price", "bear", "bull"))
    if price is not None and bear is not None and bull is not None:
        if price >= bull:
            deliveries.extend(_emit_trigger(
                coverage=coverage,
                security=security,
                severity="WATCH",
                title="Price reached/exceeded Bull fair value",
                body=f"Price {price:.2f} is at/above Bull {bull:.2f}. Revisit expected return; do not move fair value because price moved.",
                rule_id=None,
                trigger_kind="valuation",
                trigger_meta={"price": price, "bear": bear, "bull": bull, "zone": "ABOVE_BULL"},
            ))
        elif price <= bear:
            deliveries.extend(_emit_trigger(
                coverage=coverage,
                security=security,
                severity="WATCH",
                title="Price reached/breached Bear fair value",
                body=f"Price {price:.2f} is at/below Bear {bear:.2f}. Test evidence and invalidation before acting.",
                rule_id=None,
                trigger_kind="valuation",
                trigger_meta={"price": price, "bear": bear, "bull": bull, "zone": "BELOW_BEAR"},
            ))

    return {
        "coverage_id": coverage.id,
        "ticker": security.ticker,
        "evaluated": True,
        "created_alerts": sum(1 for row in deliveries if row.get("created")),
        "deliveries": deliveries,
        "smtp_ready": _smtp_ready(),
    }


def evaluate_user(user_id: int) -> dict[str, Any]:
    rows = [evaluate_coverage(c.id, user_id) for c in Coverage.query.filter_by(user_id=user_id).order_by(Coverage.id).all()]
    return {
        "user_id": user_id,
        "coverages": len(rows),
        "created_alerts": sum(row.get("created_alerts", 0) for row in rows),
        "results": rows,
    }


def evaluate_all() -> dict[str, Any]:
    results = []
    controls = User.query.filter_by(role="CONTROL", is_active=True).order_by(User.id).all()
    for control in controls:
        results.append(evaluate_user(control.id))
    return {
        "controls": len(results),
        "created_alerts": sum(row.get("created_alerts", 0) for row in results),
        "results": results,
    }


__all__ = [
    "EMAIL_PREF_KEY",
    "SUBSCRIPTION_PREFIX",
    "CATALOG_PREFIX",
    "SYSTEM_ALERTS",
    "alert_email",
    "save_alert_email",
    "alert_subscription",
    "save_alert_subscription",
    "alert_catalog",
    "save_alert_catalog",
    "subscription_catalog_for_user",
    "evaluate_coverage",
    "evaluate_user",
    "evaluate_all",
]
