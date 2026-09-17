# Data model — 0.1.0

The new research core is intentionally separate from the legacy 0.0.4 research tables during conversion. It uses `mf_*` tables for company/security identity, coverage, market snapshots, filing facts, normalized financials, research, valuation, bear case, catalysts, management, financial flows, monitoring, risk, investment state, positions, journal, events, sources, provenance, quality, jobs, refresh runs, alerts, snapshots, publications and calculation runs.

The account/security tables retain their existing names and IDs so existing CONTROL, FRIEND and INSIDER accounts continue without reset.

## State separation

`mf_coverage.research_state` describes what research concludes about the company.

`mf_investment_state.state` describes what the private portfolio is doing.

No publication reads directly from private mutable state; it reads a frozen `mf_snapshot` and applies the publication-safe filter.
