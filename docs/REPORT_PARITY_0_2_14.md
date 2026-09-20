# Market Forensics 0.2.14 — Research Report Parity Audit

This audit is the release-specific report benchmark. It compares the accepted Local V3.1.12 report implementation, the Web report implementation inherited by 0.2.13, and the required 0.2.14 outcome.

The Local benchmark was inspected from the repository's golden archive `Market_Forensics_V3_1_12_FULL.zip`, including the real `market_forensics/exporter.py` renderer. The audit is therefore based on executable Local report code, not only on the historical parity matrix. No committed Local PDF screenshot fixture exists in the archive; the concrete comparison uses the Local renderer's page structure, chart builders, report payload, tables and typography/layout code.

Status vocabulary:

- **PRESERVED** — capability retained without material regression.
- **MISSING** — capability existed or is available in the product but was absent from the current Web report.
- **WORSE** — present in the current Web report but materially less useful than the Local benchmark.
- **IMPROVED** — 0.2.14 retains the capability and makes it more decision-useful.
- **SUPERSEDED** — an older Local concept is deliberately replaced by a stronger canonical Web concept.

| Capability | Local V3.1.12 | Web inherited by 0.2.13 | 0.2.14 desired / implemented |
|---|---|---|---|
| First decision read | Decision Readiness / next action existed, but no current canonical Research Conclusion | Research Conclusion existed as one KPI inside Research Intelligence | **SUPERSEDED / IMPROVED** — Research Conclusion is the first decision information below identity and receives hero priority |
| Target-price prominence | Base Target and price gap were first-page anchors | Bear/Base/Bull existed but were visually secondary to the intelligence strip | **IMPROVED** — Base Target and Base Gap sit beside Research Conclusion; Current/Bear/Base/Bull immediately follow |
| Bear / Base / Bull | Strong first-page and scenario presentation | Present | **PRESERVED / IMPROVED** — compact decision strip + valuation map + scenario detail |
| Valuation quality / intrinsic vs provisional | Confidence existed but Web's current quality taxonomy did not | Quality existed in engine/UI but report did not make provisional state sufficiently unmistakable | **IMPROVED** — Base quality, warnings and explicit PROVISIONAL treatment are part of the report contract |
| Scenario probabilities | Reported in Local scenario bridge | Not decision-visible in current Full report | **MISSING → IMPROVED** |
| Valuation method values | P/E, EV/Sales, FCF Yield shown in scenario bridge | Canonical engine had them; report omitted them | **MISSING → IMPROVED** |
| Valuation method weights | Present in Local payload/model | Not exposed usefully in report | **MISSING → IMPROVED** — canonical contract preserves stored effective/model weights |
| DCF cross-check | Present per scenario | Available in engine, omitted in report | **MISSING → IMPROVED** |
| Share denominator / source | Strong Local audit detail | Engine stored share basis but report largely hid it | **WORSE → IMPROVED** — shares, source, verified state and note appear beside valuation detail |
| Price history vs fair value | Local native chart: market price + Bear/Base/Bull horizontal levels and range | Current Web report used only a simple target bitmap | **WORSE → IMPROVED** — native price-context chart; explicitly labels today's scenario levels, not historical model output |
| Valuation map | Local scenario range was visually intuitive | Current Web added a simple map | **PRESERVED / IMPROVED** — native MF-style current/Bear/Base/Bull map with Base emphasis and quality |
| Fundamental trend chart | Local Revenue + FCF margin chart | Current Web Full report used a table, no comparable chart | **MISSING → IMPROVED** — revenue and margins separated by scale |
| FCF / cash conversion | Local FCF context existed | Mostly textual/table | **MISSING → IMPROVED** — native FCF + CFO/Net Income chart |
| Working-capital forensics | Local/report data supported the analysis, but no dedicated high-signal report view | Web engine has DSO/DIO/CCC and inventory/receivables ratios; report underused them | **SUPERSEDED / IMPROVED** — only material stored ratios are visualized; no decorative chart |
| Fundamental table | Local concise operating table | Current Web had a reasonable table | **PRESERVED / IMPROVED** — concise forensic metrics only; unavailable metrics are not padded with rows of dashes |
| 5Y forecast chart | Dedicated Local Bear/Base/Bull forecast path | Current Web has Expectations + canonical valuation + Validate rather than the same Local forward series | **SUPERSEDED** — no synthetic forecast chart is invented unless equivalent materialized time-series exists; Expectations and point-in-time Validate carry the stronger Web evidence |
| Market view / our view | Variant view existed | Present as thesis/variant panels | **PRESERVED / IMPROVED** — MARKET VIEW, OUR VIEW / VARIANT, WHAT MUST BE TRUE and WHAT WOULD PROVE US WRONG |
| Evidence FOR / AGAINST | Less central in Local | Web introduced richer evidence but diagnostic score could dominate | **IMPROVED** — few high-information FOR/AGAINST items; diagnostic score small and explicitly secondary |
| Research intelligence | Local used several system scores | Web has canonical Decision Lenses and Research Conclusion | **SUPERSEDED** — Value, Expectations, Variant, Path, Model Confidence and Thesis Control replace gamified score emphasis |
| Management execution | Local management scorecard | Web has richer execution/accountability engine | **IMPROVED** — execution assessment and evidence coverage, without personality scoring |
| Promises vs actuals | Strong Local Filed/Metric/FY/Target/Actual/Result table | Web current report preserved some promise rows but with weaker context | **IMPROVED** — target period preserved, MET/MISS/PENDING/EVIDENCE ONLY/non-comparable are retained and material rows prioritized |
| Catalysts | Present but less structured | Present in DB/report text | **IMPROVED** — event, timing, direction, status |
| Bear case | Present | Present mainly as text | **IMPROVED** — risk, severity/status, evidence and thesis-invalidating flag |
| Tape / positioning | Local had Long Demand, Short Pressure, Absorption, resilience, short context, Net Large/Whale | Web 0.2.9+ engine is substantially richer, but report reduced it to a short line | **MISSING/WORSE → IMPROVED** — concise regime/rank/confidence/flow/pressure read plus selected native price+institutional-flow and pressure charts |
| FINRA / ATS context | Local had less complete forensic coverage | Web engine stores FINRA/ATS series | **IMPROVED CONTRACT** — series are preserved in canonical report data; visual output remains selective to avoid turning the report into a chart dump |
| Monitoring | Local invalidation concept existed, not today's locked rule ledger | Web has locked pre-investment monitoring rules but current report did not expose them fully | **SUPERSEDED / IMPROVED** — metric, threshold, direction, current value, status, last observation and locked state |
| Validation | Local did not have the current Web point-in-time validation system | Web Validate exists but report exposed mainly state | **SUPERSEDED / IMPROVED** — state, reliability, sample count/span, valuation/direction/range/assumption accuracy and point-in-time chart |
| Sources / provenance | Local included supporting provenance | Web stores richer Source rows but current report presentation was basic | **IMPROVED** — compact provider/document/published/retrieved/title audit table |
| Financial Flows | Local had less complete Web-native stored flows | Web has materialized signed flow bridges/Sankey data; report was mostly a paragraph | **SUPERSEDED / IMPROVED** — signed ledger/bridge from stored flow payload; no fabricated values |
| Executive report | Local first page was highly decision-dense | Current Web executive was visually clean but omitted too much decision context | **WORSE → IMPROVED** — decision-first page, readable density, no need to read the Full report first |
| Full report narrative | Four intentional Local pages with clear sequencing | Current Web Full report became Executive + paragraph/table dump | **WORSE → IMPROVED** — decision-process narrative rather than a data dump |
| Word report | Structured Local Word output | Current Web Word existed but was less report-grade | **WORSE → IMPROVED** — same canonical information as Full PDF, editable Market Forensics styling, native charts/tables and controlled page breaks |
| PDF report | Strong Local report identity | Current Web PDF was materially less useful | **WORSE → IMPROVED** — Market Forensics document system, page numbers/footer, native charts, decision hierarchy |
| Missing-data behavior | Local had several fallbacks | Web has fail-safe artifact fallback | **PRESERVED / IMPROVED** — optional report sections degrade to unavailable/not run; no invented data |
| Provider-free generation | Local desktop architecture was different | Web route could enqueue RECALCULATE via generic context when cache was stale | **IMPROVED** — report route calls context with recalculation queue disabled; report contract performs stored/materialized reads only |
| Privacy | Local single-user context | Web has CONTROL vs publication/member boundaries | **SUPERSEDED / PRESERVED** — report contract contains research evidence but excludes Position shares/cost/P&L/sizing/private Portfolio notes/journal/credentials |
| Rich-backend health | Not applicable to Local desktop | Required in Web production | **PRESERVED** — reportlab/python-docx backend remains a production health requirement; safe fallback remains valid |
| Discovery report | Separate capability | Separate landscape PDF | **PRESERVED** — 0.2.14 does not redesign Discovery export |

