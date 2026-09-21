# Market Forensics — How It Works, Operating Model & Product Rules

> Canonical current operating contract for Market Forensics.
>
> This document explains how the product is supposed to work end-to-end, what each analytical layer is allowed to conclude, which rules are permanent, where the current implementation is incomplete, and how future changes should be evaluated.
>
> CURRENT_STATE.md remains the release/deployment source of truth. This file is the product/decision-system source of truth.
>
> Historical specs and release notes remain useful context, but when they conflict with this document plus the current tested implementation, they are historical rather than canonical.

**Current product line:** 0.3.0  
**Architecture:** web-native Flask + MariaDB  
**Primary workflow:** Discover → Research → Validate → Portfolio  
**Core investing discipline:** BUSINESS → FUNDAMENTALS → EXPECTATIONS → VALUATION → BEAR CASE → CATALYSTS → FLOWS → RISK → POSITION SIZE → MONITORING  
**Primary operating rule:** ADD ON EVIDENCE, NOT ON PRICE.

---

## 1. What Market Forensics is

Market Forensics is a private equity-research and decision-support system.

Its job is not to produce a generic stock score. Its job is to answer, with auditable evidence:

1. What is this business and what economically drives it?
2. What do the filed numbers actually say?
3. What does the current market price appear to require?
4. What is a defensible Bear / Base / Bull fair-value range?
5. What evidence supports the thesis?
6. What evidence argues against it?
7. What must happen for the thesis to work?
8. What would invalidate it?
9. Has this research process historically behaved well enough to trust?
10. If the user owns the security, how should position risk be managed separately from the research conclusion?

The most important output is still fair value / target price, but price target alone is never sufficient to create a decision-ready conclusion.

Market Forensics must always distinguish between:

- **facts** — filed or sourced observations;
- **derived metrics** — deterministic calculations from facts;
- **model assumptions** — explicit analytical choices;
- **diagnostics** — useful context that cannot bypass the process;
- **human approval** — CONTROL confirmation that the evidence has been reviewed;
- **validation** — historical point-in-time testing of the process;
- **portfolio decisions** — capital allocation and money risk, separate from Research.

---

## 2. What Market Forensics is not

Market Forensics is not:

- a price-momentum signal generator;
- a single composite-score stock picker;
- a replacement for source review;
- a system that fabricates missing fundamentals;
- a system where a large valuation gap automatically means BUY;
- a portfolio-sizing engine disguised as Research;
- a publication system that exposes private holdings or risk;
- a synchronous page that calls providers every time the user navigates;
- a tool where an automated model silently overwrites analyst work;
- a tool where historical validation is allowed to use future information.

---

## 3. Permanent principles

These rules are product-level invariants.

### 3.1 Evidence before interpretation

The canonical data path is:

SOURCE → RAW/PAYLOAD → NORMALIZED FACT → DERIVED METRIC → ANALYTICAL MODEL → MATERIALIZED RESEARCH CACHE → HUMAN REVIEW → VALIDATE → PORTFOLIO

Important automated conclusions should be traceable to sources, transformations and assumptions.

### 3.2 Missing data stays missing

Do not cosmetically fill unavailable accounting facts.

Exact accounting bridges are allowed when mathematically deterministic, for example:

- Gross Profit = Revenue − COGS;
- FCF = CFO − CapEx;
- Q4 = FY − Q1 − Q2 − Q3 only when the components are compatible and complete.

Model priors may be used as explicit assumptions, but they are not facts and must never be presented as if they came from a filing.

### 3.3 Reported accounting is not automatically economic reality

The filed statement is always preserved. Market Forensics may add an auditable **Economic Reality** interpretation layer, but it must never silently rewrite the filing.

Permanent classification rules:

- total liabilities are never treated as financial debt;
- operating leases are contractual obligations and operating capital, not automatically borrowing;
- finance leases, supplier finance/reverse factoring, pensions, redeemable claims, preferred/minority enterprise claims and contingent consideration are classified separately from ordinary operating liabilities;
- restricted cash does not offset financing debt;
- deferred/contract revenue, deferred tax and operating provisions are not generic financial debt;
- growth-capex, SBC, restructuring/impairment/acquisition charges and high-R&D business models can distort naive FCF, margin or ROIC signals and therefore require a reported-versus-economic view;
- a D&A-based maintenance-capex estimate is a diagnostic proxy, never a reported fact;
- sector-specific balance sheets such as banks, insurers and REITs must not inherit generic industrial leverage, working-capital or FCF scoring;
- if a material classification cannot be resolved, the model must fail closed: keep the number visible, lower Economic Reality quality, disable the affected automatic score/method and require review rather than guessing.

The canonical valuation equity bridge uses classified economic net debt when available. Operating lease liabilities remain separately visible unless the valuation method itself is explicitly lease-adjusted. This avoids mixing a lease-inclusive debt bridge with operating metrics that already expense operating lease economics.

Discovery Stage 2 uses the same Economic Reality engine as Research. A material unresolved accounting classification cannot become a P1/P2 candidate; it remains WATCH until Research verifies the bridge.

### 3.4 Company quality and stock value are separate questions

Market Forensics must be able to answer two different questions without collapsing them into one score:

1. **Is this economically a good company?**
2. **Is the security attractive at the current price?**

The canonical Company Quality read is dimension-based, not a hidden composite score. It evaluates, when evidence exists:

- operating durability;
- profitability and returns on capital;
- cash quality;
- balance-sheet and fixed-charge resilience;
- reinvestment efficiency;
- capital allocation and dilution;
- accounting quality / unresolved distortions.

Dimension states are explicit: STRONG, SOUND, WATCH, RED FLAG or UNKNOWN. The overall filed-evidence state may be STRONG, SOUND, MIXED, FRAGILE, UNRESOLVED or INSUFFICIENT EVIDENCE.

A strong company does **not** receive a hidden valuation premium merely because the quality engine likes it. Its quality should already appear through observed growth, margins, returns and cash generation. Evidence-backed weaknesses may, however, conservatively reduce an automatically generated valuation through a bounded and visible Quality → Valuation policy: higher discount rate, lower forward/terminal growth, a Bear-probability shift, or exclusion of a valuation method whose accounting basis is unreliable.

Every automatic price effect must appear in a Valuation Impact Ledger. Items that are only context — for example a growth-capex proxy or R&D intensity without a defensible capitalization model — remain context and must not silently alter fair value.

User-edited valuation assumptions remain explicit human inputs. Automatic policy may define/rebuild defaults and enforce data-integrity method exclusions, but it must not secretly rewrite a reviewed manual assumption.

Company Quality is a filed/economic evidence read, not a claim that moat, competitive durability, customer concentration or product quality has been proven. Those qualitative questions remain part of the Business gate and require sourced review.

### 3.5 Fundamentals is the accounting evidence room

Fundamentals is not a headline KPI page. It is the canonical place to inspect the filed operating evidence before valuation or narrative.

The page must preserve four layers in this order:

1. **reported/normalized facts** — the complete current normalized income statement, cash-flow, balance-sheet, capital-return and share fields, plus comparable annual/quarter history;
2. **derived trends** — growth, margins, cash conversion, working-capital cycles, ROIC, asset turnover, dilution and other deterministic metrics;
3. **forensic evidence** — explicit strengths, WATCH items, RED FLAG items, deterministic reconciliation inconsistencies and data/classification gaps;
4. **Economic Reality** — financing claims, leases, liquidity offsets, fixed charges, hidden/debt-like obligations and material accounting distortions, with source provenance.

Fundamentals must expose source/provenance for the current normalized filing basis and the Economic Reality fact inputs when stored. Missing facts stay missing.

Automatic forensic interpretation is deterministic and materialized in the Research cache by the normal background recalculation job. Normal Fundamentals GET navigation reads that stored result and must not run SEC/network calls or heavy analytical engines synchronously.

Reconciliation warnings such as Revenue − COGS ≠ Gross Profit, CFO − CapEx ≠ FCF, Assets ≠ Liabilities + Equity, or Pretax − Tax ≠ Net Income are **REVIEW** evidence. They are not accusations of accounting misconduct: presentation differences, noncontrolling/mezzanine claims, discontinued operations or ingestion mapping can explain a mismatch.

Data completeness distinguishes:
- all visible/historically expected missing fields;
- decision-critical gaps needed for the core analysis;
- Economic Reality readiness.

A non-critical missing field remains visible without automatically blocking all analysis. Missing/unresolved Economic Reality does block canonical leverage and the EV-to-equity bridge.

Existing Coverage created before 0.3.0 is migrated non-destructively: if the stored current filing lacks the Economic Reality snapshot, Market Forensics queues a deduplicated SEC ingest in the background, keeps reported facts visible, and fails closed on economic leverage/EV conclusions until the new classification is materialized.

PDF/Word Full Research reports carry the same materialized Fundamentals strengths, red flags/watch items, inconsistencies and data gaps.

### 3.6 Price is the decision interface, not the thesis

The current price is used to compare against Bear / Base / Bull fair value and to reverse-engineer market-implied expectations.

Price movement by itself does not validate or invalidate a fundamental thesis.

### 3.7 ADD ON EVIDENCE, NOT ON PRICE

