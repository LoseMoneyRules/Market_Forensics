# Market Forensics 0.1.0 — Namecheap deployment

Production remains on the existing cPanel Python App / Passenger / WSGI stack and the existing MariaDB database.

## Preserve before first 0.1.0 deployment

Do not rotate or replace `MF_SECRET_KEY`, `MF_ENCRYPTION_KEY`, `MF_DATABASE_URL`, the database, or the existing CONTROL account. 0.1.0 deliberately reuses the 0.0.4 account/security tables so passwords, TOTP enrollment, roles, user IDs and encrypted API credentials survive the upgrade.

The deployment cleanup must also preserve hosting/runtime state that is not part of the Git release: `.env`, the Flask `instance/` directory, `tmp/` (Passenger restart state), Passenger/cPanel configuration and the MariaDB database itself.

## Clean production payload

The production server contains only the active 0.1.0 runtime managed by the release:

- `app.py`
- `manage.py`
- `requirements.txt`
- `VERSION`
- `mfapp/`

The first 0.1.0 cutover explicitly removes the old hosted V3/0.0.4 runtime (`market_forensics/`, `mfengine/`, `_vendor/`, deployed `docs/`, `requirements-dev.txt`, `requirements-vendor.txt`, and deployed `README.md`). `mfapp/` is synchronized with `mirror --reverse --delete`, so deleted legacy modules, templates, JavaScript and CSS are also removed remotely.

Historical V3.1.12 source/reference material remains in GitHub only and is not part of the Namecheap production payload.

## Release path

The production workflow is manual and deploys **main** only:

1. focused 0.1.0 tests and compile checks must pass;
2. an explicit minimal production payload is built;
3. obsolete 0.0.4/V3 hosted files are removed;
4. the active runtime is uploaded over FTPES;
5. Passenger is restarted;
6. `/health` must report version `0.1.0` and architecture `web-native`.

A startup migration failure prevents a healthy application response. The legacy research conversion is recorded once in `mf_schema_migration` and is not a permanent dual-write system.

## Cron jobs

Heavy work is queued in `mf_job` and should be drained by cPanel cron. A practical shared-hosting schedule is every 5 minutes:

```bash
cd /home/ACCOUNT/PYTHON_APP_ROOT && /home/ACCOUNT/virtualenv/PYTHON_APP_ROOT/3.13/bin/python manage.py run-jobs --limit 5
```

No daemon, resident worker, Redis, Celery, or long-lived background process is required.

## Runtime principle

Normal page requests read already persisted MariaDB state. API ingestion, SEC refresh, FINRA imports, scans and bulk work belong in jobs. Fast form saves and deterministic Bear/Base/Bull recalculation may execute in-request.
