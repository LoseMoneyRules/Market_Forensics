# Security Baseline — Mandatory from v0.0.1

- Never store plaintext or recoverable passwords.
- Passwords use Argon2 hashing.
- Two-factor authentication is mandatory for every account.
- CONTROL receives the strongest authorization enforcement; no UI-only bypasses.
- TOTP secrets are encrypted at rest with a server-side encryption key.
- Production secrets live only in hosting environment variables, never GitHub.
- HTTPS-only production sessions, HttpOnly cookies and SameSite protection.
- Server-side role authorization for FRIEND / INSIDER / CONTROL.
- Invite-only onboarding during the beta.
- Rate limiting / brute-force protection.
- Revocable sessions and additional security controls are expanded through the 0.x line.
- Audit log for security/admin/research access events.
- Database and application backups with a tested recovery procedure before v1.0.0.
- Data sources carry publication-rights metadata before externally exposing licensed/restricted data.
