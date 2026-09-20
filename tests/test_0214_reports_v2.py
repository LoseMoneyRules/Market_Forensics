from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import re
import zipfile

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import Company, Coverage, Event, Job, MarketSnapshot, Position, Security
from mfapp.extensions import db
from mfapp.models import User
from mfapp.report_charts import chart_bundle
from mfapp.reporting import render_docx, render_docx_safe, render_pdf, render_pdf_safe
from mfapp.report_render_v2 import _flow_rows
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def sample_report(*, provisional: bool = False, complete: bool = True) -> dict:
    history = []
    for i, year in enumerate(range(2021, 2026)):
        history.append({
            "period": f"FY{year}", "revenue": 1000 + i*120, "operating_income": 130+i*20,
            "net_income": 95+i*14, "cfo": 130+i*18, "fcf": 90+i*17,
            "revenue_growth_pct": 7+i, "gross_margin_pct": 42+i*.4,
            "operating_margin_pct": 13+i*.5, "fcf_margin_pct": 9+i*.45,
            "cfo_to_net_income": 1.18+i*.02, "roic_pct": 12+i*.8,
            "net_debt_to_fcf": .9-i*.1, "inventory_to_revenue_pct": 12-i*.3,
            "receivables_to_revenue_pct": 10+i*.2, "dso": 36+i, "dio": 50-i,
            "dpo": 35, "ccc": 51+i*.2, "share_count_growth_pct": -.5+i*.1,
        })
    prices = [{"date": (date(2026, 1, 1)+timedelta(days=i)).isoformat(), "price": 38+i*.28+(i%5)*.25} for i in range(80)]
    tape_market = [{"date": (date(2026, 6, 1)+timedelta(days=i)).isoformat(), "price": 42+i*.08, "volume": 1_000_000+i*9000} for i in range(60)]
    flows = [{"date": r["date"], "net_large": ((i%7)-3)*120000, "net_whale": ((i%5)-2)*80000} for i,r in enumerate(tape_market)]
    tape_daily = [{"date": r["date"], "absorption": 48+(i%12), "short_pressure": 43+(i%10), "net_tape": 50+(i%15)-7} for i,r in enumerate(tape_market)]
    validation_samples = [
        {"date": f"20{20+i}-06-30", "price_then": 30+i*3, "bear_then": 25+i*2.5, "base_then": 36+i*3.2, "bull_then": 48+i*4, "price_1y": 38+i*3.1, "status": "DONE"}
        for i in range(5)
    ]
    quality = "PROVISIONAL_STORED_FALLBACK" if provisional else "INTRINSIC"
    data = {
        "contract_version": "0.2.14",
        "mode": "full",
        "branding": {"title": "Market Forensics", "prepared_by": "CONTROL", "footer": "Lose Money Rules", "logo_url": ""},
        "identity": {"ticker": "EXM", "company": "Example Industrial", "sector": "Industrials", "industry": "Machinery", "generated_at": "2026-09-20T18:00:00", "market_provider": "TEST", "market_as_of": "2026-09-20T17:59:00"},
        "conclusion": "LONG WATCH",
        "decision_lenses": {"value": "ATTRACTIVE", "expectations": "BALANCED", "variant": "POSITIVE EDGE", "path": "SUPPORTIVE", "model_confidence": "STRONG", "thesis_control": "CONTROLLED", "business": "GOOD", "rows": []},
        "valuation": {
            "current_price": 44.0, "bear": 34.0, "base": 62.0, "bull": 82.0, "expected_value": 59.5,
            "base_gap_pct": 40.9, "quality": quality, "base_quality": quality,
            "decision_grade": not provisional, "provisional": provisional,
            "warnings": ["Stored fallback is not intrinsic."] if provisional else [],
            "share_basis": {"shares": 100_000_000, "source": "SEC_FY_OUTSTANDING", "verified": True, "note": "Current split basis verified."},
            "weights": {"pe": .4, "ev_sales": .25, "fcf_yield": .35}, "horizon_years": 5,
            "calibration": {"source": "POINT_IN_TIME_CALIBRATION", "sample_size": 5}, "engine_version": "0.2.0",
            "scenarios": [
                {"name": "BEAR", "target": 34.0, "probability": .25, "quality": quality, "fallback_source": "", "range_low": 31.0, "range_high": 37.0, "dcf": 35.0, "methods": [{"key":"pe","label":"P/E","value":33.0,"weight":.4},{"key":"ev_sales","label":"EV / Sales","value":36.0,"weight":.25},{"key":"fcf_yield","label":"FCF Yield","value":34.5,"weight":.35}], "flags": [], "inputs": {}},
                {"name": "BASE", "target": 62.0, "probability": .50, "quality": quality, "fallback_source": "", "range_low": 58.0, "range_high": 66.0, "dcf": 63.0, "methods": [{"key":"pe","label":"P/E","value":61.0,"weight":.4},{"key":"ev_sales","label":"EV / Sales","value":64.0,"weight":.25},{"key":"fcf_yield","label":"FCF Yield","value":62.0,"weight":.35}], "flags": [], "inputs": {}},
                {"name": "BULL", "target": 82.0, "probability": .25, "quality": quality, "fallback_source": "", "range_low": 77.0, "range_high": 87.0, "dcf": 80.0, "methods": [{"key":"pe","label":"P/E","value":81.0,"weight":.4},{"key":"ev_sales","label":"EV / Sales","value":84.0,"weight":.25},{"key":"fcf_yield","label":"FCF Yield","value":82.0,"weight":.35}], "flags": [], "inputs": {}},
            ],
        },
        "thesis": {
            "thesis": "Installed base supports recurring service revenue.", "counter_evidence": "End-market demand remains cyclical.",
            "market_view": "Market prices a slow margin recovery.", "our_view": "Service mix and working capital normalization can recover faster.",
            "variant_evidence": "Margin and cash conversion are improving ahead of consensus.",
            "what_must_be_true": [{"text": "Operating margin stays above 14%."}, {"text": "FCF conversion remains above 1.0x net income."}],
            "what_proves_wrong": [{"text": "Operating margin falls below 10% for two filings."}, {"text": "Receivables keep outrunning revenue."}],
        },
        "evidence": {
            "for": [{"label":"Valuation gap","detail":"Base fair value is materially above market."},{"label":"Cash quality","detail":"CFO remains above net income."}],
            "against": [{"label":"Working capital","detail":"Receivables are still elevated."},{"label":"Cycle","detail":"Industrial demand remains mixed."}],
            "score": 1.25, "warnings": [], "blockers": [],
        },
        "business": {"summary": "Durable installed base with service mix and cyclical equipment exposure."},
        "fundamentals": {"current": {**history[-1], "period":"TTM", "comparison_basis":"PRIOR_TTM"}, "history": history, "summary": "Margins and FCF trend positively."},
        "expectations": {
            "summary": "The market implies muted growth and flat margins.",
            "implied": {"available": True, "classification": "BALANCED", "drivers": [
                {"label":"Revenue CAGR","market_implied":.03,"base":.06,"unit":"%","read":"BELOW BASE"},
                {"label":"Net margin Y5","market_implied":.09,"base":.115,"unit":"%","read":"BELOW BASE"},
                {"label":"Exit P/E","market_implied":13.5,"base":15.0,"unit":"x","read":"BELOW BASE"},
            ]},
            "rows": [{"metric":"Revenue growth","period":"FY2027","market":3.0,"ours":6.0,"unit":"%","confidence":"MEDIUM","notes":""}],
        },
        "flows": {"summary": "Revenue converts through operating income into net income with positive FCF.", "periods": [{
            "period":"FY2025","period_end":"2025-12-31",
            "income_statement":{
                "edges":[
                    {"source":"Revenue","target":"COGS","value":850,"signed_value":850,"label":"Cost of revenue","source_field":"cogs","kind":"FLOW","sign":"POSITIVE"},
                    {"source":"Revenue","target":"Gross Profit","value":630,"signed_value":630,"label":"Gross profit","source_field":"gross_profit","kind":"FLOW","sign":"POSITIVE"},
                    {"source":"Gross Profit","target":"Operating Expenses","value":410,"signed_value":410,"label":"Operating expenses","source_field":"operating_expenses","kind":"FLOW","sign":"POSITIVE"},
                    {"source":"Gross Profit","target":"Operating Income","value":220,"signed_value":220,"label":"Operating income","source_field":"operating_income","kind":"FLOW","sign":"POSITIVE"},
                    {"source":"Operating Income","target":"Pre-Tax Income","value":190,"signed_value":190,"label":"Pre-tax income","source_field":"pretax_income","kind":"FLOW","sign":"POSITIVE"},
                    {"source":"Pre-Tax Income","target":"Net Income","value":160,"signed_value":160,"label":"Net income","source_field":"net_income","kind":"FLOW","sign":"POSITIVE"}
                ],
                "signed_exceptions":[
                    {"source":"Revenue","target":"Returns / Other","value":25,"signed_value":-25,"label":"Signed exception","source_field":"other","kind":"FLOW","sign":"NEGATIVE"}
                ],
                "derived":["Other / Interest bridge = Pre-Tax Income - Operating Income"],"warnings":[]
            },
            "cash_flow":{
                "edges":[
                    {"source":"Operating Cash Flow","target":"Capital Expenditure","value":70,"signed_value":70,"label":"Capital expenditure","source_field":"capex","kind":"FLOW","sign":"POSITIVE"},
                    {"source":"Operating Cash Flow","target":"Free Cash Flow","value":120,"signed_value":120,"label":"Free cash flow","source_field":"fcf","kind":"FLOW","sign":"POSITIVE"}
                ],"signed_exceptions":[],"derived":[],"warnings":[]
            },
        }]},
        "management": {
            "summary":"Execution improved; one guidance item remains pending.",
            "engine":{"label":"MIXED / IMPROVING","coverage_pct":82,"scorable":4,"missed":1},
            "accountability":[],
            "promises":[
                {"target_period":"FY2025","metric":"operating_margin_pct","target_text":"14%+","low":14,"high":None,"unit":"%","actual":14.9,"status":"MET","comparability":"COMPARABLE"},
                {"target_period":"FY2026","metric":"revenue_growth_pct","target_text":"mid-single digit","low":None,"high":None,"unit":"%","actual":None,"status":"PENDING","comparability":"EVIDENCE_ONLY"},
            ],
            "assessments":[],
        },
        "catalysts":[{"event":"Investor day","timing":"2026-11-15","direction":"POSITIVE","status":"OPEN","type":"GUIDANCE","evidence":"Margin framework update."}],
        "bear_case":[{"risk":"Demand reset","severity":"HIGH","probability":.25,"invalidates":True,"status":"OPEN","evidence":"A severe order decline would pressure service and equipment mix."}],
        "tape":{
            "summary":"Mixed-to-supportive tape with positive large-flow impulse.",
            "metrics":{"regime":"SUPPORTIVE","rank":"B","confidence":"HIGH","net_large":850000,"net_whale":300000,"bear_pressure":44,"absorption":63,"net_tape":61},
            "market":tape_market,"daily_market":tape_market,"short_interest":[],"short_volume":[],"institutional_flow":flows,"tape_daily":tape_daily,"ats":[],
            "what_changed":"Large flow improved over the last two weeks.","what_would_change_regime":"A break below support with rising short pressure.",
        },
        "monitoring":{
            "thesis_invalidation":"Operating margin below 10% for two filings.",
            "locked_at":"2026-09-01T12:00:00","summary":"Monitor margin, FCF conversion and receivables.",
            "rules":[{"name":"Operating margin invalidation","metric":"operating_margin_pct","operator":"<","threshold":10.0,"threshold_text":"","unit":"%","severity":"FAIL","locked_pre_investment":True,"current_value":14.9,"status":"PASS","triggered":False,"last_observation":"2026-09-10T00:00:00","note":""}],
        },
        "validation":{
            "state":"VALIDATED","available":True,"status":"DONE","sample_count":5,"reliability":72.5,"lookback_years":8,"history_span":"2020-06-30 to 2024-06-30",
            "valuation_accuracy":74.0,"direction_accuracy":70.0,"range_coverage":80.0,"assumption_accuracy":66.0,
            "summary":{"calibration_insight":"Base cases have been slightly conservative."},"samples":validation_samples,
        },
        "sources":[{"provider":"SEC","type":"10-K","title":"Example Industrial 2025 10-K","accession":"000000","published_at":"2026-02-15","retrieved_at":"2026-09-20T10:00:00","url":"https://example.invalid/10k","document":"10-K"}],
        "price_history":prices,
        "readiness":{"done":12,"total":13,"ready_to_validate":True},
        "triangulation":{"available":True,"method":"SIC peer overlay","sic":"3569","peers":["AAA","BBB"],"signals":[]},
        "data_contract":{"materialized_cache_event":123,"cache_generated_at":"2026-09-20T17:00:00","provider_refresh_started":False,"heavy_analytics_started":False},
    }
    if not complete:
        data["fundamentals"]={"current":{},"history":[],"summary":""}
        data["flows"]={"summary":"","periods":[]}
        data["management"]={"summary":"","engine":{},"accountability":[],"promises":[],"assessments":[]}
        data["tape"]={"summary":"","metrics":{},"market":[],"daily_market":[],"short_interest":[],"short_volume":[],"institutional_flow":[],"tape_daily":[],"ats":[],"what_changed":"","what_would_change_regime":""}
        data["validation"]={"state":"NOT RUN","available":False,"status":"NOT RUN","sample_count":0,"samples":[]}
        data["sources"]=[]
        data["price_history"]=[]
    return data


