from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from mfengine.v312.financial_flows import annual_rows, cash_flow_flow, income_statement_flow

from .extensions import db
from .models import AuditEvent, Company, FundamentalPeriod
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


def _engine_rows(company_id: int) -> list[dict]:
    rows = FundamentalPeriod.query.filter_by(company_id=company_id).order_by(FundamentalPeriod.fiscal_year).all()
    return [row.as_engine_row() for row in rows]


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
    return render_template(
        "financial_flows.html",
        company=company,
        years=available_years,
        selected_year=selected_year,
        income=income,
        cash=cash,
        selected=selected,
        latest=latest,
    )


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
        db.session.add(AuditEvent(
            actor_user_id=g.user.id,
            action="fundamentals.refresh",
            object_type="company",
            object_id=str(company.id),
            meta={"ticker": company.ticker, "source": "SEC Companyfacts", "rows": result["saved"], "engine": "3.1.12"},
        ))
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
    # Manual operating income is allowed as an explicit input, but is never disguised as a derived SEC bridge.
    row.flow_operating_income = values["operating_income"]
    row.flow_operating_income_method = "MANUAL_EXPLICIT" if values["operating_income"] is not None else None
    row.period_end = str(request.form.get("period_end", "")).strip()[:16] or row.period_end
    row.source = "MANUAL"
    row.provenance = {"note": str(request.form.get("source_note", "")).strip(), "engine_basis": "Market Forensics V3.1.12 FULL"}
    db.session.add(AuditEvent(
        actor_user_id=g.user.id,
        action="fundamentals.manual_save",
        object_type="company",
        object_id=str(company.id),
        meta={"ticker": company.ticker, "fiscal_year": fy, "source": "MANUAL"},
    ))
    db.session.commit()
    flash(f"FY{fy} saved as an explicitly manual fundamental row.", "success")
    return redirect(url_for("v312.financial_flows", ticker=company.ticker, year=fy))
