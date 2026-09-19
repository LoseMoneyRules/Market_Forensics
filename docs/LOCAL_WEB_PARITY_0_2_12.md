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
| Generic screener score | NOT COPIED | Final qualification remains forensic and fail-closed; no generic composite score can create a candidate. |
| Fair value central to Discovery | PRESERVED / HARDENED | Canonical valuation engine is reused with reference-price fallback disabled. Base must be INTRINSIC and supported by at least two usable methods. |
| Bear / Base / Bull | PRESERVED | Stage 2 carries canonical scenario fair values into the Discovery candidate when mathematically available. |
| Operating confirmation | HARDENED | Four coherent filed quarters are required for current TTM and a comparable prior TTM is required for the operating comparison. Annual fallback does not qualify a final Discovery candidate. |
| Long dislocation | HARDENED | Base gap >= +20% plus confirming filed TTM operating evidence. |
| Short dislocation | HARDENED | Base gap <= -20% plus confirming deterioration, price/actionability floor and current Alpaca shortable flag. Borrow depth/fee remains outside Discovery and is warned when unverified. |
| Value-trap / divergence idea | PARTIALLY RECOVERED | Evidence-backed labels derive from actual working-capital/cash/operating signals. A label cannot substitute for final Long/Short qualification. |
| No filler list | PRESERVED | Zero candidates is a successful run. |
| Promotion into research | PRESERVED / SAFER | Discovery does not create Coverage, full Research, Portfolio or thesis state. Promote remains an explicit CONTROL action and ticker validation runs again before persistence. |
| Local synchronous whole-market style | SUPERSEDED | Shared-hosting-safe cached universe + rotating Stage 1 + bounded sequential Stage 2 background work. |
| Provider discipline | IMPROVED | Normal GET is provider-free; Stage 0 is cached; snapshot work is chunked; Companyfacts is limited to Stage-2 finalists; provider-call counts are stored/displayed. |
| Resume / checkpoint | RECOVERED WEB-NATIVE | Stage-1 cursor is persisted in CONTROL-private preferences and advances after bounded scans. |
| Decision-list UI | IMPROVED | Compact two-column Long/Short list shows Price, Bear/Base/Bull, gap, quality, method count, operating evidence, invalidation, freshness, warnings and Promote. |

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

- rotate through up to 240 Stage-0 members;
- add at most 160 valid Most Active / Movers names as a secondary lane;
- include at most 80 active Coverage names for cheap stored-context triage;
- request snapshots sequentially in chunks of 60;
- use the previous completed daily bar for liquidity when available, avoiding an early-session volume bias;
- filter new names below $5, below 500k daily volume, or below $50M daily dollar volume;
- never call SEC Companyfacts;
- checkpoint the next universe cursor.

The Stage-1 result is materialized inside the completed Discovery job result. The Stage-0 catalog and cursor are cached in CONTROL-private user preferences.

## Stage 2

Deep-enrichment cap: 8 finalists per run.

Budget order is intentionally breadth-first:

1. up to 2 already-materialized intrinsic near-edge names;
2. up to 4 quiet broad-rotation names;
3. up to 2 activity-lane names;
4. any remaining capacity is filled from those same Stage-1 lanes.

This is an **investigation budget**, not a final candidate quota. Every finalist may still fail; zero final candidates is valid.

Unknown finalists use:

- one SEC ticker-map request per run;
- SEC submissions;
- SEC Companyfacts;
- canonical Market Forensics valuation.

Provider work is sequential and bounded. A normal run therefore has a hard upper bound of 480 Stage-1 snapshot symbols (8 snapshot batches) and 8 Stage-2 finalists. Stage 2 does not re-rank finalists by movers/activity.

## Final ranking

No opaque composite score.

Ordering is:

1. P1/P2 forensic priority tier;
2. absolute intrinsic Base gap;
3. number of usable valuation methods;
4. operating-confirmation strength;
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
