# Market Forensics — How It Works, Operating Model & Product Rules

> Canonical current operating contract for Market Forensics.
>
> This document explains how the product is supposed to work end-to-end, what each analytical layer is allowed to conclude, which rules are permanent, where the current implementation is incomplete, and how future changes should be evaluated.
>
> CURRENT_STATE.md remains the release/deployment source of truth. This file is the product/decision-system source of truth.
>
> Historical specs and release notes remain useful context, but when they conflict with this document plus the current tested implementation, they are historical rather than canonical.

**Current product line:** 0.2.8  
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

### 3.3 Price is the decision interface, not the thesis

The current price is used to compare against Bear / Base / Bull fair value and to reverse-engineer market-implied expectations.

Price movement by itself does not validate or invalidate a fundamental thesis.

### 3.4 ADD ON EVIDENCE, NOT ON PRICE

Position additions must be justified by improved evidence, not merely by a lower share price.

### 3.5 Invalidation is fixed before investment

Numerical thesis invalidation thresholds are set before investment and are not rewritten after earnings or price movement to preserve the narrative.

A locked pre-investment invalidation cannot be silently changed retroactively.

### 3.6 Research and Portfolio remain separate

Research asks:

> Is the company attractive, at what value, why, what must happen, and what would invalidate the thesis?

Portfolio asks:

> Given the research view and the real position, how much capital and money risk should be carried?

Shares, average cost, P/L, position size, money-loss budget and portfolio sizing live under Portfolio, not Research.

### 3.7 Human approval is explicit

Process Readiness is not a machine confidence score.

It is a record that CONTROL reviewed the current evidence for each research gate.

### 3.8 Validation cannot rescue incomplete research

Validate is downstream of Research.

A historical score cannot bypass missing Research gates or substitute for a thesis, valuation, bear case, monitoring rule or source review.

### 3.9 Diagnostics cannot override canonical decision states

Evidence score, Tape score, macro context, management score, peer comparison and similar diagnostics support interpretation.

They cannot bypass:

- Process Readiness;
- data-quality warnings;
- Validate;
- Research Conclusion logic;
- locked invalidation discipline.

### 3.10 Manual analyst work wins

Automatic drafting may populate blank fields or fields still marked as auto-generated.

Manual analyst edits are never silently overwritten by a refresh.

### 3.11 Normal navigation stays fast

Normal GET requests read stored/materialized data and render.

They must not:

- call SEC;
- call market providers;
- refresh FRED;
- run discovery;
- perform historical backtests;
- rebuild heavy analytical engines.

Heavy work is queued.

### 3.12 Fail visibly, not silently

When something cannot be proven or computed:

- show the gap;
- show the warning;
- show the data quality;
- preserve last-good information only when clearly labeled;
- never silently pretend a fallback is equivalent to primary evidence.

### 3.13 Minimal bold, institutional readability

Use as little bold as possible.

Bold/strong weight is reserved for real hierarchy, ticker symbols, critical status and primary decision outputs. Normal links, gate names, body values, explanatory text and routine labels use regular weight.

UI rules also include:

- no purple;
- institutional blue;
- body typography normally 13–14 px;
- forms around 14 px;
- micro metadata no smaller than 12 px;
- chart labels 12–14 px;
- light and dark modes must both remain usable;
- desktop problems are not solved only with horizontal scrolling;
- empty decorative panels should not be rendered;
- footer remains Lose Money Rules.

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

It has two stages.

### Stage 1 — cheap market funnel

Current screen begins with Alpaca Most Active / Movers and validates candidate securities.

For a new external candidate:

- security must be active and tradable;
- major US exchange only: NASDAQ, NYSE, AMEX, ARCA;
- exclude warrants, rights, units, ETFs, ETNs, funds, blank-check/SPAC shells, preferred securities and note-like instruments;
- Long candidate price must be at least $5;
- Short candidate price must be at least $10;
- new-name daily volume must be at least 500k when available;
- new-name dollar volume must be at least $50M;
- a Short candidate must be shortable.

Existing Coverage names may use already-materialized research context rather than being rejected solely for missing cheap-screen liquidity fields.

