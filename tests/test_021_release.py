from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import CalculationRun, Company, Coverage, Event, Job, MarketSnapshot, RefreshRun, Security
from mfapp.extensions import db
from mfapp.jobs import enqueue_job, recover_stale_running_jobs
from mfapp.models import AuditEvent, User
from mfapp.research_cache import cache_event_type
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "021-tests",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '021.db'}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_workspace(app):
    with app.app_context():
        db.create_all()
        user = User(
            email="control021@example.com", display_name="Control", role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"), is_active=True,
        )
        company = Company(legal_name="Example Co", display_name="Example Co", sector="Industrials", industry="Machinery")
        db.session.add_all([user, company]); db.session.flush()
        security = Security(company_id=company.id, ticker="EXM", exchange="NYSE", currency="USD", validation_source="TEST", active=True, is_primary=True)
        db.session.add(security); db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=security.id, status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage); db.session.flush(); ensure_workspace(coverage, user.id)
        db.session.add(MarketSnapshot(security_id=security.id, provider="TEST", price=Decimal("40"), currency="USD", as_of=datetime.now(timezone.utc).replace(tzinfo=None), quality="OBSERVED", payload={}))
        db.session.commit()
        return user.id, company.id, security.id, coverage.id


def login_control(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def seed_cache(app, coverage_id, company_id, *, ready=False):
    with app.app_context():
        payload = {
            "coverage_id": coverage_id, "ticker": "EXM",
            "valuation": {"current_price": 40, "bear": 30, "base": 55, "bull": 70, "expected_value": 53},
            "readiness": {
                "done": 13 if ready else 0, "evidence_ready": 13 if ready else 0, "total": 13,
                "gates": [], "ready_to_validate": ready,
                "validation": {"state": "NOT RUN", "run_id": None, "status": None, "samples": 0, "reliability": None},
                "bias_flags": [],
            },
            "intelligence": {
                "action": "WAIT", "stance": "WATCH", "bias": "NEUTRAL", "confidence": "MEDIUM",
                "score": 0.5, "positives": 1, "negatives": 0, "warnings": [], "blockers": [],
                "signals": [], "top_signals": [], "supporting_evidence": [], "opposing_evidence": [],
                "base_gap_pct": 37.5, "validation_state": "NOT RUN", "buy_threshold": 2.5, "sell_threshold": -2.5,
            },
            "decision_lenses": {
                "rows": [], "business": "MIXED", "value": "ATTRACTIVE", "expectations": "BALANCED",
                "variant": "POSSIBLE", "path": "UNCLEAR", "model_confidence": "UNVALIDATED",
                "thesis_control": "UNRESOLVED", "research_conclusion": "RESEARCH INCOMPLETE",
                "implied_expectations": {"available": False, "classification": "UNAVAILABLE", "drivers": []},
            },
            "brief": {"price": 40, "bear": 30, "base": 55, "bull": 70, "base_gap_pct": 37.5, "confidence": "MEDIUM", "horizon_years": 5, "target_year": 2031, "reasons": []},
            "synthesis": {"why_now": [], "why_not_yet": [], "what_changes": [], "what_kills": [], "micro_for": [], "micro_against": [], "macro": [], "invalidation": "", "next": []},
        }
        db.session.add(Event(company_id=company_id, event_type=cache_event_type(coverage_id), title="EXM research cache", event_date=datetime.now(timezone.utc).replace(tzinfo=None), payload=payload))
        db.session.commit()


def test_021_cancel_queued_and_running_jobs_is_terminal_and_audited(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    uid, company_id, security_id, coverage_id = seed_workspace(app)
    client = app.test_client(); login_control(client, uid)

    with app.app_context():
        queued = enqueue_job("RECALCULATE", user_id=uid, company_id=company_id, security_id=security_id, payload={"coverage_id": coverage_id})
        queued_id = queued.id
    response = client.post(f"/jobs/{queued_id}/cancel")
    assert response.status_code == 302
    with app.app_context():
        job = db.session.get(Job, queued_id)
        assert job.status == "CANCELLED" and job.locked_at is None and job.finished_at is not None
        assert AuditEvent.query.filter_by(action="job.cancel", object_id=str(queued_id)).first() is not None

    terminated = {}
    import mfapp.routes_publish as publish_routes
    monkeypatch.setattr(publish_routes, "terminate_job_executor", lambda pid: terminated.setdefault("pid", pid) == pid)

    with app.app_context():
        running = Job(job_type="DISCOVERY_SCAN", status="RUNNING", priority=10, user_id=uid, payload={}, result={"partial": {"kept": True}, "_executor": {"pid": 424242}}, attempts=1, max_attempts=3, run_after=datetime.utcnow(), locked_at=datetime.utcnow(), started_at=datetime.utcnow())
        db.session.add(running); db.session.flush()
        db.session.add(RefreshRun(job_id=running.id, refresh_type=running.job_type, status="RUNNING", started_at=running.started_at))
        db.session.add(CalculationRun(calculation_type=running.job_type, calculation_version="0.2.0", inputs={"job_id": running.id}, status="RUNNING", started_at=running.started_at))
        db.session.commit(); running_id=running.id
    response = client.post(f"/jobs/{running_id}/cancel")
    assert response.status_code == 302 and terminated["pid"] == 424242
    with app.app_context():
        job=db.session.get(Job,running_id)
        assert job.status=="CANCELLED" and job.locked_at is None
        assert (job.result or {}).get("partial",{}).get("kept") is True
        assert RefreshRun.query.filter_by(job_id=running_id).first().status=="CANCELLED"
        calc=next(row for row in CalculationRun.query.filter_by(calculation_type="DISCOVERY_SCAN").all() if int((row.inputs or {}).get("job_id") or 0)==running_id)
        assert calc.status=="CANCELLED"


def test_021_stale_running_recovery_requeues_then_fails_when_attempts_exhausted(tmp_path, monkeypatch):
    app=make_app(tmp_path,monkeypatch)
    uid, company_id, security_id, coverage_id=seed_workspace(app)
    old=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=10)
    with app.app_context():
        job=Job(job_type="DISCOVERY_SCAN",status="RUNNING",priority=10,user_id=uid,company_id=company_id,security_id=security_id,payload={"coverage_id":coverage_id},attempts=1,max_attempts=3,run_after=old,locked_at=old,started_at=old)
        db.session.add(job);db.session.flush()
        db.session.add(RefreshRun(job_id=job.id,refresh_type=job.job_type,status="RUNNING",started_at=old))
        db.session.add(CalculationRun(coverage_id=coverage_id,calculation_type=job.job_type,calculation_version="0.2.0",inputs={"job_id":job.id},status="RUNNING",started_at=old))
        db.session.commit();jid=job.id
        assert recover_stale_running_jobs(user_id=uid,stale_after_minutes=1)==1
        job=db.session.get(Job,jid)
        assert job.status=="QUEUED" and job.locked_at is None and job.started_at is None
        assert "Recovered stale RUNNING lease" in job.error_message
        assert RefreshRun.query.filter_by(job_id=jid).first().status=="FAILED"

        job.status="RUNNING";job.attempts=job.max_attempts;job.locked_at=old;job.started_at=old
        db.session.add(RefreshRun(job_id=jid,refresh_type=job.job_type,status="RUNNING",started_at=old))
        db.session.add(CalculationRun(coverage_id=coverage_id,calculation_type=job.job_type,calculation_version="0.2.0",inputs={"job_id":jid},status="RUNNING",started_at=old))
        db.session.commit()
        assert recover_stale_running_jobs(user_id=uid,stale_after_minutes=1)==1
        job=db.session.get(Job,jid)
        assert job.status=="FAILED" and job.finished_at is not None and job.locked_at is None


def test_021_publish_readiness_and_export_location_contract(tmp_path, monkeypatch):
    app=make_app(tmp_path,monkeypatch)
    uid,company_id,_,coverage_id=seed_workspace(app)
    seed_cache(app,coverage_id,company_id,ready=False)
    client=app.test_client();login_control(client,uid)
    html=client.get("/company/EXM/overview").get_data(as_text=True)
    assert re.search(r'<button class="button primary" type="submit" disabled aria-disabled="true">Publish</button>',html)
    assert html.count("Executive PDF")==1 and html.count("Full PDF")==1 and html.count("Full Word")==1
    assert html.index("EXPORT RESEARCH") < html.index("Executive PDF")
    business=client.get("/company/EXM/business").get_data(as_text=True)
    assert "Executive PDF" not in business and "Full PDF" not in business and "Full Word" not in business

    with app.app_context():
        event=Event.query.filter_by(company_id=company_id,event_type=cache_event_type(coverage_id)).order_by(Event.id.desc()).first()
        payload=dict(event.payload or {}); readiness=dict(payload["readiness"]);readiness.update({"done":13,"evidence_ready":13,"ready_to_validate":True});payload["readiness"]=readiness;event.payload=payload;db.session.commit()
    ready=client.get("/company/EXM/overview").get_data(as_text=True)
    assert re.search(r'<button class="button primary" type="submit" >Publish</button>|<button class="button primary" type="submit">Publish</button>',ready)
    assert 'disabled aria-disabled="true">Publish</button>' not in ready


def test_021_report_exports_work_and_are_control_only(tmp_path, monkeypatch):
    app=make_app(tmp_path,monkeypatch)
    uid,company_id,_,coverage_id=seed_workspace(app);seed_cache(app,coverage_id,company_id)
    client=app.test_client();login_control(client,uid)
    executive=client.get("/company/EXM/report/pdf?mode=executive")
    full=client.get("/company/EXM/report/pdf?mode=full")
    word=client.get("/company/EXM/report/docx?mode=full")
    assert executive.status_code==200 and executive.data.startswith(b"%PDF-")
    assert full.status_code==200 and full.data.startswith(b"%PDF-")
    assert word.status_code==200 and word.data.startswith(b"PK")
    assert Path("VERSION").read_text().strip() in executive.headers.get("Content-Disposition","")


def test_021_semantic_dark_flow_and_mobile_contracts_are_centralized():
    css=Path("mfapp/static/css/app.css").read_text()
    js=Path("mfapp/static/js/app.js").read_text()
    flows=Path("mfapp/static/js/flows.js").read_text()
    template=Path("mfapp/templates/financial_flows.html").read_text()
    base=Path("mfapp/templates/base.html").read_text()
    assert "Canonical semantic-state contract" in css
    for token in ("--semantic-positive","--semantic-negative","--semantic-caution","--semantic-info","--semantic-neutral","--semantic-cancelled"):
        assert token in css
    for state in ("ATTRACTIVE","EXPENSIVE","SUPPORTIVE","HOSTILE","MET","MISS","PASS","FAIL","STRENGTH","WEAKNESS","LONG","SHORT","RUNNING","CANCELLED"):
        assert state in js
    assert css.count('html[data-theme="dark"]{--') == 1
    assert "purple" not in css.lower() and "violet" not in css.lower()
    assert "--flow-node-bg" in css and "flow-mobile-ledger" in css
    assert "signed_exceptions" in flows and "window.MFRenderFlow" in flows
    assert "Income Statement" in template and "Cash Flow" in template and 'name="year"' in template
    assert "body.nav-open .navrail" in css and "body.nav-open .mobile-backdrop" in css
    assert "backdrop?.addEventListener('click', closeMobile)" in js and "event.key === 'Escape'" in js
    assert 'id="mf-mobile-menu"' in base


def test_021_manage_has_remove_only_and_release_is_clean():
    dashboard=Path("mfapp/templates/dashboard.html").read_text()
    company=Path("mfapp/templates/company_section.html").read_text()
    css=Path("mfapp/static/css/app.css").read_text()
    assert 'value="save"' not in dashboard
    assert 'value="archive"' in dashboard and ">Remove</button>" in dashboard
    assert company.count("Executive PDF")==1
    assert company.count("snapshot_company")==1
    assert ".publish-readiness{" in css
    assert Path("VERSION").read_text().strip()=="0.2.2"
