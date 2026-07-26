import os
import re
import subprocess

import pytest


def run_alembic(command: str) -> str:
    env = os.environ.copy()
    process = subprocess.run(
        ["alembic"] + command.split(),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return process.stdout.strip()


def parse_history(raw: str) -> list[tuple[str, str]]:
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^([0-9a-f]+) -> ([0-9a-f]+),", line)
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def test_migration_chain_is_linear():
    raw = run_alembic("history --verbose")
    rows = parse_history(raw)
    parent_counts = {}
    for _, parent in rows:
        parent_counts[parent] = parent_counts.get(parent, 0) + 1
    branched = [parent for parent, count in parent_counts.items() if count > 1]
    assert not branched, f"Branched migration chain detected at: {branched}"


def test_current_revision_matches_head():
    head = run_alembic("heads").split()[0]
    current = run_alembic("current").split()[0]
    assert current == head, f"DB is at {current}, expected head {head}"


def test_upgrade_path_from_baseline_is_available():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or "sqlite" in database_url:
        pytest.skip("Baseline migration verification requires a PostgreSQL-compatible DATABASE_URL.")

    # The repository's migration path must be valid when upgrading from a prior baseline.
    run_alembic("downgrade base")
    run_alembic("upgrade head")
