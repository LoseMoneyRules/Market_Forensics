# Market Forensics — CURRENT STATE

> READ THIS FIRST in every new development chat.
>
> This file is the single source of truth for the active Market Forensics release state.
> Update it in the SAME PR whenever VERSION, architecture, deployment state, release gates,
> job execution, provider/data logic, SEC normalization, Discovery, Fundamentals, analytical
> engines, reports/publication, security, Portfolio/Risk, or a material product workflow changes.
>
> `docs/HOW_MARKET_FORENSICS_WORKS.md` is the canonical product/decision-system contract.
> Any material change to workflow, rules, thresholds, data policy, valuation, validation,
> Portfolio separation, privacy/security or permanent UI invariants must update that file too.

**State-Version: 0.2.10**  
**Product:** Market Forensics  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → bounded background jobs → cached/materialized results → non-disruptive UI updates  
**Production:** 0.2.9 on Namecheap  
**Verified production baseline:** deploy run `35395407123` / deploy #45 = completed / success on main commit `e8ffdd5316f44f37647314aba94950912bd55fd0`; pre-upload tests, startup smoke, candidate health and post-cleanup health passed; persistent reporting-vendor rebuild/upload was skipped because it was unchanged  
**Main baseline before 0.2.10 merge:** VERSION `0.2.9` at `e8ffdd5316f44f37647314aba94950912bd55fd0`  
**Latest verified main release CI:** run `35394852070` / #927 = completed / success for the 0.2.9 merge; the later `[skip ci]` CURRENT_STATE sync did not change runtime code  
**0.2.10 candidate:** branch `release/0.2.10-correctness`, created directly from clean main `e8ffdd5316f44f37647314aba94950912bd55fd0`; no old development branch is its base  
**Verified 0.2.10 code CI before documentation/version commits:** run `35416840585` / #952 = completed / success at `52b470e34fbf59d8ad3d9508e15e0a80af217206`, including the full prior regression suite and dedicated 0.2.10 correctness tests  
**Release phase:** 0.2.10 candidate; PR/merge pending. Production remains 0.2.9. No 0.2.10 production deploy is authorized by merge alone.  
**Branch:** `release/0.2.10-correctness`

Production and main are separate states. The 0.2.10 release must merge and pass post-merge main CI before any explicit Namecheap deploy is considered.
---

## 0.2.10 release scope — correctness closure

0.2.10 is deliberately limited to three correctness gaps. It adds no unrelated feature work.

### Valuation fail-closed
- Bear / Base / Bull remain visible whenever mathematically available.
- Stored Base-quality provenance is carried into materialized valuation output.
- INTRINSIC and MANUAL_OVERRIDE are decision-grade.
- PROVISIONAL_STORED_FALLBACK, PROVISIONAL_REFERENCE_FALLBACK and unresolved/mixed data states remain visible but cannot create ATTRACTIVE / EXPENSIVE, POSITIVE / NEGATIVE EDGE, LONG / SHORT READY/WATCH or value-based Discovery qualification.
- Current market price is comparison/reference data only and cannot prove intrinsic fair value.
- Old 0.2.9 caches are re-read against stored model quality without running providers or heavy valuation work during normal GET navigation.
- Discovery external names continue to require intrinsic Base with at least two usable valuation methods; covered names also require decision-grade stored Base quality before local forensic gap logic can qualify them.

### Management promises
- Automatic guidance extraction is comparability-first.
- Promise provenance preserves source/provider, source date, accession/form when available, metric, target period, basis/definition, comparability reason and status.
- Interim/quarterly guidance, incompatible periods/units/ranges, adjusted/non-GAAP definitions, management-defined FCF, retrospective guidance, later restatements and unresolved target-year/comparator ambiguity are not forced into MET/MISS.
- Non-comparable evidence remains EVIDENCE_ONLY; comparable unresolved promises remain PENDING; only economically comparable actuals may produce MET or MISS.
- Management remains execution/accountability evidence, not personality or integrity scoring.

### Validate canonical policy
- `mfapp/validation_policy.py` is the single threshold policy.
- VALIDATED requires at least 5 valid scored samples and reliability >= 65.
- Insufficient samples are LIMITED even with a high score; very weak/failed runs are REVIEW; no run is NOT RUN.
- Historical-run result state, Validate page, Process Readiness, Decision Lenses, Research Conclusion and report/cache consumers use that canonical state rather than independent hardcoded thresholds.
- Legacy stored run rows are canonicalized on read, so a historical `VALIDATED` label cannot contradict current LIMITED policy for the same run.

