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

**State-Version: 0.3.2**  
**Product:** Market Forensics  
**0.3.2 Validate / Regime / Peer closure branch:** `fix/0.3.2-validate-regime-peers`, created directly from clean main `4056482cce9f6f15c721464ea547c4d6cdbdfb5d`; VERSION remains 0.3.2.  
**0.3.2 structural-regime rule:** company-specific historical multiples are comparable only within the current economic regime when a conservative multi-dimensional detector identifies a HIGH_CONFIDENCE_BREAK across persistent growth/margin/capital-intensity/leverage/R&D/ROIC evidence. Pre-regime anchors are excluded from automatic P10/P50/P90 calibration and historical re-rating context; POSSIBLE_BREAK is surfaced but does not alter numbers. TTM never counts as an extra fiscal year for structural-break detection.  
**0.3.2 Validate parity rule:** historical validation replays the same canonical automatic valuation engine used by live Valuation at each filing cutoff, including regime-aware company history, current filing anchor, EV/EBITDA/P-B evidence when available, lifecycle/method applicability and independent valuation-family requirements. Reference-price fallback is disabled; only decision-grade INTRINSIC samples with at least two independent valuation families contribute to reliability. Future filings/company history are excluded from the historical snapshot and future prices enter scoring only afterward.  
**0.3.2 peer-universe rule:** Coverage membership is never peer evidence. Each full-market Discovery run materializes an independent economic peer-candidate universe from its liquid SEC-frame screen. Only business-verified CLOSE/PARTIAL peers with verified SIC/industry compatibility may set peer medians or peer-adjusted value; full-market candidates with similar economics but unverified taxonomy are displayed only as candidates and cannot affect valuation. Fewer than two verified peers fails closed rather than manufacturing a relative-value answer.  
**0.3.2 Valuation Integrity branch:** `fix/0.3.2-valuation-integrity`, created directly from clean deployed 0.3.2 main `b6d18b51107b386f91b35d4f056e7ca7591923bc`; VERSION remains 0.3.2.  
**0.3.2 Valuation Integrity pull request:** #61 `0.3.2: Valuation Integrity Engine` merged by squash to `main` as `2463ebf943c9487a80bfeae0e9ec1eb358206927`; VERSION remains 0.3.2.  
**0.3.2 Valuation Integrity verification:** final branch push CI #1646 / run `35681257101` SUCCESS; final PR CI #1647 / run `35681262127` SUCCESS; post-merge main CI #1648 / run `35681364889` SUCCESS. All passed release metadata, Python/YAML/JS checks, full pytest, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke.  
**0.3.2 canonical valuation rule:** automatic Bear/Base/Bull no longer uses fixed company-type/sector multiple proxies. Each comparable multiple must come from that company's own point-in-time filing/price history: 5Y P10/P50/P90 when at least four usable anchors exist, otherwise the company's own 10Y history; an unsupported method stays unavailable instead of receiving a proxy. FCF Yield is directionally inverted (Bear high yield / Bull low yield).  
**0.3.2 method applicability rule:** P/E, P/S, EV/Sales, EV/EBITDA, FCF Yield and DCF remain individually auditable, but decision-grade confirmation counts independent economic families rather than formula count: Earnings, Sales, EBITDA and Cash Flow. FCF Yield + DCF alone is one family, not two confirmations. Thin-margin businesses disable sales multiples; high leverage suppresses equity-only shortcuts; cyclical businesses normalize operating distributions and de-emphasize spot P/E; high-margin asset-light companies can weight DCF/cash-flow evidence more heavily. Financial/REIT generic industrial valuation fails closed until sector-specific evidence is available.  
**0.3.2 scenario-integrity rule:** automatic valuation uses a deterministic seeded 10,000-draw distribution over company-evidence ranges; Bear/Base/Bull are P10/P50/P90 outcomes. An explicit final order guard forbids automatic Bear > Base or Base > Bull. A single usable method is not decision-grade INTRINSIC.  
**0.3.2 economic guardrails:** SBC is not double-counted by subtracting it from FCF and also diluting shares; reported FCF is retained while observed diluted-share growth is projected in the per-share denominator. The engine also applies bounded life-cycle, leverage, CCC, conservative net-cash floor, applicable Altman tail-risk and price/P-B freshness diagnostics. Goodwill is not arbitrarily deducted from DCF because it is not an additive DCF asset.  
**0.3.2 canonical reuse:** Research/Valuation and Discovery Stage 2 use the same `valuation_engine.py` intrinsic logic. `valuation_forensics.py` remains the stored historical/peer/re-rating interpretation layer and may not calculate an alternative intrinsic answer. Discovery unknown finalists fetch bounded 10Y point-in-time price history only after Stage-1.5 selection; old pre-integrity auto valuation caches are stale until recalculated.  
**0.3.2 Discovery market-mispricing branch:** `fix/0.3.2-discovery-market-mispricing`, created directly from clean deployed 0.3.2 main `cd875273a5f03061d77cffa10db1d2464b33749c`; VERSION remains 0.3.2.  
**0.3.2 Discovery selection rule:** every successful run still scans the full eligible Stage-0 universe and market-wide SEC-frame fundamentals, but unknown names may now enter deep Stage 2 only through an auditable Stage-1.5 market-mispricing hypothesis: valuation tension + aligned filed operating evidence, with contradiction penalties. Daily movers, alphabetical order and generic liquidity do not create eligibility; dollar volume is only a final tie-break.  
**0.3.2 Discovery deep budget:** Stage 2 is bounded at 52 finalists, with at most four reserved slots for already-covered names carrying stored intrinsic/historical/peer dislocations. Remaining capacity is balanced LONG/SHORT across genuine Stage-1.5 hypotheses; unused side capacity may flow to the other side, but there is no filler quota. P1/P2/WATCH final thresholds are unchanged and still require canonical deep valuation evidence.  
**0.3.2 Discovery evidence-breadth guard:** market-wide valuation proxies use current filed shares when available and the comparable prior filed share frame only as an explicit fallback. The run separately measures valuation-evidence coverage; below 40% it is CRITICAL and below 65% it is WARN, so a thin proxy universe can never masquerade as complete market coverage.  
**0.3.2 core-data integrity branch:** `fix/0.3.2-core-data-integrity`, created directly from clean 0.3.2 main `43f15974780f432bc23ac96c0961285b92a7e43f`; VERSION remains 0.3.2.  
**0.3.2 Fundamentals history rule:** Research targets at least 10 consecutive fiscal years and displays up to 16 stored FY. Missing fiscal years are never silently compressed: tables show explicit NOT STORED rows and charts preserve null gaps. SEC remains canonical; configured Alpha Vantage may create a missing FY or fill missing fields only when SEC-normalized evidence is absent, with provider-level provenance.  
**0.3.2 historical-price rule:** the core price cache now targets 10Y by default across Coverage, Portfolio, manual refresh and bulk/stale refresh. Valuation may plot a 2Y window, but it audits the underlying 10Y cache. Historical validation can still request its wider explicit lookback independently. Successful partial backfills use a weekly retry cooldown to avoid page-load job thrash.  
**0.3.2 core-surface regression rule:** the release suite traverses all Research company pages plus Dashboard, Discovery, Portfolio, Publications, Settings, Control, Trace, notification/alert settings, live price/history APIs, research surface APIs, and PDF/DOCX report generation against a populated workspace.  
**0.3.2 source branch:** `release/0.3.2-research-integrity`, created directly from clean main `60091bbf69caff005df245206313c7b9f5b6dc56`; no patch-on-patch ancestry.  
**0.3.2 scope:** RESEARCH INTEGRITY RECOVERY — Fundamentals, Expectations, Financial Flows and the rest of Company Research must remain readable from stored MariaDB evidence even if derived readiness/control metadata fails. Decision/approval/publication remain fail-closed.  
**0.3.2 readiness hardening:** financial-basis and thesis-version timestamps are normalized to UTC-naive comparison semantics before review-reset checks; mixed legacy/driver timezone shapes must never raise during Company GET rendering.  
**0.3.2 evidence-visibility invariant:** normal Company GETs use a degraded readiness fallback only for display continuity. Stored evidence stays visible; no gate is treated as approved, and validation/publication remain blocked until the control layer is healthy. Mutation routes continue to use strict readiness.  
**0.3.2 regression rule:** active CI now includes the still-live 0.3.1 regression suites plus a populated Company Research integrity suite that opens Fundamentals, Expectations, Financial Flows and every Research navigation route with stored financial/expectation/flow data. A generic `/health` success is no longer considered sufficient release evidence for Research pages.  
**0.3.1 source branch:** `release/0.3.1-rerating-peer-research-reset`, created directly from clean main `ed5f5e4c948acc36b93d0388fcfd2c55d247b3ff`; no patch-on-patch branch ancestry.  
**0.3.1 Discovery full-market correction branch:** `fix/0.3.1-full-universe-discovery`, created directly from current clean main `4d674707c04dc91aebeb89357049b2122386a5e6`; VERSION remains 0.3.1.  
**0.3.1 pull request:** #52 `0.3.1: Re-rating, Peer Triangulation & Research Reset` merged by squash to `main` as `917bf3eccaf07e4339df6d31a60b30aa7e7956a3`.  
**0.3.1 verified release CI:** branch/PR release suite completed successfully after legacy-adapter compatibility hardening; post-merge main CI run `35631464938` / #1467 completed successfully, including release tests, production-minimal startup + rich-report smoke, and self-contained reporting-vendor smoke.  
**0.3.1 scope:** RE-RATING, PEER TRIANGULATION & RESEARCH RESET — deterministic new-financial-evidence review reset; point-in-time historical multiple regimes and bounded multiple bridge; market-implied expectations; multi-dimensional CLOSE/PARTIAL/REFERENCE peer classification; peer-adjusted relative multiple; independent intrinsic/historical/peer triangulation; sourced catalyst timeline and evidence-based Decision Window; canonical reuse by Valuation, Discovery and Reports.  
**0.3.1 Research reset:** when a newly normalized FY/Q filing, recent restatement or material re-normalization is materialized after a gate approval, only financially dependent gates become `REVIEW REQUIRED`. Prior approval rows, analyst notes and assumptions remain preserved. Business, Tape, Journal and Audit remain approved unless their own evidence changes. The UI must show `NEW FINANCIAL EVIDENCE — REVIEW REQUIRED`, current filing basis, and reopened gates.  
**0.3.1 publication rule:** historical publications remain immutable. Current Research cannot create/publish a new snapshot while the current financial basis has reopened required gates.  
**0.3.1 valuation rule:** historical and peer relative value are independent cross-checks. No blind arithmetic average and no automatic peer blend into Bear/Base/Bull. The previous peer-overlay API remains compatibility-only with weight 0.0. Missing data stays missing; EV/EBITDA remains unavailable until EBITDA is a canonical normalized fact/derivation.  
**0.3.1 timing rule:** Decision Window states are NO URGENCY / BUILDING WINDOW / ACTIVE WINDOW / CLOSING WINDOW / THESIS BROKEN and are driven by stored catalysts, re-rating conditions, monitoring/invalidation and open valuation evidence — never price movement alone. ADD ON EVIDENCE, NOT ON PRICE.  
**0.3.1 thesis-invalidation rule:** a locked invalidation is immutable for its current Core Thesis version, not permanently immutable across all future research. An explicit Core Thesis revision archives the prior thesis + invalidation pair in ResearchVersion history, clears/unlocks only the live invalidation for the new thesis, and reopens Thesis / Variant plus Monitoring until a new invalidation is written, locked and reviewed. Existing locked invalidation cannot be edited in place.  
**0.3.1 local-time rule:** persisted timestamps and API instants remain UTC. Quote and other user-facing time-of-day displays are converted by the browser to the user’s OS/browser timezone, including DST, and quote freshness labels include the local short timezone (for example EDT/EST) to avoid ambiguity.  
**0.3.1 Discovery full-market rule:** every successful Run Discovery checks the entire eligible Stage-0 universe for market/liquidity, then applies a cached market-wide SEC XBRL Frames fundamental pre-screen to every liquid name before unknown names may consume deep Stage-2 budget. There is no rotating cursor, no Most Active/Movers allocation lane, and no market-activity-only fallback. Missing fundamental coverage is surfaced rather than guessed. Deep Companyfacts/canonical valuation remains bounded at 20 evidence-selected finalists and is balanced across LONG/SHORT pre-screen leads.  
**0.3.1 architecture:** one canonical `valuation_forensics.py` materialized by RECALCULATE. Valuation Web, Discovery Stage 2 and Reports consume the same cache payload. Normal GETs do not call providers or run peer/historical scans.  
**Permanent development rule:** NO PATCH SU PATCH. Start material releases from clean `main`; do not create parallel analytical engines.  
**Architecture:** web-native Flask + MariaDB production  
**Runtime principle:** FAST UI → bounded background jobs → cached/materialized results → non-disruptive UI updates  
**Production:** 0.3.2 on Namecheap; verified production health after manual deploy run `35670847915` / deploy #64 from main source `9ea5bdbeb0daf0ea9735edd83a84e5b5dfcdd048` at 2026-09-22T00:14:15Z; /health matched VERSION and returned `architecture=web-native`, `database=primary`, `reports=rich`, `status=ok`.  
**Verified production baseline:** manual deploy run `35448890411` / deploy #49 = completed / success on main event SHA `ff4eb1e96a92610bc3ddd2c21c6e32840a016452`; candidate and final production health returned `{"architecture":"web-native","database":"primary","reports":"rich","status":"ok","version":"0.2.11"}`; persistent reporting-vendor rebuild/stage steps were skipped because dependencies were unchanged  
**Accepted pre-0.2.12 main baseline:** `ff4eb1e96a92610bc3ddd2c21c6e32840a016452` (accepted 0.2.11 runtime `ba5a16783763f8032e3341b2e08eae8566457fbd` plus final 0.2.11 documentation sync)  
**Latest verified 0.2.11 runtime main CI:** run `35448557400` / #1040 = completed / success on `ba5a16783763f8032e3341b2e08eae8566457fbd`  
**0.2.12 source branch:** `release/0.2.12-broad-discovery`, created directly from clean main `ff4eb1e96a92610bc3ddd2c21c6e32840a016452`; final branch history was collapsed to one clean commit `4226218763c100ec71652f8dffafd4d17a4fa97c` before PR  
**0.2.12 scope:** Discovery only — cached broad operating-equity Stage 0, rotating cheap Stage 1, bounded canonical-valuation/filed-data Stage 2; no Research/Tape/Portfolio/Auth/Deploy/Settings/Financial-Flows redesign; only the Discovery-specific landscape export is adapted to the new Discovery payload  
**Verified 0.2.12 branch CI:** push run `35451749159` / #1076 = completed / success on clean head `4226218763c100ec71652f8dffafd4d17a4fa97c`; release suite, syntax/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed  
**Verified 0.2.12 PR CI:** pull-request run `35451809157` / #1077 = completed / success on `4226218763c100ec71652f8dffafd4d17a4fa97c`  
**Pull request:** #40 `0.2.12: Broad Universe Discovery` = merged by squash  
**0.2.12 merge commit:** `0d6debb6461a6dd7872c55dd5655cf6e921af1c1`  
**Verified post-merge main CI:** run `35451861271` / #1078 = completed / success on `0d6debb6461a6dd7872c55dd5655cf6e921af1c1`; release suite, production-minimal startup/rich-report smoke and reporting-vendor smoke all passed  
**0.2.11 source branch:** `release/0.2.11-position-action`, created directly from clean main `30d2e5cd2da07772235fef8c20e31e952e9fe68a`; no old branch is its base  
**0.2.11 scope:** Portfolio-owned deterministic Position Action downstream from the canonical Research Conclusion; no Research/Tape/Discovery/Valuation/deploy redesign  
**Verified 0.2.11 branch CI:** push run `35444349076` / #1035 = completed / success on head `e6d9c8ac7b489bcfc3d670d57262e2b974a0c4d8`; the earlier branch push run `35444308195` / #1034 also completed / success during the same focused implementation  
**Verified 0.2.11 PR CI:** pull-request run `35444408562` / #1036 = completed / success on `e6d9c8ac7b489bcfc3d670d57262e2b974a0c4d8`; release suite, syntax/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed  
**Pull request:** #38 `0.2.11: Portfolio Position Action` = merged by squash  
**0.2.11 merge commit:** `b74057261b53986462da37e5b62164733032a38a`  
**Verified post-merge main CI:** run `35444994019` / #1037 = completed / success on `b74057261b53986462da37e5b62164733032a38a`; release suite, production-minimal smoke and reporting-vendor smoke all passed  
**0.2.11 Portfolio command closure branch:** `fix/0.2.11-portfolio-command`, created directly from accepted main `ca02f06b4d1a179632778d19371c7d51c87d85b2`; VERSION remains `0.2.11`  
**Verified Portfolio command branch CI:** push run `35448487351` / #1038 = completed / success on head `ecb30d0037bd848a39ba96c2d9a7e7a39986772c`; full release suite and both production smoke gates passed  
**Verified Portfolio command PR CI:** pull-request run `35448502577` / #1039 = completed / success on `ecb30d0037bd848a39ba96c2d9a7e7a39986772c`  
**Portfolio command pull request:** #39 `0.2.11: close Portfolio command layer` = merged by squash  
**Accepted 0.2.11 runtime merge commit:** `ba5a16783763f8032e3341b2e08eae8566457fbd`  
**Verified final post-merge main CI:** run `35448557400` / #1040 = completed / success on `ba5a16783763f8032e3341b2e08eae8566457fbd`; release suite, production-minimal startup/rich-report smoke and reporting-vendor smoke all passed  
**0.2.12 hardening branch:** `release/0.2.12-discovery-hardening`, created directly from current main `25ba14fb5f55060677cd6db77e5c721a1503649a`; history collapsed before PR to one clean commit `405e5510eefd6c0da6c792c7bf5d4c7bbfc5501f`; VERSION remained `0.2.12` and scope remained Discovery-only.  
**Verified 0.2.12 hardening branch CI:** push run `35458992140` / #1093 = completed / success on clean head `405e5510eefd6c0da6c792c7bf5d4c7bbfc5501f`; release suite, syntax/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed.  
**0.2.12 hardening pull request:** #41 `0.2.12: Discovery observability and guardrails` = merged by squash.  
**Verified 0.2.12 hardening PR CI:** pull-request run `35459077261` / #1094 = completed / success on `405e5510eefd6c0da6c792c7bf5d4c7bbfc5501f`; full release suite and both smoke gates passed.  
**Accepted 0.2.12 runtime merge:** `1e309fa77b864ed5ccd9831478a54fcdefe05419`.  
**Verified final post-merge main CI:** run `35459124021` / #1095 = completed / success on `1e309fa77b864ed5ccd9831478a54fcdefe05419`; release suite, production-minimal startup/rich-report smoke and reporting-vendor smoke all passed.  
**Main baseline for 0.2.13:** VERSION `0.2.12` at `4f4d7466433eed69d5db7785d6a15b1d4a77522e`; accepted Discovery opportunity-funnel runtime `adfd99593f6db378cf8cf99a41976a2e85c6c49d`; latest release CI #1117 is green on that runtime, and deploy #52 independently re-ran release tests plus production smoke from the later docs-sync main SHA.  
**0.2.13 source branch:** `release/0.2.13-ui-ux-codebase-consolidation`, created from clean main `4f4d7466433eed69d5db7785d6a15b1d4a77522e`; final branch head `302260dac9003320001286481fb6c893813dd0bb`.  
**Verified 0.2.13 branch CI:** run `35472086178` / #1166 = completed / success on final branch head; release suite, Python/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed.  
**0.2.13 pull request:** #44 `0.2.13: UI/UX & Codebase Consolidation` = merged by squash.  
**Verified 0.2.13 PR CI:** run `35472218120` / #1167 = completed / success on `302260dac9003320001286481fb6c893813dd0bb`; full configured release suite and both production/reporting smoke gates passed.  
**0.2.13 merge commit:** `8b84873294cee3a4962fd4dd3eace91ef21d6e9b`.  
**Verified 0.2.13 post-merge main CI:** run `35472279193` / #1168 = completed / success on `8b84873294cee3a4962fd4dd3eace91ef21d6e9b`; release suite, syntax/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed.  
**0.2.13 mobile visibility hardening:** PR #45 merged to `main` as `39f1631929014007eea741a2c4496706383cb9b5`; PR CI #1182 / run `35473211754` and post-merge main CI #1183 / run `35473268751` both completed successfully, including the release suite and both production/reporting smoke gates. VERSION remains `0.2.13`. Scope is responsive/UI closure only: company current price/provider/freshness remains visible on phone widths; wide diagnostics are contained rather than widening the viewport; narrow Discovery, Recent Jobs, compact KPI and shared list/header layouts stack without discarding information; mobile tools/navigation can scroll safely; preview banners reserve page space; stale Trace/number-format responsive CSS is removed. No valuation, decision, Discovery, Tape, Portfolio calculation, database/schema, security/publication or deploy behavior changes.  