Position additions must be justified by improved evidence, not merely by a lower share price.

### 3.8 Invalidation is fixed before investment

Numerical thesis invalidation thresholds are set before investment and are not rewritten after earnings or price movement to preserve the narrative.

A locked pre-investment invalidation cannot be silently changed retroactively.

### 3.9 Research and Portfolio remain separate

Research asks:

> Is the company attractive, at what value, why, what must happen, and what would invalidate the thesis?

Portfolio asks:

> Given the research view and the real position, how much capital and money risk should be carried, and what does that position allow us to do now?

**Research judges the security. Portfolio decides what the existing position allows you to do.**

Shares, average cost, P/L, position size, money-loss budget, Portfolio sizing and Position Action live under Portfolio, not Research.

### 3.10 Human approval is explicit

Process Readiness is not a machine confidence score.

It is a record that CONTROL reviewed the current evidence for each research gate.

### 3.11 Validation cannot rescue incomplete research

Validate is downstream of Research.

A historical score cannot bypass missing Research gates or substitute for a thesis, valuation, bear case, monitoring rule or source review.

### 3.12 Diagnostics cannot override canonical decision states

Evidence score, Tape score, macro context, management score, peer comparison and similar diagnostics support interpretation.

They cannot bypass:

- Process Readiness;
- data-quality warnings;
- Validate;
- Research Conclusion logic;
- locked invalidation discipline.

### 3.13 Manual analyst work wins

Automatic drafting may populate blank fields or fields still marked as auto-generated.

Manual analyst edits are never silently overwritten by a refresh.

### 3.14 Normal navigation stays fast

Normal GET requests read stored/materialized data and render.

They must not:

- call SEC;
- call market providers;
- refresh FRED;
- run discovery;
- perform historical backtests;
- rebuild heavy analytical engines.

Heavy work is queued.

### 3.15 Fail visibly, not silently

When something cannot be proven or computed:

- show the gap;
- show the warning;
- show the data quality;
- preserve last-good information only when clearly labeled;
- never silently pretend a fallback is equivalent to primary evidence.

### 3.16 Minimal bold, institutional readability

Use as little bold as possible.

Bold/strong weight is reserved for real hierarchy, ticker symbols, critical status and primary decision outputs. Normal links, gate names, body values, explanatory text and routine labels use regular weight.

UI rules also include:

- no purple;
- institutional blue;
- body typography normally 13–14 px;
- forms around 14 px;
- micro metadata no smaller than 13 px;
- chart labels 13–14 px;
- light and dark modes must both remain usable;
- semantic state color is canonical across all status chips and equivalent state surfaces: positive/approved/strong = green; negative/failed/red-flag = red; watch/review/unresolved = amber; neutral/fair/lateral = gray; informational/running/queued = blue;
- neutral must never be rendered as caution merely because it is undecided, and dark mode must preserve the same semantic meaning rather than flattening all chips to one generic color;
- on company/research pages, once the main company header scrolls away, a compact ticker + latest stored/live price remains visible in the top bar so the active security is never ambiguous;
- desktop problems are not solved only with horizontal scrolling;
- empty decorative panels should not be rendered;
- footer remains Lose Money Rules;
- Settings is the only normal user-facing application-version surface;
- one canonical company header;
- one canonical mobile navigation; the drawer/control shell owns phone and tablet portrait navigation and must never leave an intermediate width without a reachable primary menu;
- research-step navigation is server-rendered; JavaScript may control open/closed state but must not reconstruct missing canonical markup;
- mobile controls use practical touch targets (about 44 px where applicable), important tables preserve information through controlled overflow/ledger layouts, and 320–430 px phone widths must remain usable without zoom;
- vertical rhythm is canonical rather than page-specific: peer sections/panels use about 18 px separation, form field/action stacks use about 14 px, and compact control groups use about 8–10 px; layout wrappers that zero child margins must restore the section gap themselves;
- shared component presentation belongs in canonical CSS; inline event/style fixes and release-number override layers are not an accepted cleanup strategy;
- Research → Financial Flows remains inside Research, not a separate primary product;
- Validate remains immediately after Sources / Audit.

### 3.17 No patch-on-patch implementation

Do not solve product regressions with stacked duplicate implementations.

Avoid:

- release-number CSS/JS layers loaded together;
- old/new duplicate controls;
- hidden DOM rewrite layers that replace canonical server-rendered research;
- silent refresh-dependent fixes;
- apparent controls with no working backend.

There should be one canonical implementation for a capability.

### 3.18 Local parity is preserved deliberately

Local V3.1.12 is not a runtime dependency, but accepted capabilities cannot silently disappear.

Every meaningful Local capability must remain classifiable as:

- PRESERVED;
- IMPROVED;
- SUPERSEDED by an explicitly stronger web-native workflow.

A page returning HTTP 200 is not proof of parity. The underlying state transition/calculation must work.

---

## 4. Architecture and execution model

### 4.1 Primary database

MariaDB is the only production primary database.

Local V3.1.12 is a design/analytical reference only. It is not a runtime dependency.

### 4.2 Request model

Fast request-path operations:

- navigation;
- reading stored Research;
- editing analyst text;
- approving/reopening readiness gates;
- saving assumptions;
- saving Portfolio state;
- creating journal entries;
- reading materialized analytics.

Heavy operations are background jobs:

- market refresh;
- price history;
- SEC ingestion;
- TTM rebuild;
- recalculation;
- research cache rebuild;
- FINRA refresh;
- positioning refresh;
- macro refresh;
- management filing scan;
- historical validation;
- market-wide Discovery;
- Portfolio correlations.

### 4.3 Job model

Typical state:

QUEUED → RUNNING → DONE / FAILED / CANCELLED / SUPERSEDED

The main worker command is:

python manage.py run-jobs --limit 5

Production normally runs this from cron, with a browser-triggered detached fallback when necessary.

Duplicate active jobs are compacted rather than multiplied.

Dead RUNNING leases are recovered so one crashed worker cannot block the queue forever.

### 4.4 Research cache

Heavy analytical outputs are materialized into a research cache.

Normal page navigation uses that cache rather than recalculating everything.

A cache cannot preserve decision-grade valuation state after the underlying Coverage or active valuation model has changed. If a materialized cache is stale, the last Bear / Base / Bull may remain visible for continuity, but valuation quality becomes DATA_WARNING, VALUE / Variant / Research Conclusion fail closed, value-based Discovery qualification is withheld, and a background RECALCULATE is queued. The normal GET does not execute providers or heavy analytical work itself.

Lightweight state — especially Process Readiness — is read live.

Gate approval/reopen may re-evaluate Decision Lenses from already-materialized evidence because this is cheap and does not require external providers.

---

## 5. Roles and privacy boundary

### CONTROL

CONTROL is the complete private operating environment.

CONTROL may access:

- Coverage;
- private Research;
- Bear/Base/Bull;
- private assumptions;
- thesis invalidation;
- Decision Journal;
- Portfolio;
- real positions;
- cost basis;
- P/L;
- position sizing;
- money-risk budgets;
- private notes;
- provider credentials;
- publication controls;
- user/access controls;
- audit history.

### FRIEND

FRIEND sees only explicitly published role-safe research.

No private Portfolio or personalized sizing.

### INSIDER

INSIDER may receive deeper explicitly published research than FRIEND, but still cannot access private Portfolio, sizing, journal, credentials or CONTROL-only operating metadata.

### Publication boundary

Canonical publication flow:

CONTROL → PRIVATE RESEARCH → VERSIONED SNAPSHOT → PREVIEW → IMMUTABLE PUBLICATION → FRIEND / INSIDER

Published versions are immutable.

Revocation hides a publication but does not rewrite its historical version.

Private Position, private money-risk and private Decision Journal data do not cross the publication boundary.

---

## 6. The complete product workflow

## 6.1 Discover

Discovery is an investigation funnel, not a BUY/SELL engine.

Any ticker entering Coverage or Portfolio must be validated again before persistence. Unknown or unresolvable symbols are rejected rather than creating placeholders. Discovery candidates do not create Coverage, full Research, Portfolio positions or thesis mutations automatically; **Promote** remains an explicit CONTROL action.

Discovery 0.2.12 has three stages.

### Stage 0 — cached broad operating-equity universe

The engine maintains a CONTROL-private cached universe from the Alpaca active US-equity asset catalog.

Stage 0 is fail-closed:

- status must be active;
- security must be tradable;
- major US exchange only: NASDAQ, NYSE, AMEX, ARCA;
- ticker syntax must be valid;
- warrants, rights, units, ETFs, ETNs, funds, preferreds, note-like securities and name/symbol patterns associated with blank-check/SPAC shells are excluded;
- the universe cache is refreshed on a bounded cadence rather than rebuilt on every page load.

The universe is materialized separately from Coverage. Being present in Stage 0 does not create a company Research record.

### Stage 1 — cheap rotating forensic screen

Stage 1 is background-only and intentionally cheap.

It advances through the cached Stage-0 universe in a bounded rotating batch and uses only market metadata/snapshots plus already-materialized Coverage clues when they exist.

Current bounded controls:

