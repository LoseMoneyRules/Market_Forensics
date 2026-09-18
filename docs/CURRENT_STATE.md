# Market Forensics — CURRENT STATE

> **READ THIS FIRST in every new development chat.**
>
> This file is the single source of truth for the current Market Forensics release state.
> It MUST be updated in the same pull request whenever VERSION, architecture, deployment state,
> release gates, job execution, or a material product workflow changes.

**State-Version: 0.2.1**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → heavy jobs in background → cached results → automatic UI refresh when jobs finish  
**Last release family:** 0.2.1 candidate

---

## 1. Current release objective

0.2.0 remains the clean web-native architectural baseline. 0.2.1 is a refinement release only: UI/UX, visual polish, job controls, dark/mobile hardening, Financial Flows presentation and small operational fixes. It does not reopen the Research, valuation, expectations, Portfolio, MariaDB, security or caching architecture.

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

Production remains on the previously deployed 0.2.0 release until the manual 0.2.1 Namecheap workflow succeeds. A 0.2.1 release is live only after external health returns:

- HTTP 200
- `status = ok`
- `version = 0.2.1`
- `architecture = web-native`

A failed candidate health check must roll back automatically.

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

When a background job finishes:

- if the user is not editing a form, the data page refreshes automatically;
- if a form is dirty, the UI offers a safe refresh instead of losing edits.

This performance model is a release gate and has automated tests.

---

## 4. Job execution model

Primary production executor:

`python manage.py run-jobs --limit 5`

Recommended cPanel cron cadence:

**every minute**

Minimum acceptable cadence for the five-minute quote freshness objective:

**every 5 minutes**

0.2.0 includes a CONTROL browser fallback, retained unchanged in principle by 0.2.1:

- the browser observes queue status;
- if jobs are due and no executor is RUNNING, it calls `/jobs/pump`;
- `/jobs/pump` MUST NOT execute a heavy job inside Passenger;
- it starts a detached CLI process and returns immediately;
- CLI and cron share a cross-process lock so only one queue executor runs at a time.

Queued jobs must therefore progress even if cron is missing/late while CONTROL is open, without sacrificing page responsiveness.

0.2.1 hardens job operations without changing that architecture:

- Recent Jobs exposes clear QUEUED / RUNNING / DONE / FAILED / CANCELLED states;
- CONTROL may cancel QUEUED or RUNNING jobs;
- cancellation is audited and preserves valid data already committed;
- RUNNING jobs carry an executor identity for best-effort verified termination;
- DISCOVERY_SCAN has a short lease in addition to its 90-second hard execution deadline;
- stale RUNNING attempts are closed cleanly and retry or fail according to max attempts;
- queue/lock cleanup must prevent a dead process from leaving a job RUNNING forever.

---

## 5. Current-price freshness

Current market price freshness target:

**5 minutes**

Behavior:

1. browser checks quote freshness every 5 minutes;
2. backend treats a quote older than 5 minutes as stale;
3. stale quote queues `MARKET_REFRESH`;
4. market refresh runs in background;
5. Research recalculation/cache refresh follows as needed;
6. page updates automatically when the job completes.

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
4. ranking / lenses
5. promotion to deep Research when selected

It does NOT run per-symbol SEC, valuation or fundamentals for the whole market.

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

In 0.2.1 the three company Research exports appear only at the bottom of Overview under EXPORT RESEARCH. Process Readiness owns the Publish entry point; publishing remains gated by current Research readiness and continues to use the existing PRIVATE / FRIEND / INSIDER separation.

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
4. complete 0.2.0 release/parity suite plus 0.2.1 contracts
5. production-minimal startup smoke
6. fast cached-navigation contract
7. real RECALCULATE → Research cache test
8. job executor tests
9. mobile-navigation contract
10. five-minute current-price refresh contract
11. job cancel/kill and stale RUNNING recovery contracts
12. Publish/readiness, export-location, semantic-color, dark-theme and Financial Flows contracts

After merge:

13. main CI green

Production:

14. manual Namecheap deploy
15. candidate /health HTTP 200
16. version 0.2.1
17. architecture web-native
18. rollback automatically if candidate fails

Only after step 17 succeeds is 0.2.1 considered LIVE. Until then, production remains the prior healthy release.

---

## 12. Current development note

0.2.1 is currently a release candidate, not production.

Its scope is deliberately limited to:

- restore Publish access under Process Readiness;
- move company Research exports to Overview bottom only and keep them functional;
- make Research Conclusion compact and hierarchical;
- add CONTROL-only job cancellation and stale RUNNING recovery;
- simplify Manage to Remove-only where applicable;
- rebuild Financial Flows presentation without changing sound accounting logic;
- centralize semantic status colors;
- audit and harden dark mode;
- preserve the stabilized mobile navigation and fast-navigation contracts.

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

`docs/RELEASE_0_2_0.md` remains the 0.2.0 baseline audit. 0.2.1 release evidence belongs in its release PR/tests and any dedicated 0.2.1 audit added before FINAL.

This file is the concise handoff; release audit documents provide the deeper evidence.
