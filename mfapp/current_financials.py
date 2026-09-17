from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import mean
from typing import Any

from .calculations import financial_metrics
from .core_models import FinancialPeriod, NormalizedFinancial, ValuationModel
from .extensions import db

FLOW_FIELDS = (
    "revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
    "pretax_income", "income_tax", "net_income", "cfo", "capex", "fcf",
    "buybacks", "dividends",
)
INSTANT_FIELDS = ("cash", "debt", "receivables", "inventory", "payables", "assets", "liabilities", "equity", "shares_outstanding")


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _period_row(period: FinancialPeriod, normalized: NormalizedFinancial) -> dict[str, Any]:
    row = {
        "period_id": period.id,
        "period_type": period.period_type,
        "fiscal_year": period.fiscal_year,
        "period_end": period.end_date.isoformat(),
        "filed_at": period.filed_at.isoformat() if period.filed_at else None,
        "period_label": f"FY{period.fiscal_year}" if period.period_type == "FY" else f"{period.period_type} FY{period.fiscal_year}",
        "source_map": normalized.source_map or {},
        "quality": normalized.quality or {},
    }
    for field in FLOW_FIELDS + INSTANT_FIELDS + ("diluted_shares",):
        row[field] = n(getattr(normalized, field, None))
    return row


def annual_rows(company_id: int, limit: int = 15) -> list[dict[str, Any]]:
    periods = FinancialPeriod.query.filter_by(company_id=company_id, period_type="FY").order_by(FinancialPeriod.end_date.desc(), FinancialPeriod.id.desc()).limit(max(1, limit)).all()
    rows: list[dict[str, Any]] = []
    for period in periods:
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if normalized:
            rows.append(_period_row(period, normalized))
    chronological = list(reversed(rows))
    previous: dict[str, Any] = {}
    for row in chronological:
        row["metrics"] = financial_metrics(row, previous)
        previous = row
    return list(reversed(chronological))


def quarterly_rows(company_id: int, limit: int = 12) -> list[dict[str, Any]]:
    periods = FinancialPeriod.query.filter(FinancialPeriod.company_id == company_id, FinancialPeriod.period_type.in_(["Q1", "Q2", "Q3", "Q4"])).order_by(FinancialPeriod.end_date.desc(), FinancialPeriod.id.desc()).limit(max(4, limit)).all()
    rows: list[dict[str, Any]] = []
    seen: set[date] = set()
    for period in periods:
        if period.end_date in seen:
            continue
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        if not normalized:
            continue
        seen.add(period.end_date)
        rows.append(_period_row(period, normalized))
    return rows


