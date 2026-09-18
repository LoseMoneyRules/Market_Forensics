# Market Forensics — CURRENT STATE

> READ THIS FIRST in every new development chat.
>
> This file is the single source of truth for the active Market Forensics release state.
> Update it in the SAME PR whenever VERSION, architecture, deployment state, release gates,
> job execution, provider/data logic, SEC normalization, Discovery, Fundamentals, analytical
> engines, reports/publication, security, Portfolio/Risk, or a material product workflow changes.

**State-Version: 0.2.6**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → bounded background jobs → cached/materialized results → non-disruptive UI updates  
**Production:** 0.2.5 on Namecheap  
**Main before 0.2.6:** 0.2.5 at `8feeda8c27ac46d5e3c7fd46cfbb0f76b19df52b`  
**0.2.5 post-merge CI:** run `35356768235` / run #771 = completed / success  
**0.2.5 Namecheap deploy:** run `35357228878` / deploy #34 = completed / success; candidate health + post-cleanup health passed; no rollback  
**Active candidate:** 0.2.6 — Local parity recovery  
**Branch:** `0.2.6`

Production remains on deployed 0.2.5 until 0.2.6 passes every gate.

0.2.5 passed its stated PR/post-merge gates and was deployed successfully. The subsequent V3.1.12-vs-Web
capability audit found material product regressions that the old smoke tests did not detect
(Portfolio-before-Research, Local-grade Risk/Position sizing, immutable journal outcomes, and the
production rich-report dependency contract). Therefore 0.2.5 remains the production baseline while 0.2.6 supersedes it as the active correction candidate.

---

## 1. Non-negotiable architecture

- Web-native Flask.
- MariaDB is the only production primary database.
- V3.1.12 is DNA/reference/accepted-product baseline only; there is no runtime dependency on it.
- CONTROL is the complete private operating workspace.
- FRIEND / INSIDER see only explicitly published, non-personalized research.
- Visibility remains PRIVATE / FRIEND / INSIDER.
- Never publish personal Portfolio holdings, shares, cost, P/L, position sizing, max loss,
  private journal, private notes, provider credentials or secrets.
- Argon2, TOTP/2FA, encrypted provider credentials, secure sessions and least privilege remain mandatory.
- Production MariaDB and the existing CONTROL account/2FA must never be reset by a release.
- `CALCULATION_VERSION` remains separate from application VERSION and is never a user-facing release label.

Normal GET navigation must not execute heavy provider or analytical work.

Primary background executor:

`python manage.py run-jobs --limit 5`

Recommended cPanel cadence: every minute.

---

## 2. Product flow and buy-side process

Product flow:

**Discover → Research → Validate → Portfolio**

Research owns:
Business, Fundamentals, Expectations, Valuation, Bear Case, Catalysts, Financial Flows,
Management, Tape / Flows, Monitoring, Decision Journal and Sources / Audit.

Portfolio owns:
real positions, LONG/SHORT side, cost basis, exposure, money risk and position sizing.

Capital Risk / Position controls must exist only under Portfolio. Research and Validate must never expose
shares, average cost, loss budget, sizing reference, liquidity/event haircut or max-position controls.
Research may keep thesis invalidation under Monitoring because that is a research falsification rule, not money-risk sizing.

Validation is separate, point-in-time and walk-forward.

Mandatory buy-side process:

**BUSINESS → FUNDAMENTALS → EXPECTATIONS → VALUATION → BEAR CASE → CATALYSTS → FLOWS → RISK → POSITION SIZE → MONITORING**

Rules:
- numerical invalidation thresholds are fixed before investment and are not rewritten after the fact;
- ADD ON EVIDENCE, NOT ON PRICE;
- actively search for evidence against the thesis;
- surface confirmation bias, thesis drift and narrative fitting;
- Bear / Base / Bull remain mathematically auditable;
- price target / fair value is a primary product output;
- missing data stays missing rather than being cosmetically filled.

---

## 3. Permanent parity rule

A web release may improve or supersede a Local V3.1.12 capability, but it may not silently remove it.

