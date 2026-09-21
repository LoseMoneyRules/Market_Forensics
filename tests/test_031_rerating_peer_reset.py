from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from mfapp import create_app
from mfapp.core_models import (
    Company, Coverage, FinancialPeriod, NormalizedFinancial, ResearchGateApproval,
    ResearchState, ResearchVersion, RiskPlan, Security, Snapshot,
)
from mfapp.extensions import db
from mfapp.models import User
from mfapp.readiness import research_readiness
from mfapp.research_basis import FINANCIAL_REVIEW_GATES, latest_financial_basis
from mfapp.security import encrypt_secret, hash_password
from mfapp.services import ensure_workspace
from mfapp.valuation_forensics import (
    _market_implied_expectations, _multiple_bridge, _peer_adjustment,
    _rerating_conditions, _triangulation,
)


def make_app(tmp_path, monkeypatch, name="031"):
    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "031-release",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
        "WTF_CSRF_ENABLED": False,
        "AUTO_MIGRATE": False,
    })


def seed_workspace(app, ticker="RRT"):
    with app.app_context():
        db.create_all()
        user = User(
            email=f"{ticker.lower()}031@example.com",
            display_name="Control",
            role="CONTROL",
            password_hash=hash_password("abcdefghijklmnop"),
            totp_secret_enc=encrypt_secret("JBSWY3DPEHPK3PXP"),
            is_active=True,
        )
        db.session.add(user); db.session.flush()
        company = Company(
            legal_name="Re-rating Test Co", display_name="Re-rating Test Co",
            sector="Consumer", industry="Footwear", country="US",
        )
        db.session.add(company); db.session.flush()
        security = Security(
            company_id=company.id, ticker=ticker, exchange="NYSE",
            currency="USD", active=True, is_primary=True,
        )
        db.session.add(security); db.session.flush()
        coverage = Coverage(
            user_id=user.id, security_id=security.id,
            status="RESEARCH", research_state="UNDER_REVIEW",
        )
        db.session.add(coverage); db.session.flush()
        ensure_workspace(coverage, user.id)
        research = ResearchState.query.filter_by(coverage_id=coverage.id).first()
        research.thesis = "Human thesis must survive a new filing."
        research.business = "Human business work must survive a new filing."
        db.session.commit()
        return user.id, company.id, coverage.id


def add_financial(company_id, period_type, fiscal_year, end_date, stamp, revenue=1000, operating_income=120, net_income=90, fcf=80):
    period = FinancialPeriod(
        company_id=company_id, period_type=period_type, fiscal_year=fiscal_year,
        end_date=end_date, filed_at=end_date + timedelta(days=35),
        accession_no=f"ACC-{period_type}-{fiscal_year}", created_at=stamp,
    )
    db.session.add(period); db.session.flush()
    normalized = NormalizedFinancial(
        financial_period_id=period.id, revenue=revenue, gross_profit=revenue * .45,
        operating_income=operating_income, net_income=net_income, cfo=fcf + 20,
        capex=-20, fcf=fcf, diluted_shares=100, shares_outstanding=100,
        cash=100, debt=50, receivables=100, inventory=150, payables=80,
        assets=1500, liabilities=700, equity=800, updated_at=stamp,
    )
    db.session.add(normalized)
    return period


def approve_every_gate(coverage_id, user_id, approved_at):
    coverage = db.session.get(Coverage, coverage_id)
    payload = research_readiness(coverage)
    for gate in payload["gates"]:
        db.session.add(ResearchGateApproval(
            coverage_id=coverage_id, gate_key=gate["key"], approved_by=user_id,
            approved_at=approved_at, evidence_hash=gate["evidence_hash"], note="pre-quarter approval",
        ))
    db.session.commit()


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"