### Stage 2 — forensic enrichment

A candidate must have a calculable intrinsic Base fair value and confirming operating evidence.

For external names:

- SEC submissions and Companyfacts are used;
- fiscal-year end must be respected;
- annual/quarterly history is reconstructed;
- current TTM is used when valid;
- the valuation engine runs with reference-price fallback disabled.

A Discovery Base is accepted only if:

- valuation quality is INTRINSIC;
- at least two valuation methods are usable.

Final direction requires:

- Long: Base gap at least +20% plus confirming Long operating evidence;
- Short: Base gap at most −20% plus confirming deterioration and short actionability.

A raw price move alone can never create the final Long/Short candidate.

There is no filler quota.

Zero candidates is a valid output.

### Discovery limitation to remember

The current Stage-1 universe is activity-driven. Quiet, liquid, materially mispriced companies that are not currently active/moving can be missed. This is a known structural limitation, not a feature.

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

Uses Base fair-value gap vs current market price.

- UNVERIFIED: gap unavailable.
- ATTRACTIVE: Base gap ≥ +20%.
- EXPENSIVE: Base gap ≤ −15%.
- FAIR: between −15% and +20%.

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
- DEFINED · UNPROVEN: views exist but no variant evidence / structured expectations.
- POSITIVE EDGE: Value ATTRACTIVE + Expectations FAVORABLE/BALANCED + variant evidence.
- NEGATIVE EDGE: Value EXPENSIVE + Expectations DEMANDING/BALANCED + variant evidence.
- POSSIBLE: defined but does not meet the stronger edge state.

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
2. All gates approved, Validate not run → **READY TO VALIDATE**
3. Value ATTRACTIVE + Variant POSITIVE EDGE + Path SUPPORTIVE + Model Confidence STRONG/MODERATE → **LONG READY**
4. Value ATTRACTIVE + Variant POSITIVE EDGE/POSSIBLE → **LONG WATCH**
5. Value EXPENSIVE + Variant NEGATIVE EDGE + Path HOSTILE + Model Confidence STRONG/MODERATE → **SHORT READY**
6. Value EXPENSIVE + Variant NEGATIVE EDGE/POSSIBLE → **SHORT WATCH**
7. Model Confidence LIMITED → **DATA REVIEW**
8. Otherwise → **NO EDGE · WAIT**

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

Management guidance/promises can be extracted from filings and compared with realized filed outcomes.

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

Tape is context, not intrinsic value.

Inputs can include:

- split-adjusted historical price;
- volume;
- turnover;
- FINRA daily short-sale volume;
- FINRA consolidated short interest;
- optional positioning data;
- optional/manual borrow fee observation;
- options context when available.

Important rule:

**FINRA daily short-sale volume is not short interest.**

### 16.1 Current compact Tape interpretation

Pressure direction:

- LONG when Long Demand − Bear Pressure ≥ +12 pts;
- SHORT when ≤ −12 pts;
- LATERAL inside that band;
- LOW DATA if the spread cannot be computed.

Posture:

- LOW confidence → WAIT FOR DATA;
- MIXED regime, lateral/low-data pressure, or battle intensity ≥ 70 → WAIT FOR CONFIRMATION;
- LONG + SUPPORTIVE regime → SUPPORTIVE TAPE;
- SHORT + HOSTILE regime → HOSTILE TAPE.

Next confirmation remains explicit.

Tape should answer:

- wait or not;
- LONG / SHORT / LATERAL pressure;
- what confirmation is still needed.

It must not be presented as a standalone trade signal.

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

Current readiness implementation requires approximately:

- reliability ≥ 65;
- at least 5 completed samples;

for VALIDATED.

### 19.4 Known validation inconsistency

The underlying historical run currently marks its own run.status as VALIDATED at reliability ≥ 60 once sample size is at least 3.

The user-facing readiness layer uses the stricter ≥65 + at least 5 sample rule.

This inconsistency should be removed.

There should be one canonical validation threshold policy.

Until corrected, Decision Lenses follow the readiness-layer state.

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

