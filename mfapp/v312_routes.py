from __future__ import annotations

import math
from copy import deepcopy

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from mfengine.v312.financial_flows import annual_rows, cash_flow_flow, income_statement_flow
from mfengine.v312.valuation_core import TYPE_PRIORS, default_cases, scenario_values

from .extensions import db
from .models import AuditEvent, Company, FundamentalPeriod, MarketSnapshot, ResearchWorkspace
from .research_core import merged_payload
from .secdata import SECRefreshError, refresh_company_fundamentals
from .security import login_required, role_required

bp = Blueprint("v312", __name__)


def _control_only() -> None:
    if not getattr(g, "user", None):
        abort(401)
    if g.user.role != "CONTROL":
        abort(403)
    if str(getattr(g, "view_role", "CONTROL") or "CONTROL").upper() != "CONTROL":
        abort(404)


def _company(ticker: str) -> Company:
    return Company.query.filter_by(ticker=ticker.upper()).first_or_404()


def _workspace(company: Company) -> ResearchWorkspace:
    row = ResearchWorkspace.query.filter_by(company_id=company.id).first()
    if row is None:
        row = ResearchWorkspace(company_id=company.id, payload=merged_payload({}), updated_by=g.user.id if getattr(g, "user", None) else None)
        db.session.add(row)
        db.session.flush()
    return row


def _engine_rows(company_id: int) -> list[dict]:
    rows = FundamentalPeriod.query.filter_by(company_id=company_id).order_by(FundamentalPeriod.fiscal_year).all()
    return [row.as_engine_row() for row in rows]


def _finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except Exception:
        return False


def _current_metrics(company: Company, valuation_config: dict | None = None) -> tuple[dict, FundamentalPeriod | None]:
    rows = FundamentalPeriod.query.filter_by(company_id=company.id, period_type="FY").order_by(FundamentalPeriod.fiscal_year).all()
    latest = rows[-1] if rows else None
    config = valuation_config or {}
    if not latest:
        return {"valuation_share_basis_usable": False, "valuation_basis_issue": "No annual fundamentals are stored yet."}, None
    revenue = latest.revenue
    net_income = latest.net_income
    operating_income = latest.operating_income
    fcf = latest.fcf
    net_margin = (net_income / revenue) if _finite(net_income) and _finite(revenue) and revenue else None
    operating_margin = (operating_income / revenue) if _finite(operating_income) and _finite(revenue) and revenue else None
    fcf_margin = (fcf / revenue) if _finite(fcf) and _finite(revenue) and revenue else None
    tax_rate = (latest.tax / latest.pretax) if _finite(latest.tax) and _finite(latest.pretax) and latest.pretax and latest.pretax > 0 else None
    cfo_ni = (latest.cfo / net_income) if _finite(latest.cfo) and _finite(net_income) and net_income else None
    cagr3 = None
    usable = [r for r in rows if _finite(r.revenue) and r.revenue and r.fiscal_year]
    if len(usable) >= 4:
        a, b = usable[-4], usable[-1]
        gap = max(1, int(b.fiscal_year) - int(a.fiscal_year))
        if a.revenue > 0 and b.revenue > 0:
            cagr3 = (float(b.revenue) / float(a.revenue)) ** (1 / gap) - 1
    net_debt = (float(latest.debt) - float(latest.cash)) if _finite(latest.debt) and _finite(latest.cash) else None
    share_source = str(config.get("share_source") or "USER_VERIFIED_CURRENT")
    shares = config.get("current_shares")
    if not _finite(shares) or float(shares) <= 0:
        if share_source == "SEC_FY_OUTSTANDING" and _finite(latest.shares_outstanding):
            shares = latest.shares_outstanding
        elif share_source == "DILUTED_WA_FALLBACK" and _finite(latest.diluted_shares):
            shares = latest.diluted_shares
        else:
            shares = None
    verified = bool(config.get("share_basis_verified")) and _finite(shares) and float(shares) > 0
    issue = ""
    if not verified:
        issue = "Current per-share denominator is not yet verified on the current split/reverse-split basis. Confirm a current share count and its corporate-action basis before trusting price targets."
    return {
        "revenue": revenue,
        "net_income": net_income,
        "operating_income": operating_income,
        "fcf": fcf,
        "net_margin": net_margin,
        "operating_margin": operating_margin,
        "fcf_margin": fcf_margin,
        "tax_rate": tax_rate,
        "cfo_ni": cfo_ni,
        "cagr3": cagr3,
        "net_debt": net_debt,
        "valuation_shares": float(shares) if _finite(shares) else None,
        "valuation_share_basis_usable": verified,
        "valuation_basis_issue": issue,
        "valuation_share_source": share_source,
        "valuation_share_confidence": "MEDIUM" if verified else "LOW",
    }, latest


def _parse_float(name: str, default=None):
    raw = str(request.form.get(name, "")).strip().replace(",", "")
    if not raw:
        return default
    try:
        value = float(raw)
        return value if math.isfinite(value) else default
    except Exception:
        return default