Dedicated regression coverage lives in `tests/test_0210_correctness.py`. No deploy workflow, authentication/security, reporting-vendor mechanism, navigation, Tape, Portfolio or Financial Flows implementation is changed.

## 0.2.9 release scope — Tape / Positioning parity recovery

0.2.9 restores the accepted Local Tape Engine as a web-native background/materialized capability:
- Alpaca historical trades feed adaptive Large / Very Large / Whale notional buckets.
- Trade direction is an explicit `TICK_RULE_PROXY`; the UI never claims buyer identity.
- Historical SIP is attempted first; IEX fallback is labeled `IEX_PARTIAL_MARKET` and receives a Data Confidence penalty.
- Raw prints are processed in memory; only bounded daily aggregates are persisted through positioning events.
- Tape restores Institutional Flow, Short Pressure, Absorption, Long Demand, Battle Intensity, Price Resilience, Data Confidence and Net Tape 0–100.
- Rank A/B/C/D/F is restored with Local thresholds: A >=70, B >=58, C >42, D >30, F <=30.
- Forensic regimes are restored: ACCUMULATION, ACCUMULATION UNDER PRESSURE, BATTLE - BUYERS ABSORB, CONSTRUCTIVE, NEUTRAL / BATTLE, DISTRIBUTION, DISTRIBUTION / BEARS CONTROL and LOW DATA.
- Decision Lenses still consume only SUPPORTIVE / HOSTILE / MIXED path translation so Tape remains contextual and cannot bypass Research/Validate.
- FINRA Weekly Summary adds delayed ATS / non-ATS ticker evidence.
- Tape restores charts for Price + cumulative institutional flow, Volume, Price + Short Interest, Daily Short %, Net Large Flow, cumulative 5D/20D Large Flow, Absorption/Short Pressure/Net Tape, Whale Flow and ATS share.
- Tape restores WHAT CHANGED and WHAT WOULD CHANGE THE REGIME.
- Large/Whale flow is explicitly a size proxy, never named institutional ownership.
- `docs/LOCAL_WEB_PARITY_0_2_9.md` records the parity recovery.
- Application VERSION is `0.2.9`; production remains 0.2.8 until an explicit Namecheap deploy after merge/CI.

## 0.2.8 release scope

Operating-model documentation merged through PR #33:
- adds `docs/HOW_MARKET_FORENSICS_WORKS.md` as the canonical end-to-end explanation of how Market Forensics works;
- consolidates permanent rules previously scattered across release notes, Local parity, security, data-source and product specs;
- distinguishes intended rules from current implementation gaps rather than preserving inconsistencies as policy;
- records known weaknesses including validation-threshold divergence, provisional valuation labeling, activity-biased Discovery, database-limited peers, missing consensus/borrow/options/ownership depth and qualitative Business gaps;
- VERSION remains `0.2.8`.

Business partial-peer hotfix merged through PR #31:
- `/company/<ticker>/business` must render when automatic triangulation has peers but the relative-value overlay is not yet eligible.
- `peer_overlay` now keeps a stable schema even when not applied; Business also reads old/partial cached overlay fields defensively.
- Regression reproduces an ORCL-style cached payload with peers present, only one valuation method, and no legacy `intrinsic_base` key.
- VERSION remains `0.2.8`.

Final 0.2.8 micro-polish merged through PR #30:
- Process Readiness gate names link directly to their corresponding Research page.
- Coverage rows are sorted alphabetically by ticker before rendering.
- VERSION remains `0.2.8`; no architecture, data, provider, report or deployment behavior changes.

Same-version typography/readiness polish merged through PR #32:
- Process Readiness gate links use regular weight; bold is intentionally minimized throughout the product.
- Approve/Reopen re-evaluates Decision Lenses from already-materialized evidence and updates Research conclusion immediately; it does not enqueue a heavy RECALCULATE or call external providers.
- Closing the final Research gate therefore moves an unvalidated file from RESEARCH INCOMPLETE to READY TO VALIDATE immediately when the cached evidence is otherwise unchanged.

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
- bold is exceptional, not the default: links, gate names, normal labels and body values remain regular weight;
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

Validate is separate from human Research gate approval. Its canonical user-facing policy is:
- NOT RUN: no historical run;
- LIMITED: fewer than 5 valid scored samples, missing reliability, or an otherwise inconclusive result;
- VALIDATED: at least 5 valid scored samples and reliability >= 65;
- REVIEW: failed/error execution or very weak reliability.

These thresholds are defined only in `mfapp/validation_policy.py`; Process Readiness does not maintain a second policy.

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

## 8.1 Permanent clean-release rule

