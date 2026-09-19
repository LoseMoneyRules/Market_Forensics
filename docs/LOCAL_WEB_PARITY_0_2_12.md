# Market Forensics 0.2.12 — Local / Web Discovery parity audit

## Scope

0.2.12 is intentionally limited to Discovery. It does not redesign Research, Valuation, Tape, Portfolio, Reports, Authentication, deployment, Settings or Financial Flows.

The objective is to recover the **broad liquid-universe / dislocation-search concept** from Local without reintroducing a generic screener or duplicating valuation logic.

## Parity result

| Local / accepted capability | Web 0.2.12 status | Web-native implementation |
| --- | --- | --- |
| Search beyond current movers | RECOVERED / IMPROVED | Stage 0 caches the active/tradable major-US-exchange operating-equity catalog. Stage 1 rotates through that broad universe incrementally. |
| Most Active / Movers | PRESERVED AS SECONDARY | Activity feeds remain one investigation lane but no longer define the universe or Stage-2 shortlist. |
| Broad liquid-universe concept | RECOVERED | Stage 1 applies validity, price, volume and dollar-liquidity filters to a bounded rotating slice and checkpoints the next cursor. |
| Quiet liquid company can be investigated | RECOVERED | Stage-2 budget explicitly includes quiet broad-rotation names that are absent from Most Active / Movers. |
| Generic screener score | NOT COPIED | Discovery uses explicit P1 / P2 / WATCH evidence tiers; no generic composite score can create a research lead. |
| Fair value central to Discovery | PRESERVED / IMPROVED | Canonical valuation engine is reused with reference-price fallback disabled. INTRINSIC + 2 methods is required for P1/P2; a mathematically usable but not yet decision-grade Base may remain visible only as WATCH with an explicit verification warning. |
| Bear / Base / Bull | PRESERVED | Stage 2 carries canonical scenario fair values into the Discovery candidate when mathematically available. |
| Operating confirmation | HARDENED / REBALANCED | Four coherent filed quarters and comparable prior TTM remain required. P1 requires aligned operating confirmation; P2 accepts stable/not-materially-contradicting operations; WATCH preserves emerging or conflicting setups for Research. |
| Long dislocation | REBALANCED | P1: >= +25% + aligned operations. P2: >= +20% decision-grade Base without material operating contradiction. WATCH: +12–20% with confirmation, or >= +20% with verification still needed. |
| Short dislocation | REBALANCED | P1/P2 use the same valuation tiers and require current short actionability; a compelling but non-actionable downside case is WATCH instead of disappearing. Borrow depth/fee remains outside Discovery and is warned when unverified. |
| Value-trap / divergence idea | PARTIALLY RECOVERED | Evidence-backed labels derive from actual working-capital/cash/operating signals. A label cannot substitute for final Long/Short qualification. |
| No filler list | PRESERVED | Zero candidates is a successful run. |
| Promotion into research | PRESERVED / SAFER | Discovery does not create Coverage, full Research, Portfolio or thesis state. Promote remains an explicit CONTROL action and ticker validation runs again before persistence. |
| Local synchronous whole-market style | SUPERSEDED | Shared-hosting-safe cached universe + rotating Stage 1 + bounded sequential Stage 2 background work. |
| Provider discipline | IMPROVED | Normal GET is provider-free; Stage 0 is cached; snapshot work is chunked; Companyfacts is limited to Stage-2 finalists; provider-call counts are stored/displayed. |
| Resume / checkpoint | RECOVERED WEB-NATIVE | Stage-1 cursor is persisted in CONTROL-private preferences and advances after bounded scans. |
| Universe coverage over time | IMPROVED | CONTROL-private touch history reports 7-day / 30-day breadth and estimated successful runs for one full rotation. |
| Universe/provider health | IMPROVED | Completed scans surface Stage-0 collapse, stale cache, snapshot return rate and concentrated Stage-1 exclusion anomalies. |
| Why-not diagnostics | IMPROVED | A bounded ticker-level log explains Stage-2 enrichment/final-qualification rejection instead of showing only aggregate counts. |
| Freshness | IMPROVED | Market, filed fundamentals, valuation materialization and universe eligibility timestamps are separated. |
| Corporate-action / basis guard | IMPROVED | Large share-count discontinuities, short filed history and recent registration/listing filings force review before final candidacy. |
| Discovery → Research provenance | IMPROVED | Explicit Promote stores originating scan evidence in permanent audit metadata without auto-creating thesis/Portfolio state. |
| Decision-list UI | IMPROVED | P1/P2 Long and Short lists remain compact; a dedicated WATCH section preserves emerging / verification-needed leads with Price/Base/gap, quality, methods, operating state, invalidation, freshness, warnings and Promote. |

## Stage 0 source and effective universe

