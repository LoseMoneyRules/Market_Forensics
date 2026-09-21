from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any

from .core_models import Company, Coverage, HistoricalTestRun, HistoricalTestSample, Security
from .extensions import db
from .historical_data import preferred_provider, price_on_or_after, refresh_historical_prices
from .secdata import DURATION_TAGS, INSTANT_TAGS, SEC_DATA, _facts, _json, _ticker_meta, _ua
from .economic_reality import DURATION_TAGS as ECONOMIC_DURATION_TAGS, INSTANT_TAGS as ECONOMIC_INSTANT_TAGS, build_economic_reality, economic_from_row, metric as economic_metric
from .valuation_engine import ENGINE_VERSION, calibrate_multiples, default_cases, evaluate, infer_company_type, metrics_from_history, n
from .validation_policy import validation_state


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _duration_days(row: dict[str, Any]) -> int | None:
    start, end = _day(row.get("start")), _day(row.get("end"))
    return (end - start).days if start and end else None


def _duration_asof(companyfacts: dict[str, Any], tags: list[str], cutoff: date) -> dict[int, dict[str, Any]]:
    candidates: dict[int, list[dict[str, Any]]] = {}
    for tag in tags:
        for raw in _facts(companyfacts, "us-gaap", tag):
            filed = _day(raw.get("filed"))
            if filed is None or filed > cutoff or raw.get("form") not in {"10-K", "10-K/A"} or str(raw.get("fp") or "") != "FY":
                continue
            days = _duration_days(raw)
            if days is None or not 300 <= days <= 430 or n(raw.get("val")) is None:
                continue
            try:
                fy = int(raw.get("fy"))
            except Exception:
                continue
            item = dict(raw); item["tag"] = tag; item["namespace"] = "us-gaap"
            candidates.setdefault(fy, []).append(item)
    out: dict[int, dict[str, Any]] = {}
    for fy, rows in candidates.items():
        rows.sort(key=lambda row: (str(row.get("filed") or ""), str(row.get("end") or ""), str(row.get("accn") or "")))
        out[fy] = rows[-1]
    return out


def _instant_asof(companyfacts: dict[str, Any], tags: list[str], cutoff: date, namespace: str = "us-gaap") -> dict[int, dict[str, Any]]:
    candidates: dict[int, list[dict[str, Any]]] = {}
    for tag in tags:
        for raw in _facts(companyfacts, namespace, tag):
            filed = _day(raw.get("filed"))
            if filed is None or filed > cutoff or raw.get("form") not in {"10-K", "10-K/A"} or raw.get("start") or n(raw.get("val")) is None:
                continue
            try:
                fy = int(raw.get("fy"))
            except Exception:
                continue
            item = dict(raw); item["tag"] = tag; item["namespace"] = namespace
            candidates.setdefault(fy, []).append(item)
    out: dict[int, dict[str, Any]] = {}
    for fy, rows in candidates.items():
        rows.sort(key=lambda row: (str(row.get("filed") or ""), str(row.get("end") or ""), str(row.get("accn") or "")))
        out[fy] = rows[-1]
    return out


