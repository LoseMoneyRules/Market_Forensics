# Market Forensics

**Current build: v0.0.1**

Market Forensics is being rebuilt as a private, responsive research platform for phone, iPad/tablet and desktop. The product name is simply **Market Forensics**.

## v0.0.1 scope

- invite-only accounts
- FRIEND / INSIDER / CONTROL access roles
- mandatory password + authenticator 2FA
- Argon2 password hashing; no plaintext/recoverable passwords
- encrypted TOTP secrets
- server-side role enforcement
- private CONTROL area
- FRIEND/INSIDER published research views
- immutable-publication data model foundation
- audit events
- responsive mobile/tablet/desktop shell
- no purple in the design system
- MariaDB-ready production configuration; SQLite for local development
- Namecheap Passenger/WSGI entrypoint

The v0.0.1 research content is intentionally minimal. Its job is to prove the platform, access control, security, responsive UI and deployment path before the full V3 research engine is migrated.

## Local start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py generate-secrets
# copy the generated values into environment variables or .env
python manage.py bootstrap-admin --email you@example.com --name "Your Name"
python manage.py seed-demo
python app.py
```

Open `http://127.0.0.1:5000`.

## Production

See `DEPLOY_NAMECHEAP.md`.
