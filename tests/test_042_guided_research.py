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
