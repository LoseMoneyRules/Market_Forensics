from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from .core_models import Coverage, Security, ValuationModel
from .data_providers import get_secret
from .discovery_engine import classify_coverage
from .discovery_forensics import (
    FORENSIC_EDGE_PCT, FORENSIC_ENRICH_LIMIT, FORENSIC_STRONG_EDGE_PCT,
    FORENSIC_WATCH_EDGE_PCT, discovery_opportunity, enrich_forensic_candidates,
)
from .discovery_universe import MAJOR_EXCHANGES, stage0_universe, stage1_screen
from .extensions import db
from .research_cache import cache_is_stale, latest_cache_map
from .services import valuation_result
from .valuation_engine import valuation_base_quality

MIN_LONG_PRICE = 5.0
MIN_SHORT_PRICE = 10.0
MIN_DOLLAR_VOLUME = 15_000_000.0
MIN_DAILY_VOLUME = 200_000.0
MAX_PER_SIDE = 10
MAX_WATCH = 10
CONTRACT_VERSION = "BROAD_FORENSIC_DISCOVERY_V2"


def _headers(user_id: int) -> dict[str, str] | None:
    key = get_secret(user_id, "alpaca_key")
    secret = get_secret(user_id, "alpaca_secret")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _n(value: Any) -> float | None:
    try:
        out = float(value) if value is not None else None
        return out if out is None or out == out else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _valuation_method_count(model: ValuationModel | None) -> int:
    if model is None:
        return 0
    assumptions = dict(model.assumptions or {})
    latest = dict(assumptions.get("latest_engine_result") or {})
    base = dict((latest.get("scenarios") or {}).get("BASE") or {})
    if not base:
        try:
            scenario = next((row for row in model.scenarios if str(row.name or "").upper() == "BASE"), None)
            base = dict(scenario.outputs or {}) if scenario is not None else {}
        except Exception:
            base = {}
    return sum(1 for key in ("pe", "ev_sales", "fcf_yield") if _n(base.get(key)) is not None)


def _coverage_context_map(user_id: int, symbols: set[str]) -> dict[str, dict[str, Any]]:
    """Batch-read materialized Research context only; no provider work."""
    if not symbols:
        return {}
    rows = (
        db.session.query(Coverage, Security)
        .join(Security, Coverage.security_id == Security.id)
        .filter(
            Coverage.user_id == user_id,
            Coverage.status != "ARCHIVED",
            Security.active.is_(True),
            db.func.upper(Security.ticker).in_(sorted(symbols)),
        )
        .all()
    )
    if not rows:
        return {}

    coverage_ids = [coverage.id for coverage, _ in rows]
    caches = latest_cache_map(coverage_ids)
    model_rows = ValuationModel.query.filter(
        ValuationModel.coverage_id.in_(coverage_ids),
        ValuationModel.is_active.is_(True),
    ).order_by(ValuationModel.id.desc()).all()
    models: dict[int, ValuationModel] = {}
    for model in model_rows:
        models.setdefault(model.coverage_id, model)

    out: dict[str, dict[str, Any]] = {}
    for coverage, security in rows:
        cache = dict(caches.get(coverage.id) or {})
        model = models.get(coverage.id)
        valuation = dict(cache.get("valuation") or {})
        if cache and cache_is_stale(cache, coverage, model):
            valuation["base_quality"] = "DATA_WARNING"
            valuation["quality"] = "DATA_WARNING"
            valuation["decision_grade"] = False
        elif not valuation.get("base_quality"):
            live_valuation = valuation_result(coverage)
            for key in ("quality", "base_quality", "decision_grade"):
                valuation[key] = live_valuation.get(key)

        intelligence = dict(cache.get("intelligence") or {})
        intelligence["valuation_base_quality"] = valuation.get("base_quality") or "DATA_WARNING"
        intelligence["valuation_decision_grade"] = bool(valuation.get("decision_grade"))
        out[security.ticker.upper()] = {
            "known": True,
            "coverage_id": coverage.id,
            "company_id": security.company_id,
            "security_id": security.id,
            "base_gap_pct": intelligence.get("base_gap_pct"),
            "readiness": dict(cache.get("readiness") or {}),
            "decision_lenses": dict(cache.get("decision_lenses") or {}),
            "discovery_labels": classify_coverage(intelligence, dict(cache.get("readiness") or {})),
            "valuation": {
                "current_price": valuation.get("current_price"),
                "bear": valuation.get("bear"),
                "base": valuation.get("base"),
                "bull": valuation.get("bull"),
                "quality": valuation.get("quality"),
                "base_quality": valuation.get("base_quality"),
                "decision_grade": valuation.get("decision_grade"),
                "warnings": list(valuation.get("warnings") or []),
            },
            "valuation_methods": _valuation_method_count(model),
            "cache_ready": bool(cache),
            "cache_generated_at": cache.get("_generated_at"),
            "market_as_of": cache.get("market_as_of"),
        }
    return out


