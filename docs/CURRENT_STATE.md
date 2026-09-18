# Market Forensics — CURRENT STATE

> READ THIS FIRST in every new development chat.
>
> This file is the single source of truth for the active Market Forensics release state.
> Update it in the SAME PR whenever VERSION, architecture, deployment state, release gates,
> job execution, provider/data logic, SEC normalization, Discovery, Fundamentals, analytical
> engines, reports/publication, security, Portfolio/Risk, or a material product workflow changes.

**State-Version: 0.2.8**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → bounded background jobs → cached/materialized results → non-disruptive UI updates  
**Production:** 0.2.8 on Namecheap  
**Verified production baseline:** deploy run `35380547531` / deploy #40 = completed / success on main commit `371af002bf9a74f9b302a7489a1b93624c6042ea`; candidate health + post-cleanup health passed with `version = 0.2.8`, `reports = rich`  
**Persistent report vendor verification:** deploy #40 detected `MF_REPORTING_VENDOR_PRESENT=1` and `MF_REPORTING_VENDOR_UPLOAD=0`; normal deploy did not retransmit the reporting vendor and vendor exclusions passed before backup  
**Main release:** 0.2.8 core merged through PR #27 at `60d9dc13a622eb9f66c414eaebfed6bfb677ccf0`; UI/report polish PR #28 at `102e6ed2bdbf2a6fe88781f334c0d178cc52f2fa`; data-completeness/layout polish PR #29 at `1a0ed6c7953890b0cc93da6fc623de7e57e707cf`; VERSION remains `0.2.8`  
**Latest verified main CI:** run `35384935210` / #842 = completed / success on `1a0ed6c7953890b0cc93da6fc623de7e57e707cf`; release tests, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed  
**0.2.8 production deploy:** deploy #40 includes PR #28 / main through `371af002bf9a74f9b302a7489a1b93624c6042ea`; PR #29 is not yet deployed  
**Release phase:** 0.2.8 + PR #28 polish are LIVE; PR #29 is complete and validated on main and needs a separate Namecheap deploy; final micro-polish candidate is pending CI / merge  
**Branch:** `polish/0.2.8-readiness-links-sort`

Deploy #40 is the authoritative production baseline. PR #29 keeps VERSION and architecture unchanged. After its production deploy, existing normalized fundamentals need one SEC re-ingest (Refresh all or per-company Refresh SEC + TTM) to benefit from the broader mappings; Recalculate alone only rebuilds analytics from already-stored normalized facts.
---

## 0.2.8 release scope

Business partial-peer hotfix candidate:
- `/company/<ticker>/business` must render when automatic triangulation has peers but the relative-value overlay is not yet eligible.
- `peer_overlay` now keeps a stable schema even when not applied; Business also reads old/partial cached overlay fields defensively.
- Regression reproduces an ORCL-style cached payload with peers present, only one valuation method, and no legacy `intrinsic_base` key.
- VERSION remains `0.2.8`.

Final 0.2.8 micro-polish merged through PR #30:
- Process Readiness gate names link directly to their corresponding Research page.
- Coverage rows are sorted alphabetically by ticker before rendering.
- VERSION remains `0.2.8`; no architecture, data, provider, report or deployment behavior changes.

Second same-version polish merged through PR #29:
- Coverage moves Refresh stale / Refresh all below the table and its single Process/Validate note; a fully approved process displays only READY rather than 13/13 plus READY.
- Validate keeps the walk-forward action bar immediately below the Research tabs.
- Fundamentals keeps its eight current KPIs on one row on wide screens and moves Data Completeness to the bottom.
- Data Completeness is analytical rather than merely “four quarters exist”: it flags statement gaps, historically expected balance-sheet fields that disappear, and historically available derived metrics such as DIO, Inventory / Revenue, ROIC and Net debt / FCF.
- SEC normalization broadens canonical concepts, adds exact consolidated statement-label fallback across available filing taxonomies, and composes debt from current + short-term + non-current components when no verified combined-debt fact exists. A bare LongTermDebt fact is not treated as total debt.
- Alpha Vantage remains secondary and fills only fields still missing after SEC; no valid SEC fact is overwritten and unresolved values remain visible rather than guessed.
- SEC ingest already recalculates Financial Flows immediately, so recovered normalized statement inputs rebuild the flow payload automatically.
- Tape posture / pressure / next-confirmation cards lose the colored top rule and use shorter descriptions.

