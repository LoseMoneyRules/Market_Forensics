from __future__ import annotations

import os
import re
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any

from .core_models import Alert, Coverage, DataQualityIssue, FinancialPeriod, MonitoringHistory, MonitoringRule, Security
from .extensions import db
from .models import AuditEvent, User, UserPreference
from .monitoring_016 import (
    NUMERIC_OPERATORS,
    _compare,
    _metric_map,
    _minimum_allowed,
    _next_filing_estimate,
    _num,
    _recent_alert,
    notification_preferences,
)

EMAIL_PREF_KEY = "alert_email_017"
POLICY_PREFIX = "alert_policy_017_"
SYSTEM_ALERTS = {
    "data_quality": "Data-quality failures",
    "filing": "Earnings / filing window",
    "valuation": "Valuation-zone events",
}
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _policy_key(coverage_id: int) -> str:
    return f"{POLICY_PREFIX}{coverage_id}"


def alert_email(user_id: int) -> str:
    pref = UserPreference.query.filter_by(user_id=user_id, key=EMAIL_PREF_KEY).first()
    if pref and isinstance(pref.value, dict) and str(pref.value.get("email") or "").strip():
        return str(pref.value.get("email") or "").strip().lower()
    user = db.session.get(User, user_id)
    return str(user.email if user else "").strip().lower()