**0.2.12 production render-fix branch:** `fix/0.2.12-discovery-rejection-render`, created directly from main `6656a7941c1e2d40a87f5318303b28527de859f8`; consolidated head `c85157460f7cbc4258760816f68ac3ddfe456f0e`; same VERSION, Discovery-only rendering/normalization fix plus regression test.  
**0.2.12 render-fix PR:** #42 `0.2.12: Fix sparse Discovery rejection rendering` = merged by squash.  
**Verified render-fix branch CI:** run `35462668640` / #1100 = completed / success on `c85157460f7cbc4258760816f68ac3ddfe456f0e`.  
**Verified render-fix PR CI:** run `35462722236` / #1101 = completed / success on `c85157460f7cbc4258760816f68ac3ddfe456f0e`.  
**Accepted render-fix merge:** `28f262da51f768c801d540d4b4d772964cfd7630`.  
**Verified post-merge render-fix main CI:** run `35462772554` / #1102 = completed / success; release suite, production-minimal startup/rich-report smoke and self-contained reporting-vendor smoke all passed.  
**0.2.12 opportunity-funnel branch:** `fix/0.2.12-discovery-opportunity-funnel`, created directly from current main `e5732676ae70937a622a3a73acb95ed3cc53b9a1`; history collapsed before PR to clean head `55f59f3fdc9099491110bfd7c72a16ade52eb281`; VERSION remained `0.2.12`; scope remained Discovery-only.  
**0.2.12 opportunity-funnel scope:** separate Discovery from final Validation: P1 strong opportunities, P2 valuation opportunities, WATCH emerging/verification-needed leads; Stage-1 liquidity rebalanced to 200k shares / $15M completed-day dollar volume; broad rotation 360 names/run; Stage-2 cap 10; no Research/Valuation/Portfolio/Tape/Auth/Settings/deploy redesign.  
**Verified opportunity-funnel branch CI:** run `35469101160` / #1115 = completed / success on clean head `55f59f3fdc9099491110bfd7c72a16ade52eb281`; release suite, syntax/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed.  
**0.2.12 opportunity-funnel pull request:** #43 `0.2.12: Rebalance Discovery opportunity funnel` = merged by squash.  
**Verified opportunity-funnel PR CI:** run `35469153142` / #1116 = completed / success on `55f59f3fdc9099491110bfd7c72a16ade52eb281`.  
**Accepted opportunity-funnel merge:** `adfd99593f6db378cf8cf99a41976a2e85c6c49d`.  
**Verified opportunity-funnel post-merge main CI:** run `35469221771` / #1117 = completed / success; release suite, production-minimal startup/rich-report smoke and self-contained reporting-vendor smoke all passed.  
**Release phase:** 0.3.0 Economic Reality + Company Quality is merged to `main`. Namecheap production remains a separately verified state until explicit manual deploy; the top-level Production line is canonical for what is actually live.  
**0.3.0 spacing-rhythm hardening:** PR #51 `0.3.0: spacing rhythm hardening` merged by squash to `main` as `f73d5fa69b7563ce766fecc04811856a47715c70`; final push CI #1433 / run `35627078900`, PR CI #1434 / run `35627115828`, and post-merge main CI #1435 / run `35627265883` completed successfully, including release tests, Python/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke. Scope is presentation-only: missing 18 px section gap between Tape change cards and Tape support KPIs restored; previously unstylized `form-stack` now has a 14 px field/action gap across login/invite/2FA/verify; legacy auth/control buttons, invite-copy, error actions and old micro/panel-label/text-link/secret-code presentation are normalized to the canonical UI. Full template/CSS spacing audit found the existing panel/table/KPI/action-bar rhythm otherwise intact. No analytical engine, database, provider, auth flow, Portfolio, publication or report behavior changes. VERSION remains 0.3.0.  
**0.3.0 UI readability hardening:** PR #49 `0.3.0: UI readability hardening` merged by squash to `main` as `8f28cf98139a74de4f43b181eb9019af38a836b8`; post-merge CI run `35614543166` / #1400 completed successfully, including release tests, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke. Scope is presentation-only: Discovery candidate/radar typography is restored to the 13–15 px readability contract; Valuation Bear/Base/Bull KPI top borders use the exact chart scenario colors; the Quality → Valuation KPI grid is one desktop row with responsive fallbacks. No valuation, Discovery, Research, database, provider, auth, Portfolio or reporting engine behavior changes.  
**0.3.0 global UI coherence hardening:** PR #50 `0.3.0: global UI coherence hardening` merged by squash to `main` as `5d0c4a31922f821cabc4044c2f5cfed231f5d343`; final branch push CI #1418 / run `35616576314`, PR CI #1419 / run `35616584581`, and post-merge main CI #1420 / run `35616771607` all completed successfully, including release tests, Python/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke. Scope is presentation-only: site-wide canonical UI/chart text floor is 13 px; semantic states are positive green, negative red, watch/review amber, neutral gray and informational blue in both light/dark mode; Tape context cards use readable semantic hierarchy; compact ticker + latest-price identity appears in the top bar after the company header scrolls away. All application templates were audited for inline visual overrides and purple usage; none were found. No analytical engine, database, provider, auth, Portfolio, publication or report logic changes.  
**0.3.0 pull request:** #48 `0.3.0: Economic Reality, Company Quality & Valuation Integrity` = merged by squash.  
**0.3.0 verified source candidate:** branch head `a927a99e2f7cc8429ec5fdb2cd16e455eb77dea3`; final branch push CI #1387 / run `35609399031` and PR CI #1388 / run `35609405574` completed successfully, including release tests, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke.  
**0.3.0 merge commit:** `400cfab8c2a286208a1a77194d97e09a2a432900`.  
**Verified 0.3.0 post-merge main CI:** run `35610326560` / #1389 = completed / success on `400cfab8c2a286208a1a77194d97e09a2a432900`; release suite, Python/YAML/JavaScript checks, production-minimal startup + rich-report smoke and self-contained reporting-vendor smoke all passed.  