- one rotating broad-universe slice of at most 360 names per run;
- Most Active and Movers are retained only as a secondary activity lane capped at 160 names;
- stored Coverage context is capped at 80 names per run;
- snapshot requests are chunked sequentially in groups of 60;
- liquidity qualification uses the previous completed daily bar when available, so an early-session run does not become activity-biased merely because the current day's volume is incomplete;
- new external names must satisfy the configured price, daily-volume and dollar-liquidity floors before deep enrichment; the 0.2.12 opportunity-funnel tuning uses $5 minimum price, 200k completed-day shares and $15M completed-day dollar volume so investable mid-caps are not discarded merely for lacking mega-cap liquidity;
- already-materialized intrinsic Coverage gaps may be used as a cheap prioritization clue;
- Stage 1 never calls SEC Companyfacts.

The deep-enrichment budget deliberately reserves capacity for quiet liquid names from the broad rotation. This prevents current market activity from monopolizing Discovery.

The Stage-1 cursor is checkpointed. Repeated scans therefore advance through the broad universe over time instead of repeatedly rescanning only the same movers.

### Stage 2 — bounded forensic enrichment

Only a small Stage-1 finalist set may spend SEC/filed-data budget. The 0.2.12 opportunity-funnel tuning caps deep enrichment at 10 finalists per run, up from 8, while keeping SEC work sequential and bounded.

For external finalists:

- SEC ticker resolution is performed once per run;
- SEC submissions and Companyfacts are called only for bounded finalists;
- fiscal-year end must be respected;
- four coherent filed quarters are required for current TTM;
- a comparable prior TTM is required for operating confirmation;
- the **canonical valuation engine** is reused; Discovery does not own a duplicate valuation model;
- reference-price fallback is disabled.

Discovery and Validation are deliberately separated. Discovery is allowed to preserve a research lead before all decision-grade checks are complete; Research/Validation remains the stricter decision layer.

Opportunity tiers are:

- **P1 · Strong Opportunity** — decision-grade INTRINSIC Base, at least two usable valuation methods, absolute Base gap of at least 25%, and aligned filed TTM operating confirmation.
- **P2 · Valuation Opportunity** — decision-grade INTRINSIC Base, at least two usable valuation methods, absolute Base gap of at least 20%, and no material filed operating contradiction. A company does not need a dramatic operating acceleration merely to deserve Research.
- **WATCH · Emerging / Verification Needed** — either a 12–20% Base gap with aligned operating confirmation, or a gap of at least 20% where valuation quality/method count, operating alignment or short actionability still needs verification.
- **Rejected** — evidence integrity prevents a useful research lead: Base/gap cannot be constructed, coherent filed TTM is unavailable, corporate-action/share basis is unresolved, ticker/SEC enrichment fails, or there is no minimum Discovery edge.

P1/P2 Short leads must also be currently actionability-compatible (minimum short price and shortable flag). A compelling downside setup that is not currently short-actionable is preserved as WATCH rather than silently disappearing.

A raw price move, Most Active rank or generic score can never create P1/P2/WATCH by itself.

Final ranking is lexicographic and visible rather than a hidden composite: opportunity tier, absolute Base gap, valuation quality/method count, operating confirmation/contradiction, then ticker.

Candidate output must expose at least:

- ticker and current price;
- Bear / Base / Bull where available;
- Base fair-value gap;
- valuation quality and method count;
- operating confirmation;
- direction and opportunity tier (P1 / P2 / WATCH);
- why the name entered;
- what would invalidate the Discovery setup;
- data freshness;
- warnings.

Evidence-grounded family labels may include Valuation Dislocation, Quality at Discount, Fundamental Inflection, Forensic Divergence and Deterioration / Short Setup. Labels are derived from the underlying gap/operating signals; they are not marketing categories.

There is no filler quota. Zero leads remains a valid successful run, but the funnel no longer requires final-validation-level operating confirmation for every valuation opportunity.

### Discovery hosting / provider discipline

Discovery is designed for shared hosting:

- normal Discovery GET is provider-free and reads stored job/cache state;
- execution happens in the existing background job system;
- Stage 0 is cached;
- Stage 1 is chunked, checkpointed and incremental;
- Stage 2 has a hard 10-finalist cap and runs SEC work sequentially;
- provider-call counts, Stage-0/1/2 counts, exclusions and job status are visible in the stored result/UI;
- a stale prior successful result remains readable while a new scan is queued/running;
- no deploy workflow change or new dependency is required by 0.2.12.

### Discovery observability, freshness and rerun cadence

0.2.12 also records whether Discovery itself is healthy enough to trust.

A completed run stores:

- Stage-0 current and prior eligible-universe size, with a critical flag for a drop greater than 30%;
- stale Stage-0 cache status;
- requested versus returned Stage-1 snapshots;
- unusually concentrated Stage-1 exclusion patterns;
- the count and percentage of Stage-0 names actually touched in the last 7 and 30 days;
- the estimated number of successful rotating runs needed to touch one full current Stage-0 universe;
- a bounded ticker-level rejection log for Stage-2 enrichment and final qualification.

Candidate freshness is not one ambiguous timestamp. It is separated into:

- market snapshot;
- filed fundamentals / TTM period;
- valuation materialization;
- Stage-0 universe eligibility refresh.

Discovery also applies conservative basis-review guards before final qualification. A large YoY share-count discontinuity, short filed history, or a recent registration/listing filing such as S-1/F-1/10-12 creates a **CORPORATE ACTION / BASIS REVIEW** rejection instead of silently allowing a possibly mismatched price/share basis into the final list.

Promotion provenance is immutable operating history: when CONTROL promotes a qualified Discovery candidate, the Coverage audit records the originating scan and the exact Discovery evidence visible at promotion time. This does not make Discovery a Research truth source; it preserves what Discovery saw when the human chose to start Research.

Recommended manual cadence is deterministic and provider-free on GET:

- critical universe/provider anomaly → retry in about 1 day;
- warning-level anomaly → retry in about 3 days;
- healthy but less than 25% of the current universe touched in 30 days → about every 3 days while establishing breadth;
- established breadth → weekly.

This is guidance, not an automatic trade or research action.

### Discovery limitation to remember

Broad coverage is **incremental**, not a synchronous full-market deep valuation pass. A single run deeply enriches only a bounded 10-name finalist set. Repeated runs advance the Stage-1 cursor through the cached operating-equity universe.

This is deliberate: it trades scan latency for provider discipline and shared-hosting reliability. Stage 1 still has no licensed whole-market fundamental/consensus dataset, so unknown names without stored evidence are prefiltered mainly by security validity, liquidity, broad rotation and current market metadata before Stage 2 performs the real forensic test.

---

## 6.2 Research

Research is company-first and position-agnostic.

Canonical sections:

1. Overview
2. Business
3. Fundamentals
4. Expectations
5. Valuation
6. Bear Case
7. Catalysts
8. Financial Flows
9. Management
10. Tape / Flows
11. Monitoring
12. Decision Journal
13. Sources / Audit
14. Validate

Validate appears after Sources / Audit but is a separate stage, not a Research gate.

---

## 7. Process Readiness

Process Readiness answers:

> Has CONTROL reviewed every required part of the research file?

It does not answer:

> Is this stock a Long, Short or good position size?

There are currently 13 Research gates.

| Gate | Evidence-ready condition |
| --- | --- |
| Thesis / Variant | Thesis + counter-evidence + our variant are present |
| Business | Business research text exists |
| Fundamentals | At least 2 annual periods + analyst Fundamentals text |
| Expectations | Structured expectation exists OR Expectations research text exists |
| Valuation | Bear + Base + Bull are all available |
| Bear Case | Structured bear item OR Bear Case research text exists |
| Catalysts | Structured catalyst OR Catalysts research text exists |
| Financial Flows | At least one stored Financial Flow exists |
| Management | Management assessment OR Management research text exists |
| Tape / Flows | Tape research text exists |
| Monitoring | Active monitoring rule OR thesis invalidation exists |
| Decision Journal | At least one persisted journal entry exists |
| Sources / Audit | At least one company source exists |

### 7.1 PENDING APPROVAL

Evidence exists, but CONTROL has not approved the gate.

### 7.2 APPROVED

CONTROL approved the evidence hash associated with that gate.

### 7.3 APPROVED · EVIDENCE CHANGED

The gate was previously approved, but one or more inputs that define that gate have changed since approval.

The gate remains approved.

This is intentional.

The system must not silently revoke a human approval because a filing, note, assumption or source changed.

Instead it tells CONTROL that the approval refers to an older evidence state.

Recommended workflow:

1. Open the linked gate.
2. Review what changed.
3. If still accepted: Reopen → Approve.
4. The new evidence hash becomes the approved state.

### 7.4 Approval is monotonic

A gate stays approved until CONTROL explicitly reopens/revokes it.

Evidence changes do not automatically reduce the approved count.

### 7.5 READY TO VALIDATE

All 13 gates approved → ready_to_validate = true.

Approve → Reopen → Approve must work without HTTP 405, without a heavy RECALCULATE requirement and without mandatory full-page reload. A 405 on Process Readiness is a release blocker.

Before validation, Research Conclusion becomes READY TO VALIDATE.

Closing the final gate must update that conclusion immediately without requiring a heavy provider refresh.

