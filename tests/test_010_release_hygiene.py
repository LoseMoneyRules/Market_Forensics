from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mfapp"


def test_legacy_runtime_is_not_shipped_in_mfapp():
    forbidden = [
        "full312.py",
        "full312_controls.py",
        "full312_downloads.py",
        "full312_routes.py",
        "marketdata.py",
        "research_core.py",
        "v312_flow_routes.py",
        "v312_models.py",
        "v312_routes.py",
        "v312_settings.py",
        "static/css/full312.css",
        "static/css/v002.css",
        "static/css/valuation.css",
        "static/js/full312_flows.js",
        "templates/full312_company.html",
        "templates/full312_controls.html",
        "templates/full312_discovery.html",
        "templates/full312_workspace.html",
        "templates/valuation_v312.html",
    ]
    leftovers = [name for name in forbidden if (APP / name).exists()]
    assert leftovers == []


def test_active_runtime_does_not_import_v3_modules():
    active = [
        APP / "__init__.py",
        APP / "auth.py",
        APP / "routes.py",
        APP / "routes_edit.py",
        APP / "routes_publish.py",
        APP / "services.py",
        APP / "jobs.py",
        APP / "schema.py",
        APP / "data_providers.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in active)
    for token in ("full312", "v312_routes", "v312_models", "research_core", "marketdata"):
        assert token not in text
