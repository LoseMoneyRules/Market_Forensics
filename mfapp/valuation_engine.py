from __future__ import annotations

from datetime import date
import hashlib
import json
from math import isfinite
import random
from statistics import median, pstdev
from typing import Any

from .economic_reality import economic_from_row, has_suppression, metric as economic_metric
from .company_quality import build_company_quality

ENGINE_VERSION = "0.3.2"

DECISION_GRADE_QUALITIES = {"INTRINSIC", "MANUAL_OVERRIDE"}
QUALITY_ALIASES = {
    "STALE_INTRINSIC_FALLBACK": "PROVISIONAL_STORED_FALLBACK",
    "REFERENCE_PRICE_FALLBACK": "PROVISIONAL_REFERENCE_FALLBACK",
}


def canonical_valuation_quality(value: Any) -> str:
    quality = str(value or "").strip().upper()
    return QUALITY_ALIASES.get(quality, quality or "DATA_WARNING")


def valuation_base_quality(valuation: dict[str, Any] | None) -> str:
    valuation = valuation or {}
    return canonical_valuation_quality(
        valuation.get("base_quality")
        or ((valuation.get("scenarios") or {}).get("BASE") or {}).get("quality")
        or valuation.get("quality")
    )


def valuation_is_decision_grade(valuation: dict[str, Any] | None) -> bool:
    return valuation_base_quality(valuation) in DECISION_GRADE_QUALITIES


def stored_model_base_quality(model: Any | None) -> str:
    """Recover Base quality from already-stored model state without running valuation."""
    if model is None:
        return "DATA_WARNING"
    try:
        scenarios = {str(row.name or "").upper(): row for row in (model.scenarios or [])}
    except Exception:
        scenarios = {}
    base = scenarios.get("BASE")
    outputs = dict(getattr(base, "outputs", None) or {}) if base is not None else {}
    assumptions = dict(getattr(model, "assumptions", None) or {})
    latest = dict(assumptions.get("latest_engine_result") or {})
    latest_scenarios = dict(latest.get("scenarios") or {})
    return canonical_valuation_quality(
        outputs.get("quality")
        or (latest_scenarios.get("BASE") or {}).get("quality")
        or latest.get("quality")
    )

# Compatibility metadata only. Automatic valuation never uses fixed sector/type multiples.
TYPE_PRIORS = {
    "Generic": {"pe": (12.0, 18.0, 24.0), "ev_sales": (0.8, 1.5, 2.4), "fcf_yield": (0.080, 0.055, 0.040)},
    "Consumer / Brand": {"pe": (15.0, 21.0, 27.0), "ev_sales": (0.9, 1.8, 3.0), "fcf_yield": (0.070, 0.050, 0.035)},
    "Industrial": {"pe": (13.0, 18.0, 24.0), "ev_sales": (0.7, 1.4, 2.2), "fcf_yield": (0.080, 0.060, 0.042)},
    "Software": {"pe": (20.0, 30.0, 42.0), "ev_sales": (3.0, 6.0, 9.0), "fcf_yield": (0.055, 0.038, 0.025)},
    "Semiconductor / AI": {"pe": (16.0, 24.0, 34.0), "ev_sales": (2.0, 4.5, 7.5), "fcf_yield": (0.065, 0.045, 0.030)},
    "Auto / EV": {"pe": (8.0, 14.0, 22.0), "ev_sales": (0.35, 0.80, 1.50), "fcf_yield": (0.110, 0.075, 0.045)},
    "Financial / REIT": {"pe": (8.0, 12.0, 16.0), "ev_sales": (0.7, 1.0, 1.4), "fcf_yield": (0.090, 0.070, 0.050)},
}