def test_new_quarter_reopens_only_financially_dependent_gates_and_preserves_work(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "reset")
    uid, company_id, coverage_id = seed_workspace(app)
    t0 = datetime(2026, 8, 1, 12, 0, 0)
    with app.app_context():
        add_financial(company_id, "FY", 2025, date(2025, 12, 31), t0 - timedelta(days=30))
        db.session.commit()
        approve_every_gate(coverage_id, uid, t0)
        before = research_readiness(db.session.get(Coverage, coverage_id))
        assert before["ready_to_validate"] is True
        assert before["review_required"] is False

        add_financial(company_id, "Q1", 2026, date(2026, 3, 31), t0 + timedelta(minutes=1), revenue=270)
        db.session.commit()
        after = research_readiness(db.session.get(Coverage, coverage_id))

        assert after["review_required"] is True
        assert set(after["reopened_gates"]) == FINANCIAL_REVIEW_GATES
        assert after["ready_to_validate"] is False
        gate_map = {row["key"]: row for row in after["gates"]}
        assert gate_map["business"]["approved"] is True
        assert gate_map["journal"]["approved"] is True
        assert gate_map["audit"]["approved"] is True
        assert gate_map["tape"]["approved"] is True
        assert gate_map["valuation"]["status"] == "REVIEW REQUIRED"
        assert gate_map["valuation"]["prior_approval_exists"] is True
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage_id).count() == 13
        research = ResearchState.query.filter_by(coverage_id=coverage_id).first()
        assert research.thesis == "Human thesis must survive a new filing."
        assert research.business == "Human business work must survive a new filing."


def test_material_restatement_changes_basis_and_reopens_dependent_research(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "restatement")
    uid, company_id, coverage_id = seed_workspace(app, "RST")
    t0 = datetime(2026, 8, 1, 12, 0, 0)
    with app.app_context():
        period = add_financial(company_id, "FY", 2025, date(2025, 12, 31), t0 - timedelta(days=10))
        db.session.commit()
        old_basis = latest_financial_basis(company_id)
        approve_every_gate(coverage_id, uid, t0)
        normalized = NormalizedFinancial.query.filter_by(financial_period_id=period.id).first()
        normalized.revenue = 1100
        normalized.updated_at = t0 + timedelta(hours=1)
        db.session.commit()
        new_basis = latest_financial_basis(company_id)
        readiness = research_readiness(db.session.get(Coverage, coverage_id))
        assert old_basis["token"] != new_basis["token"]
        assert readiness["review_required"] is True
        assert "fundamentals" in readiness["reopened_gates"]


def test_publication_is_blocked_after_new_financial_basis(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "publish")
    uid, company_id, coverage_id = seed_workspace(app, "PUB")
    t0 = datetime(2026, 8, 1, 12, 0, 0)
    with app.app_context():
        add_financial(company_id, "FY", 2025, date(2025, 12, 31), t0 - timedelta(days=30))
        db.session.commit()
        approve_every_gate(coverage_id, uid, t0)
        add_financial(company_id, "Q1", 2026, date(2026, 3, 31), t0 + timedelta(minutes=1), revenue=270)
        db.session.commit()
    client = app.test_client(); login(client, uid)
    response = client.post("/company/PUB/snapshot", follow_redirects=False)
    assert response.status_code in {302, 303}
    with app.app_context():
        assert Snapshot.query.filter_by(coverage_id=coverage_id).count() == 0


def test_historical_multiple_bridge_is_explicit_and_bounded():
    current = {
        "revenue_growth_pct": 2, "operating_margin_pct": 10, "fcf_margin_pct": 8,
        "roic_pct": 12, "cash_conversion": 1.0, "net_debt_to_fcf": .5,
        "share_count_growth_pct": 0, "inventory_to_revenue_pct": 15,
        "receivables_to_revenue_pct": 10,
    }
    observations = [
        {**current, "revenue_growth_pct": 8, "operating_margin_pct": 14, "fcf_margin_pct": 11, "roic_pct": 18},
        {**current, "revenue_growth_pct": 7, "operating_margin_pct": 13, "fcf_margin_pct": 10, "roic_pct": 17},
        {**current, "revenue_growth_pct": 9, "operating_margin_pct": 15, "fcf_margin_pct": 12, "roic_pct": 19},
    ]
    stats = {"pe": {"current": 17.0, "reference_median": 28.0, "label": "P/E"}}
    bridge = _multiple_bridge(current, stats, observations, {"factors": []})
    assert bridge["available"] is True
    assert bridge["historical_reference"] == 28.0
    assert bridge["justified_current"] < 28.0
    assert bridge["current_multiple"] == 17.0
    assert bridge["precision_warning"]
    assert all("estimated_multiple_effect" in row for row in bridge["items"])


