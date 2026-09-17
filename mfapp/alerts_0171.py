from __future__ import annotations

"""0.1.7 correction: notification email is always the account email.

The earlier 0.1.7 implementation allowed a second per-user notification email.
That creates unnecessary identity drift. This compatibility shim keeps the old
routes callable but makes the registered account email the single source of
truth for alert delivery.
"""

from .extensions import db
from .models import User


def account_email(user_id: int) -> str:
    user = db.session.get(User, user_id)
    return str(user.email if user else "").strip().lower()


def keep_account_email(user_id: int, _email: str, _actor_user_id: int) -> str:
    return account_email(user_id)


def install_account_email_policy() -> None:
    from . import monitoring_017, routes_017

    # Runtime delivery and subscription surfaces resolve these globals at call time.
    monitoring_017.alert_email = account_email
    monitoring_017.save_alert_email = keep_account_email

    # routes_017 imported these functions by name, so keep its aliases aligned too.
    routes_017.alert_email = account_email
    routes_017.save_alert_email = keep_account_email


__all__ = ["account_email", "install_account_email_policy"]