Production and main are separately verified states. A merge to main does not imply a Namecheap deploy.

---

## 0.3.0 release scope — Economic Reality / Accounting Distortion Engine

0.3.0 starts from clean 0.2.14 main and changes the analytical interpretation layer without resetting MariaDB, users, credentials, 2FA, Portfolio, publications, audit history or server configuration.

Core contract:
- reported SEC accounting remains canonical and unchanged;
- normalized rows now persist an auditable Economic Reality snapshot inside existing JSON quality metadata, so no destructive schema migration is required;
- financial debt, finance leases, operating leases, supplier finance, pensions/postretirement obligations, preferred/minority enterprise claims, contingent consideration, restricted cash, liquid investments, deferred/contract revenue, deferred tax, ARO/provisions, goodwill/intangibles and treasury-stock distortion are separated rather than collapsed into one liability/debt concept;
- operating leases remain real obligations but are kept separate from financial net debt by default; lease share of liabilities, revenue/lease-liability productivity and lease-adjusted ROIC are exposed as context;
- classified economic net debt replaces raw debt-minus-cash in the canonical EV/Sales bridge when classification is usable; unresolved material debt classification disables the EV/Sales bridge instead of guessing;
- cash offsets are conservative: known restricted cash does not reduce economic debt;
- FCF diagnostics now distinguish reported FCF from a D&A-based maintenance/growth-capex proxy and owner-cash proxy; the proxy is explicitly non-GAAP and cannot replace reported FCF;
- material SBC exposes FCF-after-SBC and prevents unadjusted cash conversion from receiving an automatic positive score;
- explicit restructuring, impairment and acquisition charges can suppress automatic reported-margin deterioration scoring while remaining visible;
- high-R&D businesses are flagged because GAAP expenses internally created intangible investment and therefore can distort margins/book-capital ROIC;
- generic industrial leverage, working-capital and FCF rules are disabled for financial/REIT-like balance sheets;
- Research Fundamentals now shows a Reported Accounting → Economic Reality panel, quality, basis, flags and unresolved classifications;
- Decision Evidence uses economic leverage and accounting-aware cash/margin rules; material unresolved classification blocks BUY/SELL;
- Discovery contract is BROAD_FORENSIC_DISCOVERY_V3; Stage 2 reuses the same Economic Reality engine, suppresses identified false short signals and caps material accounting uncertainty at WATCH instead of P1/P2;
- PDF/Word Research reports carry the same economic basis and flags;
- Company Quality adds a deterministic, non-scorecard read across operating durability, returns, cash quality, balance-sheet resilience, reinvestment efficiency, capital allocation and accounting quality; outputs are STRONG / SOUND / MIXED / FRAGILE / UNRESOLVED / INSUFFICIENT EVIDENCE with explicit dimension states and alarm bells;
- Company Quality remains separate from valuation attractiveness: a strong company receives no hidden premium, while evidenced weaknesses can only apply bounded downside adjustments;
- the canonical Quality → Valuation policy can raise auto-case discount rates, haircut forward/terminal growth, shift probability toward Bear and exclude an accounting-invalid valuation method; every effect is exposed in a Valuation Impact Ledger;
- accounting/economic effects that directly change value are distinguished from diagnostics that remain context only; operating leases change classification/fixed-charge risk without being silently double-counted as borrowing, growth-capex remains a proxy rather than an invented FCF restatement, and high R&D remains context unless a defensible capitalization model exists;
- Management execution, Research Evidence, Discovery and Valuation now consume the same Economic Reality definitions rather than maintaining parallel debt/FCF/working-capital interpretations;
- pre-0.3 valuation/research caches are automatically marked stale by engine-version mismatch so existing Coverage is recalculated through the normal background path after upgrade;
- Overview exposes Company Quality / alarm bells / strengths; Valuation exposes the Quality → Valuation bridge; PDF/Word carry both;
- Fundamentals is rebuilt as the accounting evidence room: complete normalized current anatomy, complete annual income/cash-flow and balance-sheet history, derived operating-metric history, quarterly/TTM evidence, working-capital forensics and the full Economic Reality ledger;
- Fundamentals Forensics is materialized in the Research cache and classifies evidence into strengths, WATCH, RED FLAG, deterministic reconciliation inconsistencies and data/classification gaps; normal GET navigation remains provider-free/heavy-engine-free;
- deterministic accounting reconciliations (gross-profit bridge, FCF bridge, balance-sheet identity and pretax-to-net-income bridge) are surfaced as REVIEW evidence, never as an allegation of misconduct;
- current filing and Economic Reality provenance is inspectable from Fundamentals; full visible gaps are separated from decision-critical gaps so optional missing facts are not silently hidden or allowed to block the whole process incorrectly;
- pre-0.3 Coverage without an Economic Reality snapshot queues a deduplicated SEC re-ingest automatically; reported debt-minus-cash remains visible as reported context but cannot become canonical leverage/EV evidence until classification is materialized;
- Discovery Stage 2 now follows the same fail-closed rule and no longer restores a raw debt-minus-cash fallback when Economic Reality is absent;
- Full PDF/Word Research reports carry the materialized Fundamentals forensic strengths, risks, inconsistencies and unresolved gaps;
- 0.3.0 regressions include a CMG-scale lease-heavy case plus finance leases/supplier finance/enterprise claims, restricted cash, deferred revenue, growth capex, SBC, sector-policy suppression and Discovery accounting-review gating.