def docx_text(stream: BytesIO) -> str:
    stream.seek(0)
    with zipfile.ZipFile(stream) as z:
        xml=z.read("word/document.xml").decode("utf-8")
    xml=re.sub(r"<w:tab[^>]*/>","\t",xml)
    xml=re.sub(r"</w:p>","\n",xml)
    return re.sub(r"<[^>]+>","",xml)


def test_0214_executive_full_pdf_and_word_are_valid_and_decision_first():
    executive=sample_report();executive["mode"]="executive"
    epdf=render_pdf(executive)
    assert epdf.getvalue().startswith(b"%PDF") and len(epdf.getvalue())>5000
    # Executive may use a second page to preserve readable type, but never becomes a long report.
    assert 1 <= len(re.findall(rb"/Type\s*/Page\b", epdf.getvalue())) <= 2
    full=sample_report()
    fpdf=render_pdf(full)
    docx=render_docx(full)
    assert fpdf.getvalue().startswith(b"%PDF") and len(fpdf.getvalue())>10000
    assert docx.getvalue().startswith(b"PK") and len(docx.getvalue())>10000
    text=docx_text(docx)
    upper=text.upper()
    assert upper.index("RESEARCH CONCLUSION") < upper.index("BASE TARGET")
    assert upper.index("RESEARCH CONCLUSION") < upper.index("VALUATION")
    for token in (
        "CURRENT PRICE","BEAR","BASE","BULL","BASE GAP","VALUATION QUALITY",
        "VALUE LENS","EXPECTATIONS","VARIANT","PATH","MODEL CONFIDENCE","THESIS CONTROL",
        "LONG WATCH","$62.00",
    ):
        assert token in upper