---

## 21. Reports

CONTROL exports include:

- Executive PDF;
- Full PDF;
- Full Word;
- Discovery landscape PDF.

Rich PDF/Word rendering is a production dependency and health requirement.

Report export must fail safely.

If rich rendering fails, a valid fallback artifact should still be returned.

An audit-write failure must not turn a valid report into HTTP 500.

In-memory reports are returned as normal response bytes and must not be delegated to a problematic WSGI file wrapper.

Private Portfolio holdings, sizing, P/L, private journal and credentials do not belong in published/member research reports.

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

CURRENT_STATE must be updated after material main/deploy transitions.

---

## 24. Known gaps and current weaknesses

This section is deliberately candid. A tool becomes stronger when the limits are explicit.

### 24.1 Validation has two threshold policies

As described above:

- historical run status uses one threshold;
- readiness/Decision Lenses use a stricter threshold.

**Improvement:** create one validation policy object/function used everywhere.

### 24.2 Research can display provisional reference-price valuation

This protects target-price visibility, but a market-derived provisional case can be confused with intrinsic value if labeling is weak.

**Improvement:** make valuation quality impossible to miss and prevent provisional/reference targets from contributing to any “edge” conclusion that is supposed to be intrinsic.

### 24.3 Model priors can hide weak source coverage

When growth or margins are unavailable, the valuation policy may use default priors.

That is acceptable only as an explicit model assumption.

**Improvement:** surface assumption provenance per driver: FILED / HISTORICAL CALIBRATION / TYPE PRIOR / MANUAL.

### 24.4 Readiness approval is monotonic even if evidence deteriorates

APPROVED · EVIDENCE CHANGED is useful and preserves human agency.

However a critical disappearance of evidence can theoretically remain approved until CONTROL reopens it.

**Improvement:** distinguish ordinary evidence change from a critical evidence-invalid state without silently erasing human approval.

### 24.5 Discovery is activity-biased

Most Active / Movers is efficient but not a true broad valuation universe.

**Improvement:** add a broad scheduled operating-equity universe scan, with Stage 1 designed around business/valuation dislocation rather than only current market activity.

### 24.6 Peer triangulation is database-limited

Sparse stored-company coverage creates sparse peers.

**Improvement:** build a provider-backed peer universe by SIC/industry first, then enrich a bounded peer set without requiring those names to already be in Coverage.

### 24.7 Expectations lack licensed consensus

Current price-implied expectations are useful, and structured analyst expectations are supported, but they are not Street consensus.

**Improvement:** add a licensed consensus/revisions provider when storage/display rights are clear.

### 24.8 Borrow/options/ownership are incomplete

Borrow fee may be manual; options depth and ownership flows are not yet institutional-grade.

Potential future lanes:

- securities lending;
- options OI/IV/skew/term structure;
- 13D/13G;
- 13F;
- Forms 3/4/5.

These must remain sourced and timestamped.

### 24.9 SEC taxonomy coverage can still miss issuer-specific facts

Exact-label extension fallback improves recovery but cannot guarantee every issuer taxonomy maps cleanly.

**Improvement:** add filing-instance/iXBRL-level fallback where Companyfacts remains insufficient, with strict provenance.

### 24.10 Business qualitative evidence is still underdeveloped

Numbers, valuation and source audit are becoming stronger than:

- moat evidence;
- customer concentration;
- competitive structure;
- pricing power;
- unit economics;
- industry structure;
- supplier/customer read-through.

**Improvement:** create structured qualitative evidence objects rather than relying mainly on free text.

### 24.11 Tape is heuristic

Tape combines useful context, but it is not an institutional market-microstructure feed.

**Improvement:** improve options/borrow/liquidity evidence and validate Tape heuristics historically before increasing their influence.

### 24.12 Financial / REIT valuation is simplified

The default Financial / REIT policy is currently P/E-centric.

**Improvement:** add sector-specific valuation frameworks where economically appropriate.

### 24.13 Research Conclusion thresholds need empirical calibration

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
- Research remains separate from Portfolio sizing;
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
- version is visible only where intended;
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
