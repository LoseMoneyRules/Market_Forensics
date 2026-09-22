from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import mean
from typing import Any

from sqlalchemy import or_

from .calculations import financial_metrics
from .core_models import FinancialPeriod, NormalizedFinancial, ValuationModel
from .extensions import db
from .economic_reality import DURATION_TAGS as ECONOMIC_DURATION_TAGS, INSTANT_TAGS as ECONOMIC_INSTANT_TAGS, build_economic_reality

FLOW_FIELDS = (
    "revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
    "pretax_income", "income_tax", "net_income", "cfo", "capex", "fcf",
    "buybacks", "dividends",
)
INSTANT_FIELDS = ("cash", "debt", "receivables", "inventory", "payables", "assets", "liabilities", "equity", "shares_outstanding")
NORMALIZED_FIELDS = FLOW_FIELDS + INSTANT_FIELDS + ("diluted_shares",)


def _normalized_richness(normalized: NormalizedFinancial) -> tuple[int, int]:
    populated = sum(1 for field in NORMALIZED_FIELDS if getattr(normalized, field, None) is not None)
    source_map = dict(normalized.source_map or {})
    sourced = sum(1 for field in NORMALIZED_FIELDS if source_map.get(field))
    return populated, sourced


def _period_candidate_score(period: FinancialPeriod, normalized: NormalizedFinancial, *, annual: bool) -> tuple:
    """Prefer canonical, evidence-rich rows when legacy duplicate identities share an end date.

    Old parser revisions could store the same represented period under a wrong fiscal
    year/quarter label. Those rows are audit evidence, but they must never win a live
    Research read merely because their database id is newer.
    """
    populated, sourced = _normalized_richness(normalized)
    year_match = int(bool(annual and period.end_date and int(period.fiscal_year or 0) == period.end_date.year))
    source_id = int(period.source_id or 0)
    filed = period.filed_at.toordinal() if period.filed_at else 0
    updated = normalized.updated_at.isoformat() if normalized.updated_at else ""
    return (year_match, populated, sourced, source_id, filed, updated, int(period.id or 0))


def _logical_period_type(value: str | None) -> str:
    raw = str(value or "").upper()
    if raw.startswith("SUPERSEDED_"):
        return raw.split("SUPERSEDED_", 1)[1]
    if raw.startswith("SUP_"):
        parts = raw.split("_")
        return parts[1] if len(parts) > 1 else raw
    return raw


def _period_family_filter(period_types: tuple[str, ...]):
    clauses = []
    for period_type in period_types:
        clauses.extend((
            FinancialPeriod.period_type == period_type,
            FinancialPeriod.period_type == f"SUPERSEDED_{period_type}",
            FinancialPeriod.period_type.like(f"SUP_{period_type}_%"),
        ))
    return or_(*clauses)


def _canonical_period_pairs(company_id: int, period_types: tuple[str, ...], *, annual: bool) -> list[tuple[FinancialPeriod, NormalizedFinancial]]:
    # Read every stored identity in the represented-period family, including
    # audit-preserved SUPERSEDED/SUP rows. Production can contain a rich legacy
    # row plus a sparse active shell after an older parser revision. Live reads
    # must choose the richest factual evidence, not blindly trust active/newest id.
    pairs = (
        db.session.query(FinancialPeriod, NormalizedFinancial)
        .join(NormalizedFinancial, NormalizedFinancial.financial_period_id == FinancialPeriod.id)
        .filter(FinancialPeriod.company_id == company_id, _period_family_filter(period_types))
        .order_by(FinancialPeriod.end_date.desc(), FinancialPeriod.id.desc())
        .all()
    )
    by_end: dict[date, tuple[FinancialPeriod, NormalizedFinancial]] = {}
    for period, normalized in pairs:
        current = by_end.get(period.end_date)
        if current is None or _period_candidate_score(period, normalized, annual=annual) > _period_candidate_score(current[0], current[1], annual=annual):
            by_end[period.end_date] = (period, normalized)
    return sorted(
        by_end.values(),
        key=lambda pair: (pair[0].end_date, pair[0].filed_at or date.min, pair[0].id),
        reverse=True,
    )


def canonical_annual_pairs(company_id: int) -> list[tuple[FinancialPeriod, NormalizedFinancial]]:
    return _canonical_period_pairs(company_id, ("FY",), annual=True)