def test_0214_intrinsic_and_provisional_are_impossible_to_confuse():
    intrinsic=docx_text(render_docx(sample_report(provisional=False)))
    provisional=docx_text(render_docx(sample_report(provisional=True)))
    assert "PROVISIONAL VALUATION" not in intrinsic
    assert "PROVISIONAL VALUATION" in provisional
    assert "PROVISIONAL STORED FALLBACK" in provisional


def test_0214_required_charts_are_native_and_degrade_cleanly():
    charts=chart_bundle(sample_report())
    for key in ("valuation_map","revenue_profitability","cash_conversion","working_capital","price_context","tape_price_flow","tape_pressure","validation"):
        assert key in charts
        assert charts[key].getvalue().startswith(b"\x89PNG")
        assert len(charts[key].getvalue())>1000
    partial=chart_bundle(sample_report(complete=False))
    assert "valuation_map" in partial
    for key in ("revenue_profitability","cash_conversion","working_capital","price_context","tape_price_flow","tape_pressure","validation"):
        assert key not in partial


def test_0214_full_word_contains_management_tape_validation_monitoring_and_sources():
    text=docx_text(render_docx(sample_report()))
    upper=text.upper()
    for token in (
        "MANAGEMENT","PROMISES","FY2025","MET","PENDING","TAPE &AMP; POSITIONING",
        "SUPPORTIVE","THESIS INVALIDATION / MONITORING","OPERATING MARGIN INVALIDATION",
        "VALIDATION","VALIDATED","72.5%","SOURCES / AUDIT","EXAMPLE INDUSTRIAL 2025 10-K",
    ):
        assert token in upper or token.replace("&AMP;","&") in upper


