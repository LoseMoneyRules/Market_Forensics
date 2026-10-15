"""0.4.2 release invariants — source truth, role boundaries, guided steps."""
from types import SimpleNamespace

from mfapp.research_workflow import STEP_SPEC, build_research_workflow
from mfapp.services import publication_payload, publication_view_for_role


def _snapshot():
    return SimpleNamespace(payload={
        "security": {"ticker": "EXM", "exchange": "NYSE", "currency": "USD"},
        "company": {"name": "Example", "sector": "Industrials", "industry": "Machines"},
        "market": {"price": 37.0, "as_of": "2026-10-14"},
        "valuation": {"bear": 20.0, "base": 50.0, "bull": 80.0,
                      "method": "PRIVATE_MODEL_ASSUMPTIONS"},
        "research": {"thesis": "INSIDER_THESIS_SECRET", "business": "Research detail",
                     "risk_summary": "Public research-only risk",
                     "confirmation_bias_notes": "PRIVATE_BIAS"},
        "expectations": [{"metric": "Revenue", "internal_value": 1000}],
        "financials": [{"revenue": 100}],
        "section_reviews": [{"section": "business", "verdict": "BULLISH",
                             "evidence_note": "INSIDER_REVIEW"}],
        "sources": [{"url": "https://www.sec.gov/"}],
        "decision": {"research_conclusion": "LONG WATCH"},
        "position": {"shares": 99, "notes": "PRIVATE_POSITION"},
        "risk": {"thesis_invalidation": "PRIVATE_INVALIDATION"},
        "monitoring": [{"name": "PRIVATE_PORTFOLIO_MONITOR"}],
        "captured_at": "2026-10-15",
    })


def test_042_friend_is_strict_price_target_only_allowlist():
    friend = publication_payload(_snapshot(), "FRIEND")
    assert set(friend) == {"security", "company", "market", "valuation", "captured_at", "visibility"}
    assert friend["valuation"] == {"bear": 20.0, "base": 50.0, "bull": 80.0}
    assert friend["security"] == {"ticker": "EXM", "currency": "USD"}
    assert friend["company"] == {"name": "Example"}
    for token in ("INSIDER_THESIS_SECRET", "PRIVATE_POSITION", "PRIVATE_INVALIDATION",
                  "INSIDER_REVIEW", "LONG WATCH", "PRIVATE_MODEL_ASSUMPTIONS"):
        assert token not in str(friend)


def test_042_insider_gets_research_not_private_portfolio():
    insider = publication_payload(_snapshot(), "INSIDER")
    assert insider["research"]["thesis"] == "INSIDER_THESIS_SECRET"
    assert insider["section_reviews"][0]["evidence_note"] == "INSIDER_REVIEW"
    assert insider["decision"]["research_conclusion"] == "LONG WATCH"
    assert "expectations" in insider and "financials" in insider
    for token in ("PRIVATE_POSITION", "PRIVATE_INVALIDATION",
                  "PRIVATE_PORTFOLIO_MONITOR", "PRIVATE_BIAS"):
        assert token not in str(insider)


def test_042_historic_friend_publications_filtered_on_read():
    old_payload = publication_payload(_snapshot(), "INSIDER")
    historically_permissive = dict(old_payload, visibility="FRIEND")
    restricted = publication_view_for_role(historically_permissive, "FRIEND")
    assert restricted["valuation"] == {"bear": 20.0, "base": 50.0, "bull": 80.0}
    assert "research" not in restricted
    assert "sources" not in restricted
    assert "decision" not in restricted


def test_042_all_fourteen_steps_have_a_specific_question_and_review_path():
    assert len(STEP_SPEC) == 14
    assert len({x[0] for x in STEP_SPEC.values()}) == 14
    steps = list((key, key.title()) for key in STEP_SPEC)
    context = {
        "readiness": {"done": 0, "total": 13, "gates": [], "validation": {}},
        "valuation": {"bear": None, "base": None, "bull": None, "current_price": None},
        "brief": {},
        "decision_lenses": {"research_conclusion": "DATA REVIEW"},
        "company": SimpleNamespace(sector="Industrial", industry="Machines"),
    }
    workflow = build_research_workflow(context, steps, {}, [])
    assert len(workflow["by_key"]) == 14
    assert workflow["by_key"]["overview"]["previous"] is None
    assert workflow["by_key"]["overview"]["next"][0] == "business"
    assert workflow["by_key"]["validate"]["next"] is None
    assert workflow["by_key"]["validate"]["previous"][0] == "audit"
    assert workflow["by_key"]["fundamentals"]["facts"][0]["value"] == "—"


