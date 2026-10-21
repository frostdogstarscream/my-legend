"""Alembic 环境配置。

从 PersistenceSettings / DATABASE_URL 读取连接。

生产 Schema 修改必须通过 alembic upgrade head。

开发时代替 config：
    1. 设置 DATABASE_URL 环境变量
    2. alembic upgrade head
    3. alembic revision --autogenerate -m "描述"
"""
from alembic import context
from sqlalchemy import engine_from_config, pool

# Phase 4.1: 从环境变量读取 DATABASE_URL（生产级）
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "4-引擎代码"))

from drama_engine.persistence.models import Base  # noqa: E402

config = context.config
database_url = os.environ.get("DATABASE_URL", "")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