def test_0214_valuation_weights_and_real_financial_flow_payload_are_rendered():
    text=docx_text(render_docx(sample_report()))
    upper=text.upper()
    assert "EFFECTIVE METHOD WEIGHTS" in upper
    for token in ("P/E WEIGHT","EV/SALES WEIGHT","FCF YIELD WEIGHT","40.0%","25.0%","35.0%"):
        assert token in upper
    # FinancialFlow stores edges + signed_exceptions, not a synthetic bridge_steps payload.
    for token in ("FINANCIAL FLOWS","INCOME STATEMENT","PRE-TAX INCOME","SIGNED EXCEPTION","-$25.00","FREE CASH FLOW"):
        assert token in upper
    ledger=_flow_rows(sample_report())
    assert any(row["flow"]=="Cash flow" and row["route"]=="Operating Cash Flow -> Free Cash Flow" for row in ledger)
    assert any(row["value"]==-25 for row in ledger)


def test_0214_optional_sections_never_break_rich_or_fallback_artifacts(monkeypatch):
    data=sample_report(complete=False)
    pdf=render_pdf(data);docx=render_docx(data)
    assert pdf.getvalue().startswith(b"%PDF")
    assert docx.getvalue().startswith(b"PK")
    import mfapp.reporting as reporting
    monkeypatch.setattr(reporting,"render_pdf",lambda *_a,**_k: (_ for _ in ()).throw(RuntimeError("rich fail")))
    monkeypatch.setattr(reporting,"render_docx",lambda *_a,**_k: (_ for _ in ()).throw(RuntimeError("rich fail")))
    assert render_pdf_safe(data).getvalue().startswith(b"%PDF")
    assert render_docx_safe(data).getvalue().startswith(b"PK")


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY",Fernet.generate_key().decode())
    return create_app({
        "TESTING":True,"SECRET_KEY":"0214-tests",
        "SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'0214.db'}",
        "WTF_CSRF_ENABLED":False,"AUTO_MIGRATE":False,
    })


