from __future__ import annotations

import argparse
import getpass
from pathlib import Path

import pyotp
import qrcode
from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.extensions import db
from mfapp.models import User
from mfapp.security import encrypt_secret, hash_password


def generate_secrets() -> None:
    import secrets
    print("MF_SECRET_KEY=" + secrets.token_urlsafe(48))
    print("MF_ENCRYPTION_KEY=" + Fernet.generate_key().decode("ascii"))


def bootstrap_admin(email: str, name: str) -> None:
    """Installation-only helper. Existing CONTROL accounts are never reset by migrations."""
    app = create_app({"AUTO_MIGRATE": True})
    with app.app_context():
        if User.query.filter_by(email=email.lower().strip()).first():
            raise SystemExit("User already exists. Market Forensics does not reset accounts or 2FA.")
        password = getpass.getpass("Admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match.")
        if len(password) < 12:
            raise SystemExit("Use at least 12 characters.")
        secret = pyotp.random_base32()
        user = User(email=email.lower().strip(), display_name=name.strip(), role="CONTROL", password_hash=hash_password(password), totp_secret_enc=encrypt_secret(secret), is_active=True)
        db.session.add(user); db.session.commit()
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Market Forensics")
        out = Path("instance"); out.mkdir(exist_ok=True)
        path = out / "control-2fa.png"; qrcode.make(uri).save(path)
        print(f"CONTROL user created: {user.email}")
        print(f"QR code saved to: {path.resolve()}")
        print("Delete the QR file after enrolling your authenticator.")


def migrate() -> None:
    from mfapp.schema import bootstrap_schema
    app = create_app({"AUTO_MIGRATE": False})
    with app.app_context():
        print(bootstrap_schema(migrate_legacy=True))


def _run_monitoring_for_controls() -> None:
    from mfapp.alert_engine import evaluate_all
    print({"monitoring": evaluate_all()})


def _acquire_job_executor_lock():
    """Single executor across cron and browser fallback processes."""
    lock_dir = Path("instance")
    lock_dir.mkdir(exist_ok=True)
    handle = open(lock_dir / "job-executor.lock", "a+", encoding="utf-8")
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        return handle
    except BlockingIOError:
        handle.close()
        return None
    return handle


def run_jobs(limit: int, user_id: int | None = None) -> None:
    from mfapp.jobs import run_jobs as execute
    lock = _acquire_job_executor_lock()
    if lock is None:
        print({"executor": "busy", "message": "Another Market Forensics job executor owns the lock."})
        return
    try:
        app = create_app({"AUTO_MIGRATE": True})
        with app.app_context():
            for result in execute(limit=limit, user_id=user_id):
                print(result)
            # Monitoring remains part of unattended cron runs. Browser fallback
            # processes are user-scoped and must stay short.
            if user_id is None:
                _run_monitoring_for_controls()
    finally:
        try:
            lock.close()
        except Exception:
            pass


def run_monitoring() -> None:
    app = create_app({"AUTO_MIGRATE": True})
    with app.app_context():
        _run_monitoring_for_controls()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate-secrets")
    p = sub.add_parser("bootstrap-admin"); p.add_argument("--email", required=True); p.add_argument("--name", default="Control")
    sub.add_parser("migrate")
    jobs = sub.add_parser("run-jobs"); jobs.add_argument("--limit", type=int, default=5); jobs.add_argument("--user-id", type=int)
    sub.add_parser("monitor")
    args = parser.parse_args()
    if args.cmd == "generate-secrets": generate_secrets()
    elif args.cmd == "bootstrap-admin": bootstrap_admin(args.email, args.name)
    elif args.cmd == "migrate": migrate()
    elif args.cmd == "run-jobs": run_jobs(args.limit, args.user_id)
    elif args.cmd == "monitor": run_monitoring()


if __name__ == "__main__":
    main()
