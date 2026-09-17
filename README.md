# Market Forensics 0.1.2

Market Forensics 0.1.2 is the web-native implementation of the Market Forensics research process.

The production application uses the existing account/security foundation and a MariaDB research core. V3.1.12 remains a design/reference artifact only; it is not imported into the current request path.

## Runtime model

`INGEST → NORMALIZE → CALCULATE → STORE → DISPLAY`

Page GETs read stored data and render HTML. Heavy refreshes are queued in `mf_job`. While CONTROL is open, the authenticated browser worker advances due jobs one at a time; `python manage.py run-jobs --limit 5` remains the unattended cPanel cron worker. Equivalent QUEUED/RUNNING jobs are deduplicated by user, job type and target.

## 0.1.2 data model

- SEC EDGAR is the primary audited filing/fundamental source.
- Alpaca is the preferred configured quote source, with Tiingo and Alpha Vantage as optional redundancy and a public chart as last-resort quote fallback.
- FINRA public Reg SHO files provide daily short-sale volume without credentials.
- Optional FINRA Public API credentials unlock consolidated short interest, short-interest changes, average daily volume, days-to-cover and threshold-history data.
- Evidence-based auto-draft fills blank/previously auto-generated numbers, flow summaries and Bear/Base/Bull valuation drafts from stored facts. User-edited fields are never overwritten automatically.
- Human investment judgments such as thesis, catalyst interpretation, management assessment and locked risk invalidation remain explicit user inputs.
- Financial tables support AUTO, FULL, K, M, B and T display modes without changing stored values.
- Publishing remains Snapshot → Preview → Publish and strips private portfolio/journal/risk details from external research payloads.

## Production requirements

- Python / Passenger / WSGI
- MariaDB via `MF_DATABASE_URL`
- `MF_SECRET_KEY`
- existing `MF_ENCRYPTION_KEY` (do not rotate during migration unless encrypted secrets are deliberately re-encrypted)
- SEC User-Agent for SEC ingestion
- optional provider credentials stored through the encrypted settings UI
- optional FINRA Public API Client ID + Client Secret for consolidated short-interest datasets

## Version

`Market Forensics 0.1.2`