---

## 8. Decision Lenses

Decision Lenses convert the current evidence into interpretable research states.

They are descriptive, not Portfolio sizing instructions.

### 8.1 BUSINESS

- UNPROVEN: Business gate is not evidence-ready.
- GOOD: management score ≥ 65 and positive evidence count ≥ negative evidence count.
- BAD: management score < 40.
- MIXED: everything else.

### 8.2 VALUE

Uses Base fair-value gap vs current market price only when the Base is decision-grade.

Decision-grade Research Base quality:
- INTRINSIC;
- MANUAL_OVERRIDE.

- UNVERIFIED: gap unavailable **or** Base quality is provisional/reference/mixed/data-warning.
- ATTRACTIVE: decision-grade Base gap ≥ +20%.
- EXPENSIVE: decision-grade Base gap ≤ −15%.
- FAIR: decision-grade Base gap between −15% and +20%.

Bear / Base / Bull may remain visible while VALUE is UNVERIFIED. Visible target does not mean verified intrinsic target.

These thresholds describe the current Research Lens. They do not by themselves produce a Long/Short conclusion.

### 8.3 EXPECTATIONS

Price-implied expectations reverse-engineer three Base-framework drivers:

- revenue CAGR;
- Y5 net margin;
- Y5 exit P/E.

The inversion changes one driver at a time while holding the other Base drivers constant.

This is not consensus.

Classification:

- DEMANDING: normalized demand score ≥ +0.75;
- FAVORABLE: normalized demand score ≤ −0.75;
- BALANCED: otherwise;
- UNAVAILABLE: insufficient price/revenue/shares/Base assumptions.

### 8.4 VARIANT

- UNPROVEN: market view or our view is missing.
- DEFINED · UNPROVEN: views exist but Base is not decision-grade, or there is no variant evidence / structured expectations.
- POSITIVE EDGE: decision-grade Value ATTRACTIVE + Expectations FAVORABLE/BALANCED + variant evidence.
- NEGATIVE EDGE: decision-grade Value EXPENSIVE + Expectations DEMANDING/BALANCED + variant evidence.
- POSSIBLE: defined with decision-grade valuation but does not meet the stronger edge state.

A provisional/reference Base can never create POSITIVE EDGE or NEGATIVE EDGE.

### 8.5 PATH

Path combines open catalysts with Tape regime.

Path score:

positive catalysts − negative catalysts  
+1 if Tape SUPPORTIVE  
−1 if Tape HOSTILE

- SUPPORTIVE: score ≥ +2.
- HOSTILE: score ≤ −2.
- UNCLEAR: otherwise.

### 8.6 MODEL CONFIDENCE

Inputs:

- Validate state;
- analytical engine confidence;
- active warnings.

Current mapping:

- STRONG: Validate = VALIDATED, engine confidence HIGH, no warnings.
- MODERATE: Validate = VALIDATED/LIMITED and engine confidence HIGH/MEDIUM.
- UNVALIDATED: Validate = NOT RUN.
- LIMITED: otherwise.

### 8.7 THESIS CONTROL

Research invalidation is separate from Portfolio money risk.

- CONTROLLED: locked invalidation + locked pre-investment monitoring rule + no open invalidating bear item.
- CHALLENGED: same controls exist, but an open bear item is marked as thesis invalidating.
- DEFINED · UNLOCKED: invalidation text exists but controls are not fully locked.
- UNRESOLVED: no invalidation.

---

## 9. Research Conclusion

Research Conclusion follows a precedence order.

1. Any Research gate not approved → **RESEARCH INCOMPLETE**
2. All gates approved but Base is not decision-grade → **DATA REVIEW**
3. All gates approved, decision-grade Base, Validate not run → **READY TO VALIDATE**
4. Value ATTRACTIVE + Variant POSITIVE EDGE + Path SUPPORTIVE + Model Confidence STRONG/MODERATE → **LONG READY**
5. Value ATTRACTIVE + Variant POSITIVE EDGE/POSSIBLE → **LONG WATCH**
6. Value EXPENSIVE + Variant NEGATIVE EDGE + Path HOSTILE + Model Confidence STRONG/MODERATE → **SHORT READY**
7. Value EXPENSIVE + Variant NEGATIVE EDGE/POSSIBLE → **SHORT WATCH**
8. Model Confidence LIMITED → **DATA REVIEW**
9. Otherwise → **NO EDGE · WAIT**

Important:

- Process Readiness decides whether the research file is complete enough to judge.
- It does not decide direction.
- Validation, Value, Variant and Path determine what the complete research means.
- Portfolio sizing remains downstream and separate.

---

## 10. Evidence Diagnostic

Evidence Diagnostic is a compact support/opposition view.

It may include:

- weighted supporting evidence;
- weighted opposing evidence;
- compact signals;
- blockers;
- a signed diagnostic score.

Permanent rule:

**Diagnostic score never bypasses Process Readiness, Decision Lenses or Validate.**

Threshold cards or duplicate score presentations should not clutter the UI.

The score is context, not the canonical answer.

---

## 11. Fundamentals and accounting integrity

### 11.1 Source priority

SEC filing evidence is primary for company fundamentals.

Optional secondary providers may fill only fields still missing after SEC.

A valid SEC fact is not overwritten by a secondary source.

Fallback provenance remains visible.

### 11.2 Fiscal-period integrity

Annual history:

- economic period end matters;
- duplicate fiscal periods are de-duplicated by period end;
- later filing/restatement for the same economic period may supersede older filing evidence when appropriate.

Quarterly history:

- Q1/Q2/Q3 are filing-aware;
- YTD facts are differenced only when a compatible predecessor exists;
- Q4 is derived from FY − Q1 − Q2 − Q3 only when all pieces are valid.

TTM:

- requires four fiscally consecutive stored quarters;
- sequence must be continuous;
- period span must be reasonable;
- TTM must not be fabricated from incompatible quarters.

Quarter growth compares the same fiscal quarter one year earlier.

### 11.3 Core statement fields

The system tries to preserve:

- Revenue;
- COGS / Cost of Revenue;
- Gross Profit;
- Operating Expenses;
- Operating Income;
- Pre-Tax Income;
- Income Tax;
- Net Income;
- CFO;
- CapEx;
- FCF;
- Cash;
- Debt;
- Receivables;
- Inventory;
- Payables;
- Assets;
- Liabilities;
- Equity;
- diluted/outstanding shares.

### 11.4 Exact-label fallback

When canonical US-GAAP concepts miss a valid consolidated company-extension fact, Market Forensics may use a conservative exact normalized statement-label match.

It must not use broad fuzzy label matching that can accidentally ingest segment/disclosure facts.

### 11.5 Debt

Do not treat a bare LongTermDebt fact as total debt.

Preferred hierarchy:

1. verified combined debt concept;
2. otherwise current + short-term + non-current components when they can be composed without double counting;
3. unresolved if a defensible total cannot be proven.

### 11.6 Gross Profit

If filed Revenue and COGS exist but Gross Profit does not:

Gross Profit = Revenue − COGS

This is an exact bridge, not an estimate.

### 11.7 FCF

If CFO and CapEx exist:

FCF = CFO − CapEx

### 11.8 Working-capital metrics

Current calculations include:

- DSO = Receivables / Revenue × 365;
- DIO = Inventory / COGS × 365;
- DPO = Payables / COGS × 365;
- CCC = DSO + DIO − DPO;
- Inventory / Revenue;
- Receivables / Revenue;
- CFO / Net Income;
- share-count growth;
- asset turnover;
- working capital;
- Net debt;
- Net debt / FCF when FCF > 0.

### 11.9 ROIC

ROIC is deliberately fail-closed.

It requires:

- Operating Income;
- Pre-Tax Income;
- Income Tax;
- Debt;
- Cash;
- Equity.

Tax rate is derived from reported Income Tax / Pre-Tax Income and bounded to 0–35%.

No default 21% tax rate is injected.

If required inputs are missing, ROIC remains unavailable.

### 11.10 Data Completeness

Data Completeness is analytical, not merely “four quarters exist.”

It should flag:

- missing current statement fields;
- historically present balance-sheet fields that disappear;
- derived metrics that were historically available but are now unavailable;
- incomplete quarter sequence.

Unresolved values remain visible rather than guessed.

---

## 12. Valuation engine

Fair value is the primary product output and must remain auditable.

### 12.1 Share denominator

Per-share valuation requires a usable share denominator.

Priority can include:

- explicit current/user-reviewed shares;
- SEC outstanding shares;
- diluted weighted-average shares as fallback.

Share source and verification status remain visible in model assumptions.

### 12.2 Company type

Current valuation families:

- Generic;
- Consumer / Brand;
- Industrial;
- Software;
- Semiconductor / AI;
- Auto / EV;
- Financial / REIT.

Sector/industry text is used to infer a starting family, but the user can review the model.

### 12.3 Starting assumptions

The engine estimates recent:

- revenue growth;
- net margin;
- FCF margin;
- operating margin;
- net debt;
- shares.

Historical multiples may be point-in-time calibrated when enough valid observations exist.

Otherwise company-type priors are used.

Important distinction:

**Type priors and default growth/margin values are model assumptions, not sourced facts.**

