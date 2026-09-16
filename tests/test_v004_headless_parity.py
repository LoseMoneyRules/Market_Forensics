import os

from cryptography.fernet import Fernet

os.environ.setdefault("MF_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MF_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_hosted_headless_state_matches_original_workstation_sequence(tmp_path):
    os.environ["LOCALAPPDATA"] = str(tmp_path / "parity-runtime")

    from market_forensics import db, workstation
    from mfapp.full312 import _engine_state

    db.init_db()
    ticker = "PARI"
    db.ensure_coverage(ticker)

    original = workstation.load_state(ticker, live_price=False)
    hosted = _engine_state(ticker, live_price=False)

    keys = [
        "metrics", "assumptions", "scenarios", "expected_value", "base_value", "price",
        "display_price", "model_price", "upside", "coverage", "gates", "gate_counts",
        "gate_label", "kpis", "kpi_eval", "calibration", "legacy_valuation",
        "valuation_regime", "valuation_confidence", "forensics", "management",
        "quality_validation", "forecast_assumptions", "forecast_scenarios",
        "price_reconciliation", "variant_case", "triangulation",
    ]
    for key in keys:
        assert hosted.get(key) == original.get(key), key
