import pytest
from alembic.config import Config
from alembic import command
import os

def test_migration_safety():
    """
    Automated migration safety checks in CI for ORM and schema changes.
    Validates forward/backward migration behavior.
    """
    # Assuming alembic.ini is in the root directory
    alembic_cfg = Config("alembic.ini")
    
    # Check that we can upgrade to head
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        pytest.fail(f"Forward migration failed: {e}")
        
    # Check that we can downgrade to base
    try:
        command.downgrade(alembic_cfg, "base")
    except Exception as e:
        pytest.fail(f"Backward migration failed: {e}")

    # Re-upgrade to head for subsequent tests
    command.upgrade(alembic_cfg, "head")