For every accepted Local capability, the Web must be classifiable as one of:
- PRESERVED;
- IMPROVED;
- SUPERSEDED by an explicitly equivalent or stronger web-native capability.

A feature is not considered present merely because a page returns HTTP 200 or a module exists.
Release gates must prove the user workflow and calculation actually work.

No patch-on-patch CSS, duplicate old/new implementations, silent workarounds, refresh-dependent fixes,
or apparent controls with non-functional backends.

---

## 4. 0.2.6 — Local parity recovery

### Research Command Center

The canonical table remains:
Security · Research conclusion · Value / Path · Model confidence · Price · Base · Base gap ·
Process · Validate · Freshness · Next action · Lens · Manage.

Typography contract:
- ticker and column titles may be bold;
- body values are not bold;
- Price / Base / Gap stay on one line;
- numeric cells use tabular numbers;
- Manage stays narrow;
- Freshness is only Xm / X.Xh;
- no full timestamps;
- Bear/Bull are not duplicated in the Command Center.

The explanatory note remains plain text:
`Process = approved Research gates. Validate = latest point-in-time walk-forward validation status (NOT RUN / LIMITED / VALIDATED / REVIEW).`

### Fundamentals — canonical replacement for Numbers

The canonical Research route is now:
`/company/<ticker>/fundamentals`

The old `/company/<ticker>/numbers` route is compatibility-only and permanently redirects to Fundamentals.
The stored ResearchState field may remain `numbers` internally to preserve production data; this is not a user-facing name.

Fundamentals keeps all 0.2.5 filing-correctness rules:
- fiscal year derived from economic period end + real fiscal-year-end;
- latest filing for the same economic period;
- annual period de-duplication by period end;
- Q1/Q2/Q3 filing-aware;
- Q2/Q3 YTD differencing only with predecessor;
- Q4 only from FY - Q1 - Q2 - Q3 when components exist;
- TTM only from four fiscally consecutive quarters;
- same-quarter YoY comparisons;
- explicit completeness/missing fields;
- missing facts remain missing.

Core filing fields remain:
Revenue, Gross Profit, Operating Income, Net Income, CFO, CapEx and FCF.

Restored Local forensic metrics:
- Gross Margin;
- Operating Margin;
- FCF Margin;
- CFO / Net Income;
- DSO;
- DIO;
- DPO;
- Cash Conversion Cycle;
- Inventory / Revenue;
- Receivables / Revenue;
- diluted/outstanding share-count YoY;
- ROIC only when Operating Income, Pretax, Tax, Debt, Cash and Equity are actually available.

ROIC does NOT use the Local 21% default-tax fallback when tax facts are missing.

Working Capital chart:
- Inventory = left scale;
- Receivables = right scale;
- independent axes are intentional because different balance-sheet magnitudes must not flatten one series.

### Financial Flows

Location remains Research → Financial Flows, with Income Statement and Cash Flow tabs.

The renderer:
- uses full available desktop width;
- keeps signed negatives signed;
- never creates fake positive ribbons;
- keeps the final graph column at the usable right edge;
- uses taller nodes so a two-line label cannot cover the numeric value;
- has no obsolete category legend;
- uses a ledger representation on mobile;
- exposes no internal build/calculation version.

### Portfolio — independent from Research again

A real position can exist before a thesis or Coverage row exists.

Portfolio add/edit requires:
- validated ticker;
- LONG / SHORT side;
- shares > 0;
- average cost;
- optional exposure tags;
- optional private notes.

Creating a Portfolio position must NOT automatically create Research/Coverage.

If Research already exists, Portfolio links to it.
If Research does not exist, the position remains fully usable as PORTFOLIO ONLY and Research can be started later.

Removing a holding:
- removes the current Position;
- preserves Research;
- preserves money-risk history;
- preserves audit/history;
- sets an attached Research investment state back to WATCHLIST.

Portfolio analytics include:
- gross market value;
- Long value;
- Short value;
- net exposure;
- side-aware P/L;
- gross position weights;
- concentration HHI;
- risk-limit breaches;
- manual shared-factor exposure from tags;
- cached/background pairwise correlations.

### Risk / Position

Research thesis invalidation and Portfolio money risk are separate.