def n(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return out if isfinite(out) else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def quantile(values: list[float], q: float) -> float | None:
    rows = sorted(x for x in (n(v) for v in values) if x is not None)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    pos = (len(rows) - 1) * q
    lo, hi = int(pos), min(len(rows) - 1, int(pos) + (0 if pos.is_integer() else 1))
    if lo == hi:
        return rows[lo]
    w = pos - lo
    return rows[lo] * (1 - w) + rows[hi] * w



def _row_margin(row: dict[str, Any], field: str) -> float | None:
    revenue = n(row.get("revenue"))
    value = n(row.get(field))
    return value / revenue if value is not None and revenue not in (None, 0) else None


def _ccc_days(row: dict[str, Any]) -> float | None:
    revenue, cogs = n(row.get("revenue")), n(row.get("cogs"))
    if revenue in (None, 0) or cogs in (None, 0) or cogs <= 0:
        return None
    inventory, receivables, payables = n(row.get("inventory")), n(row.get("receivables")), n(row.get("payables"))
    dso = receivables / revenue * 365.0 if receivables is not None else None
    dio = inventory / cogs * 365.0 if inventory is not None else None
    dpo = payables / cogs * 365.0 if payables is not None else None
    if dso is None and dio is None and dpo is None:
        return None
    return (dso or 0.0) + (dio or 0.0) - (dpo or 0.0)


def _history_profile(history: list[dict[str, Any]], company_type: str) -> dict[str, Any]:
    rows = [dict(row) for row in history if n(row.get("revenue")) not in (None, 0)]
    rows.sort(key=lambda row: (str(row.get("period_end") or ""), int(row.get("fiscal_year") or 0)))
    growths, share_growths = [], []
    for prior, current in zip(rows, rows[1:]):
        pr, cr = n(prior.get("revenue")), n(current.get("revenue"))
        if pr not in (None, 0) and cr is not None:
            change = cr / pr - 1.0
            if -.80 < change < 3.0: growths.append(change)
        ps = n(prior.get("diluted_shares")) or n(prior.get("shares_outstanding"))
        cs = n(current.get("diluted_shares")) or n(current.get("shares_outstanding"))
        if ps not in (None, 0) and cs is not None:
            share_growths.append(cs / ps - 1.0)

    net_margins = [v for row in rows if (v := _row_margin(row, "net_income")) is not None]
    op_margins = [v for row in rows if (v := _row_margin(row, "operating_income")) is not None]
    fcf_margins = [v for row in rows if (v := _row_margin(row, "fcf")) is not None]
    ccc = [v for row in rows if (v := _ccc_days(row)) is not None]
    latest_economic = economic_from_row(rows[-1]) if rows else {}
    growth_capex = economic_metric(latest_economic, "growth_capex_proxy")
    latest_ni = n(rows[-1].get("net_income")) if rows else None
    reinvestment = growth_capex / max(abs(latest_ni), 1.0) if growth_capex is not None and latest_ni not in (None, 0) else None
    roics = [v for row in rows if (v := economic_metric(economic_from_row(row), "economic_roic_pct")) is not None]

    recent_growth = median(growths[-3:]) if growths[-3:] else None
    long_growth = median(growths[-7:]) if growths[-7:] else recent_growth
    growth_vol = pstdev(growths[-7:]) if len(growths[-7:]) >= 2 else None
    op_vol = pstdev(op_margins[-7:]) if len(op_margins[-7:]) >= 2 else None
    if company_type == "Financial / REIT":
        life_cycle = "SECTOR_SPECIFIC"
    elif (growth_vol is not None and growth_vol >= .12) or (op_vol is not None and op_vol >= .06):
        life_cycle = "CYCLICAL"
    elif (recent_growth is not None and recent_growth >= .12) or (reinvestment is not None and reinvestment >= .50):
        life_cycle = "GROWTH"
    elif long_growth is not None and long_growth <= .06 and (reinvestment is None or reinvestment < .35) and (op_vol is None or op_vol <= .04):
        life_cycle = "MATURE"
    else:
        life_cycle = "NORMAL"

    def dist(values):
        return {"p10": quantile(values, .10), "median": median(values) if values else None, "p90": quantile(values, .90),
                "stdev": pstdev(values) if len(values) >= 2 else None, "sample_size": len(values)}

    ccc_current = ccc[-1] if ccc else None
    ccc_prior = ccc[-2] if len(ccc) >= 2 else None
    ccc_delta = ccc_current - ccc_prior if ccc_current is not None and ccc_prior is not None else None
    ccc_risk_bps, multiple_factor = 0, 1.0
    if ccc_delta is not None:
        if ccc_delta >= 30: ccc_risk_bps, multiple_factor = 100, .90
        elif ccc_delta >= 15: ccc_risk_bps, multiple_factor = 50, .95
        elif ccc_delta <= -30: ccc_risk_bps, multiple_factor = -35, 1.035
        elif ccc_delta <= -15: ccc_risk_bps, multiple_factor = -20, 1.02

    return {"life_cycle": life_cycle, "reinvestment_rate_proxy": reinvestment,
            "roic_median_pct": median(roics[-7:]) if roics else None,
            "growth": dist(growths[-10:]), "net_margin": dist(net_margins[-10:]),
            "operating_margin": dist(op_margins[-10:]), "fcf_margin": dist(fcf_margins[-10:]),
            "share_growth": dist(share_growths[-10:]), "ccc_days": ccc_current, "ccc_change_days": ccc_delta,
            "ccc_risk_bps": ccc_risk_bps, "multiple_factor": multiple_factor}


def _altman_z(metrics: dict[str, Any], current_price: Any, company_type: str) -> dict[str, Any]:
    if company_type == "Financial / REIT":
        return {"available": False, "zone": "NOT_APPLICABLE", "reason": "Altman industrial model is not applied to Financial / REIT companies."}
    assets, liabilities = n(metrics.get("assets")), n(metrics.get("liabilities"))
    ca, cl, retained = n(metrics.get("current_assets")), n(metrics.get("current_liabilities")), n(metrics.get("retained_earnings"))
    ebit, revenue, shares, price = n(metrics.get("operating_income")), n(metrics.get("revenue")), n(metrics.get("shares")), n(current_price)
    vals=(assets, liabilities, ca, cl, retained, ebit, revenue, shares, price)
    if any(v is None for v in vals) or assets <= 0 or liabilities <= 0 or shares <= 0 or price <= 0:
        return {"available": False, "zone": "UNAVAILABLE", "reason": "Filed Altman inputs or current market equity are incomplete."}
    z = 1.2 * (ca-cl)/assets + 1.4*retained/assets + 3.3*ebit/assets + .6*(shares*price)/liabilities + revenue/assets
    zone = "DISTRESS" if z < 1.81 else "GREY" if z < 2.99 else "SAFE"
    return {"available": True, "score": z, "zone": zone, "basis": "ALTMAN_Z_PUBLIC_INDUSTRIAL"}


def _triangular(rng: random.Random, values) -> float | None:
    if not values or len(values) < 3: return None
    a,m,b=(n(values[0]),n(values[1]),n(values[2]))
    if a is None or m is None or b is None: return None
    low,high=min(a,b),max(a,b); mode=min(high,max(low,m))
    return rng.triangular(low,high,mode) if high>low else mode


def _monte_carlo_distribution(metrics, cases, weights, years, current_price, iterations=10000) -> dict[str, Any]:
    base=dict(cases.get("BASE") or {}); calibration=dict(base.get("_calibration") or {})
    profile=dict(base.get("_history_profile") or metrics.get("history_profile") or {})
    if any(n((cases.get(name) or {}).get("manual_override")) is not None for name in ("BEAR","BASE","BULL")):
        return {"available":False,"reason":"Manual scenario override active.","iterations":0,"method_count":0}
    if str(profile.get("life_cycle") or "")=="SECTOR_SPECIFIC":
        return {"available":False,"reason":"Sector-specific valuation model required.","iterations":0,"method_count":0}
    shares,revenue,net_debt,ebitda=n(metrics.get("shares")),n(metrics.get("revenue")),n(metrics.get("net_debt")),n(metrics.get("ebitda"))
    if shares in (None,0) or revenue in (None,0):
        return {"available":False,"reason":"Revenue/share basis incomplete.","iterations":0,"method_count":0}

    usable=[]
    for key in ("pe","ps","ev_ebitda","fcf_yield"):
        values=calibration.get(key)
        if isinstance(values,(tuple,list)) and len(values)==3 and all(n(x) is not None for x in values): usable.append(key)
    if n(base.get("fcf_margin")) is not None and (n(weights.get("dcf")) or 0)>0: usable.append("dcf")
    usable=list(dict.fromkeys(usable))
    if len(usable)<2:
        return {"available":False,"reason":"Fewer than two company-specific valuation methods have sufficient evidence.","iterations":0,"method_count":len(usable)}

    payload={"revenue":revenue,"shares":shares,"net_debt":net_debt,"ebitda":ebitda,"calibration":calibration,"profile":profile,
             "base":{k:v for k,v in base.items() if not str(k).startswith("_")}}
    seed=int(hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()[:16],16); rng=random.Random(seed)
    growth_d=profile.get("growth") or {}; nm_d=profile.get("net_margin") or {}; fm_d=profile.get("fcf_margin") or {}; sg_d=profile.get("share_growth") or {}
    growth_t=(growth_d.get("p10"),growth_d.get("median"),growth_d.get("p90"))
    nm_t=(nm_d.get("p10"),nm_d.get("median"),nm_d.get("p90")); fm_t=(fm_d.get("p10"),fm_d.get("median"),fm_d.get("p90"))
    sg_t=(sg_d.get("p10"),sg_d.get("median"),sg_d.get("p90"))
    multiple_factor=n(profile.get("multiple_factor")) or 1.0
    freshness=dict(metrics.get("freshness_shock") or {}); freshness_mult=n(freshness.get("uncertainty_multiplier")) or 1.0
    solvency=_altman_z(metrics,current_price,str(metrics.get("company_type") or "Generic"))
    solvency_bps=150 if solvency.get("zone")=="DISTRESS" else 75 if solvency.get("zone")=="GREY" else 0
    solvency_multiple=.80 if solvency.get("zone")=="DISTRESS" else .90 if solvency.get("zone")=="GREY" else 1.0
    raw_weights={key:max(0.0,n(weights.get(key)) or 0.0) for key in ("pe","ps","ev_ebitda","fcf_yield","dcf")}
    outcomes=[]; target=max(1000,min(int(iterations),20000))
    for _ in range(target):
        growth=_triangular(rng,growth_t); growth=n(base.get("growth")) or 0.0 if growth is None else growth
        nm=_triangular(rng,nm_t); nm=n(base.get("net_margin")) if nm is None else nm
        fm=_triangular(rng,fm_t); fm=n(base.get("fcf_margin")) if fm is None else fm
        sg=_triangular(rng,sg_t)
        if sg is None: sg=0.0
        sim_shares=shares*(1+clamp(sg,-.10,.30))
        values={}
        pe_mult=_triangular(rng,calibration.get("pe"))
        if pe_mult is not None and nm is not None:
            v=pe_value(revenue,growth,nm,pe_mult*multiple_factor*solvency_multiple,sim_shares)
            if v is not None: values["pe"]=v
        ps_mult=_triangular(rng,calibration.get("ps"))
        if ps_mult is not None:
            v=ps_value(revenue,growth,ps_mult*multiple_factor*solvency_multiple,sim_shares)
            if v is not None: values["ps"]=v
        ev_mult=_triangular(rng,calibration.get("ev_ebitda"))
        if ev_mult is not None and ebitda is not None and net_debt is not None:
            v=ev_ebitda_value(ebitda,growth,ev_mult*multiple_factor*solvency_multiple,net_debt,sim_shares)
            if v is not None: values["ev_ebitda"]=v
        yld=_triangular(rng,calibration.get("fcf_yield"))
        if yld is not None and fm is not None:
            v=fcf_yield_value(revenue,growth,fm,yld,sim_shares)
            if v is not None: values["fcf_yield"]=v
        dc=n(base.get("equity_discount_rate")) or .10
        discount=rng.triangular(max(.04,dc-.0125),min(.25,dc+.0200+solvency_bps/10000.0),dc)
        tl=n((cases.get("BEAR") or {}).get("terminal_growth")) or 0.0; th=n((cases.get("BULL") or {}).get("terminal_growth")) or .03
        tm=n(base.get("terminal_growth")) or .02; lo,hi=min(tl,th),max(tl,th); tm=min(hi,max(lo,tm)); terminal=rng.triangular(lo,hi,tm)
        if fm is not None:
            v=dcf_value(revenue,growth,fm,discount,terminal,years,sim_shares,growth_end=terminal)
            if v is not None: values["dcf"]=v
        active={key:raw_weights.get(key,0.0) for key in values if raw_weights.get(key,0.0)>0}
        if len(active)<2: continue
        total=sum(active.values()); fair=sum(values[key]*active[key]/total for key in active)
        if fair>0: outcomes.append(fair)
    if len(outcomes)<max(500,target//4):
        return {"available":False,"reason":"Monte Carlo produced too few multi-method outcomes.","iterations":len(outcomes),"method_count":len(usable),"solvency":solvency}
    outcomes.sort(); p10,p50,p90=quantile(outcomes,.10),quantile(outcomes,.50),quantile(outcomes,.90)
    if p10 is None or p50 is None or p90 is None:
        return {"available":False,"reason":"Distribution quantiles unavailable.","iterations":len(outcomes),"method_count":len(usable),"solvency":solvency}
    if freshness_mult>1.0:
        p10=max(.01,p50-(p50-p10)*freshness_mult); p90=p50+(p90-p50)*freshness_mult
    floor=n(metrics.get("liquidation_floor_per_share"))
    if floor is not None and floor>0: p10,p50,p90=max(p10,floor),max(p50,floor),max(p90,floor)
    return {"available":True,"bear":min(p10,p50,p90),"base":median([p10,p50,p90]),"bull":max(p10,p50,p90),
            "iterations":len(outcomes),"method_count":len(usable),"methods":usable,"seed":seed,"solvency":solvency,
            "freshness_shock":freshness,"basis":"DETERMINISTIC_MONTE_CARLO_P10_P50_P90"}


def _median_growth(values: list[float | None]) -> float | None:
    changes: list[float] = []
    for previous, current in zip(values, values[1:]):
        if current is None or previous in (None, 0):
            continue
        change = current / previous - 1.0
        if -0.75 < change < 2.0:
            changes.append(change)
    return median(changes[-3:]) if changes else None


def _cagr(values: list[float | None], years: int = 3) -> float | None:
    usable = [(i, x) for i, x in enumerate(values) if x is not None and x > 0]
    if len(usable) < 2:
        return None
    end_i, end = usable[-1]
    start = next(((i, x) for i, x in reversed(usable[:-1]) if end_i - i >= min(years, end_i)), usable[0])
    start_i, start_value = start
    span = end_i - start_i
    if span <= 0 or start_value <= 0:
        return None
    return (end / start_value) ** (1.0 / span) - 1.0


def infer_company_type(sector: str = "", industry: str = "") -> str:
    text = f"{sector} {industry}".lower()
    if any(x in text for x in ("bank", "insurance", "financial", "reit", "real estate")):
        return "Financial / REIT"
    if any(x in text for x in ("semiconductor", "chip", "ai hardware")):
        return "Semiconductor / AI"
    if any(x in text for x in ("software", "cloud", "saas")):
        return "Software"
    if any(x in text for x in ("auto", "vehicle", "automotive", "ev ")):
        return "Auto / EV"
    if any(x in text for x in ("industrial", "machinery", "aerospace", "equipment")):
        return "Industrial"
    if any(x in text for x in ("apparel", "footwear", "consumer", "beverage", "restaurant", "retail", "brand")):
        return "Consumer / Brand"
    return "Generic"


def _empty_metrics(issue: str) -> dict[str, Any]:
    return {
        "fiscal_year": None,
        "filed_at": None,
        "revenue": None,
        "net_income": None,
        "fcf": None,
        "net_debt": None,
        "revenue_growth": None,
        "net_margin": None,
        "fcf_margin": None,
        "operating_margin": None,
        "shares": None,
        "share_source": "UNRESOLVED",
        "basis_usable": False,
        "basis_issue": issue,
        "data_warnings": [issue],
    }



def metrics_from_history(history: list[dict[str, Any]], shares_override: Any = None, share_source: str = "", company_type: str = "Generic") -> dict[str, Any]:
    rows=[row for row in history if n(row.get("revenue")) is not None]
    if not rows: return _empty_metrics("No annual filing-derived fundamentals are available.")
    latest=rows[-1]; revenue=n(latest.get("revenue")); net_income=n(latest.get("net_income")); fcf=n(latest.get("fcf"))
    cash=n(latest.get("cash")) or 0.0; debt=n(latest.get("debt")) or 0.0
    economic=economic_from_row(latest); economic_net_debt=economic_metric(economic,"economic_net_debt")
    economic_unresolved=(not bool(economic)) or bool(economic.get("material_unresolved"))
    if not economic: net_debt=None; net_debt_basis="ECONOMIC_REALITY_NOT_MATERIALIZED"
    elif economic_unresolved: net_debt=None; net_debt_basis="ECONOMIC_CLASSIFICATION_UNRESOLVED"
    elif economic_net_debt is not None: net_debt=economic_net_debt; net_debt_basis=str(economic.get("debt_basis") or "ECONOMIC_REALITY")
    else: net_debt=None; net_debt_basis="ECONOMIC_NET_DEBT_UNRESOLVED"

    revenues=[n(x.get("revenue")) for x in rows]; net_margins=[]; fcf_margins=[]; reported_fcf_margins=[]
    earnings_review=False; sbc_material=False
    for x in rows:
        xr=n(x.get("revenue")); xni=n(x.get("net_income")); xfcf=n(x.get("fcf")); xe=economic_from_row(x)
        if has_suppression(xe,"PE_EARNINGS_NORMALIZATION_REVIEW"): net_margins.append(None); earnings_review=True
        else: net_margins.append(xni/xr if xni is not None and xr not in (None,0) else None)
        reported_fcf_margins.append(xfcf/xr if xfcf is not None and xr not in (None,0) else None)
        fcf_margins.append(xfcf/xr if xfcf is not None and xr not in (None,0) else None)
        if has_suppression(xe,"FCF_POSITIVE_UNADJUSTED"): sbc_material=True
    operating_margins=[n(x.get("operating_income"))/n(x.get("revenue")) if n(x.get("operating_income")) is not None and n(x.get("revenue")) not in (None,0) else None for x in rows]

    shares=n(shares_override); resolved_source=str(share_source or "").strip().upper()
    if shares in (None,0):
        shares=n(latest.get("diluted_shares")); resolved_source="DILUTED_WA" if shares not in (None,0) else resolved_source
    if shares in (None,0):
        shares=n(latest.get("shares_outstanding")); resolved_source="SEC_FY_OUTSTANDING_FALLBACK" if shares not in (None,0) else resolved_source

    warnings=[]
    if shares in (None,0): warnings.append("No usable diluted/share denominator is available.")
    if net_income is None: warnings.append("Net income is unavailable; P/E is excluded.")
    if fcf is None: warnings.append("Free cash flow is unavailable; FCF-yield and DCF evidence are weaker.")
    if earnings_review: warnings.append("Tax/non-operating earnings anomalies exclude P/E rather than being normalized by guess.")
    if sbc_material: warnings.append("Material SBC is handled through diluted shares and observed share-count growth; reported cash FCF is not reduced a second time.")
    if not economic: warnings.append("Economic Reality is not materialized yet; enterprise-value methods are restricted.")
    elif economic_unresolved: warnings.append("Economic debt classification is materially unresolved; enterprise-value methods are restricted.")

    company_quality=build_company_quality(rows,company_type); valuation_policy=dict(company_quality.get("valuation_policy") or {})
    profile=_history_profile(rows,company_type)
    da=economic_metric(economic,"depreciation_amortization"); operating_income=n(latest.get("operating_income"))
    ebitda=operating_income+da if operating_income is not None and da is not None else None
    capex=n(latest.get("capex")); capex_to_revenue=abs(capex)/revenue if capex is not None and revenue not in (None,0) else None
    liquidation_floor=None
    if shares not in (None,0) and net_debt is not None and net_debt<0:
        one_year_burn=max(-(fcf or 0.0),0.0); distributable=max(-net_debt-one_year_burn,0.0)
        liquidation_floor=distributable/shares
    return {"fiscal_year":latest.get("fiscal_year"),"filed_at":latest.get("filed_at"),"revenue":revenue,"net_income":net_income,"fcf":fcf,
            "operating_income":operating_income,"ebitda":ebitda,"net_debt":net_debt,"net_debt_basis":net_debt_basis,
            "economic_reality":economic,"economic_reality_quality":str(economic.get("quality") or "UNAVAILABLE"),"economic_reality_unresolved":economic_unresolved,
            "company_quality":company_quality,"company_quality_state":company_quality.get("state"),"valuation_policy":valuation_policy,
            "revenue_growth":_cagr(revenues,3) or _median_growth(revenues),
            "net_margin":median([x for x in net_margins[-3:] if x is not None]) if any(x is not None for x in net_margins[-3:]) else None,
            "fcf_margin":median([x for x in fcf_margins[-3:] if x is not None]) if any(x is not None for x in fcf_margins[-3:]) else None,
            "reported_fcf_margin":median([x for x in reported_fcf_margins[-3:] if x is not None]) if any(x is not None for x in reported_fcf_margins[-3:]) else None,
            "fcf_margin_basis":"REPORTED_FCF_WITH_DILUTION_DENOMINATOR","operating_margin":median([x for x in operating_margins[-3:] if x is not None]) if any(x is not None for x in operating_margins[-3:]) else None,
            "shares":shares,"share_source":resolved_source or "UNRESOLVED","basis_usable":shares not in (None,0),
            "basis_issue":"" if shares not in (None,0) else warnings[0],"data_warnings":warnings,
            "history_profile":profile,"company_type":company_type,"capex_to_revenue":capex_to_revenue,
            "assets":n(latest.get("assets")),"liabilities":n(latest.get("liabilities")),
            "current_assets":economic_metric(economic,"current_assets"),"current_liabilities":economic_metric(economic,"current_liabilities"),
            "retained_earnings":economic_metric(economic,"retained_earnings"),"liquidation_floor_per_share":liquidation_floor}


def calibrate_multiples(observations: list[dict[str, Any]], company_type: str) -> dict[str, Any]:
    today=date.today(); series={"pe":[],"ps":[],"ev_ebitda":[],"fcf_yield":[]}
    for row in observations:
        price,shares=n(row.get("price")),n(row.get("shares")); revenue=n(row.get("revenue")); ni=n(row.get("net_income")); fcf=n(row.get("fcf"))
        net_debt=n(row.get("net_debt")); ebitda=n(row.get("ebitda"))
        if price is None or price<=0 or shares in (None,0): continue
        raw=row.get("filing_date") or row.get("date")
        try: day=date.fromisoformat(str(raw)[:10]) if raw else None
        except Exception: day=None
        cap=price*shares
        if ni is not None and ni>0:
            v=cap/ni
            if 3<=v<=100: series["pe"].append((day,v))
        if revenue is not None and revenue>0:
            v=cap/revenue
            if .02<=v<=40: series["ps"].append((day,v))
        if ebitda is not None and ebitda>0 and net_debt is not None:
            v=(cap+net_debt)/ebitda
            if 1<=v<=80: series["ev_ebitda"].append((day,v))
        if fcf is not None and fcf>0:
            v=fcf/cap
            if .003<=v<=.40: series["fcf_yield"].append((day,v))
    result={"source":"POINT_IN_TIME_COMPANY_HISTORY","company_type":company_type,"methods":{}}
    enough=0; max_sample=0; cutoff=date(today.year-5,today.month,min(today.day,28))
    for key,rows in series.items():
        allv=[v for _,v in rows]; five=[v for d,v in rows if d is not None and d>=cutoff]
        chosen=five if len(five)>=3 else allv if len(allv)>=3 else []; horizon="5Y" if len(five)>=3 else "10Y" if len(allv)>=3 else "INSUFFICIENT"
        max_sample=max(max_sample,len(chosen)); result["methods"][key]={"horizon":horizon,"sample_size":len(chosen),
            "p10":quantile(chosen,.10) if chosen else None,"median":median(chosen) if chosen else None,"p90":quantile(chosen,.90) if chosen else None}
        if len(chosen)>=3:
            enough+=1
            result[key]=(quantile(chosen,.90),median(chosen),quantile(chosen,.10)) if key=="fcf_yield" else (quantile(chosen,.10),median(chosen),quantile(chosen,.90))
        else: result[key]=(None,None,None)
    result["sample_size"]=max_sample; result["usable_method_count"]=enough; result["sufficient"]=enough>=2
    if not result["sufficient"]: result["warning"]="Fewer than two company-specific multiple histories have at least three point-in-time observations; fixed proxy multiples are disabled."
    return result


def default_cases(metrics: dict[str, Any], company_type: str="Generic", calibration: dict[str, Any]|None=None) -> dict[str, Any]:
    calibration=calibration or {"source":"INSUFFICIENT_HISTORY","sufficient":False}; profile=dict(metrics.get("history_profile") or {})
    life=str(profile.get("life_cycle") or "NORMAL"); hg=n((profile.get("growth") or {}).get("median")); cg=n(metrics.get("revenue_growth"))
    growth=hg if life=="CYCLICAL" and hg is not None else (cg if cg is not None else hg); growth=clamp(growth if growth is not None else 0.0,-.25,.50)
    nm,fm,opm=n(metrics.get("net_margin")),n(metrics.get("fcf_margin")),n(metrics.get("operating_margin"))
    pe,ps,ev,fy=calibration.get("pe") or (None,None,None),calibration.get("ps") or (None,None,None),calibration.get("ev_ebitda") or (None,None,None),calibration.get("fcf_yield") or (None,None,None)
    policy=dict(metrics.get("valuation_policy") or {}); exclusions=set(policy.get("method_exclusions") or [])
    nd,fcf=n(metrics.get("net_debt")),n(metrics.get("fcf")); capexr=n(metrics.get("capex_to_revenue"))
    asset_light=opm is not None and opm>=.20 and (capexr is None or capexr<=.08); low_margin=opm is not None and opm<=.06
    leveraged=nd is not None and fcf not in (None,0) and nd>max(abs(fcf)*3.0,0.0)
    weights={"pe":.20,"ps":0.0,"ev_ebitda":.25,"ev_sales":0.0,"fcf_yield":.25,"dcf":.30}
    if low_margin: weights.update({"pe":.10,"ps":0.0,"ev_ebitda":.40,"fcf_yield":.25,"dcf":.25})
    if asset_light: weights.update({"pe":.15,"ps":.10,"ev_ebitda":.15,"fcf_yield":.20,"dcf":.40})
    if leveraged: weights.update({"pe":.05,"ps":0.0,"ev_ebitda":.45,"fcf_yield":.20,"dcf":.30})
    if life=="GROWTH": weights.update({"pe":.10,"ps":.10 if asset_light else 0.0,"ev_ebitda":.15,"fcf_yield":.15,"dcf":.50})
    elif life=="CYCLICAL": weights.update({"pe":.10,"ps":0.0,"ev_ebitda":.40,"fcf_yield":.20,"dcf":.30})
    if company_type=="Financial / REIT": weights={k:0.0 for k in weights}
    if n(metrics.get("net_income")) is None or n(metrics.get("net_income"))<=0 or "pe" in exclusions: weights["pe"]=0.0
    if n(metrics.get("ebitda")) is None or n(metrics.get("ebitda"))<=0 or nd is None: weights["ev_ebitda"]=0.0
    if not asset_light or low_margin or leveraged: weights["ps"]=0.0
    if fm is None or fm<=0 or "fcf_yield" in exclusions: weights["fcf_yield"]=0.0; weights["dcf"]=0.0
    risk=(n(policy.get("risk_premium_bps")) or 0.0)/10000.0+max(-.0035,min(.0100,(n(profile.get("ccc_risk_bps")) or 0.0)/10000.0))
    gh=(n(policy.get("growth_haircut_bps")) or 0.0)/10000.0; th=(n(policy.get("terminal_growth_haircut_bps")) or 0.0)/10000.0
    bs=(n(policy.get("bear_probability_shift_pts")) or 0.0)/100.0; bp=clamp(.25+bs,.10,.60); basep=clamp(.50-bs/2,.20,.70); bullp=max(0.0,1-bp-basep)
    cap=.03 if life in {"MATURE","NORMAL","CYCLICAL"} else .035; terminal=clamp(min(max(hg if hg is not None else .02,0.0),cap)-th,-.005,cap)
    mf=n(profile.get("multiple_factor")) or 1.0
    def mult(vals,i):
        v=n(vals[i]) if isinstance(vals,(tuple,list)) and len(vals)>i else None
        return v*mf if v is not None else None
    common={"_calibration":calibration,"_history_profile":profile}
    return {
      "BEAR":{"growth":clamp(growth-.05-gh,-.35,.30),"growth_end":clamp(terminal-.01,-.02,.025),"net_margin":clamp((nm or 0)-.025,-.30,.60),"fcf_margin":clamp((fm or 0)-.03,-.30,.60),
              "pe":mult(pe,0),"ps":mult(ps,0),"ev_ebitda":mult(ev,0),"ev_sales":None,"target_fcf_yield":mult(fy,0),"equity_discount_rate":.11+risk,"terminal_growth":clamp(terminal-.01,-.01,.025),"probability":bp,"manual_override":None,**common},
      "BASE":{"growth":clamp(growth-gh,-.25,.40),"growth_end":terminal,"net_margin":clamp(nm or 0,-.20,.70),"fcf_margin":clamp(fm or 0,-.20,.70),
              "pe":mult(pe,1),"ps":mult(ps,1),"ev_ebitda":mult(ev,1),"ev_sales":None,"target_fcf_yield":mult(fy,1),"equity_discount_rate":.10+risk,"terminal_growth":terminal,"probability":basep,"manual_override":None,**common},
      "BULL":{"growth":clamp(growth+.05-gh,-.15,.55),"growth_end":clamp(terminal+.005,0.0,cap),"net_margin":clamp((nm or 0)+.025,-.15,.75),"fcf_margin":clamp((fm or 0)+.03,-.15,.75),
              "pe":mult(pe,2),"ps":mult(ps,2),"ev_ebitda":mult(ev,2),"ev_sales":None,"target_fcf_yield":mult(fy,2),"equity_discount_rate":.09+risk,"terminal_growth":clamp(terminal+.005,0.0,cap),"probability":bullp,"manual_override":None,**common},
      "weights":weights,"horizon_years":5,"company_type":company_type,"calibration":calibration,"company_quality":metrics.get("company_quality") or {},"valuation_policy":policy,"history_profile":profile}


def pe_value(revenue,growth,net_margin,pe,shares):
    values=[n(x) for x in (revenue,growth,net_margin,pe,shares)]
    if any(x is None for x in values) or values[-1]==0: return None
    value=values[0]*(1+values[1])*values[2]/values[4]*values[3]; return value if value>0 else None


def ps_value(revenue,growth,multiple,shares):
    values=[n(x) for x in (revenue,growth,multiple,shares)]
    if any(x is None for x in values) or values[-1]==0: return None
    value=values[0]*(1+values[1])*values[2]/values[3]; return value if value>0 else None


def ev_ebitda_value(ebitda,growth,multiple,net_debt,shares):
    values=[n(x) for x in (ebitda,growth,multiple,net_debt,shares)]
    if any(x is None for x in values) or values[-1]==0: return None
    value=(values[0]*(1+values[1])*values[2]-values[3])/values[4]; return value if value>0 else None

def ev_sales_value(revenue: Any, growth: Any, multiple: Any, net_debt: Any, shares: Any) -> float | None:
    values = [n(x) for x in (revenue, growth, multiple, net_debt, shares)]
    if any(x is None for x in values) or values[-1] == 0:
        return None
    value = (values[0] * (1 + values[1]) * values[2] - values[3]) / values[4]
    return value if value > 0 else None


def fcf_yield_value(revenue: Any, growth: Any, fcf_margin: Any, target_yield: Any, shares: Any) -> float | None:
    values = [n(x) for x in (revenue, growth, fcf_margin, target_yield, shares)]
    if any(x is None for x in values) or values[-1] == 0 or values[3] <= 0:
        return None
    value = values[0] * (1 + values[1]) * values[2] / values[3] / values[4]
    return value if value > 0 else None



def dcf_value(revenue,growth,fcf_margin,discount_rate,terminal_growth,years,shares,*,growth_end=None):
    values=[n(x) for x in (revenue,growth,fcf_margin,discount_rate,terminal_growth,shares)]
    if any(x is None for x in values) or values[-1]<=0 or values[3]<=values[4] or values[3]<=0: return None
    rev,pv=values[0],0.0; horizon=max(1,min(int(n(years) or 5),20)); start=values[1]; end=n(growth_end)
    if end is None: end=start
    for year in range(1,horizon+1):
        frac=(year-1)/max(1,horizon-1); gy=start+(end-start)*frac; rev*=1+gy; pv+=rev*values[2]/((1+values[3])**year)
    terminal_fcf=rev*(1+values[4])*values[2]; pv+=terminal_fcf/(values[3]-values[4])/((1+values[3])**horizon)
    value=pv/values[5]; return value if value>0 else None

def robust_blend(components: dict[str, Any], weights: dict[str, Any]) -> tuple[float | None, dict[str, float], list[str]]:
    valid = [(key, n(value)) for key, value in components.items() if n(value) is not None and n(value) > 0]
    if not valid:
        return None, {}, []
    med = median([value for _, value in valid])
    adjusted: dict[str, float] = {}
    flags: list[str] = []
    for key, value in valid:
        weight = max(0.0, n(weights.get(key)) or 0.0)
        if len(valid) >= 3 and med > 0 and abs(value / med - 1.0) > .45:
            weight *= .30
            flags.append(f"{key} is >45% from the cross-method median; weight reduced 70%")
        adjusted[key] = weight
    total = sum(adjusted.values())
    if total <= 0:
        adjusted = {key: 1.0 for key, _ in valid}
        total = float(len(valid))
    effective = {key: value / total for key, value in adjusted.items()}
    blend = sum(value * effective[key] for key, value in valid)
    return blend, effective, flags


def _reference_factor(name: str, cases: dict[str, dict[str, Any]]) -> float:
    if name == "BASE":
        return 1.0
    case = cases.get(name) or {}
    base = cases.get("BASE") or {}
    ratios: list[float] = []
    for key in ("pe", "ev_sales"):
        value, base_value = n(case.get(key)), n(base.get(key))
        if value is not None and base_value not in (None, 0) and value > 0 and base_value > 0:
            ratios.append(value / base_value)
    y, base_y = n(case.get("target_fcf_yield")), n(base.get("target_fcf_yield"))
    if y not in (None, 0) and base_y not in (None, 0) and y > 0 and base_y > 0:
        ratios.append(base_y / y)
    factor = median(ratios) if ratios else (0.70 if name == "BEAR" else 1.35)
    if name == "BEAR":
        return clamp(factor, .45, .95)
    return clamp(factor, 1.05, 1.80)



def scenario_value(metrics,assumptions,weights,years=5,*,fallback_value=None,reference_price=None,reference_factor=1.0,allow_reference_fallback=True):
    flags=[]; override=n(assumptions.get("manual_override"))
    if override is not None and override>0:
        return {"fair_value":override,"pe":None,"ps":None,"ev_ebitda":None,"ev_sales":None,"fcf_yield":None,"dcf":None,"range_low":override,"range_high":override,"effective_weights":{},"flags":["Manual fair-value override active"],"quality":"MANUAL_OVERRIDE","fallback_source":None,"method_count":0}
    revenue,shares,net_debt=metrics.get("revenue"),metrics.get("shares"),metrics.get("net_debt")
    basis=bool(metrics.get("basis_usable")) and n(revenue) not in (None,0) and n(shares) not in (None,0)
    pe=ps=ev=evs=fcf=dcf=fair=None; effective={}
    if basis:
        pe=pe_value(revenue,assumptions.get("growth"),assumptions.get("net_margin"),assumptions.get("pe"),shares)
        ps=ps_value(revenue,assumptions.get("growth"),assumptions.get("ps"),shares)
        ev=ev_ebitda_value(metrics.get("ebitda"),assumptions.get("growth"),assumptions.get("ev_ebitda"),net_debt,shares)
        evs=ev_sales_value(revenue,assumptions.get("growth"),assumptions.get("ev_sales"),net_debt,shares)
        fcf=fcf_yield_value(revenue,assumptions.get("growth"),assumptions.get("fcf_margin"),assumptions.get("target_fcf_yield"),shares)
        dcf=dcf_value(revenue,assumptions.get("growth"),assumptions.get("fcf_margin"),assumptions.get("equity_discount_rate"),assumptions.get("terminal_growth"),years,shares,growth_end=assumptions.get("growth_end"))
        fair,effective,bf=robust_blend({"pe":pe,"ps":ps,"ev_ebitda":ev,"ev_sales":evs,"fcf_yield":fcf,"dcf":dcf},weights); flags.extend(bf)
    methods={"pe":pe,"ps":ps,"ev_ebitda":ev,"ev_sales":evs,"fcf_yield":fcf,"dcf":dcf}
    count=sum(1 for key,value in methods.items() if value is not None and (n(weights.get(key)) or 0)>0); fallback_source=None
    quality="INTRINSIC" if fair is not None and count>=2 else "DATA_WARNING"
    if fair is not None and count<2: flags.append("DATA WARNING: fewer than two independent valuation methods are usable; value is not decision-grade.")
    if fair is None:
        stored=n(fallback_value)
        if stored is not None and stored>0: fair=stored; fallback_source="LAST_STORED_CASE"; quality="STALE_INTRINSIC_FALLBACK"; flags.append("DATA WARNING: current intrinsic inputs are incomplete; showing the last stored case value.")
        elif allow_reference_fallback:
            ref=n(reference_price)
            if ref is not None and ref>0:
                fair=max(.01,ref*max(.01,reference_factor)); fallback_source="VERIFIED_MARKET_REFERENCE"; quality="REFERENCE_PRICE_FALLBACK"
                flags.append(f"DATA WARNING: {metrics.get('basis_issue') or 'fundamental/share-basis data incomplete'} Showing a provisional reference-price case; this is not intrinsic valuation.")
    valid=[x for x in methods.values() if x is not None and x>0]
    return {"fair_value":fair,"pe":pe,"ps":ps,"ev_ebitda":ev,"ev_sales":evs,"fcf_yield":fcf,"dcf":dcf,"range_low":quantile(valid,.25) if valid else fair,"range_high":quantile(valid,.75) if valid else fair,
            "effective_weights":effective,"flags":flags,"quality":quality,"fallback_source":fallback_source,"method_count":count}


def evaluate(metrics,cases,weights,years=5,current_price=None,*,fallback_values=None,allow_reference_fallback=True):
    fallback_values=fallback_values or {}; scenarios={}; price=n(current_price); policy=dict(metrics.get("valuation_policy") or {}); exclusions=set(policy.get("method_exclusions") or [])
    canonical=dict(weights or {})
    for method in exclusions:
        if method in canonical: canonical[method]=0.0
    for name in ("BEAR","BASE","BULL"):
        row=scenario_value(metrics,cases.get(name) or {},canonical,years,fallback_value=fallback_values.get(name),reference_price=price,reference_factor=_reference_factor(name,cases),allow_reference_fallback=allow_reference_fallback)
        row["probability"]=n((cases.get(name) or {}).get("probability")) or 0.0; scenarios[name]=row
    raw={name:scenarios[name].get("fair_value") for name in ("BEAR","BASE","BULL")}
    distribution=_monte_carlo_distribution(metrics,cases,canonical,years,price,10000)
    if distribution.get("available"):
        for name,key,pct in (("BEAR","bear",10),("BASE","base",50),("BULL","bull",90)):
            scenarios[name]["deterministic_fair_value"]=scenarios[name].get("fair_value"); scenarios[name]["fair_value"]=n(distribution.get(key)); scenarios[name]["quality"]="INTRINSIC"
            scenarios[name]["flags"]=list(scenarios[name].get("flags") or [])+[f"Scenario value is P{pct} of the deterministic 10,000-draw company-history distribution."]
    else:
        vals=[n(scenarios[name].get("fair_value")) for name in ("BEAR","BASE","BULL")]
        if all(v is not None for v in vals) and not (vals[0]<=vals[1]<=vals[2]):
            for name in ("BEAR","BASE","BULL"):
                scenarios[name]["flags"]=list(scenarios[name].get("flags") or [])+["DATA WARNING: raw deterministic scenarios inverted; values suppressed until company-history distribution evidence is sufficient."]
                scenarios[name]["fair_value"]=None; scenarios[name]["quality"]="DATA_WARNING"
            distribution=dict(distribution)|{"scenario_inversion_suppressed":True,"raw_scenarios":raw}
    for name in ("BEAR","BASE","BULL"):
        row=scenarios[name]; row["gap_pct"]=((row["fair_value"]/price-1)*100) if row.get("fair_value") is not None and price not in (None,0) else None
    total=sum(max(0.0,row["probability"]) for row in scenarios.values()); expected=None
    if total>0 and all(row.get("fair_value") is not None for row in scenarios.values()): expected=sum(row["fair_value"]*max(0.0,row["probability"]) for row in scenarios.values())/total
    qualities={str(row.get("quality") or "DATA_WARNING") for row in scenarios.values()}
    overall="INTRINSIC" if qualities=={"INTRINSIC"} else "PROVISIONAL_REFERENCE_FALLBACK" if "REFERENCE_PRICE_FALLBACK" in qualities else "PROVISIONAL_STORED_FALLBACK" if "STALE_INTRINSIC_FALLBACK" in qualities else "MANUAL_OVERRIDE" if qualities=={"MANUAL_OVERRIDE"} else "MIXED"
    warnings=list(metrics.get("data_warnings") or []); calibration=dict((cases.get("BASE") or {}).get("_calibration") or {})
    if not calibration.get("sufficient"): warnings.append(str(calibration.get("warning") or "Company-specific historical multiple evidence is insufficient; fixed proxy multiples are disabled."))
    if str((metrics.get("history_profile") or {}).get("life_cycle") or "")=="SECTOR_SPECIFIC": warnings.append("Generic industrial valuation is disabled for Financial / REIT; sector-specific P/B-ROE, excess-capital or AFFO/NAV evidence is required.")
    for row in scenarios.values(): warnings.extend(flag for flag in row.get("flags") or [] if str(flag).startswith("DATA WARNING"))
    warnings=list(dict.fromkeys(str(x) for x in warnings if x))
    ordered=all(n(scenarios[name].get("fair_value")) is not None for name in ("BEAR","BASE","BULL")) and scenarios["BEAR"]["fair_value"]<=scenarios["BASE"]["fair_value"]<=scenarios["BULL"]["fair_value"]
    return {"scenarios":scenarios,"expected_value":expected,"current_price":price,"engine_version":ENGINE_VERSION,"quality":overall,"warnings":warnings,"company_quality":metrics.get("company_quality") or {},
            "valuation_policy":policy,"valuation_impact_ledger":list(policy.get("ledger") or []),"method_exclusions":sorted(exclusions),"effective_input_weights":canonical,
            "history_profile":metrics.get("history_profile") or {},"scenario_distribution":distribution,
            "scenario_integrity":{"ordered":ordered,"raw_deterministic":raw,"rule":"Bear/Base/Bull are P10/P50/P90 of one company-specific distribution when decision-grade; inverted deterministic outputs are never published."}}

__all__ = [
    "ENGINE_VERSION", "TYPE_PRIORS", "DECISION_GRADE_QUALITIES", "canonical_valuation_quality",
    "valuation_base_quality", "valuation_is_decision_grade", "stored_model_base_quality", "infer_company_type", "metrics_from_history", "calibrate_multiples",
    "default_cases", "pe_value", "ps_value", "ev_ebitda_value", "ev_sales_value", "fcf_yield_value", "dcf_value", "robust_blend",
    "scenario_value", "evaluate", "n", "clamp", "quantile",
]
