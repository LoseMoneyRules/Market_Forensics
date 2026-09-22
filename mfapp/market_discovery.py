from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from .core_models import Coverage, Security, ValuationModel
from .data_providers import get_secret
from .discovery_engine import classify_coverage
from .discovery_market_fundamentals import screen_full_universe
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
CONTRACT_VERSION = "FULL_MARKET_MISPRICING_DISCOVERY_V5"


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
    if int(base.get("independent_method_count") or 0) > 0:
        return int(base.get("independent_method_count") or 0)
    if int(base.get("method_count") or 0) > 0:
        return int(base.get("method_count") or 0)
    return sum(1 for key in ("pe", "p_sales", "ev_sales", "ev_ebitda", "fcf_yield", "dcf") if _n(base.get(key)) is not None)


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
        valuation_forensics = dict(cache.get("valuation_forensics") or {})
        bridge = dict(valuation_forensics.get("multiple_bridge") or {})
        peer_adjusted = dict(((valuation_forensics.get("peer_analysis") or {}).get("peer_adjusted") or {}))
        rerating = dict(valuation_forensics.get("rerating_conditions") or {})
        market_read = dict(valuation_forensics.get("market_read") or {})
        decision_window = dict(((valuation_forensics.get("catalyst_timeline") or {}).get("decision_window") or {}))
        historical_relative_gap_pct = None
        if _n(bridge.get("current_multiple")) not in (None, 0) and _n(bridge.get("justified_current")) is not None:
            historical_relative_gap_pct = (_n(bridge.get("justified_current")) / _n(bridge.get("current_multiple")) - 1.0) * 100.0
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
                "engine_version": valuation.get("engine_version") or dict((model.assumptions or {}).get("latest_engine_result") or {}).get("engine_version") if model else valuation.get("engine_version"),
            },
            "valuation_methods": _valuation_method_count(model),
            "valuation_forensics": {
                "historical_gap_pct": historical_relative_gap_pct,
                "peer_gap_pct": _n(peer_adjusted.get("relative_gap_pct")),
                "rerating_completion_pct": _n(rerating.get("completion_pct")),
                "deteriorating_conditions": int(rerating.get("deteriorating") or 0),
                "market_read": market_read.get("conclusion"),
                "decision_window": decision_window.get("state"),
                "triangulation_state": (valuation_forensics.get("triangulation") or {}).get("state"),
                "engine_version": valuation_forensics.get("engine_version"),
            },
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


def _discovery_health(
    universe: dict[str, Any],
    stage1: dict[str, Any],
    fundamental_screen: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    if current and requested != current:
        severity = "CRITICAL"
        flags.append(f"Stage 1 requested {requested}/{current} eligible names; full-universe scan contract was not satisfied.")

    fundamental_screen = dict(fundamental_screen or {})
    fundamental_total = int(fundamental_screen.get("total_liquid_names") or 0)
    fundamental_usable = int(fundamental_screen.get("usable_count") or 0)
    fundamental_pct = (
        fundamental_usable / fundamental_total * 100.0
        if fundamental_total else 0.0
    )
    if fundamental_total:
        if not fundamental_screen.get("configured"):
            severity = "CRITICAL"
            flags.append("SEC full-market fundamental pre-screen is not configured.")
        elif fundamental_pct < 50.0:
            severity = "CRITICAL"
            flags.append(f"Only {fundamental_usable}/{fundamental_total} liquid names have usable full-market SEC fundamentals.")
        elif fundamental_pct < 75.0:
            severity = "WARN" if severity == "OK" else severity
            flags.append(f"Full-market SEC fundamental coverage is {fundamental_pct:.0f}%; missing names are not selected blindly.")

    valuation_usable = int(fundamental_screen.get("valuation_usable_count") or 0)
    valuation_pct = (
        valuation_usable / fundamental_total * 100.0
        if fundamental_total else 0.0
    )
    if fundamental_total:
        if valuation_pct < 40.0:
            severity = "CRITICAL"
            flags.append(
                f"Only {valuation_usable}/{fundamental_total} liquid names have usable market-valuation evidence; "
                "Discovery will not pretend the full-market mispricing layer is complete."
            )
        elif valuation_pct < 65.0:
            severity = "WARN" if severity == "OK" else severity
            flags.append(
                f"Market-valuation evidence covers {valuation_pct:.0f}% of liquid names; missing names remain unranked rather than guessed."
            )

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
        "fundamental_total_count": fundamental_total,
        "fundamental_usable_count": fundamental_usable,
        "fundamental_usable_pct": round(fundamental_pct, 1),
        "valuation_usable_count": valuation_usable,
        "valuation_usable_pct": round(valuation_pct, 1),
    }