Permanent clean-release rule:
- Settings is the only user-facing application-version surface; normal templates do not hardcode release numbers.
- Heavy jobs remain explicit and background-only, including PRICE_HISTORY_REFRESH, POSITIONING_REFRESH, FINRA_IMPORT, SEC_INGEST, HISTORICAL_TEST and RECALCULATE.
- Job targets remain auditable as ticker, company or GLOBAL.
- Process Readiness state changes must support Approve → Reopen → Approve without a heavy recalculation or HTTP 405.
- desktop layout problems are not solved merely by horizontal scrolling.
- A release is not complete because a page returns 200; the underlying calculation/state transition must be exercised by tests.

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
- use as little bold as possible: reserve bold/strong weight for true hierarchy, compact headings, ticker symbols, critical statuses or primary decision outputs; normal body values, navigation links, gate names, explanatory text and routine labels should use regular weight unless there is a specific readability reason;
- readable typography is a release gate: normal UI 13–14 px, forms 14 px, micro-metadata no smaller than 12 px, chart labels 12–14 px;
- real dark mode;
- real light mode;
- centralized semantic colors;
- one canonical company header;
- one mobile navigation;
- desktop layout problems are not solved merely by horizontal scrolling;
- footer = Lose Money Rules.

---

## 10. 0.2.10 release gates

All accepted 0.2.9 architecture, UI, security, reporting-vendor and Tape behavior is inherited. Before merge, 0.2.10 must prove:

1. Python syntax, JavaScript syntax, workflow YAML and the full prior regression suite are green.
2. Dedicated `tests/test_0210_correctness.py` is green.
3. A provisional/reference Bear/Base/Bull remains visible but its Base cannot create VALUE classification, Variant edge or LONG/SHORT Research Conclusion.
4. An intrinsic Base still produces normal VALUE / Variant / Research Conclusion behavior.
5. Discovery rejects provisional/reference stored Base and keeps the existing intrinsic requirement for external candidates.
6. Management promises with incompatible period/basis/definition/unit or retrospective evidence remain EVIDENCE_ONLY rather than MET/MISS.
7. A genuinely comparable management promise produces the correct MET/MISS result and preserves provenance.
8. One validation policy requires >=5 valid samples and reliability >=65 for VALIDATED everywhere; a legacy raw run status cannot display VALIDATED on one surface and LIMITED on another.
9. Normal GET remains provider-free/heavy-engine-free.
10. No UI regression is introduced outside the minimal Management provenance text required for correctness.
11. VERSION == State-Version == 0.2.10.
12. Production is not changed by PR or main merge; Namecheap deploy remains an explicit later action.


The release is blocked by a broken capability even if its page returns HTTP 200.
---

## 11. Merge / deploy state

**Current phase:** production is 0.2.9 from successful manual deploy #45 (`35395407123`) on main commit `e8ffdd5316f44f37647314aba94950912bd55fd0`. 0.2.10 is a candidate on `release/0.2.10-correctness`; PR, merge and post-merge main CI are pending. No production deploy is part of the 0.2.10 merge workflow.

CURRENT_STATE transition rule:
- on PR/branch: document the current production baseline and candidate;
- immediately when the release is merged to `main`: sync this file on `main` with the real merge commit and post-merge CI;
- after any later successful Namecheap deploy: sync production version, deploy run and health separately;
- never infer production from `main`.

Required 0.2.10 sequence:

`0.2.9 production/main baseline verified → clean 0.2.10 branch → focused correctness changes → full tests → HOW/CURRENT_STATE/VERSION → PR CI green → merge main → post-merge main CI green → final CURRENT_STATE sync → deploy remains separate`

Production health remains mandatory for any later deploy:
- HTTP 200;
- `status = ok`;
- release `version` matches VERSION;
- `architecture = web-native`;
- `database = primary`;
- `reports = rich`.

If candidate health fails, deployment must fail/rollback rather than silently accepting a degraded runtime.

---

## 12. Mandatory update rule

Every future release PR MUST update this file in the same PR whenever VERSION, architecture,
deployment state, workflow, jobs, provider/data logic, SEC normalization, Discovery, Fundamentals,
analytical engines, Portfolio/Risk, report/publication, security, visible-version policy, release gates,
or product workflow changes.

Every future PR that materially changes how the product thinks or operates MUST also update
`docs/HOW_MARKET_FORENSICS_WORKS.md`. CURRENT_STATE records release truth; the operating-model
document records product logic, permanent rules, known weaknesses and decision-system behavior.

If VERSION and State-Version differ, CI must fail.

This file is the concise handoff; release tests and the Local/Web parity matrix contain detailed evidence.
