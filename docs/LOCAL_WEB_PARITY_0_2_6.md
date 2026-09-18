# Market Forensics 0.2.6 — Local V3.1.12 → Web capability parity audit

This audit compares the accepted Local V3.1.12 product baseline with the web-native product.
It does **not** make the Local application a runtime dependency. The purpose is to prevent a web
rewrite from silently deleting an accepted analytical or workflow capability.

Status meanings:

- **IMPROVED** — Web provides the Local capability plus a stronger/auditable implementation.
- **PRESERVED** — Web provides the same accepted user capability.
- **SUPERSEDED** — The Local page/control no longer exists literally, but a stronger canonical web workflow covers it.
- **RECOVERED 0.2.6** — A regression existed in 0.2.5 and is structurally restored in 0.2.6.

| Local V3.1.12 capability | Web 0.2.6 status | Web-native equivalent / evidence |
| --- | --- | --- |
| Discovery | IMPROVED | FORENSIC_FAIR_VALUE_V1: fail-closed operating-equity universe, intrinsic Base gap + operating confirmation, bounded SEC enrichment, zero-candidate valid state. |
| Coverage list | IMPROVED | Research Command Center with conclusion, value/path, model confidence, Price/Base/gap, readiness, validation, freshness, next action and management controls. |
| Decide | SUPERSEDED | Overview + automatic decision lenses + Research conclusion + Process Readiness + explicit Validate stage. |
| Research workspace | IMPROVED | Canonical Research sections with one company header, versioned manual ResearchState and cached analytical synthesis. |
| Fundamentals | RECOVERED 0.2.6 / IMPROVED | Canonical Fundamentals route; SEC economic-period normalization; filing-aware quarters; consecutive-quarter TTM; Revenue/FCF chart; restored CFO/NI, DSO/DIO/DPO/CCC, Inv/Rev, Rec/Rev, shares YoY and fail-closed ROIC. |
| Working-capital chart | RECOVERED 0.2.6 | Inventory and Receivables use independent left/right scales rather than flattening one series. |
| Financial Flows | PRESERVED / IMPROVED | Research → Financial Flows, Income Statement + Cash Flow, signed-negative handling, audit provenance and one renderer. 0.2.6 reserves node height for wrapped labels so numeric values remain visible. |
| Valuation / Bear-Base-Bull | IMPROVED | Shared auditable valuation engine, intrinsic quality contract, current share-basis checks, expectations/variant context and current-price gap. |
| Management forecast / promises | IMPROVED | Expectations + Management assessment + automatic promises-vs-actuals + scenario forecasts. |
| Tape | IMPROVED | Tape / Flows combines stored price/tape context, FINRA/positioning where available and cached/background refresh. |
| Monitoring | IMPROVED | Structured MonitoringRule + evaluation + alert/history workflow; no heavy work on normal GET. |
| Thesis | SUPERSEDED | Overview thesis/counter-evidence/variant + Bear Case + Monitoring invalidation + frozen Decision Journal. |
| Decision Journal original snapshot | PRESERVED | DecisionJournal freezes thesis, valuation, invalidation, research/investment state and evidence. |
| Decision Journal later outcome/post-mortem | RECOVERED 0.2.6 | Separate append-only DecisionOutcome rows; original decision is never rewritten. |
| Risk / Position sizing | RECOVERED 0.2.6 | PortfolioRiskPlan restores loss budget, sizing reference, event/liquidity haircut, max cap, factor notes, kill-switch and Local downside-based sizing formula. Research invalidation stays separate. |
| Portfolio can exist before thesis | RECOVERED 0.2.6 | Independent Position + PositionProfile keyed by user/security. Validated ticker can be added before Coverage/Research. |
| LONG / SHORT real holdings | RECOVERED 0.2.6 | Explicit side stored in PositionProfile; side-aware P/L and exposure. |
| Exposure tags / shared drivers | RECOVERED 0.2.6 | Manual comma-separated exposure tags are aggregated into factor exposure rather than relying only on sector labels. |
| Remove holding without deleting Research | RECOVERED 0.2.6 | Position removal preserves Coverage, Research, money-risk history and audit; attached InvestmentState returns to WATCHLIST. |
| Portfolio correlations | PRESERVED / IMPROVED | Pairwise correlations remain background/materialized so Portfolio GET stays fast. |
| Validate / historical test | IMPROVED | Separate point-in-time/walk-forward Validate state (NOT RUN/LIMITED/VALIDATED/REVIEW), independent from Research conclusions. |
| Sources | IMPROVED | Sources / Audit includes source rows, provenance and refresh history. |
| Settings / providers | IMPROVED | Encrypted provider credentials, display format, branding, collapsed jobs, job cancellation/recovery; version shown only here. |
| Local synchronous execution model | SUPERSEDED | FAST UI → bounded jobs → materialized/cached results → non-disruptive updates. |
| Word report | RECOVERED 0.2.6 / IMPROVED | Rich python-docx is a production dependency and production health gate; full report includes valuation, thesis/variant, evidence, Fundamentals history, expectations, management accountability, tape and sources. |
| PDF report | RECOVERED 0.2.6 / IMPROVED | Rich ReportLab is a production dependency and production health gate; Executive + Full PDF retained. |
| Discovery PDF | PRESERVED | Landscape Discovery export retained from stored scan payload. |
| Private/public separation | IMPROVED | Reports/publication never export private holdings, position sizing, cost/P&L, journal or credentials; FRIEND/INSIDER receive only published role-safe payloads. |
| Local user-only desktop model | SUPERSEDED | CONTROL private workspace plus FRIEND/INSIDER publication layer, secure sessions, Argon2, TOTP/2FA, role enforcement and audit trail. |

## 0.2.5 regressions found by the audit

The 0.2.5 post-merge run was green, but its tests were too surface-oriented in several places.

The audit found four classes of false confidence:

1. **Portfolio page existed, but Portfolio was not independent.** A user could not create a real
   holding unless a Coverage/Research workspace already existed because mutation routes depended on
   `_ctx(ticker)`.
2. **Risk fields existed, but the accepted Local position-sizing engine did not.** Money-risk and
   thesis risk had been compressed into one coverage-bound model.
3. **Journal fields existed, but the accepted temporal discipline did not.** “Outcome later” was
   stored in the original decision payload rather than appended later.
4. **Report tests returned valid files, but production did not install/require the rich renderer.**
   CI explicitly accepted `stdlib-fallback` in the production-minimal environment.

These are why 0.2.6 release tests must prove capabilities, not merely HTTP 200/page presence.

## Intentional improvements over Local behavior

Not every Local implementation is copied literally.

- Local ROIC could fall back to a 21% tax rate. Web 0.2.6 withholds ROIC when the required reported
  tax inputs are missing.
- Local synchronous workflows are replaced by bounded background jobs and stored results.
- Portfolio money-risk is separated structurally from Research thesis invalidation.
- Position-only securities no longer need placeholder Research records.
- Report rich-renderer availability is a production health requirement instead of a best-effort optional package.
- SEC fiscal periods and TTM obey the web filing-correctness rules rather than older convenience aggregation.

## Release rule created by this audit

A future release cannot claim Local parity from page names or module names.

For every capability above, regression tests must prove the critical state transition or calculation
where practical. Any new intentional removal must be documented in CURRENT_STATE and this matrix as
a named SUPERSEDED capability with the replacement identified.