## Local minimum benchmark recovered

The Local implementation establishes the minimum benchmark for:

1. immediate target-price visibility;
2. a real price-versus-fair-value visual;
3. a structured Bear/Base/Bull scenario bridge;
4. share-basis and valuation auditability;
5. operating charts before raw tables;
6. management promise accountability;
7. market-positioning context;
8. a report with deliberate page architecture rather than an export dump.

0.2.14 must preserve those strengths while using the Web-native canonical systems that did not exist, or were materially weaker, in the Local: Research Conclusion, Decision Lenses, richer Tape, point-in-time Validate, locked Monitoring, signed Financial Flows, current valuation-quality taxonomy and stronger provenance.

## Deliberate non-parity

0.2.14 does **not** reproduce the Local's score-heavy presentation or synthesize a Local-style 5Y forecast chart from data that is not materialized in the current Web model. Decision Lenses and Validate supersede those older concepts. A report chart is generated only when its underlying stored series exists and the chart answers a decision question.

## Release criterion

The release may be described as better than Local only if the final visual QA confirms that:

- Research Conclusion and Base Target are visible before any detailed section;
- the Executive artifact communicates the investment case without requiring the Full artifact;
- valuation is at least as auditable as Local;
- every retained chart is legible and decision-useful;
- Management, Tape, Monitoring and Validate are materially more useful than the Local equivalents;
- missing data degrades honestly;
- no Portfolio-private data crosses the report/publication boundary.
