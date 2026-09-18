# Market Forensics 0.2.9 — Tape / Positioning Local Parity Recovery

This addendum records the 0.2.9 recovery of accepted Local Tape capabilities that were missing from the web product.

It supplements `LOCAL_WEB_PARITY_0_2_6.md`.

## Tape capability recovery

| Accepted Local capability | 0.2.8 web | 0.2.9 web |
| --- | --- | --- |
| Net Tape 0–100 | PARTIAL | RECOVERED / IMPROVED |
| Rank A / B / C / D / F | REGRESSED | RECOVERED |
| Accumulation / Distribution regimes | REGRESSED | RECOVERED |
| Institutional Flow Score | REGRESSED | RECOVERED |
| Large trade flow | REGRESSED | RECOVERED with Alpaca historical trades |
| Very Large flow | REGRESSED | RECOVERED |
| Whale flow | REGRESSED | RECOVERED |
| Adaptive per-title notional thresholds | REGRESSED | RECOVERED |
| Short Pressure | PARTIAL | IMPROVED |
| Absorption | PARTIAL | RECOVERED / IMPROVED |
| Long Demand | PARTIAL | IMPROVED |
| Battle Intensity | PARTIAL | IMPROVED |
| Price Resilience | PARTIAL | IMPROVED |
| Data Confidence | PARTIAL | RECOVERED |
| What Changed | REGRESSED | RECOVERED |
| What Would Change the Regime | REGRESSED | RECOVERED |
| Price chart | PRESERVED | PRESERVED |
| Volume chart | REGRESSED | RECOVERED |
| Price + Short Interest | PRESERVED | PRESERVED |
| Daily Short % | PRESERVED | PRESERVED |
| Institutional Net Flow chart | REGRESSED | RECOVERED |
| Cumulative 5D / 20D flow | REGRESSED | RECOVERED |
| Absorption / Short Pressure / Net Tape history | REGRESSED | RECOVERED |
| Whale Flow chart | REGRESSED | RECOVERED |
| ATS activity chart | REGRESSED | RECOVERED when FINRA API is configured |
| Price + cumulative institutional flow | REGRESSED | RECOVERED |

## Web-native implementation

0.2.9 does not restore the Excel/Streamlit runtime.

Instead:

- Alpaca historical trades are fetched only by the background POSITIONING_REFRESH job.
- Raw prints are processed in memory and reduced to daily aggregate evidence.
- Large / Very Large / Whale thresholds are adaptive to the sampled notional distribution with hard floors.
- Direction is a transparent tick-rule proxy and is labeled as such.
- Historical SIP is attempted first; IEX fallback is explicitly marked partial-market and penalized in Data Confidence.
- FINRA Weekly Summary is stored as delayed ATS / non-ATS evidence.
- Research cache materializes Tape output so normal GET navigation remains provider-free.
- Decision Lenses receive only SUPPORTIVE / HOSTILE / MIXED path translation; detailed Tape ranks/regimes remain contextual research evidence.

## Interpretation rule

The system must never infer the identity of a buyer/seller from Large/Whale prints, ATS venue data or trade size.

A large buyer proxy is constructive only when price response and absorption support that interpretation.

Large positive flow with weak price response can coexist with DISTRIBUTION / BEARS CONTROL.

Rank remains evidence organization, not a standalone trading signal.
