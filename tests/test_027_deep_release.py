from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, DecisionJournal, ResearchGateApproval, Security,
)
from mfapp.calculations import financial_metrics
from mfapp.extensions import db
from mfapp.models import User
from mfapp.readiness import research_readiness
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace
from mfapp.triangulation_engine import apply_peer_valuation_overlay
from mfapp.macro_context import exposures_for_company


def make_app(tmp_path, monkeypatch, name="027"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "027-deep-release",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_control_workspace(app, ticker="EXM"):
    with app.app_context():
        db.create_all()
        user = User(
            email=f"{ticker.lower()}027@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        company = Company(
            legal_name="Example Industrial Co",
            display_name="Example Industrial Co",
            sector="Industrials",
            industry="Industrial Machinery",
        )
        db.session.add(company)
        db.session.flush()
        security = Security(
            company_id=company.id,
            ticker=ticker,
            exchange="NYSE",
            currency="USD",
            active=True,
            is_primary=True,
        )
        db.session.add(security)
        db.session.flush()
        coverage = Coverage(
            user_id=user.id,
            security_id=security.id,
            status="RESEARCH",
            research_state="UNDER_REVIEW",
        )
        db.session.add(coverage)
        db.session.flush()
        research, risk, investment, model = ensure_workspace(coverage, user.id)
        research.business = "Evidence-backed industrial business."
        db.session.commit()
        return user.id, coverage.id, company.id


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_027_report_routes_survive_branding_failure_and_return_valid_artifacts(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "reports")
    uid, _, _ = seed_control_workspace(app)
    client = app.test_client()
    login(client, uid)

    monkeypatch.setattr(
        "mfapp.routes_publish.get_report_branding",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated production branding failure")),
    )

    pdf = client.get("/company/EXM/report/pdf?mode=full")
    assert pdf.status_code == 200
    assert pdf.mimetype == "application/pdf"
    assert pdf.data.startswith(b"%PDF")

    docx = client.get("/company/EXM/report/docx?mode=full")
    assert docx.status_code == 200
    assert docx.mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert docx.data.startswith(b"PK")


def test_027_report_download_does_not_depend_on_audit_commit(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "report_audit")
    uid, _, _ = seed_control_workspace(app)
    client = app.test_client()
    login(client, uid)

    monkeypatch.setattr(
        "mfapp.routes_publish.audit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated audit failure")),
    )
    response = client.get("/company/EXM/report/pdf?mode=executive")
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")


def test_027_readiness_approvals_are_monotonic_and_monitoring_journal_close_live(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "readiness")
    uid, coverage_id, _ = seed_control_workspace(app)
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        research, risk, _, _ = ensure_workspace(coverage, uid)
        first = research_readiness(coverage)
        business = next(row for row in first["gates"] if row["key"] == "business")
        db.session.add(ResearchGateApproval(
            coverage_id=coverage.id,
            gate_key="business",
            evidence_hash=business["evidence_hash"],
            approved_by=uid,
        ))
        risk.thesis_invalidation = "Operating margin below locked threshold for two filings."
        db.session.add(DecisionJournal(
            coverage_id=coverage.id,
            user_id=uid,
            decision="WAIT",
            thesis_snapshot={"thesis": "test"},
            risk_snapshot={},
            valuation_snapshot={},
            evidence_for="Evidence for",
            evidence_against="Evidence against",
            bias_notes="No thesis drift",
        ))
        db.session.commit()

        # Change the approved gate's evidence. Approval must remain approved until
        # CONTROL explicitly reopens it; the change remains visible as stale_approval.
        research.business = "Updated evidence-backed industrial business."
        db.session.commit()
        current = research_readiness(coverage)
        gates = {row["key"]: row for row in current["gates"]}
        assert gates["business"]["approved"] is True
        assert gates["business"]["stale_approval"] is True
        assert gates["monitoring"]["evidence_ready"] is True
        assert gates["journal"]["evidence_ready"] is True


def test_027_fundamentals_keeps_current_basis_while_expectations_stays_forward_looking(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "basis")
    uid, _, _ = seed_control_workspace(app)
    client = app.test_client()
    login(client, uid)

    expectations = client.get("/company/EXM/expectations")
    assert expectations.status_code == 200
    html = expectations.get_data(as_text=True)
    assert "expectations-basis-strip" not in html
    assert "5Y OPERATING PATH" in html
    assert "PRICE-IMPLIED EXPECTATIONS" in html
    assert "VARIANT PERCEPTION" in html

    fundamentals = client.get("/company/EXM/fundamentals")
    assert fundamentals.status_code == 200
    html = fundamentals.get_data(as_text=True)
    assert "Current basis" in html
    assert "Gross margin" in html
    assert "ROIC" in html




def test_027_gross_margin_uses_exact_revenue_cogs_bridge_without_guessing():
    metrics = financial_metrics({"revenue": 1000, "cogs": 600, "gross_profit": None}, {})
    assert round(metrics["gross_margin_pct"], 2) == 40.00
    incomplete = financial_metrics({"revenue": 1000, "cogs": None, "gross_profit": None}, {})
    assert incomplete["gross_margin_pct"] is None