No accounting reinterpretation is allowed to erase contractual obligations. Economic Reality exists to prevent category errors in scoring and valuation, not to make liabilities disappear.

Production remains independently verified at 0.2.13 until an explicit later deploy. 0.2.14 was not deployed before this release branch; main and production remain separate states.

Deployment integrity in 0.3.0:
- CI and deploy both fail if VERSION, CURRENT_STATE State-Version and HOW_MARKET_FORENSICS_WORKS Current product line disagree;
- a successful deploy records the actual deployed source SHA before upload;
- only after final production health succeeds, the workflow updates the single top-level Production line in CURRENT_STATE on main with deployed VERSION, source SHA and workflow run;
- historical release notes are never rewritten by deployment automation;
- the sync is retried if main moves concurrently and a failed docs sync is surfaced instead of leaving a silently stale source of truth.

## 0.2.14 release scope — Research Reports V2

The Local V3.1.12 exporter is the minimum benchmark, not the target architecture. The concrete capability audit lives in `docs/REPORT_PARITY_0_2_14.md`.

Release contract:
- RESEARCH CONCLUSION is the first decision information in PDF and Word;
- Base target and Bear/Base/Bull sit before valuation-detail tables;
- the Executive export remains compact (one page when it fits; never more than two in the canonical fixture);
- Executive decision intelligence includes Current Price, Bear/Base/Bull, Base Gap, Valuation Quality, Value, Expectations, Variant, Path, Model Confidence and Thesis Control;
- the Full report follows the investment process rather than appending a table dump;
- valuation exposes scenario probability, target, quality, method values, effective method weights, DCF cross-check, share denominator/source and provisional warnings;
- native report charts cover valuation map, revenue/profitability, cash conversion, working-capital forensics, price context, selected Tape/positioning and historical Validate samples when stored data exists;
- Financial Flows reads the canonical stored `edges` / `signed_exceptions` ledger and preserves signed negatives rather than inventing balancing values;
- Management promises, Tape, Monitoring/locked invalidation, Validate and Sources/Audit are first-class Full-report sections;
- Word and PDF share one canonical report data model; presentation is renderer-specific;
- report generation disables stale-cache recalculation enqueueing and starts no SEC/Alpaca/FINRA/FRED/Discovery refresh or heavy analytical job;
- publication/member privacy boundaries remain unchanged; Portfolio holdings, sizing, cost basis, P/L, private Portfolio notes and private Decision Journal data do not enter member/public research artifacts;
- rich `python-docx` + ReportLab rendering remains a production requirement, with valid fallback artifacts and audit-write isolation.