def test_rerating_requirements_have_met_partial_not_met_or_deteriorating_states():
    current = {"revenue_growth_pct": 3, "operating_margin_pct": 9, "fcf_margin_pct": 7, "roic_pct": 10, "cash_conversion": .8, "net_debt_to_fcf": 1.5, "share_count_growth_pct": 2}
    observations = [
        {"revenue_growth_pct": 7, "operating_margin_pct": 13, "fcf_margin_pct": 10, "roic_pct": 16, "cash_conversion": 1.1, "net_debt_to_fcf": .5, "share_count_growth_pct": 0},
        {"revenue_growth_pct": 8, "operating_margin_pct": 14, "fcf_margin_pct": 11, "roic_pct": 18, "cash_conversion": 1.2, "net_debt_to_fcf": .4, "share_count_growth_pct": -1},
    ]
    result = _rerating_conditions(current, observations)
    assert result["usable"] >= 5
    assert result["completion_pct"] is not None
    assert {row["status"] for row in result["conditions"]} <= {"MET", "PARTIALLY MET", "NOT MET", "DETERIORATING", "UNAVAILABLE"}


def test_peer_adjusted_multiple_uses_only_close_and_partial_peers():
    target = {"pe": 17, "revenue_growth_pct": 5, "operating_margin_pct": 12, "fcf_margin_pct": 9, "roic_pct": 16, "net_debt_to_fcf": .5, "cash_conversion": 1.1}
    peers = [
        {"comparability": "CLOSE PEER", "pe": 24, "revenue_growth_pct": 7, "operating_margin_pct": 13, "fcf_margin_pct": 10, "roic_pct": 15, "net_debt_to_fcf": .6, "cash_conversion": 1.0},
        {"comparability": "PARTIAL PEER", "pe": 22, "revenue_growth_pct": 4, "operating_margin_pct": 11, "fcf_margin_pct": 8, "roic_pct": 14, "net_debt_to_fcf": .8, "cash_conversion": 1.0},
        {"comparability": "REFERENCE ONLY", "pe": 80, "revenue_growth_pct": 40},
    ]
    result = _peer_adjustment(target, peers, "pe")
    assert result["available"] is True
    assert result["peer_count"] == 2
    assert result["peer_median"] == 23
    assert result["justified_multiple"] < 80
    assert result["relative_gap_pct"] is not None


def test_market_implied_expectations_reverse_engineer_without_invented_consensus():
    current = {"market_cap": 1700, "enterprise_value": 1750, "net_income": 100, "revenue": 1000, "fcf": 80, "shares": 100}
    stats = {"pe": {"reference_median": 24}, "ev_sales": {"reference_median": 2.2}, "p_fcf": {"reference_median": 20}}
    result = _market_implied_expectations(current, stats, None)
    assert result["available"] is True
    assert len(result["drivers"]) == 3
    assert "not Street consensus" in result["interpretation"]


def test_triangulation_never_blind_averages_methods():
    valuation = {"base": 100}
    current = {"shares": 10, "net_income": 10, "revenue": 100, "operating_income": 15, "fcf": 12, "net_debt": 0}
    bridge = {"multiple_key": "pe", "justified_current": 12, "multiple_label": "P/E"}
    peers = {"peer_adjusted": {"available": True, "multiple_key": "pe", "justified_multiple": 15}}
    result = _triangulation(valuation, current, bridge, peers)
    assert [row["value"] for row in result["methods"]] == [100.0, 12.0, 15.0]
    assert "No arithmetic average" in result["rule"]
    source = Path("mfapp/triangulation_engine.py").read_text()
    assert '"weight": 0.0' in source
    assert "never mutates Bear/Base/Bull" in source


def test_discovery_reuses_materialized_rerating_outputs():
    source = Path("mfapp/market_discovery.py").read_text()
    assert 'cache.get("valuation_forensics")' in source
    assert '"historical_gap_pct"' in source
    assert '"peer_gap_pct"' in source
    assert '"rerating_completion_pct"' in source
    assert "Stage 1" in source and "Stage 2" in source


def test_reports_reuse_same_materialized_forensics():
    contract = Path("mfapp/report_contract.py").read_text()
    render = Path("mfapp/report_render_v2.py").read_text()
    assert 'cache.get("valuation_forensics")' in contract
    assert render.count("Re-rating / peer triangulation") >= 2
    assert "MARKET MAY BE WRONG" in render
    assert "MARKET MAY BE RIGHT" in render


