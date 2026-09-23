from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_037_release_identity_and_scope_are_synced():
    version = (ROOT / "VERSION").read_text().strip()
    current = (ROOT / "docs/CURRENT_STATE.md").read_text()
    how = (ROOT / "docs/HOW_MARKET_FORENSICS_WORKS.md").read_text()
    assert version == "0.3.7"
    assert "**State-Version: 0.3.7**" in current
    assert "**Current product line:** 0.3.7" in how
    assert "release/0.3.7-ui-restoration" in current
    assert "UI/UX restoration and responsive consolidation only" in current


def test_037_iphone_shell_uses_one_safe_area_contract():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert "--safe-top:env(safe-area-inset-top,0px)" in css
    assert "--safe-bottom:env(safe-area-inset-bottom,0px)" in css
    assert ":root{--topbar:calc(58px + var(--safe-top))}" in css
    assert "top:calc(var(--topbar) + 6px)" in css
    assert "calc(62px + var(--safe-bottom))" in css
    assert "width:44px;height:44px" in css


def test_037_mobile_contains_overflow_without_hiding_wide_evidence():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert "max-width:100%;overflow-x:hidden" in css
    assert "-webkit-overflow-scrolling:touch" in css
    assert ".coverage-table{width:100%;min-width:1040px" in css
    assert ".tape-flow-table .data-table{min-width:980px}" in css
    assert ".table-card,.table-wrap{width:100%;overflow-x:auto;overflow-y:hidden;max-width:100%" in css


def test_037_phone_density_is_readable_and_not_single_column_everywhere():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert "@media(max-width:440px){.current-strip,.historical-range,.kpi-grid.compact{grid-template-columns:1fr 1fr}" in css
    assert "@media(max-width:440px){.fundamentals-current-strip{grid-template-columns:repeat(2,minmax(0,1fr))}}" in css
    assert "@media(max-width:360px){.kpi-grid.compact{grid-template-columns:1fr}" in css
    assert ".kpi-grid,.current-strip,.historical-range,.fundamentals-current-strip,.research-intelligence-strip{grid-template-columns:1fr}" in css
    assert "@media(max-width:760px){.research-intelligence-strip{margin:0 0 12px" in css
    assert not re.search(r"font-size:\s*(?:[0-9]|1[01])px", css)


def test_037_desktop_visual_contract_stays_institutional():
    css = (ROOT / "mfapp/static/css/app.css").read_text().lower()
    for forbidden in ("purple", "#a855f7", "#9333ea", "#7e22ce", "#6d28d9", "#8b5cf6"):
        assert forbidden not in css
    assert "--mf-body-size:14px" in css
    assert ".navitem,.company-tabs a,.company-tabs a.active,.view-switch a,.tape-controls a,.role-pill{font-weight:400}" in css


def test_037_css_runtime_tokens_are_fully_defined():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    defined = set(re.findall(r"--([A-Za-z0-9_-]+)\s*:", css))
    referenced = set(re.findall(r"var\(--([A-Za-z0-9_-]+)", css))
    assert referenced <= defined
    for token in ("surface", "surface-soft", "text", "warning", "space-3", "space-5"):
        assert token in defined


def test_037_mobile_interactions_are_touchable_without_ios_focus_zoom():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert ".button.micro,.flow-tab,.tape-controls a,.company-tabs a,.top-actions .theme-toggle,.top-actions .view-switch a{min-height:44px}" in css
    assert "input,textarea,select{font-size:16px}" in css
    assert ".flash-stack{top:calc(var(--topbar) + 10px)" in css
    assert "padding:max(24px,var(--safe-top))" in css


def test_037_mobile_stacked_decision_text_remains_visible():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert ".gate-row .gate-status{grid-column:1;grid-row:2" in css
    assert ".discovery-list-title>span:last-child,.discovery-list-reason span{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere}" in css
    assert ".notice,.panel-head p,.list-row p,.company-states{overflow-wrap:anywhere}" in css


def test_037_intermediate_desktop_topbar_does_not_collide():
    css = (ROOT / "mfapp/static/css/app.css").read_text()
    assert "@media(min-width:821px) and (max-width:1100px)" in css
    assert ".view-switch span,.role-pill{display:none}" in css
