"""Phase 4 数据库引擎 & Session 工厂。

SQLAlchemy 2.x 同步引擎。Session 由请求上下文管理，不在模块级别复用。
"""
from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import PersistenceSettings

_engine: Engine | None = None
_session_factory: sessionmaker | None = None
_current_backend: str = "memory"


def get_engine(settings: PersistenceSettings | None = None) -> Engine | None:
    """返回 SQLAlchemy 引擎（内存模式返回 None）。

    内存模式直接创建内存 Repository 对象，不经过 SQLAlchemy。
    """
    global _engine, _current_backend  # noqa: PLW0603
    if settings is None:
        settings = PersistenceSettings.from_env()
    if settings.backend == "memory":
        return None
    if _engine is None or _current_backend != settings.backend:
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            connect_args={"connect_timeout": settings.connect_timeout},
        )
        _current_backend = settings.backend
    return _engine


def get_session(settings: PersistenceSettings | None = None) -> Session | None:
    """返回一个 SQLAlchemy Session（内存模式返回 None）。

    调用方负责关闭 session（with 语句或 try/finally）。
    """
    global _session_factory  # noqa: PLW0603
    if settings is None:
        settings = PersistenceSettings.from_env()
    if settings.backend == "memory":
        return None
    engine = get_engine(settings)
    if engine is None:
        return None
    if _session_factory is None:
        _session_factory = sessionmaker(bind=engine)
    return _session_factory()


def init_db(settings: PersistenceSettings | None = None) -> None:
    """创建所有表（仅 dev/test；生产请用 Alembic migration）。

    内存模式下不操作数据库。
    """
    from .models import Base  # 延迟导入避免循环
    if settings is None:
        settings = PersistenceSettings.from_env()
    if settings.backend == "memory":
        return
    engine = get_engine(settings)
    if engine is not None:
        Base.metadata.create_all(engine)