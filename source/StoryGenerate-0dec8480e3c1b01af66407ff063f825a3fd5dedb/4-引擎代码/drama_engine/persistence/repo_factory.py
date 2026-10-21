"""Phase 7.2: Repository Factory — unified backend selection.

Selects InMemory or Postgres repositories based on configuration.
Avoids if-postgres-else-inmemory scattered through business code.
"""
from __future__ import annotations

from typing import Any


def create_job_repo(backend: str = "memory", session: Any = None) -> Any:
    """Create a long-form job repository.

    Args:
        backend: "memory" | "postgres"
        session: SQLAlchemy Session (required for postgres)
    """
    if backend == "postgres":
        from .postgres_repos import PostgresLongFormJobRepository
        if session is None:
            raise ValueError("session required for postgres backend")
        return PostgresLongFormJobRepository(session)
    else:
        from drama_engine.async_runtime import InMemoryLongFormJobRepository
        return InMemoryLongFormJobRepository()


def create_checkpoint_repo(backend: str = "memory", session: Any = None) -> Any:
    """Create a checkpoint repository.

    Args:
        backend: "memory" | "postgres"
        session: SQLAlchemy Session (required for postgres)
    """
    if backend == "postgres":
        from .postgres_repos import PostgresCheckpointRepository
        if session is None:
            raise ValueError("session required for postgres backend")
        return PostgresCheckpointRepository(session)
    else:
        from drama_engine.longform_executor import InMemoryCheckpointRepository
        return InMemoryCheckpointRepository()


def create_story_record_repo(backend: str = "memory", session: Any = None) -> Any:
    """Create a StoryRecord repository.

    Args:
        backend: "memory" | "postgres"
        session: SQLAlchemy Session (required for postgres)
    """
    if backend == "postgres":
        from .repository import PostgresStoryRecordRepository
        if session is None:
            raise ValueError("session required for postgres backend")
        return PostgresStoryRecordRepository(session)
    else:
        from .memory_repo import InMemoryRecordRepo
        return InMemoryRecordRepo()


def create_character_memory_repo(backend: str = "memory", session: Any = None) -> Any:
    """Create a CharacterMemory repository.

    Args:
        backend: "memory" | "postgres"
        session: SQLAlchemy Session (required for postgres)
    """
    if backend == "postgres":
        from .memory_repo import PostgresCharacterMemoryRepository
        if session is None:
            raise ValueError("session required for postgres backend")
        return PostgresCharacterMemoryRepository(session)
    else:
        from .memory_repo import InMemoryCharacterMemoryRepository
        return InMemoryCharacterMemoryRepository()