def test_no_lookahead_and_point_in_time_contract():
    source = Path("mfapp/valuation_forensics.py").read_text()
    historical = Path("mfapp/historical_engine.py").read_text()
    assert "price_on_or_after(security_id, filed_day" in source
    assert '"point_in_time": True' in source
    assert "POINT_IN_TIME_POST_FILING_ANCHORS" in source
    assert "future_filings_excluded" in historical
    assert "future_prices_used_for_validation_only" in historical


def test_ui_mobile_and_method_audit_contracts():
    template = Path("mfapp/templates/_valuation_forensics.html").read_text()
    css = Path("mfapp/static/css/app.css").read_text()
    readiness = Path("mfapp/templates/_process_readiness.html").read_text()
    for phrase in (
        "MARKET IMPLIED EXPECTATIONS", "WHY THE MULTIPLE IS DIFFERENT TODAY",
        "RE-RATING CONDITIONS", "PEER TRIANGULATION",
        "VALUATION CONVERGENCE / DIVERGENCE",
        "WHAT THE MARKET MAY BE GETTING WRONG",
        "CATALYST / MISPRICING TIMELINE", "Method detail / audit",
    ):
        assert phrase in template
    assert "@media (max-width:620px)" in css
    assert "NEW FINANCIAL EVIDENCE — REVIEW REQUIRED" in readiness


def test_single_canonical_forensics_engine_contract():
    cache = Path("mfapp/research_cache.py").read_text()
    triangulation = Path("mfapp/triangulation_engine.py").read_text()
    discovery = Path("mfapp/market_discovery.py").read_text()
    reports = Path("mfapp/report_contract.py").read_text()
    assert "build_valuation_forensics(" in cache
    assert "automatic_triangulation(" not in cache
    assert "build_peer_analysis" in triangulation
    assert 'cache.get("valuation_forensics")' in discovery
    assert 'cache.get("valuation_forensics")' in reports


def test_031_release_metadata_and_permanent_rules():
    assert Path("VERSION").read_text().strip() == "0.3.1"
    current = Path("docs/CURRENT_STATE.md").read_text()
    how = Path("docs/HOW_MARKET_FORENSICS_WORKS.md").read_text()
    assert "**State-Version: 0.3.1**" in current
    assert "**Current product line:** 0.3.1" in how
    for phrase in (
        "NO PATCH SU PATCH", "Missing data stays missing",
        "ADD ON EVIDENCE, NOT ON PRICE",
        "NEW FINANCIAL EVIDENCE — REVIEW REQUIRED",
    ):
        assert phrase in current + how


def test_business_peer_table_is_null_safe_for_missing_canonical_multiples():
    template = Path("mfapp/templates/company_section.html").read_text()
    assert "'%.2f'|format(row.target) if row.target is not none else '—'" in template
    assert "'%.2f'|format(row.peer_median) if row.peer_median is not none else '—'" in template
    assert "MISSING" in template
    assert "Independent cross-check; never blended into Bear / Base / Bull" in template
    assert "Peer overlay applied, max ±10% shift" not in template