def save_alert_email(user_id: int, email: str, actor_user_id: int) -> str:
    email = str(email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("Enter a valid notification email address.")
    row = UserPreference.query.filter_by(user_id=user_id, key=EMAIL_PREF_KEY).first()
    if row is None:
        row = UserPreference(user_id=user_id, key=EMAIL_PREF_KEY, value={"email": email})
        db.session.add(row)
    else:
        row.value = {"email": email}
    db.session.add(AuditEvent(
        actor_user_id=actor_user_id,
        action="alerts.email.save",
        object_type="user",
        object_id=str(user_id),
        meta={"email_domain": email.rsplit("@", 1)[-1]},
    ))
    db.session.commit()
    return email


def alert_policy(user_id: int, coverage_id: int) -> dict[str, Any]:
    row = UserPreference.query.filter_by(user_id=user_id, key=_policy_key(coverage_id)).first()
    value = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
    rule_ids = []
    for raw in value.get("rule_ids", []) or []:
        try:
            rule_ids.append(int(raw))
        except (TypeError, ValueError):
            pass
    systems = [str(x) for x in (value.get("system_alerts", []) or []) if str(x) in SYSTEM_ALERTS]
    return {"rule_ids": sorted(set(rule_ids)), "system_alerts": sorted(set(systems))}


def save_alert_policy(user_id: int, coverage_id: int, *, rule_ids: list[int], system_alerts: list[str], actor_user_id: int) -> dict[str, Any]:
    allowed_rule_ids = {
        row.id for row in MonitoringRule.query.filter_by(coverage_id=coverage_id, is_active=True).all()
    }
    clean_rules = sorted({int(x) for x in rule_ids if int(x) in allowed_rule_ids})
    clean_system = sorted({str(x) for x in system_alerts if str(x) in SYSTEM_ALERTS})
    value = {"rule_ids": clean_rules, "system_alerts": clean_system}
    row = UserPreference.query.filter_by(user_id=user_id, key=_policy_key(coverage_id)).first()
    if row is None:
        row = UserPreference(user_id=user_id, key=_policy_key(coverage_id), value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.add(AuditEvent(
        actor_user_id=actor_user_id,
        action="alerts.policy.save",
        object_type="coverage",
        object_id=str(coverage_id),
        meta={"recipient_user_id": user_id, **value},
    ))
    db.session.commit()
    return value


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


def _email_allowed(user_id: int, coverage_id: int, rule_id: int | None, trigger_kind: str) -> bool:
    policy = alert_policy(user_id, coverage_id)
    if rule_id is not None:
        return rule_id in policy["rule_ids"]
    return trigger_kind in policy["system_alerts"]


def _emit(*, user: User, coverage: Coverage, security: Security, severity: str, title: str, body: str,
          rule_id: int | None, trigger_kind: str, trigger_meta: dict[str, Any], prefs: dict[str, Any],
          cooldown_hours: int | None = None) -> dict[str, Any]:
    severity = str(severity or "WATCH").upper()
    if not _minimum_allowed(severity, prefs):
        return {"created": False, "reason": "BELOW_MINIMUM_SEVERITY"}
    cooldown = int(cooldown_hours or prefs["cooldown_hours"])
    recent = _recent_alert(user.id, coverage.id, rule_id, title, cooldown)
    if recent:
        return {"created": False, "reason": "DEDUPED", "alert_id": recent.id}
    alert = Alert(
        user_id=user.id, coverage_id=coverage.id, rule_id=rule_id, severity=severity,
        title=title[:220], body=body, is_read=not bool(prefs.get("in_app_enabled", True)),
    )
    db.session.add(alert)
    db.session.flush()
    db.session.add(AuditEvent(
        actor_user_id=user.id,
        action="monitoring.trigger",
        object_type="monitoring_rule" if rule_id else "coverage",
        object_id=str(rule_id or coverage.id),
        meta={"alert_id": alert.id, "ticker": security.ticker, "severity": severity, "trigger_kind": trigger_kind, **trigger_meta},
    ))
    db.session.commit()

    status = "NOT_SELECTED"
    email = alert_email(user.id)
    if email and _email_allowed(user.id, coverage.id, rule_id, trigger_kind):
        sent, status = _send_email(email, security, alert)
        db.session.add(AuditEvent(
            actor_user_id=user.id,
            action="notification.email.sent" if sent else "notification.email.failed",
            object_type="alert",
            object_id=str(alert.id),
            meta={"status": status, "trigger_kind": trigger_kind, "recipient_domain": email.rsplit("@", 1)[-1]},
        ))
        db.session.commit()
    return {"created": True, "alert_id": alert.id, "email": status}


def evaluate_coverage(coverage_id: int, user_id: int) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    user = db.session.get(User, user_id)
    if not coverage or not user or coverage.user_id != user.id:
        return {"coverage_id": coverage_id, "evaluated": False, "reason": "NOT_FOUND_OR_NOT_OWNER"}
    security = db.session.get(Security, coverage.security_id)
    if not security:
        return {"coverage_id": coverage_id, "evaluated": False, "reason": "SECURITY_NOT_FOUND"}
    prefs = notification_preferences(user.id)
    metrics = _metric_map(coverage)
    results: list[dict[str, Any]] = []

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
            db.session.add(MonitoringHistory(rule_id=rule.id, observed_value=observed, status=status, note="Automatic monitoring evaluation"))
            db.session.commit()
        if triggered:
            results.append(_emit(
                user=user, coverage=coverage, security=security, severity=rule.severity or "WATCH",
                title=f"Threshold triggered · {rule.name}",
                body=f"Observed {rule.metric} = {observed:g}{(' ' + rule.unit) if rule.unit else ''}; locked rule: {operator} {threshold:g}{(' ' + rule.unit) if rule.unit else ''}.",
                rule_id=rule.id, trigger_kind="rule",
                trigger_meta={"metric": rule.metric, "operator": operator, "threshold": threshold, "observed": observed, "locked_pre_investment": bool(rule.locked_pre_investment)},
                prefs=prefs,
            ))

    failures = DataQualityIssue.query.filter_by(company_id=security.company_id, status="OPEN").filter(
        DataQualityIssue.severity.in_(["FAIL", "CRITICAL"])
    ).all()
    if failures:
        results.append(_emit(
            user=user, coverage=coverage, security=security, severity="FAIL",
            title="Data-quality failure requires review",
            body=f"{len(failures)} open FAIL/CRITICAL data-quality issue(s).",
            rule_id=None, trigger_kind="data_quality", trigger_meta={"open_failures": len(failures)}, prefs=prefs,
        ))

    filing_date = _next_filing_estimate(security.company_id)
    if filing_date is not None:
        days = (filing_date - date.today()).days
        if 0 <= days <= prefs["filing_lead_days"]:
            results.append(_emit(
                user=user, coverage=coverage, security=security, severity="INFO",
                title="Estimated filing window approaching",
                body=f"Estimated next SEC filing window is {filing_date.isoformat()} ({days} day(s)). Refresh fundamentals when the filing lands.",
                rule_id=None, trigger_kind="filing", trigger_meta={"estimated_filing_date": filing_date.isoformat(), "days": days}, prefs=prefs, cooldown_hours=72,
            ))

    price, bear, bull = (metrics.get(k) for k in ("price", "bear", "bull"))
    if price is not None and bear is not None and bull is not None:
        if price >= bull:
            results.append(_emit(
                user=user, coverage=coverage, security=security, severity="WATCH",
                title="Price reached/exceeded Bull fair value",
                body=f"Price {price:.2f} is at/above Bull {bull:.2f}. Revisit expected return; do not move fair value because price moved.",
                rule_id=None, trigger_kind="valuation", trigger_meta={"price": price, "bear": bear, "bull": bull, "zone": "ABOVE_BULL"}, prefs=prefs,
            ))
        elif price <= bear:
            results.append(_emit(
                user=user, coverage=coverage, security=security, severity="WATCH",
                title="Price reached/breached Bear fair value",
                body=f"Price {price:.2f} is at/below Bear {bear:.2f}. Test evidence and invalidation before acting.",
                rule_id=None, trigger_kind="valuation", trigger_meta={"price": price, "bear": bear, "bull": bull, "zone": "BELOW_BEAR"}, prefs=prefs,
            ))

    return {
        "coverage_id": coverage.id,
        "ticker": security.ticker,
        "evaluated": True,
        "created_alerts": sum(1 for row in results if row.get("created")),
        "results": results,
        "smtp_ready": _smtp_ready(),
    }


def evaluate_user(user_id: int) -> dict[str, Any]:
    rows = [evaluate_coverage(c.id, user_id) for c in Coverage.query.filter_by(user_id=user_id).order_by(Coverage.id).all()]
    return {"user_id": user_id, "coverages": len(rows), "created_alerts": sum(row.get("created_alerts", 0) for row in rows), "results": rows}


__all__ = [
    "EMAIL_PREF_KEY", "POLICY_PREFIX", "SYSTEM_ALERTS", "alert_email", "save_alert_email", "alert_policy",
    "save_alert_policy", "evaluate_coverage", "evaluate_user",
]