def _active_coverage_tickers(user_id: int) -> set[str]:
    return {
        str(ticker or "").upper()
        for (ticker,) in (
            db.session.query(Security.ticker)
            .join(Coverage, Coverage.security_id == Security.id)
            .filter(
                Coverage.user_id == user_id,
                Coverage.status != "ARCHIVED",
                Security.active.is_(True),
            )
            .all()
        )
        if ticker
    }


def _discovery_health(universe: dict[str, Any], stage1: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []
    severity = "OK"
    current = int(universe.get("member_count") or 0)
    previous = int(universe.get("previous_member_count") or 0)
    if not current:
        severity = "CRITICAL"
        flags.append("Eligible Stage 0 universe is empty.")
    if universe.get("stale_cache"):
        severity = "WARN" if severity == "OK" else severity
        flags.append("Stage 0 is using stale cached universe data.")
    if previous and current < int(previous * 0.70):
        severity = "CRITICAL"
        flags.append(f"Stage 0 universe fell from {previous} to {current} names (>30% drop).")

    requested = int(stage1.get("snapshot_requested_count") or 0)
    received = int(stage1.get("snapshot_received_count") or 0)
    snapshot_pct = (received / requested * 100.0) if requested else 0.0
    if requested >= 20 and snapshot_pct < 75.0:
        severity = "WARN" if severity == "OK" else severity
        flags.append(f"Only {received}/{requested} requested market snapshots were returned.")

    exclusions = dict(stage1.get("excluded_breakdown") or {})
    total_stage1_rejected = sum(int(v or 0) for v in exclusions.values())
    if total_stage1_rejected >= 20 and exclusions:
        dominant_reason, dominant_count = max(exclusions.items(), key=lambda item: int(item[1] or 0))
        dominant_pct = int(dominant_count or 0) / total_stage1_rejected * 100.0
        if dominant_pct >= 90.0:
            severity = "WARN" if severity == "OK" else severity
            flags.append(f"Stage 1 exclusions are unusually concentrated: {dominant_reason} = {dominant_pct:.0f}%.")

    return {
        "status": severity,
        "flags": flags,
        "stage0_count": current,
        "previous_stage0_count": previous or None,
        "snapshot_requested_count": requested,
        "snapshot_received_count": received,
        "snapshot_success_pct": round(snapshot_pct, 1),
    }


def _scan_cadence(health: dict[str, Any], coverage_progress: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pct_30d = _n(coverage_progress.get("pct_30d")) or 0.0
    health_status = str(health.get("status") or "OK")
    if health_status == "CRITICAL":
        days = 1
        reason = "Re-run tomorrow because the last universe/provider health check found a critical anomaly."
    elif health_status == "WARN":
        days = 3
        reason = "Re-run in about 3 days because the last universe/provider health check needs confirmation."
    elif pct_30d < 25.0:
        days = 3
        reason = "Run every ~3 days until the rotating broad-universe baseline has reasonable recent coverage."
    else:
        days = 7
        reason = "Weekly is the normal cadence once broad-universe coverage is established."
    return {
        "recommended_interval_days": days,
        "next_due_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "reason": reason,
        "rule": "Sooner after a failed/partial scan; otherwise build breadth first, then weekly.",
    }


def _select_stage2_finalists(
    rows: list[dict[str, Any]],
    local_context: dict[str, dict[str, Any]],
    *,
    limit: int = FORENSIC_ENRICH_LIMIT,
) -> list[dict[str, Any]]:
    """Allocate bounded deep-enrichment budget across valuation, quiet and activity lanes.

    Discovery is allowed to surface research leads before Validation is complete.
    The finalist selector therefore prioritizes known valuation dislocations from
    the WATCH edge onward, while reserving most capacity for quiet broad rotation.
    """
    known_edge: list[dict[str, Any]] = []
    quiet_broad: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    remainder: list[dict[str, Any]] = []

    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        context = local_context.get(ticker) or {}
        lanes = set(row.get("stage1_lanes") or [])
        gap = _n(context.get("base_gap_pct"))
        item = dict(row)
        if context:
            item["known_context"] = context
            item["in_coverage"] = True
        else:
            item["known_context"] = {}
            item["in_coverage"] = False

        if gap is not None and abs(gap) >= FORENSIC_WATCH_EDGE_PCT:
            item["stage2_selection_reason"] = "Stored Base gap is already at or beyond the Discovery WATCH edge."
            known_edge.append(item)
        elif "BROAD_ROTATION" in lanes and "MARKET_ACTIVITY" not in lanes:
            item["stage2_selection_reason"] = "Quiet liquid broad-universe name selected for forensic rotation."
            quiet_broad.append(item)
        elif "MARKET_ACTIVITY" in lanes:
            item["stage2_selection_reason"] = "Current market activity adds a secondary forensic investigation lane."
            activity.append(item)
        else:
            item["stage2_selection_reason"] = "Liquid Stage-1 name selected from the bounded remainder."
            remainder.append(item)

    known_edge.sort(key=lambda row: (-abs(_n((row.get("known_context") or {}).get("base_gap_pct")) or 0.0), row["ticker"]))
    quiet_broad.sort(key=lambda row: (abs(_n(row.get("move_pct")) or 0.0), -(_n(row.get("dollar_volume")) or 0.0), row["ticker"]))
    activity.sort(key=lambda row: (-abs(_n(row.get("move_pct")) or 0.0), int(row.get("activity_rank") or 9999), row["ticker"]))
    remainder.sort(key=lambda row: (-(_n(row.get("dollar_volume")) or 0.0), row["ticker"]))

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(items: list[dict[str, Any]], budget: int | None = None) -> None:
        taken = 0
        for item in items:
            if len(selected) >= limit:
                return
            ticker = item["ticker"]
            if ticker in seen:
                continue
            if budget is not None and taken >= budget:
                return
            selected.append(item)
            seen.add(ticker)
            taken += 1

    add(known_edge, 3)
    add(quiet_broad, 5)
    add(activity, 2)
    add(known_edge)
    add(quiet_broad)
    add(activity)
    add(remainder)
    return selected[:limit]


def _family_label(side: str, evidence: dict[str, Any]) -> str:
    labels = {str(row.get("label") or "").upper() for row in evidence.get("signals") or []}
    gap = _n(evidence.get("gap_pct")) or 0.0
    if side == "LONG":
        if gap >= 30 and labels.intersection({"OPERATING LEVERAGE", "CASH MARGIN INFLECTION", "EARNINGS → CASH"}):
            return "Quality at Discount"
        if labels.intersection({"REVENUE ACCELERATION", "OPERATING LEVERAGE", "CASH MARGIN INFLECTION"}):
            return "Fundamental Inflection"
        return "Valuation Dislocation"
    if labels.intersection({"INVENTORY BUILD", "RECEIVABLES BUILD", "WEAK CASH CONVERSION"}):
        return "Forensic Divergence"
    if labels.intersection({"REVENUE DETERIORATION", "OPERATING DELEVERAGE", "CASH MARGIN EROSION"}):
        return "Deterioration / Short Setup"
    return "Valuation Dislocation"


def _invalidation(side: str, priority: str = "P2") -> str:
    edge = FORENSIC_WATCH_EDGE_PCT if priority == "WATCH" else FORENSIC_EDGE_PCT
    if side == "LONG":
        return f"Discovery setup weakens if Base gap falls below +{edge:.0f}% or filed evidence materially contradicts the valuation thesis."
    return f"Discovery setup weakens if Base gap rises above -{edge:.0f}%; P1/P2 also require current short actionability."


def market_scan(user_id: int) -> dict[str, Any]:
    """Broad-universe Discovery with cheap rotation followed by bounded forensics."""
    headers = _headers(user_id)
    if not headers:
        return {
            "configured": False, "candidates": [], "long_candidates": [], "short_candidates": [],
            "errors": ["Alpaca credentials are not configured."], "contract_version": CONTRACT_VERSION,
        }

    errors: list[str] = []
    provider_calls: Counter = Counter()
    excluded = Counter()

    universe = stage0_universe(user_id, headers, errors, provider_calls)
    known_tickers = _active_coverage_tickers(user_id)
    stage1 = stage1_screen(
        user_id, headers, universe, errors, provider_calls,
        known_tickers=known_tickers,
        min_price=MIN_LONG_PRICE,
        min_daily_volume=MIN_DAILY_VOLUME,
        min_dollar_volume=MIN_DOLLAR_VOLUME,
    )
    stage1_rows = list(stage1.get("rows") or [])
    symbols = {str(row.get("ticker") or "").upper() for row in stage1_rows if row.get("ticker")}
    local_context = _coverage_context_map(user_id, symbols)
    finalists = _select_stage2_finalists(stage1_rows, local_context)

    rejection_log: list[dict[str, Any]] = []
    forensic_raw = enrich_forensic_candidates(
        user_id, finalists, local_context, errors, provider_calls, limit=FORENSIC_ENRICH_LIMIT,
        rejection_log=rejection_log,
    )
    if isinstance(forensic_raw, tuple):
        forensic, stage2_excluded = forensic_raw
    else:
        forensic, stage2_excluded = dict(forensic_raw or {}), {}
    excluded.update(stage2_excluded)

    candidates: list[dict[str, Any]] = []

    def reject_final(symbol: str, reason: str, detail: str = "", evidence: dict[str, Any] | None = None) -> None:
        if len(rejection_log) >= 24:
            return
        evidence = evidence or {}
        rejection_log.append({
            "ticker": symbol,
            "stage": "FINAL QUALIFICATION",
            "reason": reason,
            "detail": detail,
            "base": _n(evidence.get("base")),
            "base_gap_pct": _n(evidence.get("gap_pct")),
            "quality": str(evidence.get("quality") or ""),
            "valuation_methods": int(evidence.get("valuation_methods") or 0),
        })

    finalist_map = {str(row.get("ticker") or "").upper(): row for row in finalists}
    for symbol, item in finalist_map.items():
        evidence = dict(forensic.get(symbol) or {})
        if not evidence:
            if symbol not in forensic:
                excluded["NO FORENSIC FAIR VALUE / DATA"] += 1
                if not any(row.get("ticker") == symbol for row in rejection_log):
                    reject_final(symbol, "NO FORENSIC FAIR VALUE / DATA")
            continue

        basis_review = [str(x) for x in evidence.get("corporate_action_review") or [] if x]
        if basis_review:
            excluded["CORPORATE ACTION / BASIS REVIEW"] += 1
            reject_final(symbol, "CORPORATE ACTION / BASIS REVIEW", " ".join(basis_review), evidence)
            continue

        opportunity = discovery_opportunity(evidence)
        if opportunity is None:
            excluded["NO DISCOVERY EDGE"] += 1
            reject_final(
                symbol,
                "NO DISCOVERY EDGE",
                f"Needs at least ±{FORENSIC_WATCH_EDGE_PCT:.0f}% with operating confirmation or ±{FORENSIC_EDGE_PCT:.0f}% valuation dislocation.",
                evidence,
            )
            continue

        side = str(opportunity.get("side") or "").upper()
        priority = str(opportunity.get("priority") or "WATCH").upper()
        priority_rank = int(opportunity.get("priority_rank") or 3)
        forensic_score = int(opportunity.get("score") or 0)
        priority_reason = str(opportunity.get("reason") or "")
        operating_state = str(opportunity.get("operating_state") or "UNCONFIRMED")
        price = _n(item.get("price"))
        gap = _n(evidence.get("gap_pct"))
        fair = _n(evidence.get("base"))
        methods = int(evidence.get("valuation_methods") or 0)
        quality = str(evidence.get("quality") or "DATA_WARNING").upper()

        economic_reality = dict(evidence.get("economic_reality") or {})
        accounting_context = list(evidence.get("accounting_context") or [])
        warning_parts = [str(x) for x in evidence.get("warnings") or [] if x]
        if economic_reality.get("material_unresolved"):
            warning_parts.append("Economic Reality accounting classification is materially unresolved; candidate is WATCH-only until Research verification.")
        if quality != "INTRINSIC":
            warning_parts.append(f"Valuation quality is {quality}; treat this as a research lead, not a validated fair value.")
        if methods < 2:
            warning_parts.append(f"Only {methods} usable valuation method(s); Research must verify the Base.")

        if side == "SHORT":
            short_actionable = price is not None and price >= MIN_SHORT_PRICE and bool(item.get("shortable"))
            if priority in {"P1", "P2"} and not short_actionable:
                priority = "WATCH"
                priority_rank = 3
                priority_reason = "Valuation downside merits Research, but current short actionability is not verified."
                warning_parts.append("Short setup is WATCH-only until price/shortable requirements are met.")
            if item.get("shortable") and not item.get("easy_to_borrow"):
                warning_parts.append("Shortable flag is positive, but borrow depth/fee is not verified by Discovery.")

        operating_signals = [
            row for row in list(evidence.get("signals") or [])
            if str(row.get("side") or "").upper() == side
        ][:4]

        context = local_context.get(symbol) or {}
        base_label = "Intrinsic Base" if quality == "INTRINSIC" and methods >= 2 else "Indicative Base"
        reasons = [
            (base_label + " $" + format(fair, ",.2f")) if fair is not None else "Base unavailable",
            f"Base fair-value gap {gap:+.1f}%" if gap is not None else "Gap unavailable",
            priority_reason,
            str(item.get("stage2_selection_reason") or ""),
        ]
        reasons.extend(str(row.get("detail") or "") for row in operating_signals[:2])
        reasons.extend(str(row.get("detail") or "") for row in accounting_context[:2])
        reasons = [reason for reason in reasons if reason]

        candidate = dict(item)
        candidate.update({
            "research_side": side,
            "direction": side,
            "priority": priority,
            "priority_rank": priority_rank,
            "priority_reason": priority_reason,
            "opportunity_tier": priority,
            "operating_state": operating_state,
            "bear": _n(evidence.get("bear")),
            "fair_value": fair,
            "base": fair,
            "bull": _n(evidence.get("bull")),
            "base_gap_pct": gap,
            "fair_value_quality": quality,
            "valuation_methods": methods,
            "forensic_source": evidence.get("source"),
            "forensic_score": forensic_score,
            "forensic_signals": operating_signals,
            "operating_confirmation": (
                [str(row.get("detail") or "") for row in operating_signals]
                or [operating_state]
            ),
            "operating_snapshot": dict(evidence.get("snapshot") or {}),
            "economic_reality": economic_reality,
            "economic_reality_quality": str(economic_reality.get("quality") or "UNAVAILABLE"),
            "economic_reality_unresolved": bool(economic_reality.get("material_unresolved")),
            "accounting_context": accounting_context,
            "target_status": "ROOM TO BASE" if side == "LONG" else "ABOVE BASE",
            "radar_label": _family_label(side, evidence),
            "why_found": reasons,
            "what_invalidates": _invalidation(side, priority),
            "data_freshness": evidence.get("data_freshness"),
            "market_freshness": item.get("snapshot_as_of"),
            "materialized_at": evidence.get("materialized_at"),
            "freshness": {
                "market": item.get("snapshot_as_of"),
                "fundamentals": evidence.get("data_freshness"),
                "valuation": evidence.get("materialized_at"),
                "universe": universe.get("generated_at"),
            },
            "corporate_action_review": basis_review,
            "warning": " ".join(warning_parts),
            "known_context": context,
            "in_coverage": bool(context),
            "lenses": [str(row.get("label") or "") for row in operating_signals],
        })
        candidates.append(candidate)

    candidates.sort(key=lambda row: (
        int(row.get("priority_rank") or 9),
        -abs(_n(row.get("base_gap_pct")) or 0.0),
        -int(row.get("valuation_methods") or 0),
        -int(row.get("forensic_score") or 0),
        row["ticker"],
    ))
    long_candidates = [row for row in candidates if row["research_side"] == "LONG" and row.get("priority") != "WATCH"][:MAX_PER_SIDE]
    short_candidates = [row for row in candidates if row["research_side"] == "SHORT" and row.get("priority") != "WATCH"][:MAX_PER_SIDE]
    watch_candidates = [row for row in candidates if row.get("priority") == "WATCH"][:MAX_WATCH]
    final = long_candidates + short_candidates + watch_candidates

    health = _discovery_health(universe, stage1)
    coverage_progress = dict(stage1.get("coverage_progress") or {})
    cadence = _scan_cadence(health, coverage_progress)

    stage0_excluded = dict(universe.get("excluded_breakdown") or {})
    stage1_excluded = dict(stage1.get("excluded_breakdown") or {})
    combined_excluded = Counter(stage0_excluded)
    combined_excluded.update(stage1_excluded)
    combined_excluded.update(excluded)

    return {
        "configured": True,
        "candidates": final,
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "watch_candidates": watch_candidates,
        "errors": errors,
        "universe_source": "Cached Alpaca active US operating equities + rotating cheap Stage 1 + bounded SEC/filed Stage 2",
        "universe_generated_at": universe.get("generated_at"),
        "universe_cache_hit": bool(universe.get("cache_hit")),
        "stage0_count": int(universe.get("member_count") or 0),
        "stage0_raw_count": int(universe.get("raw_count") or 0),
        "stage0_excluded_count": int(universe.get("excluded_count") or 0),
        "stage0_excluded_breakdown": stage0_excluded,
        "stage1_scanned_count": int(stage1.get("scanned_count") or 0),
        "stage1_qualified_count": int(stage1.get("qualified_count") or 0),
        "stage1_broad_rotation_count": int(stage1.get("broad_rotation_count") or 0),
        "stage1_quiet_broad_count": int(stage1.get("quiet_broad_count") or 0),
        "stage1_activity_count": int(stage1.get("activity_count") or 0),
        "stage1_cursor_start": int(stage1.get("cursor_start") or 0),
        "stage1_cursor_end": int(stage1.get("cursor_end") or 0),
        "stage1_snapshot_requested_count": int(stage1.get("snapshot_requested_count") or 0),
        "stage1_snapshot_received_count": int(stage1.get("snapshot_received_count") or 0),
        "coverage_progress": coverage_progress,
        "universe_health": health,
        "scan_cadence": cadence,
        "stage2_selected_count": len(finalists),
        "stage2_enriched_count": len(forensic),
        "candidate_count": len(final),
        "known_enriched": sum(1 for row in final if row.get("in_coverage")),
        "long_count": len(long_candidates),
        "short_count": len(short_candidates),
        "watch_count": len(watch_candidates),
        "p1_count": sum(1 for row in final if row.get("priority") == "P1"),
        "p2_count": sum(1 for row in final if row.get("priority") == "P2"),
        "excluded_count": sum(combined_excluded.values()),
        "excluded_breakdown": dict(combined_excluded),
        "rejection_log": rejection_log,
        "provider_calls": dict(provider_calls),
        "provider_call_total": sum(provider_calls.values()),
        "guardrails": {
            "min_long_price": MIN_LONG_PRICE,
            "min_short_price": MIN_SHORT_PRICE,
            "min_dollar_volume": MIN_DOLLAR_VOLUME,
            "min_daily_volume": MIN_DAILY_VOLUME,
            "watch_edge_pct": FORENSIC_WATCH_EDGE_PCT,
            "fair_value_edge_pct": FORENSIC_EDGE_PCT,
            "strong_edge_pct": FORENSIC_STRONG_EDGE_PCT,
            "major_exchanges": sorted(MAJOR_EXCHANGES),
            "short_requires_shortable_for_p1_p2": True,
            "p1_p2_require_intrinsic_base": True,
            "p1_p2_require_valuation_methods": 2,
            "p1_requires_operating_confirmation": True,
            "p2_requires_no_material_operating_contradiction": True,
            "watch_allows_verification_needed": True,
            "final_requires_valid_ttm": True,
            "reference_price_fallback": False,
            "fill_quota": "none",
            "stage2_deep_enrichment_limit": FORENSIC_ENRICH_LIMIT,
        },
        "ranking_basis": [
            "priority tier",
            "absolute Base gap",
            "valuation quality / method count",
            "operating confirmation or contradiction",
            "ticker",
        ],
        "contract_version": CONTRACT_VERSION,
        "enrichment_mode": "BROAD_STAGE0_ROTATION_STAGE1_BOUNDED_FORENSIC_STAGE2",
    }


__all__ = ["market_scan", "CONTRACT_VERSION", "_select_stage2_finalists"]
