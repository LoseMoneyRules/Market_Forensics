# Market Forensics 0.1.0 — Namecheap deployment

Production remains on the existing cPanel Python App / Passenger / WSGI stack and the existing MariaDB database.

## Preserve before first 0.1.0 deployment

Do not rotate or replace `MF_SECRET_KEY`, `MF_ENCRYPTION_KEY`, `MF_DATABASE_URL`, the database, or the existing CONTROL account. 0.1.0 deliberately reuses the 0.0.4 account/security tables so passwords, TOTP enrollment, roles, user IDs and encrypted API credentials survive the upgrade.

## Release path

The production workflow is manual and deploys **main** only:

1. tests must pass;
2. the lightweight application payload is uploaded over FTPES;
3. Passenger is restarted;
4. `/health` must report version `0.1.0` and architecture `web-native`.

A startup migration failure prevents a healthy application response. The legacy research conversion is recorded once in `mf_schema_migration` and is not a permanent dual-write system.

## Cron jobs

Heavy work is queued in `mf_job` and should be drained by cPanel cron. A practical shared-hosting schedule is every 5 minutes:

```bash
cd /home/ACCOUNT/PYTHON_APP_ROOT && /home/ACCOUNT/virtualenv/PYTHON_APP_ROOT/3.13/bin/python manage.py run-jobs --limit 5
```

No daemon, resident worker, Redis, Celery, or long-lived background process is required.

## Runtime principle

Normal page requests read already persisted MariaDB state. API ingestion, SEC refresh, FINRA imports, scans and bulk work belong in jobs. Fast form saves and deterministic Bear/Base/Bull recalculation may execute in-request.