def canonical_quarter_pairs(company_id: int) -> list[tuple[FinancialPeriod, NormalizedFinancial]]:
    return _canonical_period_pairs(company_id, ("Q1", "Q2", "Q3", "Q4"), annual=False)


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def _period_row(period: FinancialPeriod, normalized: NormalizedFinancial) -> dict[str, Any]:
    logical_type = _logical_period_type(period.period_type)
    quality = dict(normalized.quality or {})
    if logical_type != str(period.period_type or ""):
        quality.setdefault("canonical_read_recovered_identity", str(period.period_type or ""))
    row = {
        "period_id": period.id,
        "period_type": logical_type,
        "fiscal_year": period.fiscal_year,
        "period_end": period.end_date.isoformat(),
        "filed_at": period.filed_at.isoformat() if period.filed_at else None,
        "period_label": f"FY{period.fiscal_year}" if logical_type == "FY" else f"{logical_type} FY{period.fiscal_year}",
        "source_map": normalized.source_map or {},
        "quality": quality,
    }
    for field in FLOW_FIELDS + INSTANT_FIELDS + ("diluted_shares",):
        row[field] = n(getattr(normalized, field, None))
    return row


def annual_rows(company_id: int, limit: int = 15) -> list[dict[str, Any]]:
    pairs = canonical_annual_pairs(company_id)[:max(1, limit)]
    rows = [_period_row(period, normalized) for period, normalized in pairs]
    chronological = list(reversed(rows))
    previous: dict[str, Any] = {}
    for row in chronological:
        row["metrics"] = financial_metrics(row, previous)
        previous = row
    return list(reversed(chronological))


ANNUAL_HISTORY_TARGET_YEARS = 10
ANNUAL_HISTORY_DISPLAY_YEARS = 16


def _annual_history_coverage_from_rows(
    rows: list[dict[str, Any]],
    *,
    target_years: int = ANNUAL_HISTORY_TARGET_YEARS,
) -> dict[str, Any]:
    years = sorted({int(row["fiscal_year"]) for row in rows if row.get("fiscal_year") is not None}, reverse=True)
    if not years:
        return {
            "target_years": target_years, "stored_years": 0, "available_years": [],
            "target_window": [], "missing_years": [], "complete": False,
            "oldest_year": None, "latest_year": None, "continuous_span_years": 0,
        }
    latest = years[0]
    target_window = list(range(latest, latest - target_years, -1))
    available = set(years)
    missing = [year for year in target_window if year not in available]
    continuous = 0
    for year in target_window:
        if year not in available:
            break
        continuous += 1
    return {
        "target_years": target_years,
        "stored_years": len(years),
        "available_years": years,
        "target_window": target_window,
        "missing_years": missing,
        "complete": len(missing) == 0,
        "oldest_year": min(years),
        "latest_year": latest,
        "continuous_span_years": continuous,
    }


def annual_history_coverage(
    company_id: int,
    *,
    target_years: int = ANNUAL_HISTORY_TARGET_YEARS,
    display_years: int = ANNUAL_HISTORY_DISPLAY_YEARS,
) -> dict[str, Any]:
    rows = annual_rows(company_id, max(target_years, display_years))
    return _annual_history_coverage_from_rows(rows, target_years=target_years)


def annual_history_grid(
    company_id: int,
    *,
    target_years: int = ANNUAL_HISTORY_TARGET_YEARS,
    display_years: int = ANNUAL_HISTORY_DISPLAY_YEARS,
) -> list[dict[str, Any]]:
    """Display annual history without silently compressing missing fiscal years.

    Real normalized rows are preserved exactly. A missing year becomes an explicit
    placeholder row so the analyst can see the hole instead of reading a chart or
    table that jumps from one fiscal year to another without warning.
    """
    rows = annual_rows(company_id, max(target_years, display_years))
    if not rows:
        return []
    by_year: dict[int, dict[str, Any]] = {}
    for row in rows:
        year = int(row.get("fiscal_year") or 0)
        if year and year not in by_year:
            by_year[year] = row
    latest = max(by_year)
    oldest_actual = min(by_year)
    target_oldest = latest - max(1, int(target_years)) + 1
    display_oldest = latest - max(1, int(display_years)) + 1
    oldest = min(target_oldest, oldest_actual)
    oldest = max(oldest, display_oldest)
    out: list[dict[str, Any]] = []
    for year in range(latest, oldest - 1, -1):
        row = by_year.get(year)
        if row is not None:
            item = dict(row)
            item["missing_year"] = False
            item["history_status"] = "STORED"
            out.append(item)
            continue
        out.append({
            "period_id": None,
            "period_type": "FY",
            "fiscal_year": year,
            "period_end": None,
            "filed_at": None,
            "period_label": f"FY{year}",
            "source_map": {},
            "quality": {"history_status": "MISSING"},
            "metrics": {},
            "missing_year": True,
            "history_status": "MISSING",
            **{field: None for field in FLOW_FIELDS + INSTANT_FIELDS + ("diluted_shares",)},
        })
    return out


