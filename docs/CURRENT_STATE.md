# Market Forensics — CURRENT STATE

> **READ THIS FIRST in every new development chat.**
>
> This file is the single source of truth for the current Market Forensics release state.
> It MUST be updated in the same pull request whenever VERSION, architecture, deployment state,
> release gates, job execution, or a material product workflow changes.

**State-Version: 0.2.3**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → heavy jobs in background → cached results → non-disruptive live updates / user-controlled full refresh  
**Last release family:** 0.2.3 candidate

---

## 1. Current release objective

0.2.0 remains the clean web-native architectural baseline. 0.2.3 is a reliability and interaction-closure release built cleanly on that architecture. It keeps the 0.2.2 market-refresh protections and closes remaining web regressions: Command Center sizing, compact Financial Flows, fail-safe-but-visible Discovery errors, automatic ticker-specific historical-price backfill, one canonical company ticker/price header, explicit job targets, clear Validate semantics, and immediate Process Readiness state changes. It does not reopen MariaDB, security/auth, Research/Portfolio separation, valuation mathematics, expectations logic or the background-job architecture.

Product flow:

**Discover → Research → Validate → Portfolio**

Research owns the thesis, evidence, valuation, invalidation and monitoring.
Portfolio owns position, sizing, exposure and money risk.
Validation is a distinct step after Research.

The local V3.1.12 codebase is reference/analytical DNA only. It is not a runtime dependency.

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

GitHub `main` is NOT automatically production.
Production changes only after the manual Namecheap deployment workflow succeeds.

The repository does not infer the currently deployed Namecheap version. 0.2.3 is not LIVE until the manual Namecheap workflow succeeds and external health returns:

- HTTP 200
- `status = ok`
- `version = 0.2.3`
- `architecture = web-native`

A failed candidate health check must roll back automatically.

**Permanent release-version rule:** GitHub test/deploy workflows must read the candidate version from the repository `VERSION` file. Release numbers must never be hard-coded in workflow health assertions. This is enforced by the release test suite so future version bumps do not require CI/deploy workflow edits.

---

## 3. Performance contract — mandatory

The site must feel fast when opening pages and changing sections.

Normal GET navigation must NOT execute heavy analytical work such as:

- SEC ingestion
- FINRA ingestion
- external market scans
- management guidance parsing
- peer triangulation computation
- 12M Tape reconstruction
- valuation recalculation
- historical validation
- portfolio correlations

Heavy work runs in background jobs.

Normal pages read:

- MariaDB
- latest stored quote
- materialized Research cache
- materialized Portfolio analytics cache

When a cache is missing, the page renders immediately with an UPDATING state and queues work.

When a background job finishes, the browser updates lightweight live fields where possible and offers a user-controlled full refresh. It MUST NOT force a full-page reload or flash while the user is working.

Lightweight CONTROL mutations that only change application state — for example Process Readiness Approve/Reopen — execute synchronously and update the visible UI immediately. They are not background jobs. External-network, bulk-data and analytical computation remain background work.

This performance model is a release gate and has automated tests.

---

## 4. Job execution model

Primary production executor:

`python manage.py run-jobs --limit 5`

Recommended cPanel cron cadence:

**every minute**

Minimum acceptable cadence for the five-minute quote freshness objective:

**every 5 minutes**

0.2.0 includes a CONTROL browser fallback, retained unchanged in principle by 0.2.3:

- the browser observes queue status;
- if jobs are due and no executor is RUNNING, it calls `/jobs/pump`;
- `/jobs/pump` MUST NOT execute a heavy job inside Passenger;
- it starts a detached CLI process and returns immediately;
- CLI and cron share a cross-process lock so only one queue executor runs at a time.

Queued jobs must therefore progress even if cron is missing/late while CONTROL is open, without sacrificing page responsiveness.

0.2.3 retains the hardened job operations and makes job scope explicit:

- Recent Jobs exposes clear QUEUED / RUNNING / DONE / FAILED / CANCELLED states;
- CONTROL may cancel QUEUED or RUNNING jobs;
- cancellation is audited and preserves valid data already committed;
- RUNNING jobs carry an executor identity for best-effort verified termination;
- DISCOVERY_SCAN has a short lease in addition to its 90-second hard execution deadline;
- stale RUNNING attempts are closed cleanly and retry or fail according to max attempts;
- queue/lock cleanup must prevent a dead process from leaving a job RUNNING forever;
- every visible job identifies its target as a ticker, a company, or **GLOBAL**;
- ticker-specific historical market backfill runs as `PRICE_HISTORY_REFRESH`; simple Research gate toggles never enter the queue.

---

## 5. Current-price freshness

Current market price freshness target:

**5 minutes**

Behavior:

