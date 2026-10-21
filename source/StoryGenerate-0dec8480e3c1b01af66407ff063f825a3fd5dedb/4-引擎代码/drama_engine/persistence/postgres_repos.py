"""Phase 7.2: PostgreSQL Repositories — Long-form Job + GenerationCheckpoint.

Provides Postgres implementations of:
  - LongFormJobRepository (extends Phase 4 story_jobs)
  - CheckpointRepository (generation_checkpoints)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import GenerationCheckpointModel, StoryJobModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ═══════════════════════════════════════════════════════════════════
# Postgres Long-form Job Repository
# ═══════════════════════════════════════════════════════════════════
class PostgresLongFormJobRepository:
    """Postgres implementation matching InMemoryLongFormJobRepository contract.

    Extends the existing story_jobs table with Phase 7 long-form columns.
    """

    def __init__(self, session: Session):
        self._session = session

    # ── CRUD ──

    def create(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new job record. Returns the dict representation."""
        row = StoryJobModel(
            job_id=job_data.get("job_id", _new_id()),
            request_id=job_data.get("request_id", ""),
            run_id=job_data.get("run_id", ""),
            story_id=job_data.get("story_id", ""),
            status=job_data.get("status", "pending"),
            current_stage=job_data.get("stage", ""),
            story_mode=job_data.get("story_mode", ""),
            template_id=job_data.get("template_id", ""),
            content_form=job_data.get("content_form", ""),
            generation_scale=job_data.get("generation_scale", ""),
            progress_pct=job_data.get("progress_pct", 0),
            current_chapter=job_data.get("current_chapter", 0),
            total_chapters=job_data.get("total_chapters", 0),
            message=job_data.get("message", ""),
            cancel_requested=1 if job_data.get("cancel_requested") else 0,
            error_code=job_data.get("error_code", ""),
            error_message=job_data.get("error_message", ""),
            created_at=_utcnow(),
            input_json={"idea": job_data.get("idea", "")},
        )
        self._session.add(row)
        self._session.flush()
        return _job_row_to_dict(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._session.query(StoryJobModel).filter_by(job_id=job_id).first()
        return _job_row_to_dict(row) if row else None

    def get_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        row = self._session.query(StoryJobModel).filter_by(run_id=run_id).first()
        return _job_row_to_dict(row) if row else None

    def update(self, job_id: str, **fields) -> dict[str, Any] | None:
        row = self._session.query(StoryJobModel).filter_by(job_id=job_id).first()
        if row is None:
            return None
        _apply_job_fields(row, fields)
        row.updated_at = _utcnow()
        self._session.flush()
        return _job_row_to_dict(row)

    def update_status(self, job_id: str, status: str, **extra) -> dict[str, Any] | None:
        return self.update(job_id, status=status, **extra)

    def update_progress(self, job_id: str, stage: str, progress_pct: int,
                        current_chapter: int = 0, message: str = "") -> dict[str, Any] | None:
        return self.update(job_id,
            current_stage=stage, progress_pct=progress_pct,
            current_chapter=current_chapter, message=message)

    def request_cancel(self, job_id: str) -> bool:
        row = self._session.query(StoryJobModel).filter_by(job_id=job_id).first()
        if row is None:
            return False
        if row.status in ("running", "pending"):
            row.cancel_requested = 1
            row.status = "cancel_requested"
            row.updated_at = _utcnow()
            self._session.flush()
            return True
        return False

    def list_by_status(self, status: str | None = None) -> list[dict[str, Any]]:
        q = self._session.query(StoryJobModel)
        if status:
            q = q.filter_by(status=status)
        return [_job_row_to_dict(r) for r in q.order_by(StoryJobModel.created_at.desc()).all()]


# ═══════════════════════════════════════════════════════════════════
# Postgres Checkpoint Repository
# ═══════════════════════════════════════════════════════════════════
class PostgresCheckpointRepository:
    """Postgres implementation of GenerationCheckpoint repository."""

    def __init__(self, session: Session):
        self._session = session

    def save_or_update(self, checkpoint: dict[str, Any]) -> None:
        """Save or update checkpoint by run_id + stage unique constraint.

        Accepts both dict and GenerationCheckpoint (Pydantic/dataclass) objects.
        """
        if not isinstance(checkpoint, dict):
            # Convert Pydantic/dataclass object to dict
            import dataclasses
            if dataclasses.is_dataclass(checkpoint):
                checkpoint = dataclasses.asdict(checkpoint)
            elif hasattr(checkpoint, 'model_dump'):
                checkpoint = checkpoint.model_dump(mode='json')
            elif hasattr(checkpoint, 'dict'):
                checkpoint = checkpoint.dict()
            elif hasattr(checkpoint, '__dict__'):
                checkpoint = checkpoint.__dict__
        existing = (
            self._session.query(GenerationCheckpointModel)
            .filter_by(run_id=checkpoint["run_id"], stage=checkpoint["stage"])
            .first()
        )
        if existing:
            _apply_checkpoint_fields(existing, checkpoint)
        else:
            row = _checkpoint_dict_to_row(checkpoint)
            self._session.add(row)
        self._session.flush()
        self._session.commit()

    def get_latest(self, run_id: str):
        """返回 GenerationCheckpoint（兼容 InMemoryCheckpointRepository 接口）。"""
        row = (
            self._session.query(GenerationCheckpointModel)
            .filter_by(run_id=run_id)
            .order_by(GenerationCheckpointModel.last_completed_chapter.desc())
            .first()
        )
        if row is None:
            return None
        from ..longform_executor import GenerationCheckpoint
        d = _checkpoint_row_to_dict(row)
        state = d.get("state_json", {})
        if isinstance(state, str):
            import json
            state = json.loads(state)
        return GenerationCheckpoint(
            run_id=d["run_id"],
            story_id=d.get("story_id", ""),
            generation_scale=d.get("generation_scale", "long_form"),
            stage=d.get("stage", ""),
            last_completed_chapter=d.get("last_completed_chapter", 0),
            next_chapter=d.get("next_chapter", 1),
            state_json=state,
            config_fingerprint=d.get("config_fingerprint", ""),
            status=d.get("status", "resumable"),
            version=d.get("version", "1.0"),
        )

    def list_by_run(self, run_id: str) -> list:
        """返回 GenerationCheckpoint 列表。"""
        rows = (
            self._session.query(GenerationCheckpointModel)
            .filter_by(run_id=run_id)
            .order_by(GenerationCheckpointModel.last_completed_chapter)
            .all()
        )
        from ..longform_executor import GenerationCheckpoint
        result = []
        for row in rows:
            d = _checkpoint_row_to_dict(row)
            state = d.get("state_json", {})
            if isinstance(state, str):
                import json
                state = json.loads(state)
            result.append(GenerationCheckpoint(
                run_id=d["run_id"],
                story_id=d.get("story_id", ""),
                generation_scale=d.get("generation_scale", "long_form"),
                stage=d.get("stage", ""),
                last_completed_chapter=d.get("last_completed_chapter", 0),
                next_chapter=d.get("next_chapter", 1),
                state_json=state,
                config_fingerprint=d.get("config_fingerprint", ""),
                status=d.get("status", "resumable"),
                version=d.get("version", "1.0"),
            ))
        return result

    def get_by_stage(self, run_id: str, stage: str) -> dict[str, Any] | None:
        row = (
            self._session.query(GenerationCheckpointModel)
            .filter_by(run_id=run_id, stage=stage)
            .first()
        )
        return _checkpoint_row_to_dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════
# Row ↔ Dict converters
# ═══════════════════════════════════════════════════════════════════

def _job_row_to_dict(row: StoryJobModel) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "request_id": row.request_id or "",
        "idea": (row.input_json or {}).get("idea", ""),
        "run_id": row.run_id or "",
        "story_id": row.story_id or "",
        "story_mode": row.story_mode or "",
        "template_id": row.template_id or "",
        "content_form": row.content_form or "",
        "generation_scale": row.generation_scale or "",
        "status": row.status or "pending",
        "stage": row.current_stage or "",
        "progress_pct": row.progress_pct or 0,
        "current_chapter": row.current_chapter or 0,
        "total_chapters": row.total_chapters or 0,
        "message": row.message or "",
        "cancel_requested": bool(row.cancel_requested),
        "error_code": row.error_code or "",
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.finished_at.isoformat() if row.finished_at else "",
    }


