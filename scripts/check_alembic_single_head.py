#!/usr/bin/env python3
"""Fail the build when Alembic has more than one head revision.

Issue #516: concurrent git branches creating Alembic migration files cause
multiple head revisions, which breaks CI migrations. This script exits
non-zero when ``alembic heads`` reports more than one revision so the check
can run in pre-commit hooks and CI.
"""
from __future__ import annotations

import subprocess
import sys
from typing import List


def get_alembic_heads() -> List[str]:
    """Return the revision ids reported by ``alembic heads``."""
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "alembic heads failed: " + (proc.stderr or proc.stdout or "unknown error")
        )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    try:
        heads = get_alembic_heads()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not heads:
        print("ERROR: `alembic heads` returned no revisions.", file=sys.stderr)
        return 1

    if len(heads) > 1:
        print(
            f"ERROR: Alembic has {len(heads)} head revisions "
            f"(expected exactly 1):",
            file=sys.stderr,
        )
        for head in heads:
            print(f"  - {head}", file=sys.stderr)
        print(
            "Merge the split heads into a single revision, e.g.:\n"
            "  alembic merge -m 'merge multiple alembic heads' "
            "<head1> <head2>",
            file=sys.stderr,
        )
        return 1

    print(f"OK: single Alembic head ({heads[0]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