1. company pages read the stored live quote before asking for a refresh;
2. backend treats a quote older than 5 minutes as stale;
3. a stale quote may queue `MARKET_REFRESH`, but an already-active job is reused and a recently completed terminal refresh is subject to cooldown (5 minutes after DONE/CANCELLED; 60 seconds after FAILED);
4. market refresh runs in background;
5. Research recalculation/cache refresh follows as needed;
6. job completion is **non-disruptive**: the page MUST NOT hard-reload automatically or flash repeatedly; live quote fields may update silently and CONTROL may choose a full refresh from the job chip.

The five-minute freshness target remains unchanged. A stale/last-good provider timestamp is not permission to create repeated refresh jobs on every navigation.

Provider cascade:

**Alpaca → Tiingo → Alpha Vantage → public cross-check / last-good preservation**

Missing provider data must be shown as unavailable; never fabricated.

---

## 6. Discovery Scan contract

Discovery is a lightweight broad-market radar, not a full-universe valuation crawler.

It performs:

1. Alpaca Most Active screener
2. Alpaca Market Movers screener
3. batch enrichment from already stored Research caches
4. transparent LONG / SHORT research-side classification and ranking
5. promotion to deep Research when selected

For names with an existing Research cache, stored Base-gap evidence controls target-room classification: material positive Base gap can enter LONG radar, material negative Base gap can enter SHORT radar, and names within ±7.5% of stored Base are demoted to **NO EDGE · AT / NEAR BASE**. For names without stored Research valuation, a large move may create only a LONG LEAD or SHORT LEAD; Discovery must say **TARGET UNKNOWN** and explain why the candidate was found.

Discovery always exposes the reason (market mover, activity rank, stored Base gap, or required deep-research check). It does NOT run per-symbol SEC, valuation or fundamentals for the whole market and never invents a target for an unknown name. Provider/search failures and malformed old scan payloads must not turn the Discovery page into a generic 500: the page stays usable and shows the explicit failed job/provider condition without inventing fallback candidates.

Expected runtime:

- normal: approximately **2–15 seconds**
- slow provider/network: approximately **20–30 seconds**
- over 60 seconds: investigate
- hard deadline: **90 seconds**

A `DISCOVERY_SCAN` exceeding 90 seconds fails fast instead of remaining RUNNING indefinitely.

---

## 7. Research workflow

Canonical Research sequence:

**Overview → Business → Numbers → Expectations → Valuation → Bear Case → Catalysts → Financial Flows → Management → Tape / Flows → Monitoring → Decision Journal → Sources / Audit**

Overview is the single-company cockpit.

0.2.3 Research-surface rules:
- Research Command Center shows Price, **Base** and Base gap; Bear/Bull are not repeated in the coverage table. Desktop column headings remain on one line, column widths follow content/title needs, and Manage collapses to the action-button width.
- Command Center KPI blocks stay on one desktop row; Exceptions identify the ticker and use normal readable type.
- Research Conclusion remains dominant but its desktop block is compact rather than occupying the majority of the strip.
- Valuation historical chart must never render as an unexplained blank. A missing or incomplete two-year price cache automatically queues one ticker-scoped `PRICE_HISTORY_REFRESH` with cooldown protection, exposes stored row/span/provider/job status, and still shows current Bear/Base/Bull references while backfill is pending.
- Tape / Flows restores two explicit historical views: **Price + FINRA Short Interest** on the same 6M/12M chart, plus a separate **FINRA Daily Short Volume %** chart. Tape context never substitutes for intrinsic value.
- Financial Flows keeps the stabilized accounting logic; hidden-tab SVG text is re-rendered and scale-normalized, and diagram/container geometry is compact enough to match the actual content instead of leaving oversized empty boxes.
- Dark mode analytical values (including evidence thresholds and MODEL INPUT BASIS) must use theme tokens with readable contrast.
- Company-detail surfaces use one canonical ticker/company/current-price/provider/timestamp header across Research sections, Valuation, Financial Flows, Validate and Portfolio security.
- Process Readiness Approve/Reopen updates immediately in-place and patches cached readiness synchronously; it does not enqueue RECALCULATE just to change a button.
- Command Center **Validate** means the latest point-in-time walk-forward validation state, while **Process** means approved Research gates.

Canonical analytical lenses:

- BUSINESS
- VALUE
- EXPECTATIONS
- VARIANT
- PATH
- MODEL CONFIDENCE
- THESIS CONTROL

Canonical Research conclusions include:

- RESEARCH INCOMPLETE
- READY TO VALIDATE
- LONG READY
- LONG WATCH
- SHORT READY
- SHORT WATCH
- DATA REVIEW
- NO EDGE · WAIT

Legacy BUY / SELL / WAIT evidence scoring may exist diagnostically but MUST NOT become a competing headline decision engine.

---

## 8. Key analytical capabilities inherited from 0.2.0

- Process Readiness and Evidence Signals
- WHY NOW / WHY NOT YET / WHAT CHANGES / WHAT KILLS
- Business Evidence Path
- automatic external triangulation: SEC SIC → SIC division → industry fallback
- peer comparisons including Growth, margins, FCF, ROIC, WC ratios, Share Change, P/E, EV/Sales, FCF Yield
- price-implied expectations:
  - 5Y Revenue CAGR
  - Y5 Net Margin
  - Y5 Exit P/E