def annual_history_asof(companyfacts: dict[str, Any], cutoff: date, company_type: str = "") -> list[dict[str, Any]]:
    duration = {key: _duration_asof(companyfacts, tags, cutoff) for key, tags in DURATION_TAGS.items()}
    instant = {key: _instant_asof(companyfacts, tags, cutoff) for key, tags in INSTANT_TAGS.items()}
    economic_duration = {key: _duration_asof(companyfacts, list(tags), cutoff) for key, tags in ECONOMIC_DURATION_TAGS.items()}
    economic_instant = {key: _instant_asof(companyfacts, list(tags), cutoff) for key, tags in ECONOMIC_INSTANT_TAGS.items()}
    dei_shares = _instant_asof(companyfacts, ["EntityCommonStockSharesOutstanding"], cutoff, namespace="dei")
    if dei_shares:
        instant["shares_outstanding"] = dei_shares
    years = sorted(set().union(*(set(rows) for rows in duration.values()), *(set(rows) for rows in instant.values())))
    out: list[dict[str, Any]] = []
    for fy in years:
        row: dict[str, Any] = {"fiscal_year": fy}
        anchor = duration.get("revenue", {}).get(fy) or next((rows.get(fy) for rows in duration.values() if rows.get(fy)), None)
        if not anchor:
            continue
        row["filed_at"] = str(anchor.get("filed") or "")[:10]
        row["period_end"] = str(anchor.get("end") or "")[:10]
        for field, records in duration.items():
            record = records.get(fy)
            row[field] = n((record or {}).get("val"))
        for field, records in instant.items():
            record = records.get(fy)
            row[field] = n((record or {}).get("val"))
        if row.get("revenue") is not None and row.get("gross_profit") is not None:
            row["cogs"] = row["revenue"] - row["gross_profit"]
        if row.get("gross_profit") is not None and row.get("operating_income") is not None:
            row["operating_expenses"] = row["gross_profit"] - row["operating_income"]
        if row.get("cfo") is not None and row.get("capex") is not None:
            row["fcf"] = row["cfo"] - row["capex"]
        row["_debt_source_tag"] = str(((instant.get("debt") or {}).get(fy) or {}).get("tag") or "")
        economic_facts = {}
        economic_sources = {}
        for field, records in economic_duration.items():
            record = records.get(fy) or {}
            value = n(record.get("val"))
            if value is not None:
                economic_facts[field] = value
                economic_sources[field] = {"tag": record.get("tag"), "filed": record.get("filed")}
        for field, records in economic_instant.items():
            record = records.get(fy) or {}
            value = n(record.get("val"))
            if value is not None:
                economic_facts[field] = value
                economic_sources[field] = {"tag": record.get("tag"), "filed": record.get("filed")}
        row["quality"] = {
            "economic_reality": build_economic_reality(
                row, facts=economic_facts, fact_sources=economic_sources, company_type=company_type
            )
        }
        out.append(row)
    return out


def _first_filing_anchors(companyfacts: dict[str, Any], start: date) -> list[tuple[int, date]]:
    candidates: dict[int, list[date]] = {}
    for tag in DURATION_TAGS["revenue"]:
        for row in _facts(companyfacts, "us-gaap", tag):
            filed = _day(row.get("filed"))
            if filed is None or filed < start or row.get("form") not in {"10-K", "10-K/A"} or str(row.get("fp") or "") != "FY":
                continue
            days = _duration_days(row)
            if days is None or not 300 <= days <= 430:
                continue
            try:
                fy = int(row.get("fy"))
            except Exception:
                continue
            candidates.setdefault(fy, []).append(filed)
    return sorted((fy, min(days)) for fy, days in candidates.items())


def _calibration_observations(security_id: int, history: list[dict[str, Any]], anchor: date, provider: str | None) -> list[dict[str, Any]]:
    out = []
    for row in history[:-1]:
        filed = _day(row.get("filed_at"))
        if filed is None or filed >= anchor:
            continue
        price = price_on_or_after(security_id, filed, 14, provider=provider)
        shares = n(row.get("shares_outstanding")) or n(row.get("diluted_shares"))
        raw_price = n(price.close_raw) if price else None
        if raw_price is None or shares in (None, 0):
            continue
        economic = economic_from_row(row)
        economic_net_debt = economic_metric(economic, "economic_net_debt")
        out.append({
            "price": raw_price, "shares": shares, "revenue": row.get("revenue"), "net_income": row.get("net_income"),
            "fcf": row.get("fcf"),
            "net_debt": economic_net_debt if economic and not economic.get("material_unresolved") else None,
        })
    return out


def _future_price(security_id: int, anchor: date, years: int, provider: str | None) -> float | None:
    target = anchor + timedelta(days=365 * years)
    row = price_on_or_after(security_id, target, 21, provider=provider)
    return n(row.close_split_adjusted) if row else None