This is a current modeling convenience and must remain clearly labeled.

### 12.4 Bear / Base / Bull default policy

Default probabilities:

- Bear 25%;
- Base 50%;
- Bull 25%.

Default horizon: 5 years.

Current policy starts from Base operating assumptions and applies bounded Bear/Bull changes.

Examples include approximately:

- Bear growth: Base − 5 pts;
- Bull growth: Base + 5 pts;
- Bear net margin: Base − 2.5 pts;
- Bull net margin: Base + 2.5 pts;
- Bear FCF margin: Base − 3 pts;
- Bull FCF margin: Base + 3 pts.

Discount/terminal assumptions differ by case.

### 12.5 Intrinsic methods

Primary blended methods:

- P/E;
- EV / Sales;
- FCF Yield.

DCF is calculated as an independent cross-check, not part of the default three-method blend.

Default weights:

- P/E 40%;
- EV / Sales 25%;
- FCF Yield 35%.

If earnings are non-positive/unavailable, P/E weight goes to zero.

If FCF is non-positive/unavailable, FCF-yield weight goes to zero.

Financial/REIT currently uses P/E only in the default policy.

### 12.6 Robust blend

When at least three methods are valid:

- a method more than 45% away from the cross-method median has its weight reduced by 70%.

This prevents one extreme method from dominating the target.

### 12.7 Manual override

A positive manual fair-value override is allowed.

It must be labeled MANUAL_OVERRIDE and remain auditable.

### 12.8 Provisional fallbacks

When current intrinsic inputs are incomplete, Research may preserve visibility of Bear/Base/Bull through:

1. last stored case value;
2. if necessary, an explicit verified-market-reference provisional case.

These are not intrinsic valuations.

Quality labels distinguish:

- INTRINSIC;
- MANUAL_OVERRIDE;
- PROVISIONAL_STORED_FALLBACK;
- PROVISIONAL_REFERENCE_FALLBACK;
- MIXED / DATA WARNING.

The market price is comparison-only for a true intrinsic valuation.

A reference-price fallback is a visibility mechanism, not evidence of intrinsic value.

Decision-grade Base quality is fail-closed:

- INTRINSIC and MANUAL_OVERRIDE may feed the VALUE lens, ATTRACTIVE / FAIR / EXPENSIVE, Variant edge and Research Conclusion;
- PROVISIONAL_STORED_FALLBACK, PROVISIONAL_REFERENCE_FALLBACK, MIXED / DATA WARNING remain visible but cannot create valuation edge or LONG / SHORT readiness/watch states;
- the displayed Bear / Base / Bull values are preserved even when valuation quality is under review;
- current market price is never evidence that an intrinsic fair value is correct;
- cached 0.2.9 research is re-read against stored Base-quality provenance before a user-facing edge is allowed;
- if the active valuation model is newer than its materialized research cache, the last Bear / Base / Bull may stay visible but the cached valuation is treated as DATA WARNING / non-decision-grade until background RECALCULATE finishes; stale gaps cannot create VALUE/Variant edge or Discovery qualification.

### 12.9 Discovery is stricter than Research display

Discovery disables reference-price fallback.

A candidate cannot qualify unless Base is intrinsic and supported by at least two valuation methods.

### 12.10 Peer valuation overlay

Automatic peer triangulation is a bounded cross-check.

It can use peer:

- P/E;
- EV / Sales;
- FCF Yield.

Eligibility requires:

- at least 2 peers;
- at least 2 peer valuation methods.

Weight:

- 15% normally;
- 20% when at least 5 peers and 3 methods exist.

The final multiplicative shift is capped at ±10%.

Peer evidence can cross-check intrinsic value.

It cannot become a peer-only valuation engine.

---

## 13. Business and external triangulation

Business combines:

- analyst qualitative business work;
- automatic numerical clues;
- management evidence;
- macro context;
- peer comparison;
- manually added customer/supplier/competitor/industry evidence.

### 13.1 Macro

Macro context is background FRED evidence.

Current factors include:

- US 10Y yield;
- high-yield credit spreads;
- trade-weighted USD;
- WTI oil;
- CPI inflation;
- industrial production;
- retail sales.

Sector/industry keyword rules map each company to directional sensitivities.

Macro refresh is a background action.

Normal Business GET does not call FRED.

Macro is contextual and cannot override company evidence.

### 13.2 Automatic peer triangulation

Current peer discovery prefers:

1. exact SEC SIC;
2. SIC division;
3. same stored industry fallback.

Important limitation:

The peer universe is constrained by companies already represented in the database with usable fundamentals.

Therefore a sparse Coverage/database can create a weak or biased peer set.

The peer engine must expose peer count, method and missing eligibility rather than pretending a weak peer set is robust.

---

## 14. Management

Management scoring is about execution evidence, not personality.

Current components:

- Operating execution — 25%;
- Cash conversion — 25%;
- Capital allocation — 20%;
- Shareholder alignment — 15%;
- Accounting discipline — 15%.

The engine uses filed operating and capital-allocation evidence.

Current label logic:

- HIGH EXECUTION CONFIDENCE: score ≥ 75 and evidence coverage ≥ 75%;
- MEDIUM EXECUTION CONFIDENCE: score ≥ 55;
- CAUTION / LOW CONFIDENCE: below 55;
- INSUFFICIENT EVIDENCE: no usable score.

Management guidance/promises are extracted from SEC-filed/furnished evidence and compared with realized filed outcomes.

The Management scan is background-only and bounded. It reads the primary 10-K / 10-Q / 8-K document and, for 8-K filings, also inspects a bounded set of relevant HTML exhibits such as EX-99.1 earnings releases. Successful filing scans carry a parser/scan version. A scan completed by an older parser does not permanently block a newer parser from rereading the filing, and an explicit CONTROL **Scan SEC guidance** action forces a reread. Normal SEC ingest also queues a deduplicated Management scan so guidance capture is part of the ordinary evidence-refresh path.

The automatic parser preserves both numeric and qualitative forward guidance. Current numeric coverage includes Revenue, Revenue Growth, Gross Margin, Operating Margin, Net Margin, canonical FCF when its CFO-minus-CapEx definition is explicit, and diluted EPS. Common decline wording is sign-aware. Qualitative statements such as low/mid/high-single-digit or roughly-flat guidance remain visible as EVIDENCE_ONLY; the system never invents a numeric target from them.

Promise scoring is comparability-first. Every stored promise preserves source/date, filing/accession, source document/exhibit, metric, target period, basis/definition, comparability and status. PENDING may become MET or MISS only when the target and actual are economically comparable. Automatic scoring requires an explicit full-year target marker such as FY / fiscal year / full-year; a bare year does not prove fiscal-period comparability. Any interim marker such as Q1–Q4, quarter, H1/H2, six months, nine months or YTD wins over an FY token in the same evidence and keeps the promise EVIDENCE_ONLY.

Management Delivery uses the **earliest publicly filed annual actual** for the target fiscal year, reconstructed from SEC Companyfacts and stored separately from the current normalized statement view. A later comparative restatement may improve current Fundamentals, but it must not retroactively rewrite whether management originally met or missed a promise. GAAP diluted EPS uses the directly reported SEC EPS fact; it is not recreated from net income divided by a later share count.

Interim/quarterly targets, incompatible fiscal periods, adjusted/non-GAAP basis, unresolved EPS basis, management-defined FCF, retrospective guidance, later/current actuals whose original point-in-time value cannot be verified, ambiguous comparator years, incompatible units/ranges or other unresolved definitions remain EVIDENCE_ONLY / non-comparable rather than receiving a false MET/MISS.

Automatic source scope is intentionally limited to SEC-filed/furnished evidence. A conference-call statement that was not filed or furnished to the SEC is not silently scraped or fabricated into this ledger; CONTROL can add a sourced promise manually until a rights-cleared transcript provider exists.

This is accountability evidence, not an integrity/personality judgment.

---

## 15. Financial Flows

Financial Flows live under Research.

### 15.1 Income Statement

Income Statement uses a sequential Revenue-to-Net waterfall.

Conceptual path:

Revenue  
→ COGS / Cost of Revenue  
→ Gross Profit  
→ Operating Expenses / Costs  
→ Operating Income  
→ Other / Interest  
→ Pre-Tax Income  
→ Income Tax  
→ Net Income

Accounting bridges are explicit.

Reconciliation tolerance is approximately 1.5%.

If the bridge does not reconcile, the UI should show a warning rather than forcing the numbers to fit.

### 15.2 Cash Flow

Cash Flow preserves signed economics.

Negative exceptions remain negative rather than being cosmetically converted into positive flows.

---

## 16. Tape / Flows

Tape is market-plumbing context, not intrinsic value and not beneficial-owner identification.

0.2.9 restores the accepted Local Tape Engine as **Tape Engine V2**, implemented as a web-native background/materialized capability.

Inputs can include:

- split-adjusted historical price;
- OHLC and volume;
- Alpaca historical trades;
- adaptive Large / Very Large / Whale trade-size flow;
- FINRA daily short-sale volume;
- FINRA consolidated short interest;
- FINRA weekly ATS / non-ATS activity;
- optional borrow / locate evidence;
- options open-interest context when available.

