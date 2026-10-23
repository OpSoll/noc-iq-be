from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

from alembic import context

# Import all ORM models so Alembic can detect them
from app.db.base import Base
import app.models.orm.outage  # noqa: F401
import app.models.orm.outage_event  # noqa: F401
import app.models.orm.sla     # noqa: F401
import app.models.orm.payment  # noqa: F401
import app.models.orm.celery_task_dead_letter  # noqa: F401
import app.models.job  # noqa: F401
import app.models.webhook  # noqa: F401
import app.models.sla_dispute  # noqa: F401

from app.core.config import settings

config = context.config

# Override sqlalchemy.url with value from app settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _ensure_version_table(connection) -> None:
    """Ensure alembic_version.version_num is wide enough for revision ids.

    Alembic's default version_num column is VARCHAR(32), but some revision
    ids in this project exceed 32 chars (e.g. 0016_webhook_signature_versioning).
    Create the table with a wider column, or widen an existing one.
    """
    if not sa.inspect(connection).has_table("alembic_version"):
        meta = sa.MetaData()
        sa.Table(
            "alembic_version",
            meta,
            sa.Column("version_num", sa.String(255), nullable=False),
        ).create(connection)
        return

    col = next(
        c for c in sa.inspect(connection).get_columns("alembic_version")
        if c["name"] == "version_num"
    )
    col_type = col["type"]
    if isinstance(col_type, sa.String) and (col_type.length or 0) < 255:
        connection.execute(
            sa.text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(255)"
            )
        )


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        with connection.begin():
            _ensure_version_table(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