Implementation files are deliberately report-scoped: `mfapp/report_contract.py`, `mfapp/report_charts.py`, `mfapp/report_render_v2.py`, reporting orchestration, report route queue control, report tests and documentation. No schema migration or reporting dependency change is required.

## 0.2.13 release scope — UI/UX & Codebase Consolidation

Scope is consolidation only. No investment feature or financial-engine behavior may change.

Completed consolidation:
- one mobile navigation path; the obsolete bottom navigation has been removed;
- the phone/tablet portrait drawer breakpoint is canonicalized so ~768 px no longer loses primary navigation;
- Research-step toggle markup is server-rendered instead of created by a DOM rewrite;
- theme behavior is consolidated into the canonical application controller; the standalone one-line theme wrapper is removed;
- inline Valuation, Portfolio, Financial Flows and Trace presentation/behavior is moved into shared JavaScript/CSS contracts;
- wide tables preserve information with controlled horizontal overflow instead of clipping;
- touch/focus/readability rules are normalized without changing data or calculations;
- the previously hidden duplicate Decision Brief markup/CSS is removed rather than preserved as dead UI;
- CI collection now explicitly includes every still-live regression contract through 0.2.13 (0.2.0 baseline, 0.2.10 correctness, 0.2.11 Portfolio/Position Action, 0.2.12 Discovery, 0.2.5 UI, 0.2.9 Tape V2 and the 0.2.13 consolidation contract); superseded release-snapshot tests remain in the repository for forensic history but are not treated as current product contracts.

