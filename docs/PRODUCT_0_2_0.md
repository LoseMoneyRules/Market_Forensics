# Market Forensics 0.2.0 — Locked Product Baseline

## Release intent
0.2.0 is the clean web-native baseline. It keeps the strongest parts of the 0.1.x web product and restores the analytical depth of the Local V3 lineage without recreating the Streamlit product or carrying its runtime architecture forward.

The production database remains MariaDB. Existing CONTROL identity, password hash, TOTP/2FA, encrypted API credentials, sessions, publication history, audit history, research data and market/financial history are preserved.

## Product flow
**Discover → Research → Validate → Portfolio**

### Discovery / Coverage
Coverage is the operating command center for companies already under study. Discovery can also look outside current Coverage and promote a validated symbol into Research.

### Research
Research is intentionally position-agnostic. A company is studied in this order:

1. Overview
2. Business
3. Numbers
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

Overview is the decision cockpit. It keeps Research Action, Bias, Confidence, Bear/Base/Bull, Base gap, Evidence Signals and Process Readiness. It explains why the current research stance exists and what evidence would change it.

Process Readiness measures completion and explicit CONTROL approval of the research evidence only. Portfolio sizing and historical validation are not Research gates.

When all Research gates are current and approved, the company becomes **READY TO VALIDATE**.

### Validate
Validate comes after Research. It tests whether the completed research process has historical support using point-in-time replay, no-lookahead controls, corporate-action/share-basis checks, valuation accuracy, direction accuracy, range coverage, assumption accuracy and Reliability.

Validation states:
- NOT RUN
- LIMITED
- VALIDATED
- FAILED / REVIEW

Validation never retroactively changes historical assumptions.

### Portfolio
Portfolio is private and separate from Research. It contains positions, shares, average cost, market value, P/L, exposure, concentration, position state, sizing limits, max loss, add/trim/exit conditions and portfolio risk.

Research answers: **Is the company attractive, at what value, why, and what would invalidate the thesis?**
Portfolio answers: **Given a validated research view, how much capital should be allocated and managed?**

Thesis/evidence invalidation belongs to Research/Monitoring. Monetary risk and sizing belong to Portfolio.

## Decision discipline
The system follows:

BUSINESS → NUMBERS → EXPECTATIONS → VALUATION → BEAR CASE → CATALYSTS → FLOWS → MANAGEMENT → TAPE → MONITORING → VALIDATE → PORTFOLIO

**ADD ON EVIDENCE, NOT ON PRICE.**

A large valuation gap alone does not create a positive Research Action. The engine must expose supporting and opposing evidence, warnings, missing gates and confidence.

## UX baseline
- Blue institutional palette; no purple.
- One production CSS bundle and one application JS bundle plus narrowly scoped chart modules.
- No DOM rewrite layers that replace server-rendered Research content after load.
- Mobile navigation is a release gate, not a later patch.
- Company research tabs remain horizontally usable on desktop and collapse into a reliable mobile research-step control.
- UTC is stored server-side; visible timestamps are localized in the browser.
- Manual analyst edits are never silently overwritten by autofill.
- Empty decorative panels are removed rather than rendered as dead UI.

## Data and evidence
- MariaDB is the only production primary database.
- Market data remains multi-provider with last-good preservation and evidence.
- SEC normalized financials retain provenance.
- FINRA positioning is contextual, never intrinsic valuation evidence.
- Valuation keeps Bear/Base/Bull visible when mathematically available, with warnings instead of unrelated hard blocking.
- Sources/Audit remain first-class and every important automated conclusion should be traceable.

## Publication boundary
CONTROL retains the full private workspace. FRIEND / INSIDER receive only explicitly published immutable snapshots. Portfolio, sizing, personal journal, private notes, credentials and private operating metadata never cross the publication boundary.

## Release definition of done
0.2.0 is complete only when:
- runtime no longer loads version-layer CSS/JS;
- runtime routes use semantic module names rather than release-number modules;
- Research → Validate → Portfolio separation is enforced in navigation and endpoints;
- Overview / Process Readiness work without browser DOM reconstruction;
- Discovery can locate symbols outside current Coverage when a configured provider is available;
- Portfolio has a working detail/edit surface separate from company Research;
- Validate is the final research-stage workflow;
- mobile navigation and company research navigation work on narrow viewports;
- Python and JavaScript syntax checks pass;
- release regression tests pass;
- merge to main is green;
- production deployment remains a separate explicit action.
