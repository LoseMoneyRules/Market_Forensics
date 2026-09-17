# Market Forensics 0.1.0

Market Forensics 0.1.0 is the web-native implementation of the Market Forensics research process.

The production application uses the existing account/security foundation and a new MariaDB research core. V3.1.12 remains a design/reference artifact only; it is not imported into the 0.1.0 request path.

## Runtime model

`INGEST → NORMALIZE → CALCULATE → STORE → DISPLAY`

Page GETs read stored data and render HTML. Heavy refreshes are queued in `mf_job` and executed by `python manage.py run-jobs --limit 5`, which is compatible with cPanel cron and does not require permanent workers.

## Production requirements

- Python / Passenger / WSGI
- MariaDB via `MF_DATABASE_URL`
- `MF_SECRET_KEY`
- existing `MF_ENCRYPTION_KEY` (do not rotate during migration unless encrypted secrets are deliberately re-encrypted)
- optional provider credentials stored through the encrypted settings UI

## Version

`Market Forensics 0.1.0`