def _aggregate_quarters(rows: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    if len(rows) < 4:
        return None
    rows = sorted(rows[:4], key=lambda x: x.get("period_end") or "")
    first_end = date.fromisoformat(rows[0]["period_end"])
    last_end = date.fromisoformat(rows[-1]["period_end"])
    if (last_end - first_end).days > 370:
        return None
    out: dict[str, Any] = {
        "period_id": None,
        "period_type": "TTM",
        "fiscal_year": rows[-1].get("fiscal_year"),
        "period_end": rows[-1]["period_end"],
        "filed_at": max((row.get("filed_at") or "" for row in rows), default="") or None,
        "period_label": label,
        "source_map": {"basis": "FOUR_STORED_QUARTERS", "quarter_period_ids": [row.get("period_id") for row in rows]},
        "quality": {"basis": "TTM", "quarter_count": 4},
    }
    for field in FLOW_FIELDS:
        values = [n(row.get(field)) for row in rows]
        out[field] = sum(values) if all(value is not None for value in values) else None
    latest = rows[-1]
    for field in INSTANT_FIELDS:
        out[field] = n(latest.get(field))
    shares = [n(row.get("diluted_shares")) for row in rows if n(row.get("diluted_shares")) is not None]
    out["diluted_shares"] = mean(shares) if shares else n(latest.get("shares_outstanding"))
    return out


def current_row(company_id: int) -> dict[str, Any] | None:
    quarters = quarterly_rows(company_id, 8)
    current = _aggregate_quarters(quarters[:4], f"TTM · {quarters[0]['period_end']}" if quarters else "TTM")
    if current:
        prior = _aggregate_quarters(quarters[4:8], "Prior TTM") if len(quarters) >= 8 else None
        current["metrics"] = financial_metrics(current, prior or {})
        current["comparison_basis"] = "PRIOR_TTM" if prior else "NO_PRIOR_TTM"
        return current
    annual = annual_rows(company_id, 1)
    if annual:
        row = dict(annual[0])
        row["comparison_basis"] = "FY_FALLBACK"
        return row
    return None


def history_with_current(company_id: int, annual_limit: int = 15) -> list[dict[str, Any]]:
    annual = list(reversed(annual_rows(company_id, annual_limit)))  # chronological
    current = current_row(company_id)
    if current and current.get("period_type") == "TTM":
        latest_end = annual[-1].get("period_end") if annual else None
        if current.get("period_end") != latest_end:
            annual.append(current)
    return annual


def forecast_rows(company_id: int, model: ValuationModel | None, years: int = 3, scenario_name: str = "BASE") -> list[dict[str, Any]]:
    base = current_row(company_id)
    if not base:
        return []
    scenario = None
    if model:
        scenario = next((row for row in model.scenarios if str(row.name).upper() == str(scenario_name).upper()), None)
    inputs = dict((scenario.inputs or {}) if scenario else {})
    growth = n(inputs.get("growth"))
    if growth is None:
        latest_growth = n((base.get("metrics") or {}).get("revenue_growth_pct"))
        growth = (latest_growth / 100.0) if latest_growth is not None else 0.03
    if abs(growth) > 1:
        growth /= 100.0
    growth = max(-0.20, min(0.30, growth))
    revenue0 = n(base.get("revenue"))
    if revenue0 in (None, 0):
        return []
    op_margin = n((base.get("metrics") or {}).get("operating_margin_pct"))
    op_margin = op_margin / 100.0 if op_margin is not None else None
    net_margin = n(inputs.get("net_margin"))
    fcf_margin = n(inputs.get("fcf_margin"))
    if net_margin is None:
        nm = n((base.get("metrics") or {}).get("net_margin_pct")); net_margin = nm / 100.0 if nm is not None else None
    if fcf_margin is None:
        fm = n((base.get("metrics") or {}).get("fcf_margin_pct")); fcf_margin = fm / 100.0 if fm is not None else None
    if net_margin is not None and abs(net_margin) > 1: net_margin /= 100.0
    if fcf_margin is not None and abs(fcf_margin) > 1: fcf_margin /= 100.0
    start_year = int(base.get("fiscal_year") or date.today().year)
    out: list[dict[str, Any]] = []
    revenue = revenue0
    for step in range(1, max(1, min(int(years), 5)) + 1):
        revenue *= 1.0 + growth
        out.append({
            "period_label": f"FY{start_year + step}E",
            "fiscal_year": start_year + step,
            "period_type": "FORECAST",
            "revenue": revenue,
            "operating_income": revenue * op_margin if op_margin is not None else None,
            "net_income": revenue * net_margin if net_margin is not None else None,
            "fcf": revenue * fcf_margin if fcf_margin is not None else None,
            "growth": growth,
            "operating_margin": op_margin,
            "net_margin": net_margin,
            "fcf_margin": fcf_margin,
            "source": f"{str(scenario_name).upper()}_CASE_MODEL",
        })
    return out


def scenario_forecasts(company_id: int, model: ValuationModel | None, years: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Five-year operating paths kept distinct from valuation-method outputs."""
    return {name: forecast_rows(company_id, model, years, name) for name in ("BEAR", "BASE", "BULL")}


__all__ = ["annual_rows", "quarterly_rows", "current_row", "history_with_current", "forecast_rows", "scenario_forecasts"]