def _apply_job_fields(row: StoryJobModel, fields: dict[str, Any]) -> None:
    field_map = {
        "status": "status",
        "stage": "current_stage",
        "story_mode": "story_mode",
        "story_id": "story_id",
        "run_id": "run_id",
        "template_id": "template_id",
        "content_form": "content_form",
        "generation_scale": "generation_scale",
        "progress_pct": "progress_pct",
        "current_chapter": "current_chapter",
        "total_chapters": "total_chapters",
        "message": "message",
        "cancel_requested": "cancel_requested",
        "error_code": "error_code",
        "error_message": "error_message",
    }
    for key, value in fields.items():
        col = field_map.get(key)
        if col and hasattr(row, col):
            if key == "cancel_requested":
                setattr(row, col, 1 if value else 0)
            else:
                setattr(row, col, value)


def _checkpoint_dict_to_row(d: dict[str, Any]) -> GenerationCheckpointModel:
    return GenerationCheckpointModel(
        run_id=d["run_id"],
        story_id=d.get("story_id", ""),
        generation_scale=d.get("generation_scale", "long_form"),
        stage=d["stage"],
        last_completed_chapter=d.get("last_completed_chapter", 0),
        next_chapter=d.get("next_chapter", 1),
        state_json=d.get("state_json", {}),
        config_fingerprint=d.get("config_fingerprint", ""),
        status=d.get("status", "resumable"),
        version=d.get("version", "1.0"),
    )


def _apply_checkpoint_fields(row: GenerationCheckpointModel, d: dict[str, Any]) -> None:
    row.story_id = d.get("story_id", row.story_id)
    row.generation_scale = d.get("generation_scale", row.generation_scale)
    row.last_completed_chapter = d.get("last_completed_chapter", row.last_completed_chapter)
    row.next_chapter = d.get("next_chapter", row.next_chapter)
    row.state_json = d.get("state_json", row.state_json)
    row.config_fingerprint = d.get("config_fingerprint", row.config_fingerprint)
    row.status = d.get("status", row.status)
    row.version = d.get("version", row.version)
    row.updated_at = _utcnow()


def _checkpoint_row_to_dict(row: GenerationCheckpointModel) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "story_id": row.story_id or "",
        "generation_scale": row.generation_scale or "long_form",
        "stage": row.stage,
        "last_completed_chapter": row.last_completed_chapter or 0,
        "next_chapter": row.next_chapter or 1,
        "state_json": row.state_json or {},
        "config_fingerprint": row.config_fingerprint or "",
        "status": row.status or "resumable",
        "version": row.version or "1.0",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
