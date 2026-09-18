# Market Forensics — CURRENT STATE

> READ THIS FIRST in every new development chat.
>
> This file is the single source of truth for the active Market Forensics release state.
> Update it in the SAME PR whenever VERSION, architecture, deployment state, release gates,
> job execution, data correctness rules, or a material product workflow changes.

**State-Version: 0.2.5**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → bounded background work → stored/materialized results → non-disruptive live updates  
**Production baseline before this release:** 0.2.4 deployed successfully to Namecheap  
**Active release:** 0.2.5 candidate

---

## 1. What 0.2.5 is

0.2.5 is a product-quality consolidation release, not a cosmetic patch.

The release is blocked unless all of these are true:

1. Settings visibly shows the current application version, and Recent Jobs is compact/collapsed by default with the full table revealed only on click.
2. Discovery is no longer a generic mover/most-active list. A visible Long/Short candidate must have a valid operating equity, meaningful liquidity, a calculable Market Forensics Base fair value, a material price-to-Base gap, and operating evidence confirming the same side.
3. Research Command Center keeps Price / Base / Gap on one line, keeps Manage narrow, shows Freshness only as relative minutes/hours, and keeps the Process/Validate explanation plain text.
4. Financial Flows use the full available width, push the final graph column to the right edge, and do not show the old redundant category legend.
5. Process Readiness Reopen is a real explicit POST action and must never return gate 405.
6. Numbers is filing-transparent: Revenue columns + FCF line; TTM only from four consecutive fiscal quarters; quarter-level evidence visible; incomplete data explicit; SEC comparative facts period-aware.
7. Every Research section and core function is release-smoke-tested; a known missing/broken function blocks merge.

Product flow remains:

**Discover → Research → Validate → Portfolio**

Research owns thesis, evidence, valuation, invalidation and monitoring.
Portfolio owns positions, sizing, exposure and money risk.
Validation remains a separate point-in-time model-quality step.

V3.1.12 remains reference/DNA only; it is not a runtime dependency.

---

## 2. Permanent clean-release rules

Every future release must be consolidated as if the corrected behavior had existed from the start:

- no fix-over-fix CSS;
- no duplicate old/new component implementations;
- no alternate company headers;
- no obsolete release/build labels visible after a version bump;
- no simple database mutation implemented as a background analytical job;
- no known regression hidden behind a manual reload or silent fallback;
- bidirectional interactions must be tested in both directions;
- desktop layout problems are solved by layout/sizing, not by default horizontal scrolling;
- heavy provider/analytical work never runs in normal GET navigation or report-export HTTP requests;
- data that cannot be verified stays missing; it is never invented to make a screen look complete;
- an empty high-conviction Discovery list is better than a low-quality filled list;
- release tests encode the current user-visible contract, not obsolete behavior from an older release.

---

## 3. Production safety invariants

Do NOT reset or delete MariaDB production data, CONTROL account/user IDs, Argon2 password hashes, TOTP/2FA, encrypted provider credentials, Research/manual edits, Portfolio state, publication history, audit/history, or server configuration.

CONTROL is the complete private research workspace.
FRIEND / INSIDER see only selectively published, non-personalized research.
Visibility remains PRIVATE / FRIEND / INSIDER.
Never publish portfolio shares/cost/P&L/sizing/max-loss/private notes/journal/credentials.

GitHub main is not production.
Production changes only after the manual Namecheap deployment workflow succeeds.

---

## 4. Runtime and job model

Normal navigation reads MariaDB, latest stored quote and materialized caches.

Heavy work runs in background jobs: SEC, FINRA, Discovery forensic scan, historical prices, management scan, triangulation, Tape reconstruction, valuation recalculation, historical validation and portfolio analytics.

Primary executor:

python manage.py run-jobs --limit 5

Recommended cPanel cadence: every minute.

Job contracts:

- duplicate logical jobs are reused;
- stale RUNNING jobs recover;
- CONTROL can cancel active work;
- committed valid data survives cancellation;
- visible job scope is ticker / company / GLOBAL;
- PRICE_HISTORY_REFRESH is ticker-scoped;
- Process Readiness Approve/Reopen is synchronous and never queued;
- DISCOVERY_SCAN has a bounded six-minute lease because the final shortlist can perform SEC forensic enrichment;
- browser fallback may spawn a detached worker, but the HTTP request does not execute heavy work itself.

---

## 5. Market price and history

Current quote freshness target: 5 minutes.

Current quote reads stored data first, reuses active MARKET_REFRESH, applies terminal cooldown, refreshes in background, and never forces a full-page reload.

