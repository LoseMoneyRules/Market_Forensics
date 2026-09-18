# Market Forensics 0.2.0 — Release Audit

Status: **FINAL CANDIDATE**

## Product flow
- [x] Discover → Research → Validate → Portfolio
- [x] Research and Portfolio are separated by route, template and write endpoint.
- [x] Research invalidation remains in Monitoring.
- [x] Money risk, sizing, entry/add/trim/exit and positions remain in Portfolio.
- [x] New validation runs require current Research Process Readiness approval.

## Coverage / Discovery
- [x] Validated ticker required before Coverage creation.
- [x] External US-equity lookup can find securities outside current Coverage using the configured provider.
- [x] Candidate promotion creates a Research workspace and queues evidence refresh.
- [x] Market-wide lightweight scanner uses provider most-active + market-movers screens without mass-building guessed valuations.
- [x] Evidence lenses: Quality at Discount, Fundamental Inflection, Long Dislocation, Potential Short, Short Dislocation, Potential Value Trap, Forensic Divergence, Research Queue.
- [x] Coverage priority can be edited.
- [x] Remove/archive hides a company from active Coverage without deleting research/audit history.
- [x] Re-adding an archived ticker restores the existing research file.
- [x] Archived coverage is excluded from refresh-all, refresh-stale and discovery-priority jobs.
- [x] Refresh stale and Refresh all are visible from the Coverage command center.

Hosting choice: Discovery scans the broad tradable market through lightweight provider screeners, then enriches only names with stored evidence. Unknown candidates remain explicitly unvalued until promotion/deep research. This preserves market-wide discovery while keeping Namecheap/shared-hosting work bounded.

## Research
- [x] Overview remains the single-company cockpit.
- [x] One canonical Research Conclusion replaces competing BUY/SELL/WAIT headlines.
- [x] Mature decision lenses: BUSINESS, VALUE, EXPECTATIONS, VARIANT, PATH, MODEL CONFIDENCE and THESIS CONTROL.
- [x] Research conclusion states include RESEARCH INCOMPLETE, READY TO VALIDATE, LONG/SHORT READY, LONG/SHORT WATCH, DATA REVIEW and NO EDGE · WAIT.
- [x] Market/Bear/Base/Bull, Expected Value and Base gap remain immediately visible.
- [x] Process Readiness remains a first-class component.
- [x] Evidence Signals remain visible.
- [x] Decision Map uses analytical research lenses rather than readiness-only labels; the evidence score remains diagnostic only.
- [x] WHY NOW / WHY NOT YET / WHAT CHANGES THE DECISION / WHAT KILLS THE THESIS are visible in Overview.
- [x] Large valuation gap alone cannot create BUY.
- [x] Research engine exposes supporting evidence, opposing evidence, warnings and blockers.
- [x] Manual analyst fields are not silently overwritten by autofill.
- [x] Business contains a permanent Evidence Path and structured external triangulation.
- [x] Automatic triangulation: exact SEC SIC → 2-digit SIC division → industry fallback, with market-cap proximity where available.
- [x] Peer comparison covers Revenue Growth, Operating Margin, FCF Margin, ROIC, Inventory/Revenue, Receivables/Revenue, Asset Turnover, Share Change, P/E, EV/Sales and FCF Yield, including relative-value-plus-quality signals.
- [x] Numbers supports FY/current TTM, working-capital and forensic evidence.
- [x] Expectations uses stored market inputs vs internal assumptions; the dead “Our view vs market expectation” chart is removed.
- [x] Price-implied expectations invert the current price into three transparent drivers: 5Y Revenue CAGR, Y5 Net Margin and Y5 Exit P/E, classified FAVORABLE / BALANCED / DEMANDING.
- [x] 5Y Bear/Base/Bull operating paths are distinct from valuation cases.
- [x] Valuation keeps multi-method intrinsic work, DCF cross-check, robust blend, share-basis checks and historical calibration.
- [x] Market price is gray in valuation history; Bear/Base/Bull remain distinct.
- [x] Bear Case, Catalysts, Financial Flows, Management accountability, Tape/Flows, Monitoring, Decision Journal and Sources/Audit are retained.
- [x] Management accountability includes a conservative SEC guidance parser plus a structured manual fallback and scores explicit promises against filed actuals as PENDING / MET / MISS.
- [x] Existing SEC filing sources can be retro-scanned for guidance without duplicating Source records.
- [x] Tape context includes absorption, price resilience, long demand, bear pressure, battle intensity, turnover impulse, net tape, rank, regime and confidence.
- [x] Options/borrow context includes Put/Call open interest, shortability/borrow status, locate information when provider-eligible, and structured sourced borrow-fee observations when an annualized fee is not exposed automatically.
- [x] Monitoring is exceptions-first: WATCH/FAIL observations and generated alerts appear before the routine monitoring schedule.
- [x] Financial Flows remain under Research and preserve Income Statement / Cash Flow flow logic.

