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
    app = create_app()
    with app.app_context():
        db.create_all()
        if User.query.filter_by(email=email.lower().strip()).first():
            raise SystemExit("User already exists.")
        password = getpass.getpass("Admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match.")
        if len(password) < 12:
            raise SystemExit("Use at least 12 characters.")
        secret = pyotp.random_base32()
        user = User(email=email.lower().strip(), display_name=name.strip(), role="CONTROL", password_hash=hash_password(password), totp_secret_enc=encrypt_secret(secret), is_active=True)
        db.session.add(user)
        db.session.commit()
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Market Forensics")
        out = Path("instance")
        out.mkdir(exist_ok=True)
        path = out / "control-2fa.png"
        qrcode.make(uri).save(path)
        print(f"CONTROL user created: {user.email}")
        print(f"Authenticator secret: {secret}")
        print(f"QR code saved to: {path.resolve()}")
        print("Delete the QR file after enrolling your authenticator.")


def seed_demo() -> None:
    from mfapp.models import Company, Publication
    app = create_app()
    with app.app_context():
        db.create_all()
        if Company.query.filter_by(ticker="MFCO").first():
            print("Demo already exists.")
            return
        c = Company(ticker="MFCO", name="Market Forensics Demo Co.", status="INVESTIGATE", score=78, summary="Demonstration research record for testing role-based visibility. Not a real security.")
        db.session.add(c)
        db.session.flush()
        p = Publication(company_id=c.id, version=1, visibility="FRIEND", is_current=True, payload={"business":"Demo business-quality review with selected evidence.","numbers":"Demo financial trend section.","expectations":"Demo expectations gap available to INSIDER and CONTROL.","valuation":"Demo valuation framework; values are intentionally fictional.","bear_case":"Demo bear case for access-control testing.","catalysts":"Demo catalyst monitoring list.","flows":"Demo flows section available to INSIDER and CONTROL.","risk":"Demo risk framework.","monitoring":"Demo monitoring checklist.","sources":"Demo source ledger.","disclosure":"Demonstration only — not investment research on a real security."})
        db.session.add(p)
        db.session.commit()
        print("Demo publication created.")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate-secrets")
    p = sub.add_parser("bootstrap-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Control")
    sub.add_parser("seed-demo")
    args = parser.parse_args()
    if args.cmd == "generate-secrets":
        generate_secrets()
    elif args.cmd == "bootstrap-admin":
        bootstrap_admin(args.email, args.name)
    elif args.cmd == "seed-demo":
        seed_demo()


if __name__ == "__main__":
    main()
