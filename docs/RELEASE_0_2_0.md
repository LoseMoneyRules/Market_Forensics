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
- [x] Evidence lenses: Quality at Discount, Long/Short Dislocation, Potential Value Trap, Forensic Divergence, Research Queue.
- [x] Coverage priority can be edited.
- [x] Remove/archive hides a company from active Coverage without deleting research/audit history.
- [x] Re-adding an archived ticker restores the existing research file.
- [x] Archived coverage is excluded from refresh-all, refresh-stale and discovery-priority jobs.
- [x] Refresh stale and Refresh all are visible from the Coverage command center.

Hosting choice: external Discovery is query-driven rather than preloading/revaluing the entire US market. This keeps Namecheap/shared-hosting work bounded and prevents a background screen from creating thousands of low-quality fair-value candidates.

## Research
- [x] Overview remains the single-company cockpit.
- [x] Research Action, stance, Bias, Confidence, Market/Bear/Base/Bull and Base gap are visible.
- [x] Process Readiness remains a first-class component.
- [x] Evidence Signals remain visible.
- [x] Decision Map adds Business / Numbers / Expectations / Valuation / Bear / Catalysts / Flows / Management / Tape / Monitoring / Audit lenses.
- [x] WHY NOW / WHY NOT YET / WHAT CHANGES THE DECISION / WHAT KILLS THE THESIS are visible in Overview.
- [x] Large valuation gap alone cannot create BUY.
- [x] Research engine exposes supporting evidence, opposing evidence, warnings and blockers.
- [x] Manual analyst fields are not silently overwritten by autofill.
- [x] Business contains a permanent Evidence Path and structured external triangulation.
- [x] Numbers supports FY/current TTM, working-capital and forensic evidence.
- [x] Expectations uses stored market inputs vs internal assumptions; the dead “Our view vs market expectation” chart is removed.
- [x] 5Y Bear/Base/Bull operating paths are distinct from valuation cases.
- [x] Valuation keeps multi-method intrinsic work, DCF cross-check, robust blend, share-basis checks and historical calibration.
- [x] Market price is gray in valuation history; Bear/Base/Bull remain distinct.
- [x] Bear Case, Catalysts, Financial Flows, Management accountability, Tape/Flows, Monitoring, Decision Journal and Sources/Audit are retained.
- [x] Tape context includes absorption, long demand, bear pressure, battle intensity, net tape, regime and confidence when evidence exists.
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
- [x] Portfolio writes do not modify Research conclusions.

## Reports
- [x] CONTROL-only Executive PDF export.
- [x] CONTROL-only Full Word export.
- [x] Market/Bear/Base/Bull valuation chart embedded in PDF and Word.
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

## Intentional non-blockers
- SMTP credentials are not configured by this release.
- Full-market Discovery is not a continuously running server-wide valuation crawler; the web implementation uses on-demand external discovery plus deep evidence after promotion.
- Production deployment is a separate workflow after main passes CI.

## Final release gate
The release may merge only after:
1. branch Python syntax check passes;
2. branch JavaScript syntax check passes;
3. 0.2.0 release suite passes;
4. PR checks pass;
5. main post-merge CI passes.
