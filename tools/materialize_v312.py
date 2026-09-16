from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "Market_Forensics_V3_1_12_FULL.zip"
EXPECTED_ZIP_SHA256 = "95cf233e1ac7e07f8684936d9bbfbce90a3ee4a99abe862c0c237360bff6745d"
PREFIX = "Market_Forensics_V3_1_12/"
EXPECTED_MODULES = {
    "__init__.py", "alpaca.py", "audit.py", "backtest.py", "config.py", "db.py",
    "decision.py", "discovery.py", "exporter.py", "financial_flows.py", "finra.py",
    "flow.py", "forecast.py", "forensics.py", "fundamentals.py", "jobs.py",
    "management.py", "marketdata.py", "opportunity.py", "sec.py", "service.py",
    "symbols.py", "triangulation.py", "ui.py", "v3.py", "valuation.py", "workstation.py",
}


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    target_resolved = target.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name.startswith(PREFIX):
            continue
        rel = Path(name[len(PREFIX):])
        if not rel.parts or "__pycache__" in rel.parts or rel.suffix == ".pyc":
            continue
        out = (target / rel).resolve()
        if target_resolved != out and target_resolved not in out.parents:
            raise SystemExit(f"Unsafe path in golden ZIP: {name}")
        if info.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(zf.read(info))


def main() -> None:
    if not ZIP_PATH.exists():
        raise SystemExit(f"Golden V3.1.12 ZIP missing: {ZIP_PATH.name}")
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    if digest != EXPECTED_ZIP_SHA256:
        raise SystemExit(f"Golden V3.1.12 ZIP checksum mismatch: {digest}")

    reference = ROOT / ".v312_reference"
    shutil.rmtree(reference, ignore_errors=True)
    reference.mkdir(parents=True)
    with zipfile.ZipFile(ZIP_PATH) as zf:
        _safe_extract(zf, reference)

    source = reference / "market_forensics"
    actual = {p.name for p in source.glob("*.py")}
    if actual != EXPECTED_MODULES:
        raise SystemExit(
            "V3.1.12 engine module mismatch: "
            f"missing={sorted(EXPECTED_MODULES-actual)} extra={sorted(actual-EXPECTED_MODULES)}"
        )
    if (reference / "VERSION.txt").read_text(encoding="utf-8").strip() != "3.1.12":
        raise SystemExit("Golden package VERSION.txt is not 3.1.12")

    target = ROOT / "market_forensics"
    staged = ROOT / ".market_forensics_v312"
    backup = ROOT / ".market_forensics_previous"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(source, staged)
    if target.exists():
        target.replace(backup)
    staged.replace(target)
    shutil.rmtree(backup, ignore_errors=True)

    print(f"V3.1.12 FULL verified and materialized: 27/27 modules; ZIP SHA-256 {digest}")


if __name__ == "__main__":
    main()
