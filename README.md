# Market Forensics 0.1.1

Market Forensics 0.1.1 is the web-native implementation of the Market Forensics research process.

The production application uses the existing account/security foundation and a new MariaDB research core. V3.1.12 remains a design/reference artifact only; it is not imported into the current request path.

## Runtime model

`INGEST → NORMALIZE → CALCULATE → STORE → DISPLAY`

Page GETs read stored data and render HTML. Heavy refreshes are queued in `mf_job`. While CONTROL is open, the authenticated browser worker advances due jobs one at a time; `python manage.py run-jobs --limit 5` remains the unattended cPanel cron worker. Equivalent QUEUED/RUNNING jobs are deduplicated by user, job type and target so repeated clicks do not create duplicate work.

## Production requirements

- Python / Passenger / WSGI
- MariaDB via `MF_DATABASE_URL`
- `MF_SECRET_KEY`
- existing `MF_ENCRYPTION_KEY` (do not rotate during migration unless encrypted secrets are deliberately re-encrypted)
- optional provider credentials stored through the encrypted settings UI
- SEC User-Agent for SEC ingestion
- no FINRA token for the current public Reg SHO daily-volume feed

## Version

`Market Forensics 0.1.1`
