"""Phase 7.3: Persistent Restart — True cross-lifecycle recovery via SQLite.

Proof that Job / Checkpoint / StoryRecord can survive repo destruction
and be independently reloaded by a new repo instance from the same
persistent backing store.

Uses file-backed SQLite for restart semantics (not a PostgreSQL claim).
"""
from __future__ import annotations

import os
import tempfile
import uuid
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from drama_engine.persistence.models import Base


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════
# Persistent Store (SQLite-backed)
# ═══════════════════════════════════════════════════════════════════
class PersistentStore:
    """SQLite-backed persistent store for restart semantics testing.

    Both repo_A and repo_B connect to the same file independently.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.path.join(tempfile.gettempdir(), f"phase7_3_test_{uuid.uuid4().hex[:8]}.db")
        self._engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)

    def new_session(self) -> Session:
        return self._session_factory()

    def dispose(self):
        self._engine.dispose()

    def cleanup(self):
        self.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


# ═══════════════════════════════════════════════════════════════════
# Persistent Repo Helpers
# ═══════════════════════════════════════════════════════════════════
def create_persistent_job_dict(store: PersistentStore, job_data: dict[str, Any]) -> dict[str, Any]:
    """Create a job via PostgresLongFormJobRepository (works with SQLite)."""
    from drama_engine.persistence.postgres_repos import PostgresLongFormJobRepository
    session = store.new_session()
    try:
        repo = PostgresLongFormJobRepository(session)
        result = repo.create(job_data)
        session.commit()
        return result
    finally:
        session.close()


def get_persistent_job(store: PersistentStore, job_id: str) -> dict[str, Any] | None:
    """Get a job from a NEW session to the same store."""
    from drama_engine.persistence.postgres_repos import PostgresLongFormJobRepository
    session = store.new_session()
    try:
        repo = PostgresLongFormJobRepository(session)
        return repo.get(job_id)
    finally:
        session.close()


def update_persistent_job(store: PersistentStore, job_id: str, **fields) -> dict[str, Any] | None:
    from drama_engine.persistence.postgres_repos import PostgresLongFormJobRepository
    session = store.new_session()
    try:
        repo = PostgresLongFormJobRepository(session)
        result = repo.update(job_id, **fields)
        session.commit()
        return result
    finally:
        session.close()


def save_persistent_checkpoint(store: PersistentStore, cp_data: dict[str, Any]) -> None:
    from drama_engine.persistence.postgres_repos import PostgresCheckpointRepository
    session = store.new_session()
    try:
        repo = PostgresCheckpointRepository(session)
        repo.save_or_update(cp_data)
        session.commit()
    finally:
        session.close()


def get_latest_checkpoint(store: PersistentStore, run_id: str) -> dict[str, Any] | None:
    from drama_engine.persistence.postgres_repos import PostgresCheckpointRepository
    session = store.new_session()
    try:
        repo = PostgresCheckpointRepository(session)
        return repo.get_latest(run_id)
    finally:
        session.close()


def list_checkpoints(store: PersistentStore, run_id: str) -> list[dict[str, Any]]:
    from drama_engine.persistence.postgres_repos import PostgresCheckpointRepository
    session = store.new_session()
    try:
        repo = PostgresCheckpointRepository(session)
        return repo.list_by_run(run_id)
    finally:
        session.close()


def save_persistent_story_record(store: PersistentStore, record: Any) -> None:
    from drama_engine.persistence.repository import StoryRecord, PostgresStoryRecordRepository
    session = store.new_session()
    try:
        repo = PostgresStoryRecordRepository(session)
        repo.create(record)
        session.commit()
    finally:
        session.close()


def get_persistent_story_record(store: PersistentStore, story_id: str) -> dict[str, Any] | None:
    from drama_engine.persistence.repository import PostgresStoryRecordRepository
    session = store.new_session()
    try:
        repo = PostgresStoryRecordRepository(session)
        record = repo.get_by_story_id(story_id)
        if record:
            return {
                "story_id": record.story_id,
                "story_mode": record.story_mode,
                "content_json": record.content_json,
                "output_json": record.output_json,
                "content_form": record.content_form,
                "engine_version": record.engine_version,
                "template_ids": record.template_ids,
            }
        return None
    finally:
        session.close()