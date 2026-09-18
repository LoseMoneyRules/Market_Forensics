# Market Forensics — CURRENT STATE

> READ THIS FIRST in every new development chat.
>
> This file is the single source of truth for the current Market Forensics release state.
> It MUST be updated in the same pull request whenever VERSION, architecture, deployment state,
> release gates, job execution, or a material product workflow changes.

**State-Version: 0.2.4**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → heavy jobs in background → cached/materialized results → non-disruptive live updates  
**Current release family:** 0.2.4 candidate

---

## 1. Release objective

0.2.4 is a deep consolidation release. It must not behave like another patch layer over 0.2.3. The affected surfaces are to be left in one clean canonical state, as if the corrected behavior had existed from the start.

Blocking release scope:

1. Process Readiness supports the complete Approve → Reopen → Approve cycle immediately, with no page reload and no RECALCULATE job for the simple state mutation.
2. Research Command Center stays inside the desktop content width. Header labels remain on one line; cells may wrap. Horizontal scrolling is only a narrow-screen fallback.
3. Historical market-price coverage is reliable and observable. Missing/incomplete history queues a ticker-specific backfill, valid raw provider data is preserved if an adjustment request fails, failures are visible, and Valuation live-loads rows when the background job completes.
4. Discovery is a two-column Long / Short prioritized radar. Stored Research Base gaps outrank raw moves; unknown names must pass quality guardrails; penny/low-price short candidates are filtered; unknown intrinsic targets remain unknown.
5. Settings is the only user-facing place where the application version is displayed. Research, audit, Financial Flows, Trace, reports, exported filenames and publication surfaces must not display release/build/calculation-version labels.
6. Financial Flows use intrinsic diagram dimensions and compact containers. Wide screens must not stretch a small flow into a tall empty box.
7. Reports read stored/materialized data only. PDF/Word export requests must not run heavy research calculations synchronously and must return a valid fallback document if optional rich rendering or malformed stored data fails.
8. Every company-detail page uses the same canonical company header with the same ticker, exchange, company name, Research/Coverage/Process state, current price, provider and timestamp.

Product flow remains:

**Discover → Research → Validate → Portfolio**

Research owns thesis, evidence, valuation, invalidation and monitoring.
Portfolio owns positions, sizing, exposure and money risk.
Validation is a distinct point-in-time model-quality step after Research.

The local V3.1.12 codebase is reference/analytical DNA only. It is not a runtime dependency.

### Permanent clean-release rule

Every future release must be consolidated as if the corrected behavior had existed from the start:

- no fix-over-fix CSS or duplicate old/new components;
- no alternate headers for the same company identity;
- no obsolete release/build labels left visible after a version bump;
- no simple database mutation implemented as a background analytical job;
- no release may be called FINAL while a known regression is merely hidden by a fallback or manual reload;
- stateful interactions must be regression-tested in both directions, not only the first happy-path click;
- desktop layout problems must be fixed through sizing/layout, not by defaulting to horizontal scroll;
- heavy provider/analytical work never runs inside normal navigation or report-export requests.

---

## 2. Production safety invariants

Do NOT reset or delete:

- MariaDB production data
- CONTROL account / user IDs
- Argon2 password hashes
- TOTP / 2FA
- encrypted provider credentials / secrets
- Research data and manual analyst edits
- Portfolio state
- publication history
- audit/history
- server .env / production configuration

GitHub main is not automatically production.
Production changes only after the manual Namecheap deployment workflow succeeds.

The repository may expose machine-readable release identity through /health for deployment verification. That does not override the UI rule: Settings is the only user-facing version location.

A failed candidate health check must roll back automatically.

---

## 3. Performance contract

The site must feel fast when opening pages and changing Research sections.

Normal GET navigation must NOT execute heavy work such as:

- SEC ingestion
- FINRA ingestion
- external market scans
- historical market backfill
- management guidance parsing
- peer triangulation computation
- Tape reconstruction
- valuation recalculation
- historical validation
- portfolio correlations

Heavy work runs in background jobs.

Normal pages read MariaDB, the latest stored quote, and materialized Research/Portfolio caches.

When cache/data is missing, the page renders immediately with an updating/pending state and queues bounded background work where appropriate.

When a background job finishes, lightweight fields may update in place. The application must not force disruptive full-page reloads.