Source: Alpaca active US-equity asset catalog.

Fail-closed filters:

- active;
- tradable;
- valid ticker syntax;
- NASDAQ / NYSE / AMEX / ARCA;
- name/symbol exclusion for warrants, rights, units, ETFs, ETNs, funds, preferred securities, note-like securities and blank-check/SPAC patterns.

The **actual Stage-0 member count is runtime data**, because the provider catalog changes. Every completed Discovery result records:

- raw catalog count;
- accepted Stage-0 count;
- exclusion count/breakdown;
- cache timestamp and whether the run reused the cache.

The documentation deliberately does not hard-code a fake market-size number.

## Stage 1

Per run:

- rotate through up to 360 Stage-0 members;
- add at most 160 valid Most Active / Movers names as a secondary lane;
- include at most 80 active Coverage names for cheap stored-context triage;
- request snapshots sequentially in chunks of 60;
- use the previous completed daily bar for liquidity when available, avoiding an early-session volume bias;
- filter new names below $5, below 200k completed-day shares, or below $15M completed-day dollar volume;
- never call SEC Companyfacts;
- checkpoint the next universe cursor.

The Stage-1 result is materialized inside the completed Discovery job result. The Stage-0 catalog and cursor are cached in CONTROL-private user preferences.

## Stage 2

Deep-enrichment cap: 10 finalists per run.

Budget order is intentionally breadth-first:

1. up to 3 already-materialized names whose stored Base gap is at least the 12% WATCH edge;
2. up to 5 quiet broad-rotation names;
3. up to 2 activity-lane names;
4. any remaining capacity is filled from those same Stage-1 lanes.

This is an **investigation budget**, not a final candidate quota. Every finalist may still fail; zero final candidates is valid.

Unknown finalists use:

- one SEC ticker-map request per run;
- SEC submissions;
- SEC Companyfacts;
- canonical Market Forensics valuation.

Provider work is sequential and bounded. A normal run has a hard upper bound of 600 Stage-1 snapshot symbols (10 snapshot batches) and 10 Stage-2 finalists. The cold-run provider envelope remains bounded at 34 calls. Stage 2 does not re-rank finalists by movers/activity.

## Final ranking

No opaque composite score.

Ordering is:

1. P1 / P2 / WATCH opportunity tier;
2. absolute Base gap;
3. valuation quality / usable method count;
4. operating confirmation or contradiction;
5. ticker.

The UI does not display the old generic scan-score dump.

## Explicit limits

0.2.12 is not a licensed whole-market fundamental screener.

A single run does **not** deeply value every US equity. Broadness comes from the cached Stage-0 universe plus checkpointed rotation across repeated background runs.

Unknown Stage-1 names do not yet have whole-market cheap fundamentals/consensus. Until such a licensed source exists, their cheap lane is based on security validity, liquidity, broad rotation and current market metadata; the expensive forensic decision is deferred to bounded Stage 2.

## Regression boundaries

0.2.12 does not change:

- Research workflow/gates/conclusion;
- Valuation engine or valuation policy;
- Tape;
- Portfolio / Position Action / money risk;
- Research Reports/publication;
- Authentication/security;
- Financial Flows;
- Settings behavior;
- deploy workflow;
- reporting-vendor dependencies.

No purple is introduced. Existing typography/design system remains the source of styling.


## Same-version 0.2.12 hardening

The final 0.2.12 hardening intentionally does not create a 0.2.13 contract. It adds observability and guardrails around the accepted 0.2.12 funnel without changing canonical valuation, Research, Tape, Portfolio, Authentication, Settings, Financial Flows or deployment architecture.

Recommended operating cadence is adaptive but manual: retry the next day after a critical universe/provider anomaly, retry in roughly three days for warning-level health or while recent broad-universe coverage is still thin, and settle to weekly once coverage is established. Normal Discovery GET remains provider-free.


## Same-version 0.2.12 opportunity-funnel rebalance

The final 0.2.12 Discovery tuning separates **finding something worth researching** from **proving an investment case**. It does not lower Research/Validation standards and does not create BUY/SELL outputs.

Key bounded changes:

- Stage 1 broad rotation: 240 → 360 names/run.
- External liquidity floor: 500k shares / $50M dollar volume → 200k shares / $15M dollar volume on the completed-day basis.
- Stage 2 deep-enrichment cap: 8 → 10.
- P1 requires >=25% absolute intrinsic edge plus aligned operating confirmation.
- P2 requires >=20% absolute intrinsic edge with at least two methods and no material operating contradiction.
- WATCH preserves 12–20% edges with confirmation and >=20% dislocations that still need valuation/operating/actionability verification.
- Corporate-action/share-basis guards, coherent filed TTM requirements, provider discipline, explicit Promote, privacy boundaries and no-filler behavior remain intact.