Current same-version polish on `polish/0.2.8-ui-reports`:
- Research intelligence summary pills use regular weight instead of bold.
- Evidence compact signals regain semantic positive / negative / watch color treatment and the diagnostic score is more visible.
- Expectations no longer renders the redundant empty current KPI strip.
- Fundamentals explains Net debt / FCF basis and falls back to the latest available annual filed ratio when the current TTM ratio is unavailable.
- Tape Machine Read adds explicit posture, LONG/SHORT/LATERAL pressure and the next confirmation needed; old cached tape metrics are upgraded in place without forcing a provider refresh.
- Monitoring explains proposed schedule vs committed active rules vs observations/alerts; READY TO VALIDATE is moved to the bottom immediately before Publish.
- PDF/DOCX reports are redesigned around the accepted Local report DNA: research-intelligence strip, valuation map, current fundamentals, thesis/variant panels, FOR/AGAINST evidence and then full detail. Runtime remains web-native and uses current 0.2.8 data only.

0.2.8 is a correctness and workflow consolidation release built from the verified 0.2.7 production baseline:
- Overview has one curated Evidence block only, placed at the bottom immediately before report export. FOR, AGAINST, compact Evidence Signals, diagnostic score and blockers live together; no duplicate Evidence Signals card remains.
- PDF and DOCX exports no longer rely on Flask/Werkzeug `send_file(BytesIO)`. In-memory report bytes are returned as normal HTTP responses so Passenger / WSGI file wrappers cannot fail after the renderer fallback boundary.
- Expectations is forward-looking again. The repeated current Fundamentals strip is removed; Bear/Base/Bull operating path, price-implied expectations, variant perception and analyst expectation inputs remain.
- Gross Margin ingestion now accepts direct SEC Cost of Revenue / COGS facts and derives Gross Profit only through the exact Revenue − COGS bridge when Gross Profit is absent.
- Alpha Vantage, when the existing CONTROL API key is configured, is an optional secondary fundamental fallback for missing income-statement, balance-sheet and cash-flow fields. It never overwrites a valid SEC fact, matches exact fiscal period end dates and records separate provenance. SEC remains primary.
- ROIC remains filing/fundamental-data backed: broader source coverage may populate missing debt/equity/cash/tax inputs, but ROIC is still withheld rather than guessed when the required inputs cannot be proven.
- Income Statement Financial Flows is a sequential Revenue-to-Net waterfall rather than a split Sankey: Revenue → costs → Gross Profit → operating costs → Operating Income → other/interest → Pre-Tax → tax → Net Income. Cash Flow keeps the existing magnitude-flow renderer.
- Validate is restored as a first-class Research tab immediately after Sources / Audit and Decision Lenses link to the real validation route.
- Normal Namecheap application backup/upload/rollback mirrors explicitly exclude both `_reporting_vendor` and obsolete `_vendor`; the report vendor is still uploaded only when its dependency hash changes.
- Dedicated regression coverage lives in `tests/test_028_release.py`.

## 0.2.7 release scope

0.2.6 production baseline was verified before any 0.2.7 modification: main commit `6ecc385b29445db2a3940451f071a2425cd4f3c3`, VERSION `0.2.6`, CI #787 successful, Deploy #35 successful. CURRENT_STATE had still described production as 0.2.5; that stale statement is corrected here.

