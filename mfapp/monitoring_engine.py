from __future__ import annotations

import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from math import isfinite
from typing import Any

from .core_models import (
    Alert,
    Coverage,
    DataQualityIssue,
    FinancialPeriod,
    MonitoringHistory,
    MonitoringRule,
    Security,
)
from .current_financials import current_row
from .data_providers import latest_snapshot
from .extensions import db
from .finra import stored_summary as finra_stored_summary
from .models import AuditEvent, User, UserPreference
from .services import valuation_result


NOTIFICATION_PREF_KEY = "notifications"
DEFAULT_PREFS = {
    "email_enabled": True,
    "in_app_enabled": True,
    "cooldown_hours": 24,
    "filing_lead_days": 7,
    "minimum_severity": "WATCH",
}
SEVERITY_RANK = {"INFO": 1, "WATCH": 2, "FAIL": 3, "CRITICAL": 4}
NUMERIC_OPERATORS = {"<", "<=", ">", ">=", "==", "!="}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def notification_preferences(user_id: int) -> dict[str, Any]:
    row = UserPreference.query.filter_by(user_id=user_id, key=NOTIFICATION_PREF_KEY).first()
    value = dict(DEFAULT_PREFS)
    if row and isinstance(row.value, dict):
        value.update(row.value)
    value["cooldown_hours"] = max(1, min(int(value.get("cooldown_hours") or 24), 168))
    value["filing_lead_days"] = max(1, min(int(value.get("filing_lead_days") or 7), 30))
    value["minimum_severity"] = str(value.get("minimum_severity") or "WATCH").upper()
    return value