Historical price:
- roughly two years stored for Valuation;
- missing/incomplete history queues PRICE_HISTORY_REFRESH;
- valid Alpaca raw history survives split-adjustment failure;
- public fallback uses adjusted close when available;
- provider errors are visible;
- Valuation live-loads stored rows when the background job completes;
- missing history is never fabricated.

---

## 6. Discovery — Market Forensics contract

### Stage 1: investigation funnel

Cheap inputs decide only where to investigate:

- Alpaca Most Active
- Alpaca Market Movers
- IEX snapshot price/volume/day move
- Alpaca active US-equity metadata
- materialized Research cache for known names

Fail-closed filters:

- active + tradable operating equity
- major US exchange
- no warrants / rights / units / ETFs / ETNs / funds / blank-check shells / preferred / notes
- new Long price >= $5
- new Short price >= $10
- new-name daily volume >= 500k when available
- new-name daily dollar volume >= $50M
- Short must be shortable

A price move alone can NEVER appear in the final radar.

### Stage 2: bounded forensic enrichment

Only a bounded shortlist receives deeper work.

Already-covered names use stored Research Base fair value plus normalized SEC operating data.

External names:
- fetch SEC submissions metadata + Companyfacts;
- resolve fiscal year from the fact period end and company fiscal-year-end, not a later filing's fy;
- infer company type from SEC SIC description;
- build annual + filing-quarter history;
- calculate a provisional Base with the SAME Market Forensics valuation engine;
- allow no market/reference-price fallback;
- require INTRINSIC quality and at least two usable valuation methods.

Final Long:
- Base gap >= +20%;
- Long operating score passes threshold and exceeds Short score.

Final Short:
- Base gap <= -20%;
- Short operating score passes threshold and exceeds Long score;
- shortability/actionability rules pass.

Operating forensic signals include revenue acceleration/deterioration, operating leverage/deleverage, FCF-margin inflection/erosion, inventory discipline/build, receivables quality/build, earnings-to-cash conversion, and price/fundamentals disconnect.

Priority:
- P1 = large fair-value gap + strong confirming operating evidence
- P2 = qualifying fair-value gap + confirming evidence

There is no filler quota and no P3 filler list.
If nothing passes, the correct result is an empty radar.

Discovery result contract: **FORENSIC_FAIR_VALUE_V1**.
Legacy stored scan payloads are not rendered after 0.2.5.

UI stays two columns, Long and Short, as compact lists with Promote/Open Research.
Each candidate shows current price, our Base, gap, move, forensic score and operating reasons.

---

## 7. Numbers — data correctness contract

Numbers is built from normalized SEC filing facts.

### SEC period selection

Companyfacts repeats comparative periods in later filings.
Do NOT assign a fact to a fiscal year from row['fy'] alone.

Rules:
- resolve fiscal year from the fact's own period end;
- use company fiscal-year-end metadata;
- keep the latest filing for that exact economic period;
- annual UI deduplicates legacy stored periods by period end;
- no destructive cleanup of historical production data is required.

### Quarter / TTM rules

- filing-aware Q1 / Q2 / Q3 / derived Q4;
- Q2/Q3 YTD differencing only when predecessor exists;
- Q4 = FY - Q1 - Q2 - Q3 only when all components exist;
- TTM only from four distinct fiscally consecutive quarters;
- if a quarter is missing, TTM is withheld;
- quarterly growth compares the same fiscal quarter one year earlier;
- data completeness shows annual count, quarter count, current basis and missing core fields.

Core fields: Revenue, Gross Profit, Operating Income, Net Income, CFO, CapEx, FCF.

Visualization:
- Revenue = columns
- FCF = line
- TTM enters only when its four-quarter sequence is valid
- forecast Revenue is visually distinct
- margins and working-capital remain separate context

Missing SEC facts remain missing; the UI does not fill them cosmetically.

---

## 8. Research Command Center

Desktop rules:

- Price / Base / Base gap never wrap;
- numeric cells use tabular numbers;
- Manage is a narrow action-width column;
- Freshness is only Xm or X.Xh, never a timestamp;
- horizontal scroll is only a narrow-screen fallback;
- Bear/Bull do not repeat in the table;
- note below table is plain text: Process = approved Research gates. Validate = latest point-in-time walk-forward validation status (NOT RUN / LIMITED / VALIDATED / REVIEW).

---

## 9. Process Readiness

Canonical mutation endpoints are explicit POST routes:

- /company/<ticker>/readiness/<gate>/approve
- /company/<ticker>/readiness/<gate>/revoke

Approve → Reopen → Approve must all return success through the partial-update UI.
A 405 is a release blocker.

