# Market Forensics 0.1.6 — Namecheap deployment

Production remains on the existing cPanel Python App / Passenger / WSGI stack and the existing MariaDB database.

## Preserve on every deployment

Do not rotate or replace `MF_SECRET_KEY`, `MF_ENCRYPTION_KEY`, `MF_DATABASE_URL`, the database, or the existing CONTROL account. The web-native line deliberately preserves account/security tables so passwords, TOTP enrollment, roles, user IDs and encrypted API credentials survive releases.

Deployment cleanup must also preserve hosting/runtime state that is not part of the Git release: `.env`, the Flask `instance/` directory, `tmp/` (Passenger restart state), Passenger/cPanel configuration and the MariaDB database itself.

## Clean production payload

The production server contains only the active web-native runtime managed by the release:

- `app.py`
- `manage.py`
- `requirements.txt`
- `VERSION`
- `mfapp/`

Historical V3.1.12 source/reference material remains in GitHub only and is not part of the Namecheap production payload. The deployed runtime must not depend on `mfengine/v312` or SQLite.

## Release path

The production workflow is **manual** (`workflow_dispatch`) and always checks out **main**. Merging a release into `main` does not by itself prove that production changed.

For 0.1.6:

1. release-branch CI and PR checks must be green;
2. merge the reviewed release into `main`;
3. main CI must be green;
4. manually dispatch `Deploy Market Forensics to Namecheap`;
5. the deploy workflow reruns tests and compile checks before upload;
6. an explicit minimal production payload is built and uploaded over FTPES;
7. Passenger is restarted;
8. `/health` must report version `0.1.6` and architecture `web-native` before the release is called deployed.

A startup migration failure prevents a healthy application response. Research/account state remains in the existing MariaDB primary database; there is no permanent dual-write system.

## Monitoring cron

Heavy work is queued in `mf_job` and drained by cPanel cron. In 0.1.6 the same unattended command also evaluates CONTROL monitoring rules after the job queue pass, including threshold, data-quality, valuation/invalidation and filing-window checks.

A practical shared-hosting schedule is every 5 minutes:

```bash
cd /home/ACCOUNT/PYTHON_APP_ROOT && /home/ACCOUNT/virtualenv/PYTHON_APP_ROOT/3.13/bin/python manage.py run-jobs --limit 5
```

For a monitoring-only manual diagnostic run:

```bash
cd /home/ACCOUNT/PYTHON_APP_ROOT && /home/ACCOUNT/virtualenv/PYTHON_APP_ROOT/3.13/bin/python manage.py monitor
```

No daemon, resident worker, Redis, Celery, or long-lived background process is required.

## CONTROL email notifications

In-app alerts require no extra service. Email delivery is optional and reads SMTP configuration only from the production environment; never commit credentials.

Supported variables:

- `MF_SMTP_HOST`
- `MF_SMTP_PORT` (default `587`)
- `MF_SMTP_USER`
- `MF_SMTP_PASSWORD`
- `MF_SMTP_FROM`
- `MF_SMTP_STARTTLS` (`1` by default)
- `MF_SMTP_SSL` (`0` by default)

If SMTP is not configured, trigger records and audit events are still persisted and email is safely skipped. Email is sent only to CONTROL and uses the configured cooldown/deduplication policy.

## Runtime principle

Normal page requests read already persisted MariaDB state. API ingestion, SEC refresh, FINRA imports, scans and bulk work belong in jobs. Fast form saves and deterministic Bear/Base/Bull recalculation may execute in-request. Manual research edits remain authoritative and must not be overwritten by autofill.