@bp.get("/research/<ticker>/financial-flows")
@login_required
def financial_flows(ticker):
    _control_only()
    company = _company(ticker)
    rows = annual_rows(_engine_rows(company.id))
    available_years = [r["fiscal_year"] for r in rows]
    requested = request.args.get("year", type=int)
    selected_year = requested if requested in available_years else (available_years[-1] if available_years else None)
    selected = next((r for r in rows if r["fiscal_year"] == selected_year), None)
    income = income_statement_flow(selected or {}) if selected else {"ok": False, "reason": "No annual fundamentals stored yet.", "rows": []}
    cash = cash_flow_flow(selected or {}) if selected else {"ok": False, "reason": "No annual fundamentals stored yet.", "rows": []}
    latest = FundamentalPeriod.query.filter_by(company_id=company.id).order_by(FundamentalPeriod.fiscal_year.desc()).first()
    return render_template("financial_flows.html", company=company, years=available_years, selected_year=selected_year, income=income, cash=cash, selected=selected, latest=latest)


@bp.post("/research/<ticker>/refresh-fundamentals")
@role_required("CONTROL")
def refresh_fundamentals(ticker):
    _control_only()
    company = _company(ticker)
    try:
        result = refresh_company_fundamentals(company, g.user.id)
    except SECRefreshError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash(f"SEC refresh failed safely ({type(exc).__name__}). Existing last-good fundamentals were preserved.", "error")
    else:
        db.session.add(AuditEvent(actor_user_id=g.user.id, action="fundamentals.refresh", object_type="company", object_id=str(company.id), meta={"ticker": company.ticker, "source": "SEC Companyfacts", "rows": result["saved"], "engine": "3.1.12"}))
        db.session.commit()
        flash(f"{company.ticker}: {result['saved']} annual SEC rows refreshed for the V3.1.12 engine.", "success")
    return redirect(url_for("v312.financial_flows", ticker=company.ticker))


@bp.post("/research/<ticker>/fundamentals/manual")
@role_required("CONTROL")
def save_manual_fundamental(ticker):
    """Explicit manual annual row. Never silently overwrites SEC provenance as SEC data."""
    _control_only()
    company = _company(ticker)
    try:
        fy = int(request.form.get("fiscal_year", ""))
    except Exception:
        flash("Enter a valid fiscal year.", "error")
        return redirect(url_for("v312.financial_flows", ticker=company.ticker))
    if fy < 1900 or fy > 2200:
        abort(400)

    def number(name):
        raw = str(request.form.get(name, "")).strip().replace(",", "")
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            raise ValueError(name)

    fields = ["revenue", "gross_profit", "operating_income", "pretax", "tax", "net_income", "cfo", "capex", "buybacks", "dividends"]
    try:
        values = {name: number(name) for name in fields}
    except ValueError as exc:
        flash(f"Invalid numeric value: {exc.args[0]}.", "error")
        return redirect(url_for("v312.financial_flows", ticker=company.ticker, year=fy))

    row = FundamentalPeriod.query.filter_by(company_id=company.id, period_key=f"FY{fy}").first()
    if row is None:
        row = FundamentalPeriod(company_id=company.id, period_key=f"FY{fy}", period_type="FY", fiscal_year=fy)
        db.session.add(row)
    for name, value in values.items():
        setattr(row, name, value)
    row.fcf = values["cfo"] - values["capex"] if values["cfo"] is not None and values["capex"] is not None else None
    row.flow_operating_income = values["operating_income"]
    row.flow_operating_income_method = "MANUAL_EXPLICIT" if values["operating_income"] is not None else None
    row.period_end = str(request.form.get("period_end", "")).strip()[:16] or row.period_end
    row.source = "MANUAL"
    row.provenance = {"note": str(request.form.get("source_note", "")).strip(), "engine_basis": "Market Forensics V3.1.12 FULL"}
    db.session.add(AuditEvent(actor_user_id=g.user.id, action="fundamentals.manual_save", object_type="company", object_id=str(company.id), meta={"ticker": company.ticker, "fiscal_year": fy, "source": "MANUAL"}))
    db.session.commit()
    flash(f"FY{fy} saved as an explicitly manual fundamental row.", "success")
    return redirect(url_for("v312.financial_flows", ticker=company.ticker, year=fy))