def _next_realized(history_future: list[dict[str, Any]], fiscal_year: int | None) -> dict[str, float | None]:
    if fiscal_year is None:
        return {}
    prior = next((x for x in history_future if int(x.get("fiscal_year") or 0) == fiscal_year), None)
    nxt = next((x for x in history_future if int(x.get("fiscal_year") or 0) == fiscal_year + 1), None)
    if not prior or not nxt:
        return {}
    revenue0, revenue1 = n(prior.get("revenue")), n(nxt.get("revenue"))
    return {
        "growth": (revenue1 / revenue0 - 1.0) if revenue1 is not None and revenue0 not in (None, 0) else None,
        "net_margin": (n(nxt.get("net_income")) / revenue1) if n(nxt.get("net_income")) is not None and revenue1 not in (None, 0) else None,
        "fcf_margin": (n(nxt.get("fcf")) / revenue1) if n(nxt.get("fcf")) is not None and revenue1 not in (None, 0) else None,
    }


def _score_sample(anchor_price: float | None, bear: float | None, base: float | None, bull: float | None, expected: float | None,
                  future_1y: float | None, base_assumptions: dict[str, Any], realized: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    if expected not in (None, 0) and future_1y not in (None, 0):
        error = abs(expected / future_1y - 1.0)
        scores["valuation_accuracy"] = max(0.0, 100.0 - error * 100.0)
    if anchor_price not in (None, 0) and expected is not None and future_1y is not None:
        model_gap = expected / anchor_price - 1.0
        actual_gap = future_1y / anchor_price - 1.0
        if abs(model_gap) < .05 and abs(actual_gap) < .05:
            scores["direction_accuracy"] = 100.0
        elif model_gap * actual_gap > 0:
            scores["direction_accuracy"] = 100.0
        elif abs(model_gap) < .05 or abs(actual_gap) < .05:
            scores["direction_accuracy"] = 50.0
        else:
            scores["direction_accuracy"] = 0.0
    if future_1y is not None and bear is not None and bull is not None:
        low, high = min(bear, bull), max(bear, bull)
        scores["range_coverage"] = 100.0 if low <= future_1y <= high else 0.0
    errors = []
    for key in ("growth", "net_margin", "fcf_margin"):
        assumed, actual = n(base_assumptions.get(key)), n(realized.get(key))
        if assumed is not None and actual is not None:
            errors.append(abs(assumed - actual))
    if errors:
        scores["assumption_accuracy"] = max(0.0, 100.0 - mean(errors) * 250.0)
    weights = {"valuation_accuracy": .40, "direction_accuracy": .20, "range_coverage": .20, "assumption_accuracy": .20}
    available = [(key, scores[key], weight) for key, weight in weights.items() if key in scores]
    total = sum(weight for _, _, weight in available)
    scores["reliability"] = sum(score * weight for _, score, weight in available) / total if total else None
    return scores


def run_historical_test(coverage_id: int, user_id: int, lookback_years: int = 10) -> dict[str, Any]:
    coverage = db.session.get(Coverage, coverage_id)
    if coverage is None:
        raise RuntimeError("Coverage not found")
    security = db.session.get(Security, coverage.security_id)
    company = db.session.get(Company, security.company_id) if security else None
    if security is None or company is None:
        raise RuntimeError("Security/company not found")
    years = max(3, min(int(lookback_years), 40))
    market_refresh = refresh_historical_prices(security, user_id, years)
    provider = preferred_provider(security.id)
    user_agent = _ua(user_id)
    meta = _ticker_meta(security.ticker, user_agent)
    companyfacts = _json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{meta['cik']}.json", user_agent)
    start = date.today() - timedelta(days=366 * years)
    anchors = _first_filing_anchors(companyfacts, start)
    company_type = infer_company_type(company.sector, company.industry)
    full_history = annual_history_asof(companyfacts, date.today(), company_type)

    run = HistoricalTestRun(
        coverage_id=coverage.id, user_id=user_id, status="RUNNING", engine_version=ENGINE_VERSION,
        lookback_years=years, config={"provider": provider, "company_type": company_type, "policy": "frozen_auto_policy", "market_refresh": market_refresh},
    )
    db.session.add(run); db.session.flush()
    reliability_rows: list[dict[str, Any]] = []

    for fiscal_year, filing_date in anchors:
        history = annual_history_asof(companyfacts, filing_date, company_type)
        if len(history) < 2:
            continue
        anchor_row = price_on_or_after(security.id, filing_date, 14, provider=provider)
        if anchor_row is None:
            continue
        metrics = metrics_from_history(history, company_type=company_type)
        if not metrics.get("basis_usable"):
            continue
        observations = _calibration_observations(security.id, history, filing_date, provider)
        calibration = calibrate_multiples(observations, company_type)
        policy = default_cases(metrics, company_type, calibration)
        raw_anchor = n(anchor_row.close_raw)
        basis_factor = n(anchor_row.split_basis_factor) or 1.0
        result = evaluate(metrics, policy, policy["weights"], int(policy["horizon_years"]), current_price=raw_anchor)
        scenario = result["scenarios"]
        bear_raw, base_raw, bull_raw = scenario["BEAR"].get("fair_value"), scenario["BASE"].get("fair_value"), scenario["BULL"].get("fair_value")
        expected_raw = result.get("expected_value")
        anchor_adjusted = n(anchor_row.close_split_adjusted)
        bear = bear_raw * basis_factor if bear_raw is not None else None
        base = base_raw * basis_factor if base_raw is not None else None
        bull = bull_raw * basis_factor if bull_raw is not None else None
        expected = expected_raw * basis_factor if expected_raw is not None else None
        future_1y = _future_price(security.id, anchor_row.trade_date, 1, provider)
        future_3y = _future_price(security.id, anchor_row.trade_date, 3, provider)
        future_5y = _future_price(security.id, anchor_row.trade_date, 5, provider)
        realized = _next_realized(full_history, fiscal_year)
        scores = _score_sample(anchor_adjusted, bear, base, bull, expected, future_1y, policy["BASE"], realized)
        leakage = {
            "cutoff": filing_date.isoformat(),
            "latest_input_filed_at": max((str(row.get("filed_at") or "") for row in history), default=""),
            "future_filings_excluded": all((_day(row.get("filed_at")) or filing_date) <= filing_date for row in history),
            "current_saved_assumptions_used": False,
            "future_prices_used_in_model": False,
            "future_prices_used_for_validation_only": True,
            "historical_price_basis": "split-adjusted for outcome comparison; raw for contemporaneous valuation calibration",
        }
        sample = HistoricalTestSample(
            run_id=run.id, anchor_date=anchor_row.trade_date, fiscal_year=fiscal_year, anchor_price=anchor_adjusted,
            bear_value=bear, base_value=base, bull_value=bull, expected_value=expected,
            inputs={"metrics": metrics, "calibration": calibration, "provider": provider, "raw_anchor_price": raw_anchor, "basis_factor": basis_factor},
            assumptions={"cases": {key: policy[key] for key in ("BEAR", "BASE", "BULL")}, "weights": policy["weights"], "horizon_years": policy["horizon_years"], "company_type": company_type},
            outcomes={"price_1y": future_1y, "price_3y": future_3y, "price_5y": future_5y, "realized_next_fy": realized},
            scores=scores, leakage_checks=leakage, status="DONE" if future_1y is not None else "PENDING_OUTCOME",
        )
        db.session.add(sample)
        if scores.get("reliability") is not None:
            reliability_rows.append(scores)

    run.sample_size = len(reliability_rows)
    for field in ("valuation_accuracy", "direction_accuracy", "range_coverage", "assumption_accuracy"):
        values = [n(row.get(field)) for row in reliability_rows if n(row.get(field)) is not None]
        setattr(run, field, mean(values) if values else None)
    values = [n(row.get("reliability")) for row in reliability_rows if n(row.get("reliability")) is not None]
    run.reliability_score = mean(values) if values else None
    run.status = validation_state(
        exists=True,
        execution_status="COMPLETED",
        sample_size=run.sample_size,
        reliability=run.reliability_score,
    )
    run.summary = {
        "anchors_considered": len(anchors), "completed_samples": run.sample_size,
        "anti_leakage": "Future filings and prices are excluded from each model snapshot. Future prices enter validation only.",
        "provider": provider, "company_type": company_type,
    }
    run.finished_at = utcnow()
    db.session.commit()
    return {"run_id": run.id, "status": run.status, "sample_size": run.sample_size, "reliability_score": n(run.reliability_score), "provider": provider}


__all__ = ["annual_history_asof", "run_historical_test"]