Simple CONTROL state mutations such as Process Readiness Approve/Reopen execute synchronously and update the visible UI immediately.

---

## 4. Job execution model

Primary production executor:

python manage.py run-jobs --limit 5

Recommended cPanel cadence: every minute.
Minimum acceptable cadence for the five-minute quote objective: every 5 minutes.

Browser fallback may start a detached CLI worker through /jobs/pump, but the Passenger request itself must not execute heavy work.

Queue contracts:

- duplicate logical jobs are reused rather than multiplied;
- stale RUNNING jobs recover safely;
- CONTROL can cancel active jobs;
- valid committed data is preserved on cancellation;
- every visible job identifies its target as ticker, company or GLOBAL;
- PRICE_HISTORY_REFRESH is ticker-scoped;
- new Coverage queues quote + historical-price backfill + evidence work as available;
- stale/full refresh includes historical-price coverage when it is missing or stale;
- Process Readiness state toggles never enter the background queue.

---

## 5. Current market price and historical price

Current quote freshness target: 5 minutes.

Current-price behavior:

1. read stored quote first;
2. if stale, reuse active MARKET_REFRESH if present;
3. recently finished refreshes use cooldown to prevent storms;
4. refresh runs in background;
5. completion never forces a full-page reload.

Provider cascade for current quote remains Alpaca → Tiingo → Alpha Vantage → public cross-check / last-good preservation.

Historical-price behavior:

- Valuation requires roughly two years of stored history for the chart;
- incomplete history queues one PRICE_HISTORY_REFRESH for that ticker;
- Alpaca raw history remains valid even if a separate split-adjustment request fails;
- public fallback uses adjusted close when available;
- provider errors are shown in Valuation instead of leaving an unexplained blank;
- an open Valuation page polls stored DB history and draws the chart as soon as the background backfill commits rows;
- manual Refresh 2Y price history remains available;
- missing provider data is never fabricated.

---

## 6. Discovery contract

Discovery is a research radar, not an automatic trading recommendation and not a full-market valuation crawler.

Broad-market inputs:

1. Alpaca Most Active
2. Alpaca Market Movers
3. one bounded snapshot qualification call for price/liquidity
4. one batch lookup of already materialized Research caches

Known Research names:

- stored Base gap within ±7.5% is filtered as no material edge;
- Base gap ≥ +15% qualifies for Long radar;
- Base gap ≤ -15% qualifies for Short radar;
- stored Research evidence outranks one-day price movement.

Unknown names:

- unknown Long leads require price ≥ $5;
- unknown Short leads require price ≥ $10;
- where daily liquidity is available, unknown leads require at least $25M dollar volume;
- downside dislocation may create a Long lead;
- upside overextension may create a Short lead;
- intrinsic target stays TARGET UNKNOWN until deep Research exists;
- penny/low-price movers must not become high-priority Short ideas from percentage move alone.

UI contract:

- Long and Short are separate columns;
- candidates are prioritized P1 / P2 / P3;
- P1 means researched material value gap;
- P2 means researched edge or highly liquid qualified dislocation;
- P3 means watch / deeper check;
- every card shows price, move, Base gap when known, target status and why it was found;
- filtered-universe reasons are auditable.

No per-symbol SEC/fundamental/valuation work is allowed inside the broad scan.

---

## 7. Research workflow and UI

Canonical Research sequence:

**Overview → Business → Numbers → Expectations → Valuation → Bear Case → Catalysts → Financial Flows → Management → Tape / Flows → Monitoring → Decision Journal → Sources / Audit**

Research Command Center:

- Price, Base and Base gap are visible; Bear/Bull are not repeated there;
- column titles remain one line on desktop;
- cells can wrap to keep the table inside available width;
- Manage is action-width;
- horizontal scroll is permitted only on narrower screens;
- Process means approved Research gates;
- Validate means latest point-in-time walk-forward validation state.

Process Readiness:

- explicit approve/revoke submit actions;
- full Approve → Reopen → Approve behavior;
- synchronous DB mutation;
- immediate partial replacement;
- inline error visibility;
- no RECALCULATE just to toggle approval.

Company identity header:

The same _company_header.html partial is mandatory on:

- Research company sections
- Valuation
- Financial Flows
- Validate
- Portfolio security

The visible fields must be identical across those surfaces.

Canonical Research conclusions remain:

