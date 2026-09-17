from __future__ import annotations

from .core_models import HistoricalTestRun, HistoricalTestSample
from .extensions import db
from .historical_engine import run_historical_test as _run_historical_test


def run_historical_test(coverage_id: int, user_id: int, lookback_years: int = 10):
    requested = max(3, min(int(lookback_years), 15))
    result = _run_historical_test(coverage_id, user_id, requested)
    run = db.session.get(HistoricalTestRun, int(result["run_id"]))
    if run is None:
        return result
    samples = HistoricalTestSample.query.filter_by(run_id=run.id).order_by(HistoricalTestSample.anchor_date.asc()).all()
    first = samples[0].anchor_date if samples else None
    last = samples[-1].anchor_date if samples else None
    span_years = ((last - first).days / 365.25) if first and last else 0.0
    summary = dict(run.summary or {})
    market_refresh = dict((run.config or {}).get("market_refresh") or {})
    summary.update({
        "requested_lookback_years": requested,
        "generated_samples": len(samples),
        "first_anchor": first.isoformat() if first else None,
        "last_anchor": last.isoformat() if last else None,
        "anchor_span_years": round(span_years, 2),
        "historical_market_first": market_refresh.get("first_date"),
        "historical_market_last": market_refresh.get("last_date"),
        "historical_market_span_years": market_refresh.get("span_years"),
        "coverage_warnings": market_refresh.get("errors") or [],
    })
    run.summary = summary
    # Do not pretend a requested 15Y replay is complete when the price/filing history is shorter.
    minimum_span = max(2.0, requested - 1.5)
    if samples and span_years < minimum_span:
        run.status = "PARTIAL_COVERAGE"
    db.session.commit()
    result.update({
        "status": run.status,
        "generated_samples": len(samples),
        "first_anchor": summary.get("first_anchor"),
        "last_anchor": summary.get("last_anchor"),
        "anchor_span_years": summary.get("anchor_span_years"),
    })
    return result


__all__ = ["run_historical_test"]