def seed_workspace(app):
    with app.app_context():
        db.create_all()
        user=User(email="control@example.com",display_name="Control",role="CONTROL",password_hash=hash_password("abcdefghijklmnop"),totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),is_active=True)
        company=Company(legal_name="Example Co",display_name="Example Co",sector="Industrials",industry="Machinery")
        db.session.add_all([user,company]);db.session.flush()
        security=Security(company_id=company.id,ticker="EXM",exchange="NYSE",currency="USD",validation_source="TEST",active=True,is_primary=True)
        db.session.add(security);db.session.flush()
        coverage=Coverage(user_id=user.id,security_id=security.id,status="RESEARCH",research_state="UNDER_REVIEW")
        db.session.add(coverage);db.session.flush();ensure_workspace(coverage,user.id)
        db.session.add(MarketSnapshot(security_id=security.id,provider="TEST",price=Decimal("40"),currency="USD",as_of=datetime.now(timezone.utc).replace(tzinfo=None),quality="OBSERVED",payload={}))
        db.session.commit()
        return user.id,company.id,security.id,coverage.id


def login(client,user_id):
    with client.session_transaction() as session:
        session["user_id"]=user_id;session["view_as"]="CONTROL"


def test_0214_report_get_does_not_enqueue_refresh_and_audit_failure_is_isolated(tmp_path,monkeypatch):
    app=make_app(tmp_path,monkeypatch)
    uid,company_id,security_id,coverage_id=seed_workspace(app)
    with app.app_context():
        coverage=db.session.get(Coverage,coverage_id)
        coverage.research.thesis="Stored thesis"
        coverage.risk_plan.entry_conditions="PRIVATE_ENTRY_CONDITION"
        db.session.add(Position(user_id=uid,security_id=security_id,shares=Decimal("321.5"),avg_cost=Decimal("27.75"),currency="USD",notes="PRIVATE_POSITION_NOTE"))
        db.session.add(Event(company_id=company_id,event_type=f"RESEARCH_CACHE_{coverage_id}",title="cache",event_date=datetime.now(timezone.utc).replace(tzinfo=None),payload={
            "coverage_id":coverage_id,"ticker":"EXM",
            "valuation":{"current_price":40,"bear":30,"base":55,"bull":70,"expected_value":53,"quality":"INTRINSIC","base_quality":"INTRINSIC","decision_grade":True},
            "intelligence":{"action":"WAIT","stance":"WATCH","bias":"NEUTRAL","confidence":"MEDIUM","score":0.5,"positives":1,"negatives":0,"warnings":[],"blockers":[],"signals":[],"top_signals":[],"supporting_evidence":[],"opposing_evidence":[],"base_gap_pct":37.5,"validation_state":"NOT RUN"},
            "decision_lenses":{"rows":[],"business":"MIXED","value":"ATTRACTIVE","expectations":"BALANCED","variant":"POSSIBLE","path":"UNCLEAR","model_confidence":"UNVALIDATED","thesis_control":"UNRESOLVED","research_conclusion":"LONG WATCH","implied_expectations":{"available":False,"drivers":[]}},
            "brief":{"price":40,"bear":30,"base":55,"bull":70,"base_gap_pct":37.5,"confidence":"MEDIUM"},
            "synthesis":{"why_now":[],"why_not_yet":[],"what_changes":[],"what_kills":[]},
            "management":{},"management_accountability":[],"management_promises":[],
            "tape":{"months":12,"market":[],"daily_market":[],"short_interest":[],"short_volume":[],"institutional_flow":[],"tape_daily":[],"ats":[],"metrics":{"regime":"MIXED","confidence":"MEDIUM"}},
            "tape_metrics":{"regime":"MIXED","confidence":"MEDIUM"},
        }))
        db.session.commit()
        before=Job.query.count()

    def forbidden_enqueue(*_a,**_k):
        raise AssertionError("report generation must not enqueue refresh or heavy analytics")
    monkeypatch.setattr("mfapp.routes.enqueue_job",forbidden_enqueue)
    monkeypatch.setattr("mfapp.routes_publish.audit",lambda *_a,**_k: (_ for _ in ()).throw(RuntimeError("audit write failed")))
    client=app.test_client();login(client,uid)
    response=client.get("/company/EXM/report/pdf?mode=full")
    assert response.status_code==200 and response.data.startswith(b"%PDF")
    with app.app_context():
        assert Job.query.count()==before
        from mfapp.routes import _ctx
        from flask import g
        with app.test_request_context("/company/EXM/report/pdf"):
            g.user=db.session.get(User,uid)
            ctx=_ctx("EXM",queue_recalc=False)
            from mfapp.reporting import research_report_data
            payload=research_report_data(ctx,mode="full")
            text=str(payload)
            assert "PRIVATE_ENTRY_CONDITION" not in text
            assert "PRIVATE_POSITION_NOTE" not in text
            assert "321.5" not in text
            assert payload["data_contract"]["provider_refresh_started"] is False
            assert payload["data_contract"]["heavy_analytics_started"] is False


def test_0214_report_engine_is_materialized_only_and_production_rich_backend_remains_required():
    contract=Path("mfapp/report_contract.py").read_text()
    route=Path("mfapp/routes_publish.py").read_text()
    routes=Path("mfapp/routes.py").read_text()
    requirements=Path("requirements-reporting.txt").read_text()
    workflow=Path(".github/workflows/tests.yml").read_text()
    for forbidden in ("SECClient","Alpaca","FINRAClient","fredapi","evaluate_promises(","enqueue_job("):
        assert forbidden not in contract
    assert "_ctx(ticker, queue_recalc=False)" in route
    assert "stale_cache and active_recalc is None and queue_recalc" in routes
    assert "python-docx" in requirements and "reportlab" in requirements
    assert 'report_backend_status()["backend"] == "rich"' in workflow
