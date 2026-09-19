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
2. Portfolio risk-limit breach;
3. Portfolio-only / incomplete / data-review / unvalidated Research;
4. opposite-direction READY evidence;
5. no-position candidate state;
6. same-direction READY conditional add only with VALIDATED + CONTROLLED thesis + coherent Path + risk headroom + explicit evidence-to-add condition;
7. WATCH / NO EDGE holds or waits.

## UI

The only new primary surface is a compact Position Action block in the existing Portfolio security detail. The old manual “Portfolio action” field is no longer presented as the primary action surface; its stored legacy field is retained for compatibility/history.

No Research redesign, no Tape/Discovery/Valuation redesign, no purple, no deploy-workflow change.