Important rules:

**FINRA daily short-sale volume is not short interest.**

**Large / Very Large / Whale is a trade-size proxy, not the identity of a fund, institution or whale.**

### 16.1 Institutional Flow Engine

Historical trades are downloaded only in a background POSITIONING_REFRESH job.

Raw trade pages are processed in memory and reduced to daily aggregates. Millions of raw prints are not persisted into MariaDB.

Trade direction currently uses a transparent tick-rule proxy:

- trade above prior trade price → aggressive-buy proxy;
- trade below prior trade price → aggressive-sell proxy;
- unchanged price inherits the last directional tick where possible;
- unresolved first/ambiguous prints remain neutral.

This is explicitly labeled TICK_RULE_PROXY.

Trade-size thresholds adapt to the stock/day:

- Large = max(P75 notional, $100k);
- Very Large = max(P90 notional, $250k);
- Whale = max(P99 notional, $500k).

Stored daily evidence includes:

- Large Buy / Sell / Neutral;
- Very Large Buy / Sell / Neutral;
- Whale Buy / Sell / Neutral;
- Net Large / Very Large / Whale;
- share of turnover classified Large / Very Large / Whale;
- directional coverage;
- feed;
- feed scope;
- source status;
- flow confidence.

### 16.2 SIP vs IEX

Historical Alpaca flow attempts consolidated SIP first.

When the available request cannot use SIP, the engine may fall back to IEX.

The source is always visible:

- CONSOLIDATED_SIP;
- IEX_PARTIAL_MARKET.

IEX evidence receives a material Data Confidence penalty.

Partial/page-capped samples are labeled PARTIAL_SAMPLED and also reduce confidence.

The system must never present IEX/partial evidence as if it represented the full US consolidated tape.

### 16.3 Tape scores

Tape deliberately keeps multiple dimensions rather than one opaque score:

- Institutional Flow Score 0–100;
- Short Pressure Score 0–100;
- Absorption Score 0–100;
- Long Demand Score 0–100;
- Battle Intensity Score 0–100;
- Price Resilience Score 0–100;
- Data Confidence Score 0–100;
- Net Tape Score 0–100.

The central distinction is:

High Short Pressure + high Institutional Flow + high Absorption can mean bearish supply is being absorbed.

High Short Pressure + high Institutional Flow + low Absorption means large buyers may be present while sellers still control price.

A large positive flow number while price keeps making weak closes/new lows is not automatically bullish.

### 16.4 Rank

Net Tape maps to the accepted Local rank:

- A: ≥70;
- B: ≥58;
- C: >42 and <58;
- D: >30 and ≤42;
- F: ≤30.

Rank organizes evidence. It is not a standalone trade signal.

### 16.5 Forensic regime

Possible regimes:

- ACCUMULATION;
- ACCUMULATION UNDER PRESSURE;
- BATTLE - BUYERS ABSORB;
- CONSTRUCTIVE;
- NEUTRAL / BATTLE;
- DISTRIBUTION;
- DISTRIBUTION / BEARS CONTROL;
- LOW DATA.

Data Confidence below 55 forces LOW DATA rather than inventing a regime.

For Decision Lenses only, the forensic regime is translated into a compact path state:

- constructive regimes → SUPPORTIVE;
- distribution regimes → HOSTILE;
- neutral/low-data regimes → MIXED.

### 16.6 Compact decision context

The existing compact context remains:

Pressure direction:

- LONG when Long Demand − Bear Pressure ≥ +12 pts;
- SHORT when ≤ −12 pts;
- LATERAL inside that band;
- LOW DATA if the spread cannot be computed.

Posture:

- LOW confidence → WAIT FOR DATA;
- MIXED path, lateral/low-data pressure, or extreme battle → WAIT FOR CONFIRMATION;
- LONG + SUPPORTIVE path → SUPPORTIVE TAPE;
- SHORT + HOSTILE path → HOSTILE TAPE.

Tape must answer:

- what regime the market plumbing suggests;
- who is winning the price response;
- whether Large/Whale flow confirms or conflicts;
- what changed since the prior observation;
- what evidence would change the regime;
- whether the user should wait for more confirmation.

### 16.7 Tape charts

0.2.9 restores the Local chart contract in Research → Tape / Flows:

1. Price + cumulative institutional flow;
2. Daily Volume;
3. Price + FINRA Short Interest;
4. FINRA Daily Short Volume %;
5. Institutional Net Large Flow;
6. Cumulative 5D / 20D Large Flow;
7. Absorption / Short Pressure / Net Tape history;
8. Whale Flow;
9. FINRA ATS share of reported OTC activity.

Where history has not yet accumulated, the chart remains honestly incomplete rather than backfilled with fabricated flow.

### 16.8 FINRA ATS / OTC

FINRA Weekly Summary is delayed context.

The engine aggregates ATS and non-ATS reported shares/trades by week and can calculate ATS share of reported OTC activity.

A venue name or ATS print does not identify the beneficial buyer or seller.

ATS evidence is market-plumbing context only.

---

## 17. Monitoring

Monitoring has three distinct layers.

### 17.1 Suggested monitoring plan

The application can propose useful review cadence and triggers based on current research.

Suggested rules are not automatically committed rules.

### 17.2 Active MonitoringRule

CONTROL explicitly creates the active rule.

This becomes part of the Research record.

### 17.3 Monitoring observations and alerts

Observed values/status are appended over time.

Locked pre-investment thresholds cannot be retroactively rewritten to fit the outcome.

Monitoring belongs to thesis falsification.

Portfolio money-risk belongs elsewhere.

---

## 18. Decision Journal

Decision Journal is designed to prevent retrospective narrative editing.

At commit time, the original decision freezes:

- decision;
- Research state;
- investment state;
- thesis snapshot;
- valuation snapshot;
- invalidation snapshot;
- evidence for;
- evidence against;
- bias/discipline notes.

Later outcome, post-mortem and lessons are appended in separate DecisionOutcome records.

They do not rewrite the original decision.

---

## 19. Validate

Validate tests the process point-in-time.

It must avoid look-ahead leakage.

### 19.1 Historical reconstruction

For each historical filing anchor:

- only filings available by that date are included;
- contemporaneous raw price is used for calibration;
- split-adjusted price is used for outcome comparison;
- future filings are excluded;
- current saved assumptions are not injected into the historical model;
- future prices enter scoring only after the historical model snapshot is frozen.

### 19.2 Validation dimensions

Current sample scoring can include:

- valuation accuracy — 40%;
- direction accuracy — 20%;
- Bear/Bull range coverage — 20%;
- assumption accuracy — 20%.

Only available components are reweighted into each sample reliability score.

### 19.3 Canonical user-facing states

Current Process/Decision Lens state:

- NOT RUN: no historical run;
- REVIEW: failed/error run or very weak reliability;
- LIMITED: insufficient sample / incomplete reliability / middling result;
- VALIDATED: strong enough reliability with enough samples.

The canonical validation policy is defined once and used by historical-run status, Validate, Process Readiness, Decision Lenses, Research Conclusion, report/export and cached/API-facing readiness:

- VALIDATED requires at least 5 valid scored samples;
- VALIDATED requires reliability ≥ 65;
- fewer than 5 valid samples is LIMITED regardless of a high reliability score;
- failed/error execution or very weak reliability is REVIEW;
- no run is NOT RUN.

No user-facing surface may independently reinterpret these thresholds.

---

## 20. Portfolio

Portfolio can exist before Research.

A user may own a security without creating a placeholder thesis.

Required position fields:

- validated ticker;
- LONG / SHORT;
- shares > 0;
- average cost.

Optional:

- exposure tags;
- private notes.

Removing a holding removes the current Position but preserves Research, risk history and audit history.

If Research exists, its investment state returns to WATCHLIST.

### 20.1 Position sizing

Portfolio money risk uses:

loss_to_reference = abs(current_price − sizing_reference_price) / current_price

adjusted_loss = loss_to_reference + event/liquidity haircut

suggested_position = min(max_position_cap, risk_budget / adjusted_loss)

The web UI expresses these as percentages.

Current default PortfolioRiskPlan values:

- risk budget: 0.75%;
- event/liquidity haircut: 5%;
- max position cap: 10%.

The sizing reference price is not the thesis invalidation price.

### 20.2 Portfolio analytics

Current Portfolio analytics include:

- gross market value;
- Long value;
- Short value;
- net exposure;
- side-aware P/L;
- gross weights;
- top weight;
- concentration HHI;
- max-position breaches;
- validation coverage;
- manual shared-factor exposure from tags;
- cached pairwise correlations.

Correlations are background/materialized.

The current implementation considers the largest positions and requires at least 30 overlapping return observations for a pair.

### 20.3 Position Action

Position Action is the Portfolio-owned final action layer downstream from Research Conclusion.

Canonical flow:

**DATA → VALUE → THESIS → TIMING → RISK → ACTION**

Research remains position-agnostic and can conclude only with the canonical Research states such as RESEARCH INCOMPLETE, READY TO VALIDATE, LONG READY/WATCH, SHORT READY/WATCH, DATA REVIEW or NO EDGE · WAIT. It does not know shares, cost basis, Portfolio weight or sizing.