- RESEARCH INCOMPLETE
- READY TO VALIDATE
- LONG READY
- LONG WATCH
- SHORT READY
- SHORT WATCH
- DATA REVIEW
- NO EDGE · WAIT

---

## 8. Financial Flows and Tape

Financial Flows:

- remain under Research → Financial Flows;
- Income Statement and Cash Flow use the same renderer;
- signed negatives are never converted into fake positive ribbons;
- SVG uses intrinsic width/height;
- flow canvas has no artificial minimum height;
- font size stays readable and independent of container stretching;
- flow UI never displays an internal engine/calculation version.

Tape / Flows remains context rather than intrinsic value and keeps:

- Price + FINRA Short Interest combined historical view
- separate FINRA Daily Short Volume %
- positioning/borrow/options context where stored

---

## 9. Reports, publication and version display

CONTROL reports:

- Executive PDF
- Full PDF
- Full Word
- Discovery landscape PDF

Report contract:

- report routes read stored/materialized context;
- they must not synchronously run automatic peer triangulation, Tape reconstruction or similar heavy calculations;
- rich report libraries are optional;
- rich-render failure falls back to a valid standard-library PDF/DOCX;
- malformed optional research data falls back to a minimal stored-data report rather than HTTP 500;
- filenames do not include release/build numbers;
- reports do not display release/build numbers.

Publication separation remains PRIVATE / FRIEND / INSIDER.

Never publish personal portfolio details, sizing, P/L, private notes/journal, credentials or secrets.

### Visible version rule

The current application version is displayed only in Settings.

Do not display release/build/calculation-version identifiers in:

- Research
- Sources / Audit
- Financial Flows
- Trace
- reports
- report filenames
- publication pages
- normal navigation/header/footer

Internal database lineage and /health may retain machine-readable metadata for audit/deployment purposes.

---

## 10. Mobile / UI contract

- one canonical responsive navigation controller
- hamburger open/close
- backdrop
- Escape close
- link close
- resize reset
- Research sub-navigation usable on phone
- no purple
- institutional blue theme
- centralized semantic state colors
- purpose-built light and dark themes
- responsive forms/tables/charts
- desktop tables fit their content area unless the viewport is genuinely narrow
- footer: Lose Money Rules

Broken mobile navigation blocks release.

---

## 11. Deployment gates

Before merge:

1. Python syntax
2. JavaScript syntax
3. workflow YAML validation
4. complete baseline + 0.2.1 / 0.2.2 / 0.2.3 / 0.2.4 regression suite
5. production-minimal startup smoke
6. fast cached-navigation contract
7. real RECALCULATE → Research cache test
8. job executor/cancellation/stale recovery tests
9. mobile navigation contract
10. five-minute current-price refresh contract
11. Process Readiness Approve → Reopen → Approve test with no RECALCULATE job
12. Command Center desktop-width / one-line-header / compact-Manage contract
13. historical provider resilience + ticker backfill + live Valuation update contract
14. Discovery penny/low-price guardrail + Long/Short/P1-P3 contract
15. Settings-only visible-version contract
16. Financial Flow intrinsic-size / no artificial minimum-height contract
17. Executive PDF, Full PDF and Full Word HTTP tests
18. malformed-data / rich-renderer report fallback test
19. canonical company-header include contract across every company-detail page
20. no user-facing old build/calculation-version labels

After merge:

21. main CI green

Production:

22. manual Namecheap deploy
23. candidate /health HTTP 200
24. version = 0.2.4
25. architecture = web-native
26. automatic rollback if candidate health fails

Only after step 25 succeeds is 0.2.4 considered LIVE.

---

## 12. Current development note

0.2.4 is the active release candidate, not yet production.

The eight regressions in Section 1 are blocking acceptance criteria. A candidate is not ready if any item is merely cosmetically hidden, silently fails, or still depends on a manual page reload.

Before telling the user to deploy:

PR CI green → merge to main → post-merge main CI green → only then manual Namecheap deploy.

---

## 13. Mandatory update rule

Every future release PR MUST update this file when any of these change:

- VERSION
- deployment state
- architecture
- page/workflow structure
- job/queue execution
- price freshness or historical-price behavior
- providers
- security assumptions
- analytical engines
- reports/publication
- version-display policy
- known production issue
- next release work

If VERSION changes and State-Version does not match, CI must fail.

This file is the concise handoff. Release-specific tests and PR evidence provide the detailed audit.
