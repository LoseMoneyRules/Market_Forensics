# Local → Web parity — 0.2.11 Position Action

## Purpose

0.2.11 restores the useful Local concept of a final position action without collapsing the Web architecture back into a position-aware Research engine.

The Local conceptual path was:

**DATA → VALUE → THESIS → TIMING → RISK → ACTION**

The Web keeps the stronger separation:

- **Research judges the security.**
- **Portfolio decides what the existing position allows you to do.**

## Parity mapping

| Local capability | 0.2.11 Web implementation |
| --- | --- |
| Final practical action | Position Action inside Portfolio security detail |
| Research direction | Canonical Research Conclusion consumed read-only |
| Existing Long / Short awareness | Portfolio Position + PositionProfile |
| Money-risk / max size | PortfolioRiskPlan + current gross weight + suggested position |
| Thesis invalidation precedence | Locked Research invalidation + latest stored locked Monitoring status |
| Add discipline | ADD ON EVIDENCE / ADD SHORT ON EVIDENCE are conditional; price is never confirmation |
| Evidence confirmation | Optional Portfolio ADD / TRIM / EXIT links reuse existing Monitoring rules with explicit OK-vs-triggered semantics |
| Position sizing translation | Current → suggested weight, headroom and reference dollars/shares; descriptive only, never an order |
| Portfolio command | Background-materialized Needs Attention list from deterministic Position Action |
| Action history | Bounded transition history records changes in Action/rule/Research Conclusion |
| Portfolio downside budget | Sum of configured per-position loss budgets vs currently used adjusted-downside amount; descriptive, not VaR |
| No-position action | BUY CANDIDATE / SHORT CANDIDATE / WAIT / DATA REVIEW |
| Existing Long | HOLD / ADD ON EVIDENCE / HOLD-WAIT / REDUCE / EXIT-SELL / DATA REVIEW |
| Existing Short | HOLD SHORT / ADD SHORT ON EVIDENCE / HOLD-WAIT / REDUCE SHORT / COVER / DATA REVIEW |
| Auditability | deterministic rule id + normalized input trace |
| Privacy | CONTROL-only; excluded from publication/member payloads |

## Permanent non-parity by design

0.2.11 does **not** restore any Local coupling where Research knows shares, cost basis, P/L or sizing.

It does not move Risk into Research, does not change Tape, Discovery or Valuation policy, and does not make price movement a thesis signal.

## Fail-closed precedence

1. locked thesis invalidation triggered;
2. confirmed explicit Portfolio EXIT condition;
3. Portfolio risk-limit breach;
4. confirmed explicit Portfolio TRIM condition;
5. Portfolio-only / incomplete / data-review / unvalidated Research;
6. opposite-direction READY evidence;
7. no-position candidate state;
8. same-direction READY conditional add only with VALIDATED + CONTROLLED thesis + coherent Path + risk headroom + explicit evidence-to-add condition; a linked Monitoring confirmation must also be satisfied;
9. WATCH / NO EDGE holds or waits.

## UI

The only new primary surface is a compact Position Action block in the existing Portfolio security detail. The old manual “Portfolio action” field is no longer presented as the primary action surface; its stored legacy field is retained for compatibility/history.

No Research redesign, no Tape/Discovery/Valuation redesign, no purple, no deploy-workflow change.