Portfolio Position Action may combine that Research Conclusion with the user's stored position side, Validate state, Value / Variant / Path / Model Confidence, thesis control, latest stored locked-monitoring status, PortfolioRiskPlan, current gross exposure, downside-based suggested position, max-position cap and optional explicit links from Portfolio conditions to existing Research Monitoring rules.

Precedence is conservative:

1. A triggered **locked pre-investment thesis invalidation** overrides valuation and forces EXIT / SELL for a Long or COVER for a Short.
2. A confirmed explicit Portfolio EXIT condition may force EXIT / SELL or COVER without rewriting the thesis invalidation.
3. A Portfolio money-risk breach overrides directional Research and requires REDUCE / REDUCE SHORT.
4. A confirmed explicit Portfolio TRIM condition may require REDUCE / REDUCE SHORT.
5. PORTFOLIO ONLY, RESEARCH INCOMPLETE, DATA REVIEW and READY TO VALIDATE fail closed and cannot create BUY / ADD / new SHORT exposure.
6. Strong opposite-direction READY Research may require REDUCE, but does not by itself rewrite the locked invalidation or force a full exit.
7. No-position LONG READY / SHORT READY may produce BUY CANDIDATE / SHORT CANDIDATE. Candidate is not an order.
8. Existing same-direction READY may produce **ADD ON EVIDENCE / ADD SHORT ON EVIDENCE** only when Validate is VALIDATED, thesis control is CONTROLLED, Path is directionally supportive/hostile, Portfolio risk has headroom and an explicit stored evidence-to-add condition exists. If that condition is linked to Monitoring, the linked rule must also be confirmed.
9. LONG WATCH, SHORT WATCH and NO EDGE · WAIT cannot authorize an add.

Permanent rule:

**ADD ON EVIDENCE, NOT ON PRICE.**

Market price, average cost and P/L are deliberately not direct Position Action inputs. Price can change marked exposure and downside sizing, so it can reveal a genuine risk-limit breach or remove a sizing blocker, but price movement never satisfies the evidence-to-add condition and never independently creates ADD or SELL.

The Position Action output is deliberately compact and auditable:

- Action;
- Why now;
- What blocks a stronger action;
- Next confirmation;
- Risk / invalidation state;
- an internal deterministic rule/audit trace, not a user-facing score.

Position Action is calculated from stored/materialized state on normal GET. It performs no provider calls.

Portfolio condition confirmation is optional and reuses existing Research → Monitoring rules. Each ADD / TRIM / EXIT link explicitly defines whether confirmation means the rule is currently **OK** or its threshold has **TRIGGERED**. Portfolio does not reinterpret a Monitoring status implicitly.

The background `PORTFOLIO_RECALCULATE` job materializes:

- the latest deterministic Position Action for each live position;
- a bounded action-transition history when Action, deterministic rule or Research Conclusion changes;
- a Needs Attention command list ordered by exit/reduce/data-review urgency;
- the existing correlation analytics.

The Portfolio also exposes a descriptive sizing map:

- current weight;
- suggested weight;
- percentage-point headroom;
- reference target value/shares at the latest stored price and current gross portfolio value;
- per-position downside budget used = current gross weight × adjusted downside.

Reference dollars/shares are never an order and never feed back into Research Conclusion or Position Action.

Portfolio-level downside-budget totals are simple sums of the configured per-position loss budgets and their currently used amounts. They are intentionally descriptive and are not presented as VaR or diversification-adjusted risk.

Position Action and personalized sizing are CONTROL-private Portfolio data. They do not enter FRIEND/INSIDER publications or member-facing research artifacts.

---

## 21. Reports

CONTROL exports include:

- Executive PDF;
- Full PDF;
- Full Word;
- Discovery landscape PDF.

### 21.1 Research report data contract

0.2.14 uses one canonical stored-data research-report contract for Executive PDF, Full PDF and Full Word.

The contract may read:

- the materialized Research cache;
- current stored valuation model/scenarios and their persisted engine output;
- normalized stored Fundamentals;
- stored Financial Flow payloads;
- Management accountability/promises already materialized by the background Management engine;
- stored Tape / positioning series and summary;
- stored Monitoring rules/history;
- stored Historical Validation runs/samples;
- stored Sources / provenance;
- lightweight Research, Expectations, Catalysts and Bear Case rows.

A report request does **not** start or refresh SEC ingest, market data, FINRA, FRED, Discovery, Management scanning or heavy analytics. The report route explicitly suppresses the generic stale-cache recalculation enqueue. Missing or stale materialized evidence is rendered as unavailable, not run, incomplete or data warning; it is never invented during export.

PDF and Word are presentation renderers over the same contract. They are not independent financial engines and must not recalculate valuation, Tape, validation or accounting logic.

### 21.2 Decision hierarchy

The first decision information below company identity is always **RESEARCH CONCLUSION**.

Beside it, the Base target and Base Gap receive primary valuation emphasis.

Immediately after the hero, the compact executive strip contains, when available:

- Current Price;
- Bear;
- Base;
- Bull;
- Base Gap;
- Valuation Quality;
- Value Lens;
- Variant;
- Path;
- Model Confidence.

A report must not make the reader traverse multiple pages before learning the conclusion and target range.

The Executive report is a decision brief rather than a compressed Full report. It contains the conclusion, target range, valuation map, market view vs our view, what must be true, what would prove the thesis wrong, high-information FOR / AGAINST evidence, Tape context, next catalyst and thesis invalidation. One page is preferred only when readability is preserved; two readable pages are valid.

The Full report follows the investment process:

Research Conclusion → Valuation → Thesis / Variant → Expectations → Evidence FOR / AGAINST → Business → Fundamentals → Financial Flows → Management → Catalysts / Bear Case → Tape & Positioning → Monitoring / Invalidation → Validation → Sources / Audit.

### 21.3 Valuation and charts

Valuation is presented in decision order:

Current Price → Bear / Base / Bull → Base Gap → method detail.

The report preserves stored scenario probabilities, method values/weights, DCF cross-check, share denominator/source/verification and quality warnings. A provisional/stored-fallback valuation must be visually and textually distinguishable from decision-grade intrinsic valuation.

Report charts are generated natively from stored data; browser screenshots are not used.

Canonical report charts are:

- Valuation Map — Current Price vs Bear/Base/Bull;
- Revenue / Profitability Trend — revenue is scale-separated from margin series;
- FCF / Cash Conversion — FCF is scale-separated from CFO/Net Income;
- Working-Capital Forensics — material Inventory/Revenue and Receivables/Revenue divergence;
- Price Context — historical market price against today's stored scenario levels, explicitly not historical fair-value output;
- selected Tape charts — price + institutional-flow proxy and Absorption / Short Pressure / Net Tape when enough observations exist;
- Validation — historical Price Then vs Bear/Base/Bull Then plus stored outcome where available.

Charts are omitted cleanly when the underlying stored series is insufficient. No decorative chart is added simply to fill space. No rainbow palette and no purple are used.

### 21.4 Research intelligence and evidence

Research intelligence is a set of decision lenses, not gamification.

The report uses:

- Research Conclusion;
- Value;
- Expectations;
- Variant;
- Path;
- Model Confidence;
- Thesis Control.

Evidence is reduced to high-information FOR and AGAINST items. The weighted diagnostic score may appear only as secondary context and never overrides canonical readiness, validation or Decision Lenses.

Thesis / Variant is structured as:

- MARKET VIEW;
- OUR VIEW / VARIANT;
- WHAT MUST BE TRUE;
- WHAT WOULD PROVE US WRONG.

Only stored/materialized content is reused; the report renderer does not invent thesis statements.

### 21.5 Fundamentals, flows, Management, Tape and Validation

Fundamentals prioritize trends first, concise table second. Useful stored metrics may include Revenue growth, Gross/Operating/FCF margin, CFO/Net Income, ROIC, Net Debt/FCF, Inventory/Revenue, Receivables/Revenue, DSO/DIO/DPO/CCC and Share Count Growth. Metrics that are unavailable are not padded into a large table of dashes.

Financial Flows use the stored signed bridge/ledger. Signed deductions remain negative. The report never fabricates balancing values to make a visual flow appear complete.

Management reports execution/evidence coverage and material promises vs actuals. Promise states retain MET, MISS, PENDING and EVIDENCE ONLY/non-comparable semantics. Management is not personality-scored.

Tape is summarized before charts: regime/rank/confidence, Large/Whale positioning when stored, Short Pressure, Absorption, Net Tape, what changed and what would change regime. The Full report selects only decision-useful chart views; it does not reproduce the entire Tape page.

Monitoring exposes Thesis Invalidation plus active rules with metric, locked threshold, direction, current state, triggered state and last observation. Pre-investment thresholds are not rewritten retroactively.

If Validate has not run, the report says **NOT RUN** and implies no validation confidence. When a run exists, the report can show state, reliability, sample count/history span, valuation accuracy, direction accuracy, range coverage, assumption accuracy and point-in-time sample chart/calibration insight.

### 21.6 Styling, privacy and failure behavior