0.2.7 is a deep research-integrity release, not a UI-only patch:
- Report exports are fail-safe at the HTTP request boundary. Rich PDF/Word remains preferred; dependency-free PDF/DOCX emergency artifacts are served if branding/render/audit persistence fails. Audit failure cannot turn a valid download into HTTP 500.
- Namecheap report dependencies are deployment-cached: `mfapp/_reporting_vendor` persists across ordinary releases, is keyed by the SHA-256 of `requirements-reporting.txt`, and is excluded from normal backup/upload mirrors. Unchanged dependencies are not retransferred; changed dependencies are staged separately and swapped server-side with rollback preservation.
- Fundamentals always exposes Current basis, Gross Margin and ROIC. Missing filing inputs stay missing and are explained; no ROIC is guessed.
- Expectations has a current observed basis strip: Revenue growth, Gross Margin, Operating Margin, FCF Margin, Cash Quality (CFO/NI) and ROIC.
- Settings Refresh Runs is collapsed like Recent Jobs.
- Research Command Center removes Discover/Portfolio shortcuts, moves Refresh stale/all under the Process/Validate legend, removes bold from Research Conclusion, and gives Next Action/Lens/Manage safe wrapping/width.
- Overview Evidence Diagnostic is consolidated into the canonical FOR / AGAINST panel. Each visible signal shows its signed contribution weight; only the small summed diagnostic score remains. Threshold/validation cards are removed from this panel because they duplicate canonical Decision Lenses, readiness and Validate state.
- Readiness is served from live DB state rather than the heavy research cache. Monitoring accepts a locked thesis invalidation or active monitoring rules as evidence. Decision Journal closes as soon as a persisted journal exists.
- Research gate approvals are monotonic: changed evidence is flagged as changed/stale for review but an approval does not silently reopen until CONTROL explicitly reopens/revokes it.
- Business adds sourced macro FOR/AGAINST evidence from background FRED context (rates, credit, USD, oil, inflation, industrial production, retail sales) mapped to explicit sector sensitivities. Macro fetching never runs during normal GET navigation.
- Automatic triangulation now builds a peer fair-value cross-check from P/E, EV/Sales and FCF yield where evidence permits. It can influence forensic fair value only with >=2 peers and >=2 valuation methods, at an explicit 15–20% weight and a maximum +/-10% scenario shift. Intrinsic values remain stored/auditable and peer-only valuation is never allowed.
- 0.2.7 has dedicated regression coverage in `tests/test_027_deep_release.py`.


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
- renders Income Statement as a sequential Revenue-to-Net waterfall so each deduction/addition and remaining subtotal are explicit;
- keeps Cash Flow as a magnitude-flow diagram;
- keeps signed negatives signed and never fabricates positive widths;
- uses a ledger representation on mobile;
- exposes reconciliation warnings separately from the accounting bridge;
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
- Namecheap keeps the private report runtime at `mfapp/_reporting_vendor`, so the application path remains stable and no manual cPanel pip step is required;
- the deploy workflow hashes `requirements-reporting.txt` and stores a matching marker with the persistent report runtime;
- when that hash is unchanged, `_reporting_vendor` is excluded from production backup, application upload and rollback mirrors using the explicit lftp extended regex `^_reporting_vendor(/|$)`; the previous `--exclude-glob _reporting_vendor*` form is forbidden because lftp matches directory names with a trailing slash;
- before any real production backup, the workflow runs the exact exclusion as a remote `mirror --just-print` preflight and aborts if the plan contains any `_reporting_vendor` path;
- the first cache-aware deploy may bootstrap the marker from an already healthy vendor when the deployed `requirements-reporting.txt` has the same hash, avoiding a needless one-time retransmission;
- when report dependencies actually change, a new vendor tree is built separately, uploaded to a staging directory, and swapped into the stable path with server-side rename while the previous tree is retained for rollback;
- `app.py` continues loading that private report runtime before importing the application;
- the production-minimal smoke must report `reports = rich`;
- candidate production `/health` must also report `reports = rich`;
- a deployment without the rich report backend is a failed candidate and must not be declared LIVE.

