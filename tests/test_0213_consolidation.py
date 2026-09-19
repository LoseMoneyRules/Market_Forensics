from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_0213_release_identity_and_docs_are_synced():
    version = (ROOT / "VERSION").read_text().strip()
    current = (ROOT / "docs/CURRENT_STATE.md").read_text()
    how = (ROOT / "docs/HOW_MARKET_FORENSICS_WORKS.md").read_text()
    assert version == "0.2.13"
    assert f"**State-Version: {version}**" in current
    assert f"**Current product line:** {version}" in how


def test_0213_frontend_has_one_canonical_navigation_and_runtime():
    base = (ROOT / "mfapp/templates/base.html").read_text()
    tabs = (ROOT / "mfapp/templates/_research_tabs.html").read_text()
    app_js = (ROOT / "mfapp/static/js/app.js").read_text()
    valuation = (ROOT / "mfapp/templates/valuation.html").read_text()

    assert "mobile-nav" not in base
    assert "js/theme.js" not in base
    assert not (ROOT / "mfapp/static/js/theme.js").exists()
    assert "data-company-tabs-toggle" in tabs
    assert 'id="mf-research-tabs"' in tabs
    assert "document.createElement('button')" not in app_js
    assert "valuation-model-form" in app_js
    assert "<script>" not in valuation


def test_0213_templates_do_not_reintroduce_inline_fix_layers():
    allowed_inline_script = "document.documentElement.dataset.theme"
    for path in (ROOT / "mfapp/templates").glob("*.html"):
        text = path.read_text()
        assert " style=" not in text, path
        assert " onclick=" not in text, path
        assert " onsubmit=" not in text, path
        assert " onchange=" not in text, path
        inline_scripts = re.findall(r"<script>(.*?)</script>", text, flags=re.S)
        assert all(allowed_inline_script in script for script in inline_scripts), path


def test_0213_css_readability_responsive_and_cleanup_contract():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    lowered = css.lower()
    assert "purple" not in lowered
    for forbidden in ("#a855f7", "#9333ea", "#7e22ce", "#6d28d9", "#8b5cf6"):
        assert forbidden not in lowered
    assert "font-size:11px" not in css
    assert not re.search(r"/\*\s*0\.2\.\d+", css)
    assert css.count("!important") <= 14
    assert "body{margin:0" in css and "font-size:14px" in css
    assert ":focus-visible{" in css
    assert ".table-card,.table-wrap{width:100%;overflow-x:auto;overflow-y:hidden" in css
    assert "@media(max-width:820px){\n  :root{--topbar:58px}" in css
    assert "width:44px;height:44px" in css
    assert ".company-tabs-toggle{display:flex;width:100%;min-height:44px" in css
    assert "@media(max-width:540px)" in css
    assert "@media(max-width:440px)" in css


def test_0213_removed_duplicate_decision_brief_is_not_hidden_dead_ui():
    company = (ROOT / "mfapp/templates/company_section.html").read_text()
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert 'class="panel decision-brief"' not in company
    assert ".decision-brief{" not in css


def test_0213_mobile_keeps_quote_metadata_and_contains_wide_diagnostics():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    header = (ROOT / "mfapp/templates/_company_header.html").read_text()
    trace = (ROOT / "mfapp/templates/trace_console.html").read_text()

    assert "Current price · refreshes every 5 min" in header
    assert "data-live-price-meta" in header
    assert ".company-actions .company-market{display:none}" not in css
    assert ".company-market{width:100%;text-align:left}" in css
    assert ".company-market small{max-width:none;overflow-wrap:anywhere}" in css
    assert ".table-card,.table-wrap{width:100%;overflow-x:auto" in css
    assert 'class="table-wrap"' in trace
    assert ".full-table{min-width:900px}" in css
    assert ".trace-event" not in css
    assert ".number-format-form" not in css
    assert css.count(".mobile-backdrop{display:none}") == 1


def test_0213_narrow_phone_layout_stacks_dense_controls_without_hiding_information():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    base = (ROOT / "mfapp/templates/base.html").read_text()

    assert ".kpi-grid.compact{grid-template-columns:1fr}" in css
    assert ".recent-jobs-summary{align-items:flex-start;flex-direction:column}" in css
    assert ".discovery-list-row{grid-template-columns:1fr}" in css
    assert ".discovery-list-action,.discovery-list-action form,.discovery-list-action .button{width:100%}" in css
    assert ".panel-head{align-items:flex-start;flex-wrap:wrap}" in css
    assert "max-height:calc(100dvh - 76px);overflow-y:auto" in css
    assert "padding-bottom:max(18px,env(safe-area-inset-bottom))" in css
    assert "preview-mode" in base
    assert ".preview-mode .app-shell{padding-top:" in css