## Validate
- [x] Validate is outside the Research section sequence and follows completed Research.
- [x] Point-in-time walk-forward uses no-lookahead protocol.
- [x] Historical depth can request up to 40 years when provider/SEC history exists.
- [x] Partial history is disclosed rather than fabricated.
- [x] Reliability, valuation accuracy, direction accuracy, Bear/Bull range coverage and assumption accuracy are shown.
- [x] Replay inputs, assumptions and leakage checks remain auditable.
- [x] Corporate-action/share-basis handling remains part of historical validation.

## Portfolio
- [x] Shares, average cost, market value and P/L.
- [x] Portfolio weight / exposure.
- [x] Research/validation status shown as reference only.
- [x] Max position / max loss / entry / add / trim / exit rules.
- [x] Position-limit breach warnings.
- [x] Top-position concentration and HHI.
- [x] Validation coverage across positions.
- [x] Pairwise correlations for top positions from stored split-adjusted price history.
- [x] Coverage acts as the Decision Queue with priority, conclusion, Value/Path, Model Confidence, price/scenarios/gap, Process Readiness, Validate state, freshness and explicit next action.
- [x] Portfolio writes do not modify Research conclusions.

## Reports
- [x] CONTROL-only Executive PDF, Full PDF and Full Word research exports.
- [x] CONTROL-only Discovery landscape PDF from the latest market-wide scan.
- [x] Market/Bear/Base/Bull valuation chart embedded in PDF and Word.
- [x] Research reports carry canonical decision lenses, price-implied expectations, triangulation, management promises and Tape/positioning context.
- [x] Configurable report title / prepared-by / footer / optional safe HTTPS logo branding.
- [x] Thesis, counter-evidence, variant, evidence for/against and full Research sections.
- [x] Sources included in full report.
- [x] Portfolio shares, average cost, money-risk limits and private position notes are excluded from Research reports.

## Security / publication
- [x] MariaDB production database preserved.
- [x] CONTROL account, password hash, TOTP/2FA and encrypted provider credentials preserved.
- [x] Preference keys migrate additively; account email is the notification source of truth.
- [x] FRIEND/INSIDER cannot access Portfolio or CONTROL report endpoints.
- [x] Publication payload excludes private Position and money-risk data.
- [x] Immutable snapshot/publication model retained.
- [x] Audit/provenance retained.

## Data resilience / hosting
- [x] Quote cascade is web-native: Alpaca → Tiingo → Alpha Vantage → public cross-check, with disagreement protection and last-good preservation.
- [x] Global Refresh All includes market, SEC, recalculation, FINRA, options/borrow positioning and management-guidance evidence where configured.
- [x] Refresh Stale runs the same evidence layers on bounded freshness windows rather than re-fetching everything.
- [x] The browser worker/cPanel cron architecture remains bounded for shared hosting; no IBKR Gateway or desktop daemon is required.
- [x] Automatic research never fabricates missing consensus, borrow fees, peer fundamentals or fair values.

## Runtime / UX
- [x] VERSION = 0.2.0.
- [x] One canonical app.css runtime bundle.
- [x] One canonical app.js controller plus narrowly scoped chart modules.
- [x] Numbered 0.1.x route/CSS/JS/template runtime layers removed.
- [x] No MutationObserver DOM reconstruction.
- [x] Mobile menu has one controller with backdrop, Escape, link close and resize reset.
- [x] Research tabs use one mobile step selector.
- [x] UTC remains storage basis; visible timestamps are localized in browser.
- [x] Purple removed from canonical styling.
- [x] Footer/brand remains Lose Money Rules.
- [x] Pre-0.2.0 test suites retired; 0.2.0 is the active runtime contract.
- [x] V3 code/archive is reference-only and has no runtime import.

## Intentional non-blockers / data availability
- SMTP credentials are not configured by this release.
- Market-wide Discovery is a broad lightweight screener, not a continuously running server-wide valuation crawler; valuation begins only after evidence exists/promotion.
- Existing companies gain exact SEC SIC metadata on SEC refresh; industry fallback is used until then.
- Automatic management-promise scoring only converts explicit numeric guidance with a target year; ambiguous language remains unscored/manual rather than guessed.
- Put/Call OI and borrow status depend on provider entitlement/coverage. A sourced manual borrow-fee observation is supported when no annualized fee is exposed by the provider.
- Production deployment remains a separate workflow after main passes CI.

## Final release gate
The release may merge only after:
1. branch Python syntax check passes;
2. branch JavaScript syntax check passes;
3. 0.2.0 release suite passes;
4. PR checks pass;
5. main post-merge CI passes.
