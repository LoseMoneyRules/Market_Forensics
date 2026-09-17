# Market Forensics 0.1.2 — Data Sources

## Design rule

Market Forensics stores evidence before interpretation. Every provider feeds a normalized internal layer so research pages never depend directly on a third-party response shape.

`SOURCE → RAW/PAYLOAD → NORMALIZED FACT → CALCULATION → RESEARCH DRAFT → HUMAN DECISION`

Automatic drafting may populate blank or previously auto-generated fields. Manual research, valuation overrides, locked risk limits and journal decisions always win.

## Active sources in 0.1.2

| Need | Primary source | Secondary/fallback | Stored objects |
|---|---|---|---|
| Company identity / audited filings | SEC EDGAR | — | Source, FinancialPeriod, RawFinancialFact, NormalizedFinancial, Provenance |
| Revenue / margins / working capital / cash flow | SEC Companyfacts + filing context | — | NormalizedFinancial, FinancialFlow, DataQualityIssue |
| Current equity price | Alpaca IEX when configured | Tiingo → Alpha Vantage → public chart fallback | MarketSnapshot |
| Daily short-sale volume | FINRA Reg SHO public daily files | — | Source + FINRA_SHORT_VOLUME_SERIES event |
| Consolidated short interest | FINRA Query API Public credential | — | Source + FINRA_SHORT_INTEREST_SERIES event |
| Short-interest change / ADV / days-to-cover | FINRA consolidatedShortInterest | — | Same short-interest event payload |
| OTC threshold history when applicable | FINRA thresholdList | — | Source + FINRA_THRESHOLD_HISTORY event |
| Filing/event history | SEC submissions | — | Source + Event |
| Deterministic valuation / flow calculations | Internal calculation engine | — | CalculationRun, ValuationModel, FinancialFlow |

## Clean extension lanes

These are intentionally separate capabilities rather than extra fields mixed into the existing UI. They can be added when a reliable source is selected.

### Ownership / holders
- SEC Schedule 13D / 13G for material beneficial ownership changes.
- SEC 13F for institutional-manager holdings, requiring issuer/security mapping and quarter-over-quarter normalization.
- Insider Forms 3 / 4 / 5 for officer/director ownership transactions.

### Options / derivatives
- Open interest, volume, implied volatility, skew, term structure, put/call structure and unusual positioning.
- Must come from a licensed provider with clear timestamp and contract symbology; no scraped estimates.

### Market microstructure
- Historical OHLCV, relative volume, gaps, realized volatility and liquidity measures.
- FINRA OTC/ATS weekly/monthly aggregates are useful for venue-level context, but are not a substitute for exchange order-book data.

### Macro / industry
- FRED / official government series where a thesis genuinely depends on rates, FX, unemployment, CPI, commodity or industry-specific drivers.
- Macro series should be attached to a research thesis/monitoring rule instead of globally cluttering every company page.

### Expectations
- Consensus revenue/EPS/margin estimates and estimate revisions require a provider whose licensing permits storage and display.
- Until then Market Forensics keeps structured user-entered expectations rather than fabricating consensus data.

### Borrow / securities lending
- Borrow fee, utilization, lendable supply and recall activity require a securities-lending provider. FINRA short interest does not provide these fields.

## Provider policy

1. Prefer regulatory/issuer sources for factual company data.
2. Persist source URL/provider/timestamp before derived calculations.
3. Never silently replace a failed provider with guessed data.
4. A fallback quote may support display, but its quality flag remains visible in storage.
5. Do not treat daily FINRA short-sale volume as short interest.
6. Do not treat 13F ownership as current real-time ownership; preserve report period and filing date.
7. Do not overwrite manual research with automatic drafts.
8. Keep personal portfolio/risk/journal data outside publication payloads.
