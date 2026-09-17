# Architecture — 0.1.0

## Boundaries

- Account/security foundation: retained from 0.0.4 (`user`, `invite`, `app_secret`, user audit).
- Research core: new `mf_*` MariaDB schema.
- V3.1.12: historical/design reference only; no runtime import.
- Publication layer: immutable snapshot-derived research, separated from private CONTROL state.

## Request model

GET requests perform stored-data reads and rendering only. They do not initialize databases, call market/SEC APIs, or reconstruct companies.

Fast POST actions may persist research, valuation assumptions, risk, portfolio state and journal entries. Heavy work is queued.

## Job model

`mf_job.status`: `QUEUED → RUNNING → DONE | FAILED`.

Supported adapters in 0.1.0: market refresh, SEC Companyfacts ingestion/normalization, deterministic recalculation and FINRA Reg SHO daily short-volume import. Other registered heavy action types fail explicitly until a provider adapter is enabled.

## Publishing

`CONTROL → SNAPSHOT → PREVIEW → PUBLISH`

Publication payload construction strips personal positions, sizing, journal data and locked private thesis invalidation logic.

## Observability

Every handled request receives timing in `Server-Timing`; request failures receive a trace ID. Heavy jobs and calculations persist status/timing in MariaDB and trace failures server-side.