@bp.route("/research/<ticker>/valuation", methods=["GET", "POST"])
@login_required
def valuation(ticker):
    _control_only()
    company = _company(ticker)
    workspace = _workspace(company)
    payload = merged_payload(workspace.payload)
    config = deepcopy(payload.get("v312_valuation") or {})
    company_type = str(config.get("company_type") or "Generic")
    if company_type not in TYPE_PRIORS:
        company_type = "Generic"
    metrics, latest = _current_metrics(company, config)

    if request.method == "POST":
        company_type = str(request.form.get("company_type") or company_type)
        if company_type not in TYPE_PRIORS:
            company_type = "Generic"
        config["company_type"] = company_type
        config["share_source"] = str(request.form.get("share_source") or "USER_VERIFIED_CURRENT")
        config["current_shares"] = _parse_float("current_shares")
        config["share_basis_verified"] = request.form.get("share_basis_verified") in {"1", "on", "true", "yes"}
        config["share_basis_note"] = str(request.form.get("share_basis_note") or "").strip()
        config["horizon_years"] = max(1, min(15, int(_parse_float("horizon_years", 5) or 5)))
        default_metrics, latest = _current_metrics(company, config)
        defaults = default_cases(default_metrics, company_type)
        cases = {}
        for case in ("bear", "base", "bull"):
            d = (config.get("cases") or {}).get(case) or defaults[case]
            cases[case] = {
                "growth": _parse_float(f"{case}_growth", d.get("growth")),
                "net_margin": _parse_float(f"{case}_net_margin", d.get("net_margin")),
                "fcf_margin": _parse_float(f"{case}_fcf_margin", d.get("fcf_margin")),
                "pe": _parse_float(f"{case}_pe", d.get("pe")),
                "ev_sales": _parse_float(f"{case}_ev_sales", d.get("ev_sales")),
                "target_fcf_yield": _parse_float(f"{case}_target_fcf_yield", d.get("target_fcf_yield")),
                "equity_discount_rate": _parse_float(f"{case}_equity_discount_rate", d.get("equity_discount_rate")),
                "terminal_growth": _parse_float(f"{case}_terminal_growth", d.get("terminal_growth")),
                "prob": _parse_float(f"{case}_prob", d.get("prob")),
                "manual_override": _parse_float(f"{case}_manual_override", None),
            }
        config["cases"] = cases
        defaults_weights = defaults["weights"]
        config["weights"] = {
            "pe": max(0.0, _parse_float("weight_pe", defaults_weights["pe"]) or 0.0),
            "ev_sales": max(0.0, _parse_float("weight_ev_sales", defaults_weights["ev_sales"]) or 0.0),
            "fcf_yield": max(0.0, _parse_float("weight_fcf_yield", defaults_weights["fcf_yield"]) or 0.0),
        }
        config["origin"] = "USER_REVIEWED_V3.1.12"
        metrics, latest = _current_metrics(company, config)
        scenarios, expected = scenario_values(metrics, config["cases"], config["weights"], config["horizon_years"])
        config["last_expected_value"] = expected
        config["last_results"] = scenarios
        payload["v312_valuation"] = config
        # Decide consumes the same current-case values. Never replace them with guessed prices.
        payload["bear_value"] = scenarios["bear"].get("fair_value")
        payload["base_value"] = scenarios["base"].get("fair_value")
        payload["bull_value"] = scenarios["bull"].get("fair_value")
        payload["bear_prob"] = config["cases"]["bear"].get("prob") or .25
        payload["base_prob"] = config["cases"]["base"].get("prob") or .50
        payload["bull_prob"] = config["cases"]["bull"].get("prob") or .25
        payload["valuation_method"] = "V3.1.12 robust blend: P/E + EV/Sales + FCF Yield; DCF shown as FCFE-style cross-check"
        payload["valuation_summary"] = "V3.1.12 current valuation model. Share-basis status: " + ("VERIFIED" if metrics.get("valuation_share_basis_usable") else "DATA REVIEW")
        workspace.payload = payload
        workspace.updated_by = g.user.id
        db.session.add(AuditEvent(actor_user_id=g.user.id, action="valuation.v312_save", object_type="company", object_id=str(company.id), meta={"ticker": company.ticker, "share_basis_verified": bool(metrics.get("valuation_share_basis_usable")), "company_type": company_type, "engine": "3.1.12"}))
        db.session.commit()
        flash("V3.1.12 valuation model saved. Decide now reads these Bear/Base/Bull outputs.", "success")
        return redirect(url_for("v312.valuation", ticker=company.ticker))

    defaults = default_cases(metrics, company_type)
    cases = deepcopy(config.get("cases") or {k: defaults[k] for k in ("bear", "base", "bull")})
    weights = deepcopy(config.get("weights") or defaults["weights"])
    years = int(config.get("horizon_years") or defaults["horizon_years"])
    scenarios, expected = scenario_values(metrics, cases, weights, years)
    snapshot = MarketSnapshot.query.filter_by(company_id=company.id).first()
    price = snapshot.price if snapshot else None
    for name in ("bear", "base", "bull"):
        fv = scenarios[name].get("fair_value")
        scenarios[name]["gap_pct"] = ((fv / price - 1) * 100.0) if _finite(fv) and _finite(price) and price and price > 0 else None
    return render_template("valuation_v312.html", company=company, config=config, metrics=metrics, latest=latest, company_types=list(TYPE_PRIORS), cases=cases, weights=weights, years=years, scenarios=scenarios, expected=expected, snapshot=snapshot)
