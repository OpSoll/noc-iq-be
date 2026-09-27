#!/usr/bin/env python3
"""
Database schema verification — Issue #525.

Verifies that the SQLAlchemy models do not drift from the current Alembic
migration head by running ``alembic check``. Alembic's autogenerate
comparison detects differences between the models and the live database
schema (which must be migrated to head first).

Exit codes:
    0   schema matches the current migration head
    1   model/migration drift detected (or the check could not run)

Usage:
    python scripts/verify_db_schema.py

Environment:
    DATABASE_URL   PostgreSQL connection string (required).
                   Example: postgresql://user:pass@host:5432/nociq
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL is required. "
            "Example: DATABASE_URL=postgresql://user:pass@host:5432/nociq "
            "python scripts/verify_db_schema.py",
            file=sys.stderr,
        )
        return 1

    if "sqlite" in database_url:
        print(
            "WARNING: alembic check requires PostgreSQL; SQLite is not supported. "
            "Skipping schema verification.",
            file=sys.stderr,
        )
        return 0

    print(f"Verifying schema against migration head on {database_url.split('@')[-1]} ...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(REPO_ROOT),
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode == 0:
        print("Schema verification passed: models match the current migration head.")
        return 0

    print(
        "\nERROR: schema drift detected — model changes are missing an Alembic "
        "migration. Generate one with:\n"
        "    alembic revision --autogenerate -m 'describe change'\n"
        "then run `alembic upgrade head` and re-run this script.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