def test_locked_invalidation_is_immutable_per_thesis_but_new_thesis_gets_fresh_control(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "thesis-version")
    uid, _, coverage_id = seed_workspace(app, "TVR")
    old_thesis = "Installed-base recovery drives margin normalization."
    old_invalidation = "Operating margin stays below 8% for two filed quarters."
    approval_time = datetime.now() - timedelta(days=1)

    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        research = coverage.research
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        research.thesis = old_thesis
        research.counter_evidence = "Demand could remain structurally weak."
        research.variant_us = "Recovery is stronger than priced."
        risk.thesis_invalidation = old_invalidation
        risk.invalidation_locked_at = approval_time
        research.risk_summary = old_invalidation
        db.session.commit()

        readiness = research_readiness(coverage)
        for key in ("overview", "monitoring"):
            gate = next(row for row in readiness["gates"] if row["key"] == key)
            db.session.add(ResearchGateApproval(
                coverage_id=coverage_id,
                gate_key=key,
                approved_by=uid,
                approved_at=approval_time,
                evidence_hash=gate["evidence_hash"],
                note="approved old thesis",
            ))
        db.session.commit()

    client = app.test_client()
    login(client, uid)

    # Same thesis version: the locked invalidation cannot be rewritten.
    response = client.post(
        "/company/TVR/thesis-invalidation",
        data={"thesis_invalidation": "A softer retroactive rule."},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        assert risk.thesis_invalidation == old_invalidation
        assert risk.invalidation_locked_at is not None

    # Explicit Core Thesis revision starts a new thesis version.
    new_thesis = "Direct-to-consumer mix, not installed-base recovery, drives the re-rating."
    response = client.post(
        "/company/TVR/research/overview",
        data={
            "thesis": new_thesis,
            "counter_evidence": "DTC economics could fail to scale.",
            "variant_us": "The market underestimates DTC margin leverage.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        research = coverage.research
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        assert research.thesis == new_thesis
        assert risk.thesis_invalidation == ""
        assert risk.invalidation_locked_at is None
        assert research.risk_summary == ""

        archived = (
            ResearchVersion.query
            .filter_by(coverage_id=coverage_id)
            .filter(ResearchVersion.reason.like("Thesis revised · archived prior thesis%"))
            .order_by(ResearchVersion.id.desc())
            .first()
        )
        assert archived is not None
        assert archived.payload["thesis"] == old_thesis
        assert archived.payload["_thesis_control"]["invalidation"] == old_invalidation
        assert archived.payload["_thesis_control"]["locked"] is True

        readiness = research_readiness(coverage)
        gates = {row["key"]: row for row in readiness["gates"]}
        assert readiness["thesis_review_required"] is True
        assert set(key for key in readiness["reopened_gates"] if key in {"overview", "monitoring"}) == {"overview", "monitoring"}
        assert gates["overview"]["approved"] is False
        assert gates["overview"]["thesis_review_required"] is True
        assert gates["monitoring"]["approved"] is False
        assert gates["monitoring"]["thesis_review_required"] is True
        assert gates["monitoring"]["evidence_ready"] is False
        # Prior human approvals are preserved until they are re-reviewed.
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage_id).count() == 2

    # A new invalidation can now be written and locked for the new thesis.
    new_invalidation = "DTC gross margin fails to improve for two filed quarters."
    response = client.post(
        "/company/TVR/thesis-invalidation",
        data={"thesis_invalidation": new_invalidation, "lock_invalidation": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        assert risk.thesis_invalidation == new_invalidation
        assert risk.invalidation_locked_at is not None
        readiness = research_readiness(coverage)
        gates = {row["key"]: row for row in readiness["gates"]}
        assert gates["monitoring"]["evidence_ready"] is True
        assert gates["monitoring"]["thesis_review_required"] is True

    page = client.get("/company/TVR/monitoring")
    assert page.status_code == 200
    assert b"Previous thesis versions and invalidations" in page.data
    assert old_invalidation.encode() in page.data


def test_invalidation_cannot_be_locked_without_a_core_thesis(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, "no-thesis-lock")
    uid, _, coverage_id = seed_workspace(app, "NTL")
    with app.app_context():
        coverage = db.session.get(Coverage, coverage_id)
        coverage.research.thesis = ""
        db.session.commit()

    client = app.test_client()
    login(client, uid)
    response = client.post(
        "/company/NTL/thesis-invalidation",
        data={"thesis_invalidation": "Revenue falls below threshold.", "lock_invalidation": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        risk = RiskPlan.query.filter_by(coverage_id=coverage_id).first()
        assert risk.invalidation_locked_at is None
        assert risk.thesis_invalidation == ""


def test_thesis_version_ui_and_documentation_contract():
    company = Path("mfapp/templates/company_section.html").read_text()
    readiness = Path("mfapp/templates/_process_readiness.html").read_text()
    how = Path("docs/HOW_MARKET_FORENSICS_WORKS.md").read_text()
    current = Path("docs/CURRENT_STATE.md").read_text()

    assert "The lock belongs to this thesis version, not forever." in company
    assert "Previous thesis versions and invalidations" in company
    assert "THESIS CHANGED — NEW INVALIDATION REQUIRED" in readiness
    assert "NEW THESIS VERSION" in readiness
    assert "Invalidation is immutable per thesis version" in how
    assert "0.3.1 thesis-invalidation rule" in current
    assert Path("VERSION").read_text().strip() == "0.3.1"