Real bugs found by the cleanup:
1. the 761–820 px range could hide the desktop sidebar before the mobile drawer controls became available;
2. `pytest.ini` excluded still-live regression suites added after the older collection pattern; the active correctness suites are now collected explicitly without reviving superseded UI/release snapshots;
3. `CURRENT_STATE.md` was stale about production: deploy #52 had already placed current 0.2.12 main on Namecheap successfully.

Hard boundaries:
- valuation, Bear/Base/Bull, Research Conclusion, Decision Lenses, Validate, Discovery ranking, Tape calculations, Portfolio sizing/action, Monitoring, Management scoring, SEC normalization and Financial Flows accounting are unchanged;
- authentication, authorization, FRIEND/INSIDER/CONTROL, publication/privacy and database data are unchanged;
- `.github/workflows/deploy-namecheap.yml`, Namecheap deployment logic, backup logic and reporting-vendor behavior are unchanged;
- production remains 0.2.12 until a later explicit manual deploy.

## 0.2.12 release scope — Broad Universe Discovery

- Discovery now follows Stage 0 → Stage 1 → Stage 2 rather than treating Most Active / Movers as the effective market universe.
- Stage 0 caches the Alpaca active US-equity catalog and fail-closes on active/tradable status, major US exchanges, ticker syntax and non-operating security patterns.
- Stage 0 is CONTROL-private and does not create Coverage, Research or Portfolio records.
- Stage 1 advances through a bounded 360-name rotating universe slice, caps the secondary Most Active / Movers lane at 160 and stored Coverage context at 80, chunks snapshot work in groups of 60 and checkpoints the next cursor only after snapshot work succeeds.
- Stage 1 performs no SEC Companyfacts work.
- Stage 2 has a hard 10-finalist deep-enrichment cap. It gives most budget to quiet broad-rotation names while preserving near-edge stored names and a smaller activity lane, then performs sequential SEC/filed enrichment only on those finalists.
- Stage 2 reuses the canonical valuation engine with reference-price fallback disabled; Discovery has no duplicate valuation model.
- Discovery no longer requires final-validation-level confirmation merely to preserve a research lead. P1 requires an INTRINSIC Base, at least 2 methods, absolute gap >=25% and aligned operating confirmation; P2 requires the same decision-grade Base with absolute gap >=20% and no material operating contradiction.
- WATCH preserves a 12–20% gap with aligned operating confirmation, or an absolute gap >=20% where valuation quality/method count, operating alignment or Short actionability still needs verification. P1/P2 Shorts remain actionability-gated.
- Zero leads remains a valid successful result; thresholds are not relaxed to fill the screen.
- Ranking is auditable/lexicographic rather than a hidden composite score: P1 / P2 / WATCH, absolute Base gap, valuation quality/method count, operating state, ticker.
- Candidate UI shows P1/P2 Long/Short plus a dedicated WATCH section with Price, Bear/Base/Bull where available, Base gap, quality, method count, operating state, direction, why found, invalidation, filed/market freshness, warning and explicit Promote; the Discovery-only landscape PDF carries the same tier without reintroducing scan_score.
- Normal Discovery GET remains provider-free. Job status, Stage-0/1/2 counts, exclusions and provider-call counts are stored/displayed.
- Promotion remains explicit and ticker validation runs again before persistence.
- Discovery promotion now writes the originating scan/candidate evidence into the permanent audit meta: scan job, direction/family, price, Bear/Base/Bull, Base gap, valuation quality/methods, operating confirmation, why found, invalidation and freshness.
- Stage 1 now persists broad-universe touch history and reports 7-day / 30-day breadth plus estimated runs for one full rotation.
- Completed scans expose universe/provider health: Stage-0 collapse vs the prior catalog, stale universe cache, snapshot return rate and unusually concentrated Stage-1 exclusions.
- Stage-2 rejection diagnostics are bounded and ticker-specific, so investigated-but-rejected names show the actual failed gate instead of disappearing into aggregate counts.
- Candidate freshness is split into market, filed fundamentals, valuation materialization and universe eligibility timestamps.
- Share-count discontinuities, short filing histories and recent S-1/F-1/10-12 registration patterns are conservative pre-candidate basis-review guards; flagged names cannot become final Discovery candidates until the basis is reviewed.
- Stage-1 investability floors are $5 price, 200k completed-day shares and $15M completed-day dollar volume for external names; this intentionally admits more investable mid-caps than the prior $50M/day gate.
- Scan cadence is explicit: retry in ~1 day after unhealthy/partial runs, ~3 days while recent broad-universe coverage is still thin, then weekly once breadth is established.
- No dependency or deploy-workflow change is required.
- Dedicated regressions live in `tests/test_0212_discovery.py`; `docs/LOCAL_WEB_PARITY_0_2_12.md` records Local/Web parity and the remaining incremental-breadth limitation.
- Production was advanced to 0.2.12 by manual deploy #50. The deploy itself passed health checks. A first real Discovery scan then exposed a sparse rejection-row rendering defect: Stage-2 enrichment rejections may legitimately omit Base-gap fields, while the template attempted numeric formatting of an undefined value. The same-version fix normalizes optional rejection fields and renders the gap only when a numeric value is present.