def test_027_publication_snapshot_freezes_forensic_valuation_contract(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "publication_valuation")
    uid, coverage_id, _ = seed_control_workspace(app)

    monkeypatch.setattr(
        "mfapp.triangulation_engine.automatic_triangulation",
        lambda company_id, user_id=None: {"peer_value_crosscheck": {"eligible": True, "estimate": 123.0}},
    )
    monkeypatch.setattr(
        "mfapp.triangulation_engine.apply_peer_valuation_overlay",
        lambda valuation, triangulation: {
            **dict(valuation or {}),
            "base": 123.0,
            "peer_overlay": {"applied": True, "peer_estimate": 123.0, "applied_factor": 1.05},
        },
    )

    with app.app_context():
        from mfapp.services import create_snapshot
        coverage = db.session.get(Coverage, coverage_id)
        snapshot = create_snapshot(coverage, uid)
        assert snapshot.payload["valuation"]["base"] == 123.0
        assert snapshot.payload["valuation"]["peer_overlay"]["applied"] is True


def test_027_business_get_never_fetches_macro_provider(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "macro_get")
    uid, _, _ = seed_control_workspace(app)
    client = app.test_client()
    login(client, uid)
    calls = {"n": 0}

    def forbidden_fetch(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("normal GET must not fetch FRED")

    monkeypatch.setattr("mfapp.macro_context.requests.get", forbidden_fetch)
    response = client.get("/company/EXM/business")
    assert response.status_code == 200
    assert calls["n"] == 0

def test_027_peer_overlay_is_bounded_auditable_and_never_peer_only():
    base = {"bear": 70.0, "base": 100.0, "bull": 150.0, "expected_value": 105.0, "current_price": 80.0, "downside_pct": -12.5, "base_upside_pct": 25.0, "bull_upside_pct": 87.5}
    tri = {
        "peer_value_crosscheck": {
            "eligible": True,
            "estimate": 200.0,
            "peer_count": 6,
            "method_count": 3,
            "components": [{"method": "P/E", "value": 190.0}],
        }
    }
    out = apply_peer_valuation_overlay(base, tri)
    assert out["peer_overlay"]["applied"] is True
    assert out["peer_overlay"]["weight"] == 0.20
    assert out["peer_overlay"]["applied_factor"] == 1.10
    assert round(out["base"], 2) == 110.00
    assert round(out["base_upside_pct"], 2) == 37.50
    assert out["intrinsic_scenarios"]["base"] == 100.0
    assert out["intrinsic_valuation"]["base_upside_pct"] == 25.0

    insufficient = apply_peer_valuation_overlay(base, {"peer_value_crosscheck": {"eligible": False, "estimate": 200}})
    assert insufficient["base"] == 100.0
    assert insufficient["peer_overlay"]["applied"] is False


def test_027_macro_mapping_and_ui_contract_are_explicit(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "macro")
    _, _, company_id = seed_control_workspace(app)
    with app.app_context():
        company = db.session.get(Company, company_id)
        exposures = exposures_for_company(company)
        assert exposures["industrial"] == "RISING"
        assert exposures["rates"] == "FALLING"
        assert exposures["credit"] == "FALLING"

    template = Path("mfapp/templates/company_section.html").read_text()
    assert "MACRO · FOR" in template
    assert "MACRO · AGAINST" in template
    assert "Refresh macro" in template
    assert "Peer fair-value cross-check" in template
    assert "Bounded; never peer-only" in template


def test_027_command_center_and_settings_contract():
    dashboard = Path("mfapp/templates/dashboard.html").read_text()
    settings = Path("mfapp/templates/settings.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()

    assert ">Discover<" not in dashboard
    assert ">Portfolio<" not in dashboard
    assert "Process = approved Research gates." in dashboard
    assert "NOT RUN / LIMITED / VALIDATED / REVIEW" in dashboard
    assert "research-conclusion-text" in dashboard
    assert "Next action" in dashboard
    assert "coverage-wrap-col" in dashboard
    assert 'class="panel recent-jobs refresh-runs"' in settings
    assert ".coverage-table .research-conclusion-text" in css
    assert "font-weight:400" in css


def test_027_evidence_diagnostic_is_consolidated_and_weighted():
    template = Path("mfapp/templates/company_section.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()

    assert "Weighted support / opposition" not in template
    assert "Positive threshold" not in template
    assert "Negative threshold" not in template
    assert "Diagnostic score" in template
    assert "sum of visible weights" in template
    assert "s.weight" in template
    assert "evidence-weight positive" in template
    assert "evidence-weight negative" in template
    assert ".evidence-score-compact" in css


def test_027_version_and_state_are_locked():
    assert Path("VERSION").read_text().strip() == "0.2.7"
    state = Path("docs/CURRENT_STATE.md").read_text()
    assert "**State-Version: 0.2.7**" in state
    assert "0.2.6 production baseline" in state
    assert "CI #787" in state
    assert "Deploy #35" in state