def quarterly_rows(company_id: int, limit: int = 12) -> list[dict[str, Any]]:
    pairs = canonical_quarter_pairs(company_id)[:max(8, limit)]
    rows = [_period_row(period, normalized) for period, normalized in pairs]

    lookup = {(row.get("fiscal_year"), row.get("period_type")): row for row in rows}
    for row in rows:
        prior = lookup.get(((row.get("fiscal_year") or 0) - 1, row.get("period_type"))) or {}
        row["metrics"] = financial_metrics(row, prior)
        row["comparison_basis"] = "SAME_QUARTER_PRIOR_YEAR" if prior else "NO_PRIOR_QUARTER"
    return rows[:max(4, limit)]


def _quarter_sequence_value(row: dict[str, Any]) -> int | None:
    quarter = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}.get(str(row.get("period_type") or "").upper())
    try:
        fiscal_year = int(row.get("fiscal_year"))
    except (TypeError, ValueError):
        return None
    return fiscal_year * 4 + quarter if quarter else None



def _ttm_economic_reality(rows: list[dict[str, Any]], statement: dict[str, Any]) -> dict[str, Any] | None:
    if not rows:
        return None
    snapshots = [dict(((row.get("quality") or {}).get("economic_reality") or {})) for row in rows]
    if not any(snapshots):
        return None

    facts: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for key in ECONOMIC_DURATION_TAGS:
        values = [n((snapshot.get("facts") or {}).get(key)) for snapshot in snapshots]
        if all(value is not None for value in values):
            facts[key] = sum(values)
            sources[key] = {"method": "FOUR_STORED_QUARTERS"}
    latest = snapshots[-1]
    for key in ECONOMIC_INSTANT_TAGS:
        value = n((latest.get("facts") or {}).get(key))
        if value is not None:
            facts[key] = value
            sources[key] = dict((latest.get("fact_sources") or {}).get(key) or {"method": "LATEST_QUARTER"})

    result = build_economic_reality(statement, facts=facts, fact_sources=sources)
    # Sector-policy flags are classification facts rather than arithmetic facts.
    for flag in latest.get("flags") or []:
        if str(flag.get("code") or "") == "SECTOR_BALANCE_SHEET_POLICY":
            if not any(str(row.get("code") or "") == "SECTOR_BALANCE_SHEET_POLICY" for row in result["flags"]):
                result["flags"].append(dict(flag))
            result["suppressions"] = sorted(set(result["suppressions"]) | set(latest.get("suppressions") or []))
    return result