---

## 0.2.11 release scope — Portfolio Position Action

- Adds a deterministic, auditable Position Action policy owned by Portfolio.
- Research remains position-agnostic and continues to emit only its canonical Research Conclusion.
- Precedence is fail-closed: locked thesis invalidation → Portfolio risk breach → Research/data gates → directional compatibility → conditional candidate/add/hold action.
- A triggered locked pre-investment invalidation forces EXIT / SELL for a Long or COVER for a Short and cannot be overridden by valuation.
- A max-position/downside-sizing breach forces REDUCE / REDUCE SHORT even when Research remains directionally READY.
- Portfolio-only securities continue to work but cannot receive BUY/ADD/new-SHORT actions until Research exists.
- No-position LONG READY / SHORT READY produces BUY CANDIDATE / SHORT CANDIDATE, never an order.
- Existing same-direction READY can produce ADD ON EVIDENCE / ADD SHORT ON EVIDENCE only with VALIDATED research, CONTROLLED thesis, coherent Path, risk headroom and an explicit evidence-to-add condition.
- Market price, average cost and P/L are not direct Position Action inputs. Price movement never satisfies the evidence-to-add condition and never independently creates ADD or SELL.
- The Portfolio security detail gains one compact primary Position Action conclusion with Why now, blocker, next confirmation and risk/invalidation state.
- The old manual Portfolio action input is removed from the primary Portfolio UI to avoid two competing action truths; legacy storage is retained for compatibility/history.
- Position Action is CONTROL-private and never crosses publication/member payload boundaries.
- Normal GET reads only stored/materialized data and performs no provider call.
- Dedicated regression coverage lives in `tests/test_0211_position_action.py`.
- Same-release Portfolio command closure adds descriptive current→suggested sizing/headroom, optional ADD/TRIM/EXIT Monitoring confirmation links, background-materialized Needs Attention, deterministic Action transition history and aggregate descriptive downside-budget usage.
- Research edits/Monitoring changes queue the existing deduplicated `PORTFOLIO_RECALCULATE` job after Research recalculation so Portfolio command state is materialized from the latest stored Research cache.
- The extension adds no provider work to normal GET, no new public/member payload fields and no deploy-workflow change.
- Additional regression coverage lives in `tests/test_0211_portfolio_command.py`.
- `docs/LOCAL_WEB_PARITY_0_2_11.md` records the Local → Web recovery without re-coupling Research and Portfolio.
- VERSION remains 0.2.11. Production remains 0.2.10 until a later explicit deploy.

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
- Discovery external names continue to require INTRINSIC Base with at least two usable valuation methods; covered names also require INTRINSIC stored Base quality before local forensic gap logic can qualify them. MANUAL_OVERRIDE may be decision-grade for Research, but it does not satisfy Discovery's stricter intrinsic-only rule.

### Management promises
- Automatic guidance extraction is comparability-first.
- Promise provenance preserves source/provider, source date, accession/form when available, metric, target period, basis/definition, comparability reason and status.
- Interim/quarterly guidance, incompatible periods/units/ranges, adjusted/non-GAAP definitions, management-defined FCF, retrospective guidance, later restatements and unresolved target-year/comparator ambiguity are not forced into MET/MISS.
- Non-comparable evidence remains EVIDENCE_ONLY; comparable unresolved promises remain PENDING; only economically comparable actuals may produce MET or MISS.
- Management remains execution/accountability evidence, not personality or integrity scoring.