Mutation is synchronous, patches cached readiness immediately, creates no RECALCULATE job, and shows inline errors.

---

## 10. Financial Flows

Location: Research → Financial Flows.

Rules:
- Income Statement and Cash Flow share one renderer;
- signed negatives stay signed;
- full available desktop width is used;
- final graph column sits at the right edge of the available diagram width;
- no artificial minimum-height box;
- no old Operating/bridge/Profit/retained cash/Cost/distribution/loss legend below the diagram;
- mobile uses ledger representation;
- no visible internal engine/build version.

---

## 11. Settings and visible version

Settings is the ONLY user-facing version surface and must show current VERSION clearly.

Recent Jobs:
- collapsed by default;
- compact summary shows running / queued / failed / cancelled;
- click reveals full table;
- full table retains target, status, attempts, errors and cancellation controls.

Do not show release/build/calculation-version identifiers in Research, Audit, Financial Flows, Trace, reports, report filenames, publication pages, normal navigation/header/footer.

Internal DB lineage and /health may keep machine-readable metadata.

---

## 12. Canonical Research workflow

**Overview → Business → Numbers → Expectations → Valuation → Bear Case → Catalysts → Financial Flows → Management → Tape / Flows → Monitoring → Decision Journal → Sources / Audit**

Canonical conclusions:
- RESEARCH INCOMPLETE
- READY TO VALIDATE
- LONG READY
- LONG WATCH
- SHORT READY
- SHORT WATCH
- DATA REVIEW
- NO EDGE · WAIT

One literal _company_header.html component is mandatory across Research sections, Valuation, Financial Flows, Validate and Portfolio security.

Manual analyst edits are sacred.

---

## 13. Reports and publication

CONTROL reports:
- Executive PDF
- Full PDF
- Full Word
- Discovery landscape PDF

Report requests read stored/materialized data, never run heavy calculations synchronously, and fall back to valid PDF/DOCX if rich rendering or optional stored data fails.

Publication remains PRIVATE / FRIEND / INSIDER.

---

## 14. UI / mobile

- canonical responsive navigation
- institutional blue; never purple
- centralized semantic colors
- real light/dark themes
- mobile Research tabs usable
- backdrop / Escape / resize reset
- responsive forms/tables/charts
- desktop tables fit available content width unless viewport is genuinely narrow
- footer: Lose Money Rules

Broken mobile navigation blocks release.

---

## 15. 0.2.5 release gates

Before merge:

1. Python syntax
2. JavaScript syntax
3. workflow YAML
4. all baseline tests
5. 0.2.1–0.2.4 regression tests updated only where old behavior is intentionally obsolete
6. dedicated 0.2.5 regression suite
7. production-minimal startup smoke
8. every Research section GET returns HTTP 200 in release smoke
9. explicit approve/revoke endpoints pass Approve → Reopen → Approve
10. gate mutation creates no RECALCULATE job
11. Settings version + collapsed Recent Jobs
12. Command Center numeric-nowrap / compact Manage / relative Freshness / plain note
13. Financial Flow full-width / no redundant legend
14. SEC comparative-period resolver tests
15. consecutive-quarter TTM tests including missing-quarter rejection
16. Numbers Revenue-column + FCF-line
17. Numbers quarterly evidence + completeness
18. Discovery rejects penny/invalid/non-operating securities before forensic work
19. Discovery final candidate requires Base fair value + operating confirmation
20. Discovery rejects legacy pre-forensic payloads
21. Discovery may validly return zero candidates
22. report export tests
23. mobile navigation tests
24. no user-facing old release/build labels
25. VERSION == State-Version == 0.2.5

After merge:

26. main CI green

Production:

27. manual Namecheap deploy
28. candidate /health HTTP 200
29. version = 0.2.5
30. architecture = web-native
31. automatic rollback if candidate health fails

Only after production health succeeds is 0.2.5 LIVE.

---

## 16. Development / deployment state

Production remains on deployed 0.2.4 until 0.2.5 passes every gate.

0.2.5 branch: 0.2.5.

Required sequence:

0.2.5 branch → PR CI green → merge main → post-merge main CI green → manual Namecheap deploy → production /health confirms 0.2.5.

Do NOT call 0.2.5 deployed merely because it exists in GitHub or main.

---

## 17. Mandatory update rule

Every future release PR MUST update this file when VERSION, deployment state, architecture, workflows, jobs, price/history behavior, provider logic, SEC normalization, Discovery logic, analytical engines, reports/publication, security assumptions, visible-version policy, known production issue, or next-release work changes.

If VERSION and State-Version differ, CI must fail.

This file is the concise handoff; release-specific tests and PR evidence are the detailed audit.
