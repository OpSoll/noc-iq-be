"""Tests for autocommit_block wrapping of ALTER TYPE migration steps (Issue #518).

PostgreSQL raises ``ActiveSqlTransaction: cannot run inside a transaction
block`` when ``ALTER TYPE ... ADD VALUE`` executes inside the default Alembic
transaction. Every such statement must be wrapped in
``op.get_context().autocommit_block()``.
"""
from pathlib import Path

VERSIONS_DIR = Path(__file__).parent.parent / "alembic" / "versions"

# Migrations that extend a PostgreSQL enum type via ALTER TYPE.
_ALTER_TYPE_MIGRATIONS = [
    "0023_job_quarantine_and_dr_replay.py",
    "0024_mercy60_job_enhancements.py",
]


def test_alter_type_statements_are_wrapped_in_autocommit_block():
    for name in _ALTER_TYPE_MIGRATIONS:
        path = VERSIONS_DIR / name
        assert path.exists(), f"Expected migration {name} to exist"
        src = path.read_text()

        assert "ALTER TYPE" in src, f"{name} should contain an ALTER TYPE statement"
        assert "autocommit_block" in src, (
            f"{name} must wrap ALTER TYPE in op.get_context().autocommit_block()"
        )

        # Every ALTER TYPE statement must appear after an autocommit_block
        # opener and before the matching context close.
        lines = src.splitlines()
        in_autocommit = False
        for idx, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if "autocommit_block" in line and "with op.get_context()" in line:
                in_autocommit = True
                continue
            # Leaving the with block: a statement at column 0 ends the scope.
            if in_autocommit and not line.startswith(" ") and "def " in line:
                in_autocommit = False
            if "ALTER TYPE" in line:
                assert in_autocommit, (
                    f"{name}: ALTER TYPE at line {idx} is not "
                    "inside an autocommit_block"
                )