Research `RiskPlan` keeps thesis invalidation and pre-investment thesis discipline.

New private `PortfolioRiskPlan` is user+security scoped and stores:
- max portfolio loss budget %;
- sizing reference price;
- liquidity/event haircut %;
- max position cap %;
- correlation/factor notes;
- portfolio/thesis kill-switch;
- entry/add/trim/exit conditions;
- private notes.

Sizing math preserves the accepted Local model:

`loss_to_reference = abs(current_price - sizing_reference_price) / current_price`

`adjusted_loss = loss_to_reference + liquidity_event_haircut`

`suggested_position = min(max_position_cap, risk_budget / adjusted_loss)`

The sizing reference is explicitly NOT the thesis invalidation price.

Existing 0.2.5 money-risk values are copied into the new PortfolioRiskPlan by an additive,
non-destructive migration. The old Research RiskPlan is not deleted or rewritten.

### Decision Journal

The original decision record is immutable after commit.

A committed Decision Journal entry freezes:
- decision;
- Research state;
- investment state;
- thesis snapshot;
- valuation snapshot;
- Research invalidation snapshot;
- evidence for;
- evidence against;
- bias/discipline notes.

Outcome, post-mortem and lessons are appended later in separate `DecisionOutcome` rows.
Appending an outcome never rewrites the original DecisionJournal or decision snapshot.

---

## 5. Reports — production contract

CONTROL reports remain:
- Executive PDF;
- Full PDF;
- Full Word;
- Discovery landscape PDF.

The rich PDF/Word stack is now a first-class production dependency:
- `requirements.txt` includes `requirements-reporting.txt`;
- the deploy payload vendors the report runtime into `mfapp/_reporting_vendor`, so Namecheap does not depend on a forgotten manual cPanel pip step;
- `app.py` loads that private report runtime before importing the application;
- the production-minimal smoke must report `reports = rich`;
- candidate production `/health` must also report `reports = rich`;
- a deployment without the rich report backend is a failed candidate and must not be declared LIVE.

The internal stdlib fallback may remain as defensive error containment, but it no longer qualifies a
production release as healthy.

Report exports must never expose private Portfolio shares/cost/P&L/sizing/private notes/journal data.
Report requests read stored/materialized data and do not perform heavy provider work synchronously.

---

## 6. Discovery — forensic contract retained

Discovery result contract remains **FORENSIC_FAIR_VALUE_V1**.
Legacy pre-0.2.5 Discovery payloads are rejected.

Stage 1 only identifies where to investigate using cheap sources:
- Alpaca Most Active / Movers;
- IEX snapshot;
- asset metadata;
- stored Research cache.

Fail-closed universe rules remain:
- active/tradable operating equity;
- major US exchange;
- no warrants, rights, units, ETFs, ETNs, funds, blank-check/SPAC shells, preferreds or notes;
- new Long >= $5;
- new Short >= $10;
- new-name volume >= 500k when available;
- new-name dollar volume >= $50M;
- Short must be shortable.

Stage 2 is a bounded forensic enrichment.
External names use SEC submissions + Companyfacts, actual fiscal-year-end, SIC/company type,
annual and quarterly filing history, and the same Market Forensics valuation engine.

A final Long requires Base gap >= +20% and confirming Long operating evidence.
A final Short requires Base gap <= -20%, confirming deterioration and shortability/actionability.
Base must be INTRINSIC with at least two usable valuation methods; no market/reference fallback.

There is no filler quota. Zero candidates is a valid and preferred result when nothing qualifies.

---

## 7. Process Readiness

Canonical state-mutation endpoints remain:
- `/company/<ticker>/readiness/<gate>/approve`
- `/company/<ticker>/readiness/<gate>/revoke`

Approve → Reopen → Approve must work without:
- HTTP 405;
- RECALCULATE job;
- mandatory page reload.

A 405 is a release blocker.

The old `numbers` gate key is migrated non-destructively to canonical `fundamentals`.

---

## 8. Additive 0.2.6 production migration

0.2.6 adds tables; it does not rebuild or reset production data:
- `mf_position_profile`;
- `mf_portfolio_risk_plan`;
- `mf_decision_outcome`.