### Management autofill repair — same 0.2.10 release identity
- Corrective branch: `fix/0.2.10-management-autofill`, created directly from the accepted 0.2.10 main baseline; VERSION remains `0.2.10`.
- Root cause addressed: a successful old `MANAGEMENT_GUIDANCE_SCAN` marker could permanently skip the same filing even when the old parser stored zero promises, and the scanner read only the filing primary document.
- Management scans are now versioned. Old-version completion markers are eligible for re-read; an explicit CONTROL scan is forceful even when a queued unattended scan is reused.
- 8-K scans inspect a bounded set of relevant HTML exhibits (including EX-99.1-style earnings releases) in addition to the primary document.
- SEC ingest automatically queues a deduplicated Management scan; the scan still remains background-only and normal GET navigation remains provider-free.
- Parser coverage now includes diluted EPS, sign-correct revenue declines, common money-range wording and qualitative guidance. Qualitative/ambiguous evidence is displayed but remains EVIDENCE_ONLY.
- Management actuals are reconstructed from earliest-public SEC Companyfacts and stored as point-in-time original actuals. Current/later comparative values cannot independently create MET/MISS.
- Parser upgrades reconcile matching automatic promises in place so corrected extraction does not leave contradictory duplicate rows.
- Research cache, Management UI and PDF/DOCX/text exports preserve qualitative target text, source exhibit/document provenance and original-actual provenance.
- Automatic evidence scope remains SEC-filed/furnished material; non-SEC conference-call statements are not silently scraped.
- PR #37 is merged by squash at `250b8092d66495533d3406f8bd711725c0ffdd8d`; main CI #1033 is green and deploy #47 attempt 2 is successful. The Management autofill repair is LIVE on Namecheap while VERSION remains `0.2.10`.

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

## 6. Discovery — broad-universe forensic contract

Discovery result contract is **BROAD_FORENSIC_DISCOVERY_V2**. Older stored Discovery payloads are rejected on read so a legacy mover/activity result cannot silently masquerade as a 0.2.12 result.

### Stage 0 — cached universe

The source universe is the cached Alpaca active US-equity asset catalog. Qualification is fail-closed:
- active;
- tradable;
- valid ticker syntax;
- major US exchange only: NASDAQ, NYSE, AMEX, ARCA;
- exclude warrants, rights, units, ETFs, ETNs, funds, preferreds, note-like securities and blank-check/SPAC-shell name or symbol patterns.

The Stage-0 universe is private Discovery material. It does not create Coverage, Research or Portfolio records.

### Stage 1 — cheap rotating screen

Stage 1 is background-only and incremental:
- broad-universe rotating slice: at most 240 names per run;
- Most Active / Movers: secondary lane only, capped at 160 names;
- already-materialized Coverage context: capped at 80 names;
- total snapshot set is hard bounded and fetched sequentially in chunks of 60;
- previous completed daily-bar volume/liquidity is preferred when available so an early-session run does not become activity-biased;
- new external names still obey price/volume/dollar-liquidity floors;
- Stage 1 performs no SEC Companyfacts deep enrichment;
- the Stage-1 cursor advances only after usable snapshot work succeeds, so failed runs do not silently skip universe slices.

### Stage 2 — bounded forensic enrichment

Stage 2 deeply enriches at most 8 finalists per run. Selection deliberately reserves capacity for quiet liquid broad-universe names so current Most Active/Movers cannot monopolize the SEC budget.

For external finalists:
- SEC ticker map is fetched at most once per run;
- submissions and Companyfacts are called sequentially only for the bounded finalists;
- actual fiscal-year-end and filed-period integrity are respected;
- current TTM and comparable prior TTM require four coherent fiscal quarters;
- the canonical valuation_engine is reused;
- allow_reference_fallback=False;
- Base quality must be INTRINSIC;
- at least two canonical valuation methods must be valid.

Final Long:
- Base gap >= +20%;
- confirming filed operating evidence.

Final Short:
- Base gap <= -20%;
- confirming deterioration;
- current shortability/actionability.

No raw price move, activity rank or hidden composite score can create a final candidate. Zero candidates is a valid successful run.

Final ranking is auditable and lexicographic:
1. priority tier;
2. absolute intrinsic Base gap;
3. valuation-method count;
4. operating-confirmation strength;
5. ticker.

Candidate output exposes Price, Bear/Base/Bull when available, Base gap, valuation quality, method count, operating confirmation, LONG/SHORT direction, why selected, invalidation, freshness and warnings.

Normal GET /discovery is provider-free and reads stored job/cache state. Stage counts, exclusions, provider-call counts and job status are materialized in the completed result. Promote remains explicit; ticker validation runs again before persistence.

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
11. The active release VERSION and top-level State-Version must match; historical gate text must not hardcode the current release identity.
12. Production is not changed by PR or main merge; Namecheap deploy remains an explicit later action.


The release is blocked by a broken capability even if its page returns HTTP 200.
---

## 11. Merge / deploy state

**Current phase:** 0.2.13 is the active source release on its dedicated branch; production is independently verified at 0.2.12 deploy #52 and has not been advanced to 0.2.13.

Accepted 0.2.12 release sequence:
- original broad-universe source baseline: main ff4eb1e96a92610bc3ddd2c21c6e32840a016452;
- original clean branch: release/0.2.12-broad-discovery;
- original clean branch head: 4226218763c100ec71652f8dffafd4d17a4fa97c;
- original branch CI: run 35451749159 / #1076 = success;
- PR #40 0.2.12: Broad Universe Discovery;
- original PR CI: run 35451809157 / #1077 = success;
- original squash merge: 0d6debb6461a6dd7872c55dd5655cf6e921af1c1;
- original post-merge main CI: run 35451861271 / #1078 = success;
- hardening source baseline: main 25ba14fb5f55060677cd6db77e5c721a1503649a;
- hardening clean branch: release/0.2.12-discovery-hardening;
- hardening clean branch head: 405e5510eefd6c0da6c792c7bf5d4c7bbfc5501f;
- hardening branch CI: run 35458992140 / #1093 = success;
- PR #41 0.2.12: Discovery observability and guardrails;
- hardening PR CI: run 35459077261 / #1094 = success;
- final same-version squash merge: 1e309fa77b864ed5ccd9831478a54fcdefe05419;
- final post-merge main CI: run 35459124021 / #1095 = success;
- final documentation sync follows on main with [skip ci] because it changes documentation only.

Production remains separate:
- currently deployed version: 0.2.11;
- last verified deploy: run 35448890411 / deploy #49;
- verified production health: HTTP 200, status=ok, version=0.2.11, architecture=web-native, database=primary, reports=rich;
- no 0.2.12 production deploy was triggered by branch, PR, merge or documentation sync.

For any later explicit 0.2.12 deploy, production health remains mandatory:
- HTTP 200;
- status = ok;
- version = 0.2.12;
- architecture = web-native;
- database = primary;
- reports = rich.

main != production remains a permanent rule.

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
