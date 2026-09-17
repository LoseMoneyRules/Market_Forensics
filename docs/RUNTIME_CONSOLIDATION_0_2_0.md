# Market Forensics 0.2.0 — Runtime Consolidation Plan

## Preserve
- MariaDB schema and stored research.
- User IDs, roles, password hashes and TOTP.
- Encrypted provider credentials.
- Publication snapshots and access rules.
- Audit records, jobs, sources, provenance and historical data.

## Consolidate
Numbered runtime modules are replaced by semantic modules:
- research gates/readiness
- research routes
- workspace routes
- research synthesis
- financial-flow engine
- monitoring engine
- alert engine/routes

Versioned static CSS/JS layers are removed from the runtime and replaced by the consolidated application assets.

## Compatibility
Old URL entry points that are likely to be bookmarked may redirect to the 0.2.0 canonical URL, but new templates and navigation never emit the old paths.

## Database
0.2.0 is intentionally additive/no-reset. No migration is allowed to delete or recreate account/security tables. No migration requires CONTROL to reset 2FA or re-enter provider credentials.