Migration key:
`release_0_2_6_local_web_parity`

The migration:
- creates PositionProfile for existing holdings;
- infers LONG/SHORT from the current stored investment state when possible;
- copies existing monetary RiskPlan values into PortfolioRiskPlan;
- renames stored Research gate approval `numbers` → `fundamentals`;
- records the migration;
- does not delete the legacy Research RiskPlan;
- does not reset users, passwords, TOTP, encrypted secrets, Research, Portfolio positions,
  publication history or audit history.

`db.create_all()` creates additive tables before the migration runs.

---

## 9. Settings, versioning and UI invariants

Settings is the ONLY normal user-facing application-version surface.

Recent Jobs:
- collapsed by default;
- compact running / queued / failed / cancelled summary;
- click expands full list;
- ticker/target, status, attempts, errors and cancellation controls remain.

Never show CALCULATION_VERSION to users.

Workflow health assertions read VERSION dynamically; do not hardcode a 0.2.x release in deployment health logic.

Permanent UI rules:
- never purple;
- institutional blue;
- readable typography is a release gate: normal UI 13–14 px, forms 14 px, micro-metadata no smaller than 12 px, chart labels 12–14 px;
- real dark mode;
- real light mode;
- centralized semantic colors;
- one canonical company header;
- one mobile navigation;
- desktop layout problems are not solved merely by horizontal scrolling;
- footer = Lose Money Rules.

---

## 10. 0.2.6 release gates

Before merge, all of the following must pass:

1. Python syntax.
2. JavaScript syntax.
3. workflow YAML.
4. all baseline/regression tests.
5. 0.2.1–0.2.5 tests updated only where 0.2.6 intentionally changes the contract.
6. dedicated `tests/test_026_parity.py`.
7. every Research section returns without 500.
8. Fundamentals is canonical and old Numbers route redirects.
9. SEC comparative-period and four-consecutive-quarter TTM correctness remains green.
10. Working Capital uses two independent scales.
11. Command Center body has no bold except ticker.
12. Financial Flow two-line node labels leave the value visible.
13. Portfolio can add a validated real holding before Research exists.
14. Portfolio-only security detail works.
15. LONG/SHORT P/L and exposure are side-aware.
16. remove Position preserves Research and money-risk history.
17. Risk/Position sizing reproduces the accepted downside formula.
18. 0.2.6 migration preserves old RiskPlan and existing holdings.
19. Decision Journal outcome/post-mortem is append-only.
20. rich PDF and Word renderer is installed by production requirements.
21. production-minimal smoke requires `reports = rich`.
22. candidate Namecheap `/health` requires `reports = rich`.
23. report/publication privacy boundaries remain green.
24. Discovery FORENSIC_FAIR_VALUE_V1 tests remain green.
25. Approve → Reopen → Approve remains green with no RECALCULATE.
26. Settings is the only visible version surface.
27. VERSION == State-Version == 0.2.6.

The release is blocked by a broken capability even if its page returns HTTP 200.

---

## 11. Merge / deploy state

Required sequence:

`0.2.6 branch → PR CI green → merge main → post-merge main CI green → manual Namecheap deploy → production health`

Do not merge merely because individual fixes look correct.

Do not redeploy 0.2.5 as a substitute for the 0.2.6 corrections.

Do not call 0.2.6 LIVE until the deploy workflow succeeds and production `/health` returns:
- HTTP 200;
- `status = ok`;
- `version = 0.2.6`;
- `architecture = web-native`;
- `reports = rich`.

If candidate health fails, deployment must fail/rollback rather than silently accepting a degraded report backend.

---

## 12. Mandatory update rule

Every future release PR MUST update this file in the same PR whenever VERSION, architecture,
deployment state, workflow, jobs, provider/data logic, SEC normalization, Discovery, Fundamentals,
analytical engines, Portfolio/Risk, report/publication, security, visible-version policy, release gates,
or product workflow changes.

If VERSION and State-Version differ, CI must fail.

This file is the concise handoff; release tests and the Local/Web parity matrix contain detailed evidence.
