from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "market_forensics"
REFERENCE = ROOT / ".v312_reference" / "market_forensics"


def main() -> None:
    expected = sorted(p.name for p in REFERENCE.glob("*.py"))
    actual = sorted(p.name for p in ENGINE.glob("*.py"))
    if len(expected) != 27 or actual != expected:
        raise SystemExit(f"V3.1.12 engine set mismatch: reference={len(expected)} runtime={len(actual)}")
    for name in expected:
        source = (REFERENCE / name).read_bytes()
        runtime = (ENGINE / name).read_bytes()
        if source != runtime:
            raise SystemExit(f"V3.1.12 {name} differs from golden ZIP")
    digest = hashlib.sha256((ROOT / "Market_Forensics_V3_1_12_FULL.zip").read_bytes()).hexdigest()
    print(f"V3.1.12 verification PASS: 27/27 exact modules; golden {digest}")


if __name__ == "__main__":
    main()