def _aggregate_quarters(rows: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    if len(rows) < 4:
        return None
    rows = sorted(rows[:4], key=lambda x: x.get("period_end") or "")
    sequence = [_quarter_sequence_value(row) for row in rows]
    if any(value is None for value in sequence) or len(set(sequence)) != 4:
        return None
    if any(sequence[idx] - sequence[idx - 1] != 1 for idx in range(1, 4)):
        return None
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
        if out[field] is not None:
            out["source_map"][field] = {
                "method": "FOUR_STORED_QUARTERS",
                "quarter_period_ids": [row.get("period_id") for row in rows],
                "quarter_sources": [
                    (row.get("source_map") or {}).get(field) for row in rows
                ],
            }
    # A structurally consecutive quarter set is not automatically a usable TTM.
    # Revenue is the operating anchor for Current Financial Anatomy, Expectations
    # and Financial Flows. Returning an all-empty TTM here would hide a valid FY
    # fallback and cascade blank surfaces across Research.
    if n(out.get("revenue")) is None:
        return None
    latest = rows[-1]
    for field in INSTANT_FIELDS:
        out[field] = n(latest.get(field))
        if out[field] is not None:
            source_ref = (latest.get("source_map") or {}).get(field)
            out["source_map"][field] = (
                dict(source_ref) if isinstance(source_ref, dict)
                else {"method": "LATEST_QUARTER_INSTANT", "source": source_ref}
            )
            out["source_map"][field].setdefault("method", "LATEST_QUARTER_INSTANT")
            out["source_map"][field]["quarter_period_id"] = latest.get("period_id")
    shares = [n(row.get("diluted_shares")) for row in rows if n(row.get("diluted_shares")) is not None]
    out["diluted_shares"] = mean(shares) if len(shares) == 4 else None
    if out["diluted_shares"] is not None:
        out["source_map"]["diluted_shares"] = {
            "method": "MEAN_OF_FOUR_QUARTER_WEIGHTED_AVERAGES",
            "quarter_period_ids": [row.get("period_id") for row in rows],
            "quarter_sources": [(row.get("source_map") or {}).get("diluted_shares") for row in rows],
        }
    elif n(latest.get("shares_outstanding")) is not None:
        out["diluted_shares"] = n(latest.get("shares_outstanding"))
        out["source_map"]["diluted_shares"] = {
            "method": "LATEST_SHARES_OUTSTANDING_FALLBACK",
            "quarter_period_id": latest.get("period_id"),
            "source": (latest.get("source_map") or {}).get("shares_outstanding"),
        }
    economic = _ttm_economic_reality(rows, out)
    if economic is not None:
        out["quality"]["economic_reality"] = economic
    return out


def current_row(company_id: int) -> dict[str, Any] | None:
    quarters = quarterly_rows(company_id, 8)
    ttm = _aggregate_quarters(quarters[:4], f"TTM · {quarters[0]['period_end']}" if quarters else "TTM")
    annual = annual_rows(company_id, 1)
    latest_fy = dict(annual[0]) if annual else None

    # "Current" means the freshest filed economic basis, not "TTM at any cost".
    # A newly filed 10-K can be newer than the latest reconstructable TTM when Q4
    # quarter synthesis is incomplete. On the same end date, FY is also preferred:
    # its duration totals are equivalent to the fiscal-year TTM while its audited
    # balance-sheet instants are usually richer and more authoritative.
    if latest_fy:
        try:
            fy_end = date.fromisoformat(str(latest_fy.get("period_end") or "")[:10])
        except (TypeError, ValueError):
            fy_end = None
        try:
            ttm_end = date.fromisoformat(str((ttm or {}).get("period_end") or "")[:10]) if ttm else None
        except (TypeError, ValueError):
            ttm_end = None
        if ttm is None or (fy_end is not None and (ttm_end is None or fy_end >= ttm_end)):
            latest_fy["comparison_basis"] = "LATEST_FILED_FY"
            return latest_fy

    if ttm:
        prior = _aggregate_quarters(quarters[4:8], "Prior TTM") if len(quarters) >= 8 else None
        ttm["metrics"] = financial_metrics(ttm, prior or {})
        ttm["comparison_basis"] = "PRIOR_TTM" if prior else "NO_PRIOR_TTM"
        return ttm
    if latest_fy:
        latest_fy["comparison_basis"] = "FY_FALLBACK"
        return latest_fy
    return None


def history_with_current(company_id: int, annual_limit: int = 15) -> list[dict[str, Any]]:
    annual = list(reversed(annual_rows(company_id, annual_limit)))  # chronological
    current = current_row(company_id)
    if current and current.get("period_type") == "TTM":
        latest_end = annual[-1].get("period_end") if annual else None
        if current.get("period_end") != latest_end:
            annual.append(current)
    return annual


def numbers_completeness(company_id: int) -> dict[str, Any]:
    quarters = quarterly_rows(company_id, 12)
    annual = annual_rows(company_id, ANNUAL_HISTORY_DISPLAY_YEARS)
    current = current_row(company_id)
    metrics = dict((current or {}).get("metrics") or {})

    # Statement anchors should be present for a full operating-company study.
    core = ("revenue", "gross_profit", "operating_income", "net_income", "cfo", "capex", "fcf")
    missing_current = [field for field in core if not current or n(current.get(field)) is None]

    # Balance-sheet items are applicability-aware: if a recent filed annual period
    # reported the field, its disappearance from the current basis is an ingestion
    # gap worth surfacing. A business that never reports inventory is not penalized.
    continuity_fields = ("cash", "debt", "receivables", "inventory", "payables", "assets", "liabilities", "equity", "shares_outstanding", "diluted_shares")
    historically_present = {
        field for field in continuity_fields
        if any(n(row.get(field)) is not None for row in annual[:3])
    }
    missing_continuity = [
        field for field in continuity_fields
        if field in historically_present and (not current or n(current.get(field)) is None)
    ]

    derived_labels = {
        "gross_margin_pct": "gross margin",
        "dso": "DSO",
        "dio": "DIO",
        "dpo": "DPO",
        "cash_conversion_days": "CCC",
        "inventory_to_revenue_pct": "Inventory / Revenue",
        "receivables_to_revenue_pct": "Receivables / Revenue",
        "cfo_to_net_income": "CFO / Net Income",
        "roic_pct": "ROIC",
        "net_debt_to_fcf": "Net debt / FCF",
    }
    historical_metric_presence = {
        key for key in derived_labels
        if any(n((row.get("metrics") or {}).get(key)) is not None for row in annual[:3])
    }
    missing_derived = [
        derived_labels[key] for key in derived_labels
        if key in historical_metric_presence and n(metrics.get(key)) is None
    ]

    latest_quarters = quarters[:4]
    quarter_gaps = []
    if len(latest_quarters) < 4:
        quarter_gaps.append(f"Only {len(latest_quarters)} stored quarter(s) are available for current TTM.")
    else:
        ordered = sorted(latest_quarters, key=lambda row: row.get("period_end") or "")
        seq = [_quarter_sequence_value(row) for row in ordered]
        if any(value is None for value in seq) or any(seq[idx] - seq[idx - 1] != 1 for idx in range(1, len(seq))):
            quarter_gaps.append("Latest four stored quarters are not a consecutive fiscal sequence; TTM is withheld.")
        elif any(n(row.get("revenue")) is None for row in ordered):
            quarter_gaps.append("Latest four quarters do not all contain Revenue; TTM is withheld and the latest filed annual basis remains active.")

    ttm_ready = bool(current and current.get("period_type") == "TTM")
    normalized_fields = FLOW_FIELDS + INSTANT_FIELDS + ("diluted_shares",)
    historically_expected = {
        field for field in normalized_fields
        if any(n(row.get(field)) is not None for row in annual[:3])
    }
    missing_expected_fields = [
        field for field in normalized_fields
        if field in historically_expected and (not current or n(current.get(field)) is None)
    ]
    source_map = dict((current or {}).get("source_map") or {})
    source_covered_fields = sorted(
        field for field in normalized_fields
        if current and n(current.get(field)) is not None and source_map.get(field)
    )
    critical_unresolved_count = len(set(missing_current) | set(missing_continuity)) + len(missing_derived)
    unresolved_count = len(set(missing_current) | set(missing_continuity) | set(missing_expected_fields)) + len(missing_derived)
    economic = dict(((current or {}).get("quality") or {}).get("economic_reality") or {})
    economic_ready = bool(economic) and not bool(economic.get("material_unresolved"))
    annual_history = _annual_history_coverage_from_rows(annual)
    return {
        "annual_count": len(annual),
        "annual_history_target_years": annual_history["target_years"],
        "annual_history_stored_years": annual_history["stored_years"],
        "annual_history_missing_years": annual_history["missing_years"],
        "annual_history_target_window": annual_history["target_window"],
        "annual_history_complete": annual_history["complete"],
        "annual_history_continuous_span_years": annual_history["continuous_span_years"],
        "quarter_count": len(quarters),
        "current_basis": current.get("period_type") if current else None,
        "current_label": current.get("period_label") if current else None,
        "missing_current_fields": missing_current,
        "missing_continuity_fields": missing_continuity,
        "missing_derived_metrics": missing_derived,
        "missing_expected_fields": missing_expected_fields,
        "normalized_field_count": len(normalized_fields),
        "current_populated_field_count": sum(1 for field in normalized_fields if current and n(current.get(field)) is not None),
        "source_covered_field_count": len(source_covered_fields),
        "source_covered_fields": source_covered_fields,
        "unresolved_count": unresolved_count,
        "critical_unresolved_count": critical_unresolved_count,
        "economic_reality_ready": economic_ready,
        "quarter_gaps": quarter_gaps,
        "ttm_ready": ttm_ready,
        "analysis_ready": bool(ttm_ready and critical_unresolved_count == 0 and economic_ready and annual_history["complete"]),
    }


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


__all__ = ["annual_rows", "annual_history_grid", "annual_history_coverage", "quarterly_rows", "current_row", "history_with_current", "numbers_completeness", "forecast_rows", "scenario_forecasts"]
