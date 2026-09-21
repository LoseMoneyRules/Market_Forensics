from __future__ import annotations

"""Synchronize the production line in docs/CURRENT_STATE.md after a verified deploy.

The deploy workflow calls this only after final production health succeeds.
Historical release notes are never rewritten.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re


PRODUCTION_PREFIX = "**Production:**"
BT = chr(96)


def production_line(
    *,
    version: str,
    source_sha: str,
    run_id: str,
    run_number: str,
    deployed_at: str,
) -> str:
    version = str(version or "").strip()
    source_sha = str(source_sha or "").strip()
    run_id = str(run_id or "").strip()
    run_number = str(run_number or "").strip()
    deployed_at = str(deployed_at or "").strip()
    if not version:
        raise ValueError("version is required")
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", source_sha):
        raise ValueError("source_sha must be a Git commit SHA")
    if not run_id:
        raise ValueError("run_id is required")
    return (
        f"{PRODUCTION_PREFIX} {version} on Namecheap; verified production health after "
        f"manual deploy run {BT}{run_id}{BT} / deploy #{run_number or '?'} from main source "
        f"{BT}{source_sha}{BT} at {deployed_at}; /health matched VERSION and returned "
        f"{BT}architecture=web-native{BT}, {BT}database=primary{BT}, {BT}reports=rich{BT}, {BT}status=ok{BT}.  "
    )


def sync_current_state(
    text: str,
    *,
    version: str,
    source_sha: str,
    run_id: str,
    run_number: str,
    deployed_at: str,
) -> str:
    lines = text.splitlines()
    matches = [idx for idx, line in enumerate(lines) if line.startswith(PRODUCTION_PREFIX)]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one top-level Production line, found {len(matches)}")
    idx = matches[0]
    lines[idx] = production_line(
        version=version,
        source_sha=source_sha,
        run_id=run_id,
        run_number=run_number,
        deployed_at=deployed_at,
    )
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="docs/CURRENT_STATE.md")
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-number", default="")
    parser.add_argument("--deployed-at", default="")
    args = parser.parse_args()

    deployed_at = args.deployed_at.strip() or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = Path(args.path)
    current = path.read_text(encoding="utf-8")
    updated = sync_current_state(
        current,
        version=args.version,
        source_sha=args.source_sha,
        run_id=args.run_id,
        run_number=args.run_number,
        deployed_at=deployed_at,
    )
    path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