def _integration_workspace(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from mfapp import create_app
    from mfapp.core_models import Company, Coverage, ResearchGateApproval, Security
    from mfapp.extensions import db
    from mfapp.models import User
    from mfapp.services import ensure_workspace
    from mfapp.readiness import research_readiness

    monkeypatch.setenv("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app({
        "TESTING": True, "SECRET_KEY": "042-integrated-test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / '042-integration.db'}",
        "WTF_CSRF_ENABLED": False, "AUTO_MIGRATE": False,
    })
    with app.app_context():
        db.create_all()
        user = User(email="analyst042@example.com", display_name="CONTROL",
                    role="CONTROL", password_hash="test", totp_secret_enc="test", is_active=True)
        company = Company(legal_name="Example Machine", display_name="Example Machine",
                          sector="Industrials", industry="Machines")
        db.session.add_all([user, company])
        db.session.flush()
        sec = Security(company_id=company.id, ticker="MFT", exchange="NYSE",
                       validation_source="TEST", active=True, is_primary=True, currency="USD")
        db.session.add(sec)
        db.session.flush()
        coverage = Coverage(user_id=user.id, security_id=sec.id,
                            status="RESEARCH", research_state="UNDER_REVIEW")
        db.session.add(coverage)
        db.session.flush()
        research, *_ = ensure_workspace(coverage, user.id)
        research.business = "Research already reviewed against filing evidence."
        db.session.commit()
        business = next(g for g in research_readiness(coverage)["gates"] if g["key"] == "business")
        assert business["evidence_ready"]
        db.session.add(ResearchGateApproval(
            coverage_id=coverage.id, gate_key="business",
            approved_by=user.id, evidence_hash=business["evidence_hash"],
        ))
        db.session.commit()
        user_id, coverage_id = user.id, coverage.id
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["view_as"] = "CONTROL"
    return app, client, coverage_id


def test_042_analyst_edit_reopens_human_approval_without_losing_old_record(tmp_path, monkeypatch):
    from mfapp.core_models import Coverage, ResearchGateApproval, ResearchSectionReview
    from mfapp.extensions import db
    from mfapp.readiness import research_readiness
    app, client, coverage_id = _integration_workspace(tmp_path, monkeypatch)
    with app.app_context():
        initial = next(g for g in research_readiness(db.session.get(Coverage, coverage_id))["gates"] if g["key"] == "business")
        assert initial["approved"]
    posted = client.post("/company/MFT/section-review/business", data={
        "verdict": "MIXED", "confidence": "HIGH",
        "metric_label": "FCF margin check", "metric_value": "12.5", "metric_unit": "%",
        "horizon_months": "12", "evidence_note": "The filing evidence needs closer inspection.",
        "reconsider_if": "FCF conversion below 10%", "source_url": "https://www.sec.gov/",
    })
    assert posted.status_code == 302
    with app.app_context():
        gate = next(g for g in research_readiness(db.session.get(Coverage, coverage_id))["gates"] if g["key"] == "business")
        assert not gate["approved"], gate
        assert gate["manual_review_required"]
        assert ResearchGateApproval.query.filter_by(coverage_id=coverage_id, gate_key="business").count() == 1
        review = ResearchSectionReview.query.filter_by(coverage_id=coverage_id, section_key="business").one()
        assert str(review.metric_value) == "12.50000000"
    approved = client.post("/company/MFT/readiness/business/approve")
    assert approved.status_code == 302
    with app.app_context():
        gate = next(g for g in research_readiness(db.session.get(Coverage, coverage_id))["gates"] if g["key"] == "business")
        assert gate["approved"], gate
        assert not gate["manual_review_required"]


def test_042_guided_pages_render_without_external_requests(tmp_path, monkeypatch):
    app, client, _ = _integration_workspace(tmp_path, monkeypatch)
    for step in STEP_SPEC:
        url = (f"/company/MFT/{step}" if step not in {"financial-flows", "valuation", "validate"}
               else f"/company/MFT/{step}")
        response = client.get(url)
        assert response.status_code == 200, (step, response.get_data(as_text=True)[:800])
        html = response.get_data(as_text=True)
        assert "guided-step" in html, step
        assert STEP_SPEC[step][0] in html, step
