"""Alembic environment.

App runtime uses asyncpg; alembic migrations run synchronously with psycopg3.
env.py converts the async URL to sync URL.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic 能 import app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db.base import Base
# 导入所有 models 让 Base.metadata 能识别
from app.models import (  # noqa: F401
    AgentRun,
    ApiKey,
    AuditLog,
    Artifact,
    Organization,
    Project,
    Subscription,
    UsageRecord,
    User,
    WebhookEndpoint,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# async URL → sync URL (asyncpg → psycopg3)
sync_url = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql+psycopg://"
)
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
