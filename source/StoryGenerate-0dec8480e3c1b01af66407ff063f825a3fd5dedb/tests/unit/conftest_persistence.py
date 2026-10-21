"""Phase 9 Final Closure: SQLite-backed Checkpoint Repository.

Provides SQLiteCheckpointRepository — same interface as InMemoryCheckpointRepository
but persists to a file-backed SQLite database via SQLAlchemy.

This proves true persistent restart semantics:
  Runtime A → writes to SQLite file
  Runtime B → reads from same SQLite file (fresh Session, fresh Repo)
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Integer, String, Text, DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ── SQLAlchemy model ──────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class CheckpointRow(Base):
    __tablename__ = "generation_checkpoints"

    run_id = Column(String(64), primary_key=True, nullable=False)
    stage = Column(String(64), primary_key=True, nullable=False)
    story_id = Column(String(64), nullable=False, default="")
    generation_scale = Column(String(32), nullable=False, default="standard")
    last_completed_chapter = Column(Integer, nullable=False, default=0)
    next_chapter = Column(Integer, nullable=False, default=1)
    state_json = Column(Text, nullable=False, default="{}")
    config_fingerprint = Column(String(256), nullable=False, default="")
    status = Column(String(32), nullable=False, default="resumable")
    version = Column(String(16), nullable=False, default="1.0")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


# ── InMemoryCheckpointRepository-compatible checkpoint object ─────
class GenerationCheckpoint:
    """Mirror of longform_executor.GenerationCheckpoint for SQLite path."""

    def __init__(
        self,
        run_id: str,
        story_id: str = "",
        generation_scale: str = "standard",
        stage: str = "planned",
        last_completed_chapter: int = 0,
        next_chapter: int = 1,
        state_json: dict[str, Any] | None = None,
        config_fingerprint: str = "",
        status: str = "resumable",
        version: str = "1.0",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.run_id = run_id
        self.story_id = story_id
        self.generation_scale = generation_scale
        self.stage = stage
        self.last_completed_chapter = last_completed_chapter
        self.next_chapter = next_chapter
        self.state_json = state_json or {}
        self.config_fingerprint = config_fingerprint
        self.status = status
        self.version = version
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "story_id": self.story_id,
            "generation_scale": self.generation_scale,
            "stage": self.stage,
            "last_completed_chapter": self.last_completed_chapter,
            "next_chapter": self.next_chapter,
            "state_json": self.state_json,
            "config_fingerprint": self.config_fingerprint,
            "status": self.status,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GenerationCheckpoint:
        state = d.get("state_json", {})
        if isinstance(state, str):
            state = json.loads(state)
        return cls(
            run_id=d["run_id"],
            story_id=d.get("story_id", ""),
            generation_scale=d.get("generation_scale", "standard"),
            stage=d.get("stage", "planned"),
            last_completed_chapter=d.get("last_completed_chapter", 0),
            next_chapter=d.get("next_chapter", 1),
            state_json=state,
            config_fingerprint=d.get("config_fingerprint", ""),
            status=d.get("status", "resumable"),
            version=d.get("version", "1.0"),
        )


# ── SQLite-backed Repository ──────────────────────────────────────
class SQLiteCheckpointRepository:
    """Checkpoint repository backed by file-based SQLite.

    Same interface as InMemoryCheckpointRepository (works with GenerationCheckpoint objects),
    but persists to a real SQLite file. This proves persistent restart semantics.

    Usage:
        db_path = "/tmp/test_mystery.db"
        repo_a = SQLiteCheckpointRepository(db_path)
        # ... run pipeline (Runtime A) ...
        repo_a.session.close()

        # Runtime B
        repo_b = SQLiteCheckpointRepository(db_path)
        assert repo_b._session is not repo_a._session
        ckpt = repo_b.get_latest(run_id)
        # Data survived the restart
    """

    def __init__(self, db_path: str, create_tables: bool = True):
        self.db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        if create_tables:
            Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        self._session = self._session_factory()

    @property
    def session(self) -> Session:
        return self._session

    def close(self):
        """Close the session (but not the engine — same DB file reusable)."""
        self._session.close()

    def save_or_update(self, checkpoint: GenerationCheckpoint) -> None:
        d = checkpoint.to_dict()
        d["state_json"] = json.dumps(d["state_json"])
        # Parse ISO datetime strings back to datetime objects for SQLite
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, str) and val:
                try:
                    d[key] = datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    d[key] = datetime.now(timezone.utc)
        existing = (
            self._session.query(CheckpointRow)
            .filter_by(run_id=d["run_id"], stage=d["stage"])
            .first()
        )
        if existing:
            for k, v in d.items():
                setattr(existing, k, v)
            existing.updated_at = datetime.now(timezone.utc)
        else:
            row = CheckpointRow(**{k: v for k, v in d.items() if hasattr(CheckpointRow, k)})
            self._session.add(row)
        self._session.flush()
        self._session.commit()

    def get_latest(self, run_id: str) -> GenerationCheckpoint | None:
        row = (
            self._session.query(CheckpointRow)
            .filter_by(run_id=run_id)
            .order_by(CheckpointRow.last_completed_chapter.desc())
            .first()
        )
        if row is None:
            return None
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        return GenerationCheckpoint.from_dict(d)

    def list_by_run(self, run_id: str) -> list[GenerationCheckpoint]:
        rows = (
            self._session.query(CheckpointRow)
            .filter_by(run_id=run_id)
            .order_by(CheckpointRow.last_completed_chapter)
            .all()
        )
        return [GenerationCheckpoint.from_dict(
            {c.name: getattr(r, c.name) for c in r.__table__.columns}
        ) for r in rows]

    def get_by_stage(self, run_id: str, stage: str) -> GenerationCheckpoint | None:
        row = (
            self._session.query(CheckpointRow)
            .filter_by(run_id=run_id, stage=stage)
            .first()
        )
        if row is None:
            return None
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        return GenerationCheckpoint.from_dict(d)


def create_temp_db_path(prefix: str = "test") -> str:
    """Create a temp file path for SQLite database."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix=f"{prefix}_")
    os.close(fd)
    return path