- 5Y Bear/Base/Bull operating paths
- multi-method valuation with Bear/Base/Bull + Expected Value
- Financial Flows Sankey under Research
- Management promises vs actuals: PENDING / MET / MISS
- deeper Tape / positioning context
- exceptions-first Monitoring
- immutable/versioned Decision Journal / snapshots
- Sources / provenance / audit
- point-in-time Validate workflow
- separated Portfolio risk / sizing / exposure / correlations

Manual analyst edits are sacred and must not be silently overwritten by automated fills.

---

## 9. Reports and publication

CONTROL reports:

- Executive PDF
- Full PDF
- Full Word
- Discovery landscape PDF

In 0.2.2 the three company Research exports appear only at the bottom of Overview under EXPORT RESEARCH. Process Readiness owns the Publish entry point; publishing remains gated by current Research readiness and continues to use the existing PRIVATE / FRIEND / INSIDER separation.

Optional reporting packages must NEVER prevent application startup.
If rich report dependencies are missing, startup remains healthy and standard-library fallbacks are used.

FRIEND / INSIDER receive only selectively published non-personalized Research.

Never publish:

- personal shares
- average cost
- personal P/L
- position sizing
- max-loss / money-risk settings
- private notes / journal
- credentials / secrets

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
- centralized semantic status colors: positive green, negative red, caution/neutral amber or gray, informational blue
- purpose-built light and dark themes
- one canonical company-detail ticker/price header component
- responsive tables/charts/forms
- Financial Flows readable on desktop and mobile without falsifying negative values
- footer: Lose Money Rules

Broken mobile navigation blocks release.

---

## 11. Deployment gates

Before merge:

1. Python syntax
2. JavaScript syntax
3. workflow YAML validation
4. complete 0.2.0 release/parity suite plus 0.2.1, 0.2.2 and 0.2.3 regression contracts
5. production-minimal startup smoke
6. fast cached-navigation contract
7. real RECALCULATE → Research cache test
8. job executor tests
9. mobile-navigation contract
10. five-minute current-price refresh contract
11. job cancel/kill and stale RUNNING recovery contracts
12. Publish/readiness, export-location, semantic-color, dark-theme and Financial Flows contracts
13. MARKET_REFRESH cooldown / no forced reload contract
14. two-sided Discovery / target-room transparency contract
15. Command Center Base-only / ticker Exceptions contract
16. Valuation fallback chart, Tape combined positioning and Cash Flow typography contracts
17. Command Center one-line headers / compact Manage contract
18. Discovery request resilience and explicit-error contract
19. ticker-scoped historical-price backfill / coverage-diagnostic contract
20. canonical company ticker/price header contract
21. job target ticker/company/GLOBAL contract
22. synchronous Process Readiness interaction / no-simple-job contract

After merge:

23. main CI green

Production:

24. manual Namecheap deploy
25. candidate /health HTTP 200
26. version 0.2.3
27. architecture web-native
28. rollback automatically if candidate fails

Only after step 27 succeeds is 0.2.3 considered LIVE. Until then, the deployed version must be treated as the last externally verified healthy release.

---

## 12. Current development note

0.2.3 is currently a release candidate, not production.

Its scope is deliberately limited to closing the remaining web interaction and visibility regressions without changing the analytical thesis model:

- keep every Research Command Center title on one line and make Manage action-width;
- compact Financial Flows containers while preserving accounting and readable typography;
- make Discovery survive provider/stored-payload failures with explicit visible errors;
- detect missing/incomplete ticker price history, queue a bounded 3Y backfill, and expose its audit status in Valuation;
- use one company ticker/current-price header everywhere a company detail surface shows that identity;
- label jobs with ticker, company or GLOBAL scope;
- define Validate as point-in-time walk-forward status, distinct from Process readiness;
- make Process Readiness Approve/Reopen synchronous and immediately visible, with no unnecessary RECALCULATE job;
- preserve 0.2.2 MARKET_REFRESH cooldown/non-disruptive completion, stabilized Tape, dark-mode, security, MariaDB, publication and fast-navigation contracts.

Before telling the user to deploy, verify PR CI green, merge to main, verify post-merge main CI green, and only then run the manual Namecheap deployment workflow.

---

## 13. Mandatory update rule

Every future release PR MUST update this file if any of these change:

- VERSION
- current production version
- architecture
- page/workflow structure
- job/queue execution
- price freshness
- providers
- deployment flow
- security assumptions
- analytical engines
- reports/publication
- known production issue
- next release work

If VERSION changes and **State-Version** does not match, CI must fail.

Detailed release-specific audit remains in:

`docs/RELEASE_0_2_0.md` remains the 0.2.0 baseline audit. 0.2.3 release evidence belongs in its release PR/tests and any dedicated 0.2.3 audit added before FINAL.

This file is the concise handoff; release audit documents provide the deeper evidence.