def _scan_cadence(health: dict[str, Any], coverage_progress: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    health_status = str(health.get("status") or "OK")
    if health_status == "CRITICAL":
        days = 1
        reason = "Re-run tomorrow because the last universe/provider health check found a critical anomaly."
    elif health_status == "WARN":
        days = 3
        reason = "Re-run in about 3 days because the last universe/provider health check needs confirmation."
    else:
        days = 7
        reason = "Weekly is the normal cadence; every successful run scans the full eligible universe rather than advancing a rotation."
    return {
        "recommended_interval_days": days,
        "next_due_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "reason": reason,
        "rule": "Sooner after failed/partial full-market coverage; otherwise weekly.",
    }



def _market_mispricing_hypothesis(row: dict[str, Any]) -> dict[str, Any]:
    """Build an auditable full-market mispricing hypothesis before deep valuation.

    Stage 1.5 does NOT estimate fair value and does not decide P1/P2. It asks a
    narrower question: is the current market valuation visibly in tension with
    the direction of filed operating evidence? Only names with evidence on both
    sides of that question are allowed to spend deep Companyfacts/valuation work.
    """
    screen = dict(row.get("fundamental_screen") or {})
    metrics = dict(screen.get("metrics") or {})
    status = str(screen.get("status") or "MISSING").upper()
    if status not in {"READY", "PARTIAL"}:
        return {
            "eligible": False, "side": "NEUTRAL", "dislocation_points": 0,
            "valuation_points": 0, "operating_points": 0, "contradiction_points": 0,
            "valuation_signals": [], "operating_signals": [],
            "reason": "Comparable SEC fundamental evidence is unavailable.",
        }

    revenue = _n(metrics.get("revenue_yoy_pct"))
    op_margin = _n(metrics.get("operating_margin_pct"))
    op_change = _n(metrics.get("operating_margin_change_pp"))
    fcf_margin = _n(metrics.get("fcf_margin_pct"))
    inv_growth = _n(metrics.get("inventory_growth_pct"))
    rec_growth = _n(metrics.get("receivables_growth_pct"))
    pe = _n(metrics.get("pe_proxy"))
    ps = _n(metrics.get("ps_proxy"))
    fcf_yield = _n(metrics.get("fcf_yield_pct"))

    long_operating: list[tuple[int, str]] = []
    short_operating: list[tuple[int, str]] = []
    long_valuation: list[tuple[int, str]] = []
    short_valuation: list[tuple[int, str]] = []

    def add(bucket: list[tuple[int, str]], points: int, text: str) -> None:
        bucket.append((int(points), text))

    # Operating direction: improvement/deterioration from comparable filed data.
    if revenue is not None:
        if revenue >= 8:
            add(long_operating, 2, f"Revenue {revenue:+.1f}% YoY")
        elif revenue >= 2:
            add(long_operating, 1, f"Revenue {revenue:+.1f}% YoY")
        elif revenue <= -8:
            add(short_operating, 2, f"Revenue {revenue:+.1f}% YoY")
        elif revenue <= -2:
            add(short_operating, 1, f"Revenue {revenue:+.1f}% YoY")

    if op_change is not None:
        if op_change >= 2:
            add(long_operating, 3, f"Operating margin {op_change:+.1f} pp YoY")
        elif op_change >= .75:
            add(long_operating, 1, f"Operating margin {op_change:+.1f} pp YoY")
        elif op_change <= -2:
            add(short_operating, 3, f"Operating margin {op_change:+.1f} pp YoY")
        elif op_change <= -.75:
            add(short_operating, 1, f"Operating margin {op_change:+.1f} pp YoY")

    if fcf_margin is not None:
        if fcf_margin >= 8:
            add(long_operating, 2, f"FCF margin {fcf_margin:.1f}%")
        elif fcf_margin >= 3:
            add(long_operating, 1, f"FCF margin {fcf_margin:.1f}%")
        elif fcf_margin < 0:
            add(short_operating, 2, f"FCF margin {fcf_margin:.1f}%")

    if revenue is not None and inv_growth is not None:
        spread = inv_growth - revenue
        if spread <= -8:
            add(long_operating, 1, f"Inventory growth trails revenue by {abs(spread):.1f} pp")
        elif spread >= 12:
            add(short_operating, 1, f"Inventory growth exceeds revenue by {spread:.1f} pp")

    if revenue is not None and rec_growth is not None:
        spread = rec_growth - revenue
        if spread <= -8:
            add(long_operating, 1, f"Receivables growth trails revenue by {abs(spread):.1f} pp")
        elif spread >= 12:
            add(short_operating, 1, f"Receivables growth exceeds revenue by {spread:.1f} pp")

    # Valuation tension: only a hypothesis. Canonical Stage 2 valuation decides
    # whether a real intrinsic gap exists; these thresholds only decide where to look.
    if fcf_yield is not None:
        if fcf_yield >= 10:
            add(long_valuation, 4, f"FCF yield proxy {fcf_yield:.1f}%")
        elif fcf_yield >= 7:
            add(long_valuation, 3, f"FCF yield proxy {fcf_yield:.1f}%")
        elif fcf_yield >= 5:
            add(long_valuation, 2, f"FCF yield proxy {fcf_yield:.1f}%")
        elif fcf_yield <= 0:
            add(short_valuation, 3, f"FCF yield proxy {fcf_yield:.1f}%")
        elif fcf_yield <= 2:
            add(short_valuation, 2, f"FCF yield proxy {fcf_yield:.1f}%")
        elif fcf_yield <= 3:
            add(short_valuation, 1, f"FCF yield proxy {fcf_yield:.1f}%")

    if pe is not None:
        if pe <= 10:
            add(long_valuation, 3, f"P/E proxy {pe:.1f}x")
        elif pe <= 15:
            add(long_valuation, 2, f"P/E proxy {pe:.1f}x")
        elif pe <= 20:
            add(long_valuation, 1, f"P/E proxy {pe:.1f}x")
        if pe >= 45 and (revenue is None or revenue <= 3):
            add(short_valuation, 3, f"P/E proxy {pe:.1f}x with weak growth")
        elif pe >= 35 and revenue is not None and revenue <= 0:
            add(short_valuation, 2, f"P/E proxy {pe:.1f}x with non-growing revenue")
        elif pe >= 30 and revenue is not None and revenue < 0:
            add(short_valuation, 1, f"P/E proxy {pe:.1f}x despite revenue decline")
        if (
            revenue is not None and revenue >= 8 and pe <= 40
            and pe / max(revenue, .1) <= 1.5
            and not any("P/E proxy" in text for _, text in long_valuation)
        ):
            add(long_valuation, 2, f"Growth-adjusted P/E {pe:.1f}x vs revenue growth {revenue:.1f}%")

    if ps is not None:
        if ps <= 1 and (revenue is not None and revenue >= 0) and (op_margin is None or op_margin >= 0):
            add(long_valuation, 2, f"P/S proxy {ps:.1f}x with non-deteriorating revenue")
        elif ps <= 2 and (revenue is not None and revenue >= 0) and (op_margin is not None and op_margin >= 5):
            add(long_valuation, 1, f"P/S proxy {ps:.1f}x with positive operating margin")
        if ps >= 8 and (revenue is None or revenue <= 5):
            add(short_valuation, 3, f"P/S proxy {ps:.1f}x without matching growth")
        elif ps >= 5 and revenue is not None and revenue <= 0:
            add(short_valuation, 2, f"P/S proxy {ps:.1f}x with non-growing revenue")
        elif ps >= 3 and revenue is not None and revenue <= -5:
            add(short_valuation, 1, f"P/S proxy {ps:.1f}x despite revenue decline")
        efficiency = (
            revenue + op_margin
            if revenue is not None and op_margin is not None
            else None
        )
        if (
            efficiency is not None and efficiency >= 30 and ps <= 5
            and not any("P/S proxy" in text for _, text in long_valuation)
        ):
            add(long_valuation, 2, f"Growth+margin adjusted P/S {ps:.1f}x vs {efficiency:.1f} combined points")
        elif (
            efficiency is not None and efficiency >= 20 and ps <= 3.5
            and not any("P/S proxy" in text for _, text in long_valuation)
        ):
            add(long_valuation, 1, f"Growth+margin adjusted P/S {ps:.1f}x vs {efficiency:.1f} combined points")

    long_op = sum(points for points, _ in long_operating)
    short_op = sum(points for points, _ in short_operating)
    long_val = sum(points for points, _ in long_valuation)
    short_val = sum(points for points, _ in short_valuation)

    long_ok = long_val >= 2 and (long_op >= 2 or (long_val >= 4 and long_op >= 1)) and short_op < max(3, long_op)
    short_ok = short_val >= 2 and (short_op >= 2 or (short_val >= 4 and short_op >= 1)) and long_op < max(3, short_op)

    choices: list[tuple[tuple[int, int, int, int], str]] = []
    if long_ok:
        choices.append(((long_val + long_op, long_val, long_op, -short_op), "LONG"))
    if short_ok:
        choices.append(((short_val + short_op, short_val, short_op, -long_op), "SHORT"))
    choices.sort(reverse=True)

    if not choices or (len(choices) > 1 and choices[0][0] == choices[1][0]):
        return {
            "eligible": False, "side": "NEUTRAL",
            "dislocation_points": max(long_val + long_op, short_val + short_op),
            "valuation_points": max(long_val, short_val),
            "operating_points": max(long_op, short_op),
            "contradiction_points": min(long_op, short_op),
            "valuation_signals": [], "operating_signals": [],
            "reason": "No clean valuation-versus-operating tension survived contradiction checks.",
        }

    side = choices[0][1]
    valuation_rows = long_valuation if side == "LONG" else short_valuation
    operating_rows = long_operating if side == "LONG" else short_operating
    contradiction = short_op if side == "LONG" else long_op
    valuation_points = long_val if side == "LONG" else short_val
    operating_points = long_op if side == "LONG" else short_op
    valuation_signals = [text for _, text in sorted(valuation_rows, reverse=True)]
    operating_signals = [text for _, text in sorted(operating_rows, reverse=True)]
    reason = (
        f"{side} market-mispricing hypothesis: valuation tension {valuation_points} pts + "
        f"operating evidence {operating_points} pts"
        + (f" vs {contradiction} contradictory pts" if contradiction else "")
        + ". "
        + "; ".join((valuation_signals + operating_signals)[:4])
    )
    return {
        "eligible": True,
        "side": side,
        "dislocation_points": valuation_points + operating_points,
        "valuation_points": valuation_points,
        "operating_points": operating_points,
        "contradiction_points": contradiction,
        "evidence_count": len(valuation_rows) + len(operating_rows),
        "valuation_signals": valuation_signals,
        "operating_signals": operating_signals,
        "reason": reason,
    }


def _select_stage2_finalists(
    rows: list[dict[str, Any]],
    local_context: dict[str, dict[str, Any]],
    *,
    limit: int = FORENSIC_ENRICH_LIMIT,
) -> list[dict[str, Any]]:
    """Allocate deep valuation only to auditable market-mispricing hypotheses.

    Known Coverage can enter from already-materialized intrinsic/historical/peer
    gaps. Unknown names must first show BOTH valuation tension and aligned filed
    operating evidence across the full-market SEC pre-screen. Price movement,
    ticker order and liquidity never create eligibility; dollar volume is only a
    final tie-break after the evidence dimensions.
    """
    known_edge: list[dict[str, Any]] = []
    broad_long: list[dict[str, Any]] = []
    broad_short: list[dict[str, Any]] = []

    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        context = local_context.get(ticker) or {}
        gap = _n(context.get("base_gap_pct"))
        vf = dict(context.get("valuation_forensics") or {})
        directional_known = [
            value for value in (
                _n(gap), _n(vf.get("historical_gap_pct")), _n(vf.get("peer_gap_pct"))
            ) if value is not None
        ]
        strongest_forensic_gap = max((abs(value) for value in directional_known), default=None)
        item = dict(row)
        item["known_context"] = context
        item["in_coverage"] = bool(context)

        if context and strongest_forensic_gap is not None and strongest_forensic_gap >= FORENSIC_WATCH_EDGE_PCT:
            item["stage2_selection_reason"] = (
                "Stored Coverage intrinsic / historical / peer-relative evidence is already "
                "at or beyond the Discovery WATCH edge."
            )
            item["stage2_forensic_gap_pct"] = strongest_forensic_gap
            item["market_mispricing"] = {
                "eligible": True,
                "side": "LONG" if (_n(gap) or 0) >= 0 else "SHORT",
                "source": "STORED_RESEARCH",
                "dislocation_points": 0,
                "reason": item["stage2_selection_reason"],
            }
            known_edge.append(item)
            continue

        hypothesis = _market_mispricing_hypothesis(row)
        if not hypothesis.get("eligible"):
            continue

        item["market_mispricing"] = hypothesis
        item["stage2_selection_reason"] = str(hypothesis.get("reason") or "")
        item["stage2_screen_strength"] = int(hypothesis.get("dislocation_points") or 0)
        item["stage2_screen_signal_count"] = int(hypothesis.get("evidence_count") or 0)
        if hypothesis.get("side") == "LONG":
            broad_long.append(item)
        elif hypothesis.get("side") == "SHORT":
            broad_short.append(item)

    known_edge.sort(key=lambda row: (
        -max(
            abs(_n((row.get("known_context") or {}).get("base_gap_pct")) or 0.0),
            abs(_n(((row.get("known_context") or {}).get("valuation_forensics") or {}).get("historical_gap_pct")) or 0.0),
            abs(_n(((row.get("known_context") or {}).get("valuation_forensics") or {}).get("peer_gap_pct")) or 0.0),
        ),
        row["ticker"],
    ))

    def market_sort(row: dict[str, Any]) -> tuple:
        hypothesis = dict(row.get("market_mispricing") or {})
        return (
            -int(hypothesis.get("dislocation_points") or 0),
            -int(hypothesis.get("valuation_points") or 0),
            -int(hypothesis.get("operating_points") or 0),
            int(hypothesis.get("contradiction_points") or 0),
            -int(hypothesis.get("evidence_count") or 0),
            -(_n(row.get("dollar_volume")) or 0.0),
            row["ticker"],
        )

    broad_long.sort(key=market_sort)
    broad_short.sort(key=market_sort)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append(item: dict[str, Any]) -> None:
        ticker = str(item.get("ticker") or "").upper()
        if ticker and ticker not in seen and len(selected) < limit:
            selected.append(item)
            seen.add(ticker)

    # Coverage gets a small reserved lane because it already has decision-grade
    # materialized evidence. It cannot crowd the full-market search out.
    known_cap = min(4, len(known_edge), max(0, int(limit)))
    for item in known_edge[:known_cap]:
        append(item)

    market_capacity = max(0, int(limit) - len(selected))
    long_target = (market_capacity + 1) // 2
    short_target = market_capacity // 2
    for item in broad_long[:long_target]:
        append(item)
    for item in broad_short[:short_target]:
        append(item)

    # If one side has fewer genuine hypotheses, use remaining capacity for the
    # other side by evidence rank. Never add neutral/filler names to hit quota.
    leftovers = broad_long[long_target:] + broad_short[short_target:]
    leftovers.sort(key=market_sort)
    for item in leftovers:
        append(item)

    for item in known_edge[known_cap:]:
        append(item)

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
    """Full-market evidence-first Discovery followed by bounded deep forensics."""
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
    stage1_rows, fundamental_screen = screen_full_universe(
        user_id, stage1_rows, errors, provider_calls,
    )
    stage15_hypotheses = [_market_mispricing_hypothesis(row) for row in stage1_rows]
    stage15_eligible = [row for row in stage15_hypotheses if row.get("eligible")]
    stage15_long = sum(1 for row in stage15_eligible if row.get("side") == "LONG")
    stage15_short = sum(1 for row in stage15_eligible if row.get("side") == "SHORT")
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
        research_forensics = dict(context.get("valuation_forensics") or {})
        base_label = "Intrinsic Base" if quality == "INTRINSIC" and methods >= 2 else "Indicative Base"
        reasons = [
            (base_label + " $" + format(fair, ",.2f")) if fair is not None else "Base unavailable",
            f"Base fair-value gap {gap:+.1f}%" if gap is not None else "Gap unavailable",
            priority_reason,
            str(item.get("stage2_selection_reason") or ""),
        ]
        reasons.extend(str(row.get("detail") or "") for row in operating_signals[:2])
        reasons.extend(str(row.get("detail") or "") for row in accounting_context[:2])
        if _n(research_forensics.get("historical_gap_pct")) is not None:
            reasons.append(f"Historical driver-adjusted multiple gap {_n(research_forensics.get('historical_gap_pct')):+.1f}%.")
        if _n(research_forensics.get("peer_gap_pct")) is not None:
            reasons.append(f"Peer-adjusted relative gap {_n(research_forensics.get('peer_gap_pct')):+.1f}%.")
        if research_forensics.get("market_read"):
            reasons.append(str(research_forensics.get("market_read")))
        if _n(research_forensics.get("rerating_completion_pct")) is not None and _n(research_forensics.get("rerating_completion_pct")) < 50:
            warning_parts.append("Fewer than half of measurable re-rating conditions are met; historical cheapness alone is not enough.")
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
            "valuation_forensics": research_forensics,
            "decision_window": research_forensics.get("decision_window"),
            "in_coverage": bool(context),
            "lenses": [str(row.get("label") or "") for row in operating_signals],
        })
        candidates.append(candidate)

    candidates.sort(key=lambda row: (
        int(row.get("priority_rank") or 9),
        -max(
            abs(_n(row.get("base_gap_pct")) or 0.0),
            abs(_n((row.get("valuation_forensics") or {}).get("historical_gap_pct")) or 0.0),
            abs(_n((row.get("valuation_forensics") or {}).get("peer_gap_pct")) or 0.0),
        ),
        -int(row.get("valuation_methods") or 0),
        -int(row.get("forensic_score") or 0),
        row["ticker"],
    ))
    long_candidates = [row for row in candidates if row["research_side"] == "LONG" and row.get("priority") != "WATCH"][:MAX_PER_SIDE]
    short_candidates = [row for row in candidates if row["research_side"] == "SHORT" and row.get("priority") != "WATCH"][:MAX_PER_SIDE]
    watch_candidates = [row for row in candidates if row.get("priority") == "WATCH"][:MAX_WATCH]
    final = long_candidates + short_candidates + watch_candidates

    health = _discovery_health(universe, stage1, fundamental_screen)
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
        "universe_source": "Alpaca active US operating equities + full-market price/liquidity scan + full-market SEC frame pre-screen + bounded deep Companyfacts Stage 2",
        "universe_generated_at": universe.get("generated_at"),
        "universe_cache_hit": bool(universe.get("cache_hit")),
        "stage0_count": int(universe.get("member_count") or 0),
        "stage0_raw_count": int(universe.get("raw_count") or 0),
        "stage0_excluded_count": int(universe.get("excluded_count") or 0),
        "stage0_excluded_breakdown": stage0_excluded,
        "stage1_scanned_count": int(stage1.get("scanned_count") or 0),
        "stage1_qualified_count": int(stage1.get("qualified_count") or 0),
        "stage1_broad_rotation_count": 0,
        "stage1_quiet_broad_count": 0,
        "stage1_activity_count": 0,
        "stage1_full_universe_count": int(stage1.get("full_universe_count") or 0),
        "stage1_scan_mode": str(stage1.get("scan_mode") or "FULL_UNIVERSE"),
        "fundamental_screen": fundamental_screen,
        "stage15_mispricing_count": len(stage15_eligible),
        "stage15_long_count": stage15_long,
        "stage15_short_count": stage15_short,
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
            "full_universe_each_run": True,
            "unknown_stage2_requires_marketwide_fundamental_screen": True,
            "stage15_requires_valuation_operating_tension": True,
            "stage15_price_move_ranked": False,
            "stage15_liquidity_tiebreak_only": True,
            "stage15_no_fill_quota": True,
            "stage2_deep_enrichment_limit": FORENSIC_ENRICH_LIMIT,
        },
        "ranking_basis": [
            "full-market SEC fundamental evidence",
            "Stage 1.5 valuation-versus-operating mispricing hypothesis",
            "valuation tension strength",
            "aligned filed operating evidence",
            "contradiction penalty",
            "evidence breadth",
            "dollar volume only as final tie-break",
            "stored intrinsic / historical / peer evidence for known names",
            "canonical valuation gap and quality after deep forensics",
            "ticker only as deterministic final tie-break",
        ],
        "contract_version": CONTRACT_VERSION,
        "enrichment_mode": "FULL_MARKET_MISPRICING_STAGE15_THEN_BOUNDED_CANONICAL_STAGE2",
    }


__all__ = ["market_scan", "CONTRACT_VERSION", "_select_stage2_finalists"]