Research PDF/Word use the Market Forensics document language: white/light institutional background, Market Forensics blue, subdued grays, restrained semantic positive/negative/caution states, light rules, clear typography, minimal bold and controlled white space. Reports are paginated documents; Web mobile CSS is not reused as a PDF layout system.

Full Word follows the same information structure as Full PDF while remaining editable in Microsoft Word. It uses standard Office-safe fonts and native document tables/images rather than a PDF screenshot.

Sources/Audit remain readable: provider, document/type, publication date when available, retrieval time and concise title/provenance. Raw URL dumps are not the primary presentation.

Rich PDF/Word rendering is a production dependency and health requirement.

Report export must fail safely.

If rich rendering fails, a valid fallback artifact should still be returned.

An audit-write failure must not turn a valid report into HTTP 500.

In-memory reports are returned as normal response bytes and must not be delegated to a problematic WSGI file wrapper.

CONTROL research reports may contain CONTROL-private research evidence permitted by the research workspace. Publication/member artifacts must not expose Portfolio shares, cost basis, P/L, Position Action, sizing, PortfolioRiskPlan, private notes, private Decision Journal or credentials. FRIEND / INSIDER continue to receive only explicitly published research.

The concrete Local V3.1.12 → Web → 0.2.14 capability audit is documented in `docs/REPORT_PARITY_0_2_14.md`.

---

## 22. Security

Permanent security rules:

- no plaintext/recoverable passwords;
- Argon2 password hashing;
- mandatory 2FA;
- CONTROL receives strongest authorization enforcement;
- TOTP secret encrypted at rest;
- production secrets only in hosting environment, never committed;
- HTTPS production sessions;
- HttpOnly cookies;
- SameSite protection;
- server-side FRIEND / INSIDER / CONTROL authorization;
- invite-only onboarding during beta;
- rate limiting / brute-force protection;
- audit trail;
- production database/user/2FA must never be reset by a release;
- data redistribution rights must be respected before external publication.

---

## 23. Release and deployment rules

Code in main is not the same as production.

Canonical release sequence:

branch  
→ tests  
→ PR  
→ PR CI green  
→ merge main  
→ post-merge main CI green  
→ explicit Namecheap deploy  
→ production health green  
→ CURRENT_STATE sync

Do not claim a release is live before production health exists.

Production health must prove:

- HTTP 200;
- status = ok;
- correct VERSION;
- architecture = web-native;
- database = primary;
- reports = rich.

Normal deploy must not retransmit the persistent reporting vendor when its requirements hash has not changed.

Code merged to main and production deployment are always separate states. Never infer one from the other.

CURRENT_STATE must be updated after material main/deploy transitions.

Release metadata is a hard gate: VERSION, CURRENT_STATE State-Version and HOW_MARKET_FORENSICS_WORKS Current product line must agree before CI/deploy can pass.

After a successful production health check, the deploy workflow synchronizes the single top-level Production line in CURRENT_STATE back to main with the verified deployed VERSION, source SHA and workflow run. Historical release notes are never rewritten by this automation. If production is healthy but that source-of-truth sync cannot be committed, the deploy workflow must surface the failure rather than silently leave documentation stale.

---

## 24. Known gaps and current weaknesses

This section is deliberately candid. A tool becomes stronger when the limits are explicit.

### 24.1 Model priors can hide weak source coverage

When growth or margins are unavailable, the valuation policy may use default priors.

That is acceptable only as an explicit model assumption.

**Improvement:** surface assumption provenance per driver: FILED / HISTORICAL CALIBRATION / TYPE PRIOR / MANUAL.

### 24.2 Readiness approval is monotonic even if evidence deteriorates

APPROVED · EVIDENCE CHANGED is useful and preserves human agency.

However a critical disappearance of evidence can theoretically remain approved until CONTROL reopens it.

**Improvement:** distinguish ordinary evidence change from a critical evidence-invalid state without silently erasing human approval.

### 24.3 Discovery breadth is incremental

0.2.12 removes the structural Most Active / Movers universe dependency by maintaining a cached broad operating-equity universe and rotating Stage 1 through it.

The remaining limitation is depth-per-run: shared-hosting/provider discipline means only a bounded finalist set receives SEC Companyfacts and canonical valuation on each scan. 0.2.12 now exposes 7/30-day universe touch coverage and a suggested rerun cadence so this limitation is measurable rather than hidden.

**Improvement:** if a licensed whole-market fundamental dataset with appropriate storage/display rights is added later, strengthen Stage-1 cheap dislocation signals without weakening the bounded Stage-2 forensic contract.

### 24.4 Peer triangulation is database-limited

Sparse stored-company coverage creates sparse peers.

**Improvement:** build a provider-backed peer universe by SIC/industry first, then enrich a bounded peer set without requiring those names to already be in Coverage.

### 24.5 Expectations lack licensed consensus

Current price-implied expectations are useful, and structured analyst expectations are supported, but they are not Street consensus.

**Improvement:** add a licensed consensus/revisions provider when storage/display rights are clear.

### 24.6 Borrow/options/ownership are incomplete

Borrow fee may be manual; options depth and ownership flows are not yet institutional-grade.

Potential future lanes:

- securities lending;
- options OI/IV/skew/term structure;
- 13D/13G;
- 13F;
- Forms 3/4/5.

These must remain sourced and timestamped.

### 24.7 SEC taxonomy coverage can still miss issuer-specific facts

Exact-label extension fallback improves recovery but cannot guarantee every issuer taxonomy maps cleanly.

**Improvement:** add filing-instance/iXBRL-level fallback where Companyfacts remains insufficient, with strict provenance.

### 24.8 Business qualitative evidence is still underdeveloped

Numbers, valuation and source audit are becoming stronger than:

- moat evidence;
- customer concentration;
- competitive structure;
- pricing power;
- unit economics;
- industry structure;
- supplier/customer read-through.

**Improvement:** create structured qualitative evidence objects rather than relying mainly on free text.

### 24.9 Tape is heuristic

Tape combines useful context, but it is not an institutional market-microstructure feed.

**Improvement:** improve options/borrow/liquidity evidence and validate Tape heuristics historically before increasing their influence.

### 24.10 Financial / REIT valuation is simplified

The default Financial / REIT policy is currently P/E-centric.

**Improvement:** add sector-specific valuation frameworks where economically appropriate.

### 24.11 Research Conclusion thresholds need empirical calibration

ATTRACTIVE +20%, EXPENSIVE −15%, catalyst/path thresholds and Tape thresholds are currently explicit and deterministic, which is better than hidden logic, but they still need validation.

**Improvement:** use historical validation to test thresholds without retrofitting individual names.

---

## 25. How to decide whether a future improvement is actually better

Every material feature should identify which layer it improves.

### SOURCE

Does it add better factual evidence?

Measure:

- coverage;
- freshness;
- reliability;
- licensing;
- provenance.

### NORMALIZATION

Does it map evidence more correctly?

Measure:

- missing-field recovery;
- false mapping rate;
- reconciliation;
- restatement handling.

### MODEL

Does it improve fair value or analytical interpretation?

Measure:

- valuation accuracy;
- calibration stability;
- sensitivity;
- out-of-sample historical validation.

### DECISION

Does it improve the ability to distinguish:

- incomplete research;
- genuine Long/Short edge;
- no edge;
- conflicting evidence;
- thesis invalidation?

### WORKFLOW

Does it reduce manual friction without removing required judgment?

### UI

Does it make the same information easier to interpret without adding duplicate cards, scores or decorative noise?

### OPERATIONS

Does it improve speed, reliability, deployment or observability without weakening data integrity?

No feature should be added merely because it produces more data.

The standard is:

> Does this materially improve a real investment decision, the reliability of the evidence behind it, or the discipline of the process?

---

## 26. Product rules checklist

Before accepting a material future change, verify:

- fair value remains a primary output;
- Bear/Base/Bull remain auditable;
- current price does not become intrinsic evidence unless explicitly labeled provisional;
- missing facts are not guessed;
- manual analyst work is preserved;
- counter-evidence remains visible;
- Readiness remains human-approved;
- APPROVED · EVIDENCE CHANGED remains understandable;
- Validation remains point-in-time;
- Research remains separate from Portfolio sizing and Position Action;
- invalidation remains pre-investment and lockable;
- Decision Journal remains immutable at decision time;
- Discovery uses no filler quota;
- FINRA short-sale volume is not mislabeled short interest;
- macro does not override company evidence;
- peer evidence remains bounded;
- private Portfolio data never crosses publication/report boundaries;
- normal GET requests remain provider-free;
- production MariaDB/users/2FA are never reset;
- no purple;
- minimal bold;
- typography remains readable;
- Settings remains the only normal version surface;
- deploy remains separate from merge;
- tests prove state transitions/calculations, not merely HTTP 200.

---

## 27. Documentation rule

This file is a living product contract.

A future PR must update this document when it materially changes:

- workflow;
- research gates;
- Decision Lenses;
- Research Conclusion;
- valuation methods/thresholds;
- data-source policy;
- SEC normalization;
- Discovery;
- validation policy;
- Portfolio sizing;
- publication/privacy;
- security;
- UI invariants;
- any known limitation that materially changes.

CURRENT_STATE remains the concise release handoff.

This document explains how the machine is supposed to think.