def save_notification_preferences(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    current = notification_preferences(user_id)
    current.update({
        "email_enabled": bool(payload.get("email_enabled")),
        "in_app_enabled": bool(payload.get("in_app_enabled", True)),
        "cooldown_hours": max(1, min(int(payload.get("cooldown_hours") or 24), 168)),
        "filing_lead_days": max(1, min(int(payload.get("filing_lead_days") or 7), 30)),
        "minimum_severity": str(payload.get("minimum_severity") or "WATCH").upper(),
    })
    if current["minimum_severity"] not in SEVERITY_RANK:
        current["minimum_severity"] = "WATCH"
    row = UserPreference.query.filter_by(user_id=user_id, key=NOTIFICATION_PREF_KEY).first()
    if row is None:
        row = UserPreference(user_id=user_id, key=NOTIFICATION_PREF_KEY, value=current)
        db.session.add(row)
    else:
        row.value = current
    db.session.add(AuditEvent(actor_user_id=user_id, action="notifications.preferences.save", object_type="user", object_id=str(user_id), meta={k: v for k, v in current.items() if k != "smtp_password"}))
    db.session.commit()
    return current


def _compare(observed: float, operator: str, threshold: float) -> bool:
    if operator == "<": return observed < threshold
    if operator == "<=": return observed <= threshold
    if operator == ">": return observed > threshold
    if operator == ">=": return observed >= threshold
    if operator == "==": return abs(observed - threshold) <= max(1e-9, abs(threshold) * 1e-9)
    if operator == "!=": return abs(observed - threshold) > max(1e-9, abs(threshold) * 1e-9)
    return False


def _metric_map(coverage: Coverage) -> dict[str, float | None]:
    security = db.session.get(Security, coverage.security_id)
    if not security:
        return {}
    current = current_row(security.company_id) or {}
    metrics = dict(current.get("metrics") or {})
    market = latest_snapshot(security.id)
    valuation = valuation_result(coverage)
    out: dict[str, float | None] = {str(k).lower(): _num(v) for k, v in metrics.items()}
    out.update({
        "price": _num(market.price) if market else None,
        "current_price": _num(market.price) if market else None,
        "revenue": _num(current.get("revenue")),
        "fcf": _num(current.get("fcf")),
        "reported_fcf": _num(current.get("fcf")),
        "fcf_after_sbc": _num(metrics.get("fcf_after_sbc")),
        "owner_cash_proxy": _num(metrics.get("owner_cash_proxy")),
        "economic_net_debt": _num(metrics.get("economic_net_debt")),
        "net_income": _num(current.get("net_income")),
        "bear": _num(valuation.get("bear")),
        "base": _num(valuation.get("base")),
        "bull": _num(valuation.get("bull")),
        "expected_value": _num(valuation.get("expected_value")),
    })
    return out


def _minimum_allowed(severity: str, prefs: dict[str, Any]) -> bool:
    return SEVERITY_RANK.get(str(severity).upper(), 2) >= SEVERITY_RANK.get(str(prefs.get("minimum_severity") or "WATCH").upper(), 2)


def _recent_alert(user_id: int, coverage_id: int, rule_id: int | None, title: str, cooldown_hours: int) -> Alert | None:
    cutoff = utcnow() - timedelta(hours=cooldown_hours)
    query = Alert.query.filter(
        Alert.user_id == user_id,
        Alert.coverage_id == coverage_id,
        Alert.title == title,
        Alert.created_at >= cutoff,
    )
    query = query.filter(Alert.rule_id == rule_id) if rule_id is not None else query.filter(Alert.rule_id.is_(None))
    return query.order_by(Alert.created_at.desc()).first()


def _smtp_ready() -> bool:
    return bool(os.environ.get("MF_SMTP_HOST", "").strip() and os.environ.get("MF_SMTP_FROM", "").strip())


def _deliver_email(user: User, security: Security, alert: Alert) -> tuple[bool, str]:
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
    msg["To"] = user.email
    msg.set_content(
        f"Market Forensics monitoring alert\n\n"
        f"Ticker: {security.ticker}\n"
        f"Severity: {alert.severity}\n"
        f"Alert: {alert.title}\n\n{alert.body}\n\n"
        "This message was generated from CONTROL monitoring evidence. "
        "ADD ON EVIDENCE, NOT ON PRICE."
    )
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                if username: smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if use_starttls: smtp.starttls()
                if username: smtp.login(username, password)
                smtp.send_message(msg)
        return True, "SENT"
    except Exception as exc:  # delivery failure must never break monitoring persistence
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _emit_alert(*, user: User, coverage: Coverage, security: Security, severity: str, title: str, body: str,
                rule_id: int | None, trigger_meta: dict[str, Any], prefs: dict[str, Any], cooldown_hours: int | None = None) -> dict[str, Any]:
    severity = str(severity or "WATCH").upper()
    if not _minimum_allowed(severity, prefs):
        return {"created": False, "reason": "BELOW_MINIMUM_SEVERITY"}
    cooldown = int(cooldown_hours or prefs["cooldown_hours"])
    recent = _recent_alert(user.id, coverage.id, rule_id, title, cooldown)
    if recent:
        return {"created": False, "reason": "DEDUPED", "alert_id": recent.id}

    alert = Alert(user_id=user.id, coverage_id=coverage.id, rule_id=rule_id, severity=severity, title=title[:220], body=body)
    if prefs.get("in_app_enabled", True):
        db.session.add(alert); db.session.flush()
    else:
        # Alert remains the immutable trigger record even when the in-app surface is disabled.
        db.session.add(alert); db.session.flush(); alert.is_read = True
    db.session.add(AuditEvent(
        actor_user_id=user.id,
        action="monitoring.trigger",
        object_type="monitoring_rule" if rule_id else "coverage",
        object_id=str(rule_id or coverage.id),
        meta={"alert_id": alert.id, "ticker": security.ticker, "severity": severity, "title": title, **trigger_meta},
    ))
    db.session.commit()

    email_status = "DISABLED"
    if prefs.get("email_enabled") and str(user.role or "").upper() == "CONTROL":
        sent, email_status = _deliver_email(user, security, alert)
        db.session.add(AuditEvent(
            actor_user_id=user.id,
            action="notification.email.sent" if sent else "notification.email.skipped_or_failed",
            object_type="alert",
            object_id=str(alert.id),
            meta={"ticker": security.ticker, "status": email_status},
        ))
        db.session.commit()
    return {"created": True, "alert_id": alert.id, "email": email_status}


def _next_filing_estimate(company_id: int) -> date | None:
    latest = FinancialPeriod.query.filter(
        FinancialPeriod.company_id == company_id,
        FinancialPeriod.filed_at.is_not(None),
    ).order_by(FinancialPeriod.filed_at.desc(), FinancialPeriod.id.desc()).first()
    if not latest or not latest.filed_at:
        return None
    estimate = latest.filed_at + timedelta(days=91)
    today = date.today()
    while estimate < today:
        estimate += timedelta(days=91)
    return estimate


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
        last_status = str(last.status or "").upper() if last else ""
        status = "FAIL" if triggered else "OK"
        if not last or last_status != status or (utcnow() - last.observed_at) >= timedelta(hours=prefs["cooldown_hours"]):
            db.session.add(MonitoringHistory(rule_id=rule.id, observed_value=observed, status=status,
                                             note="Automatic 0.3.0 monitoring evaluation"))
            db.session.commit()
        if triggered:
            results.append(_emit_alert(
                user=user, coverage=coverage, security=security, severity=rule.severity or "WATCH",
                title=f"Threshold triggered · {rule.name}",
                body=f"Observed {rule.metric} = {observed:g}{(' ' + rule.unit) if rule.unit else ''}; "
                     f"locked rule: {operator} {threshold:g}{(' ' + rule.unit) if rule.unit else ''}.",
                rule_id=rule.id,
                trigger_meta={"metric": rule.metric, "operator": operator, "threshold": threshold, "observed": observed,
                              "locked_pre_investment": bool(rule.locked_pre_investment)},
                prefs=prefs,
            ))

    open_failures = DataQualityIssue.query.filter_by(company_id=security.company_id, status="OPEN").filter(
        DataQualityIssue.severity.in_(["FAIL", "CRITICAL"])
    ).order_by(DataQualityIssue.detected_at.desc()).all()
    if open_failures:
        sample = "; ".join(f"{row.code}: {row.message[:120]}" for row in open_failures[:3])
        results.append(_emit_alert(
            user=user, coverage=coverage, security=security, severity="FAIL",
            title="Data-quality failure requires review",
            body=f"{len(open_failures)} open FAIL/CRITICAL data-quality issue(s). {sample}",
            rule_id=None, trigger_meta={"open_failures": len(open_failures), "codes": [row.code for row in open_failures[:10]]},
            prefs=prefs,
        ))

    filing_date = _next_filing_estimate(security.company_id)
    if filing_date is not None:
        days = (filing_date - date.today()).days
        if 0 <= days <= prefs["filing_lead_days"]:
            results.append(_emit_alert(
                user=user, coverage=coverage, security=security, severity="INFO",
                title="Estimated filing window approaching",
                body=f"Estimated next SEC filing window is {filing_date.isoformat()} ({days} day(s)). "
                     "Refresh fundamentals when the filing lands and re-evaluate evidence hashes.",
                rule_id=None, trigger_meta={"estimated_filing_date": filing_date.isoformat(), "days": days},
                prefs=prefs, cooldown_hours=72,
            ))

    price, bear, base, bull = (metrics.get(k) for k in ("price", "bear", "base", "bull"))
    if price is not None and all(v is not None for v in (bear, base, bull)):
        if price >= bull:
            results.append(_emit_alert(
                user=user, coverage=coverage, security=security, severity="WATCH",
                title="Price reached/exceeded Bull fair value",
                body=f"Price {price:.2f} is at/above Bull {bull:.2f}. Revisit expected return and evidence; do not move fair value because price moved.",
                rule_id=None, trigger_meta={"price": price, "bear": bear, "base": base, "bull": bull, "zone": "ABOVE_BULL"}, prefs=prefs,
            ))
        elif price <= bear:
            results.append(_emit_alert(
                user=user, coverage=coverage, security=security, severity="WATCH",
                title="Price reached/breached Bear fair value",
                body=f"Price {price:.2f} is at/below Bear {bear:.2f}. Treat this as a valuation event, then test whether evidence or invalidation changed before acting.",
                rule_id=None, trigger_meta={"price": price, "bear": bear, "base": base, "bull": bull, "zone": "BELOW_BEAR"}, prefs=prefs,
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
    rows = []
    for coverage in Coverage.query.filter(Coverage.user_id == user_id, Coverage.status != "ARCHIVED").order_by(Coverage.id).all():
        rows.append(evaluate_coverage(coverage.id, user_id))
    return {"user_id": user_id, "coverages": len(rows), "created_alerts": sum(row.get("created_alerts", 0) for row in rows), "results": rows}


def monitoring_interpretation(coverage: Coverage) -> dict[str, Any]:
    security = db.session.get(Security, coverage.security_id)
    if not security:
        return {"legend": [], "signals": [], "flip": "Security unavailable."}
    current = current_row(security.company_id) or {}
    metrics = dict(current.get("metrics") or {})
    valuation = valuation_result(coverage)
    market = latest_snapshot(security.id)
    price = _num(market.price) if market else None
    bear, bull = _num(valuation.get("bear")), _num(valuation.get("bull"))
    signals: list[dict[str, str]] = []

    def add(name: str, state: str, detail: str, flip: str) -> None:
        signals.append({"name": name, "state": state, "detail": detail, "flip": flip})

    if price is not None and bear is not None and bull is not None:
        state = "BULLISH" if price < bear else ("BEARISH" if price > bull else "NEUTRAL")
        add("Valuation zone", state, f"Price {price:.2f} vs Bear {bear:.2f} / Bull {bull:.2f}.",
            "Valuation alone never flips the thesis; require company evidence or a locked invalidation trigger.")

    growth = _num(metrics.get("revenue_growth_pct"))
    if growth is not None:
        add("Revenue direction", "BULLISH" if growth > 0 else ("BEARISH" if growth < 0 else "NEUTRAL"),
            f"Current revenue growth {growth:+.1f}%.", "Flip direction when filing evidence crosses the committed growth threshold, not on price action.")
    fcf_margin = _num(metrics.get("fcf_margin_pct"))
    suppressions = set(metrics.get("economic_reality_suppressions") or [])
    owner_cash = _num(metrics.get("owner_cash_proxy"))
    fcf_after_sbc = _num(metrics.get("fcf_after_sbc"))
    reported_fcf = _num(current.get("fcf"))
    if fcf_margin is not None:
        if "NEGATIVE_FCF_AUTOMATIC" in suppressions and reported_fcf is not None and reported_fcf < 0 and owner_cash is not None and owner_cash > 0:
            add("Cash conversion", "NEUTRAL",
                f"Reported FCF margin {fcf_margin:+.1f}% includes material growth reinvestment; owner-cash proxy remains positive at {owner_cash:,.0f}.",
                "Treat deterioration as bearish only if owner-cash/reinvestment economics also weaken.")
        elif "FCF_POSITIVE_UNADJUSTED" in suppressions and fcf_after_sbc is not None:
            adjusted_margin = (fcf_after_sbc / _num(current.get("revenue")) * 100.0) if _num(current.get("revenue")) not in (None, 0) else None
            state = "BULLISH" if adjusted_margin is not None and adjusted_margin > 0 else "BEARISH"
            detail = f"Reported FCF margin {fcf_margin:+.1f}%; FCF after SBC {fcf_after_sbc:,.0f}" + (f" ({adjusted_margin:+.1f}% of revenue)." if adjusted_margin is not None else ".")
            add("Cash conversion", state, detail, "Monitor the post-SBC cash basis, not reported FCF alone.")
        else:
            add("Cash conversion", "BULLISH" if fcf_margin > 0 else ("BEARISH" if fcf_margin < 0 else "NEUTRAL"),
                f"Current FCF margin {fcf_margin:+.1f}%.", "A sustained threshold breach or recovery should change the evidence state.")

    finra = finra_stored_summary(security.company_id)
    s5 = _num(finra.get("daily_5d_short_pct")); s20 = _num(finra.get("daily_20d_short_pct"))
    if s5 is not None and s20 is not None:
        spread = (s5 - s20) * 100
        state = "BEARISH" if spread >= 5 else ("BULLISH" if spread <= -5 else "NEUTRAL")
        add("Short-flow pressure", state, f"5d short-volume share vs 20d: {spread:+.1f} pts.",
            "Flow is context only; it cannot by itself flip intrinsic thesis direction.")

    locked = MonitoringRule.query.filter_by(coverage_id=coverage.id, is_active=True, locked_pre_investment=True).all()
    flip = (
        "A bull/long thesis can flip only when locked invalidation evidence is triggered and confirmed by the underlying filing/operating evidence; "
        "a bear/short thesis requires the symmetric recovery/re-acceleration evidence. Price and short flow alone are not sufficient."
    )
    return {
        "legend": [
            {"state": "BULLISH", "meaning": "Evidence improves the long/bull case."},
            {"state": "BEARISH", "meaning": "Evidence strengthens the short/bear case or weakens the long case."},
            {"state": "NEUTRAL", "meaning": "No material thesis-direction change."},
        ],
        "signals": signals,
        "locked_invalidation_rules": len(locked),
        "flip": flip,
    }


__all__ = [
    "DEFAULT_PREFS", "NUMERIC_OPERATORS", "notification_preferences", "save_notification_preferences",
    "evaluate_coverage", "evaluate_user", "monitoring_interpretation",
]