The internal stdlib fallback may remain as defensive error containment, but it no longer qualifies a
production release as healthy.

Report exports must never expose private Portfolio shares/cost/P&L/sizing/private notes/journal data.
Report requests read stored/materialized data and do not perform heavy provider work synchronously.

Research and Discovery report downloads are in-memory artifacts and must be returned as normal response bytes. Do not reintroduce `send_file(BytesIO)` or another path that delegates an already-rendered in-memory artifact to Passenger's / the WSGI server's file wrapper.

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

## 10. 0.2.8 release gates

All 0.2.7 architecture/parity/security gates remain inherited. Before merge, 0.2.8 must additionally pass:

1. Python syntax, JavaScript syntax, workflow YAML and the full regression suite.
2. dedicated `tests/test_028_release.py`.
3. Full PDF and Full Word routes must return valid artifacts even with a hostile `wsgi.file_wrapper`; report delivery must not call `send_file(BytesIO)`.
4. Overview contains a single combined FOR / AGAINST / Signals evidence area after the editable research/readiness area and immediately before report export.
5. Expectations does not repeat the current Fundamentals KPI strip; forward Bear/Base/Bull path, price-implied expectations, variant perception and expectation inputs remain.
6. SEC normalization must ingest direct COGS/Cost of Revenue and recover Gross Profit only with the exact Revenue − COGS bridge when needed.
7. Optional Alpha Vantage fundamental fallback fills only missing fields, never overwrites SEC, matches fiscal period end and stores provider provenance.
8. Gross Margin and ROIC remain visible core Fundamentals outputs; no invented Gross Profit, debt, tax rate or ROIC is permitted.
9. Income Statement Financial Flows exposes `presentation = WATERFALL` and a sequential Revenue-to-Net bridge whose deltas reconcile to reported subtotals.
10. Cash Flow rendering remains intact and signed exceptions remain signed.
11. Validate appears immediately after Sources / Audit and links to the actual point-in-time validation route.
12. ordinary deploy backup/upload/rollback mirrors exclude both `^_reporting_vendor(/|$)` and `^_vendor(/|$)`.
13. unchanged reporting requirements keep `MF_REPORTING_VENDOR_UPLOAD=0`; changed requirements remain the only condition that stages/replaces the persistent report runtime.
14. VERSION == State-Version == 0.2.8.
15. normal GET navigation performs no new synchronous external fundamental fetches; provider refresh stays job/action driven.
16. no production merge/deploy state is claimed until the corresponding GitHub CI and Namecheap health results exist.

The release is blocked by a broken capability even if its page returns HTTP 200.
---

## 11. Merge / deploy state

**Current phase:** 0.2.7 is LIVE on Namecheap via successful deploy #38 (`35372729870`) from main commit `564ea20a6eb0663380f88a375f80b3f109cf54e9`. The deployment proved the recursive reporting-vendor exclusion in the real remote mirror plan, reused the persistent rich-report runtime with `MF_REPORTING_VENDOR_UPLOAD=0`, and passed candidate plus post-cleanup production health. 0.2.8 is currently a candidate on `release/0.2.8`; merge and deploy are pending.

CURRENT_STATE transition rule:
- on PR/branch: document the current production baseline and candidate;
- immediately when the release is merged to `main`: update this file on `main` to record the actual main commit and mark deploy as pending;
- immediately after successful Namecheap deploy: update this file on `main` again with the deploy run, production version and production health result;
- never leave an older production/main statement in this file after either transition.

Required 0.2.8 sequence:

`0.2.7 production baseline verified → 0.2.8 branch → PR CI green → merge main → post-merge main CI green → Namecheap deploy → production health green → CURRENT_STATE production sync`

Do not merge merely because individual fixes look correct.

Production health for a release remains mandatory:
- HTTP 200;
- `status = ok`;
- release `version` matches VERSION;
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
