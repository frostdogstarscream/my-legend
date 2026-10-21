"""Phase 6.5: In-memory repository for persistence roundtrip testing.

Phase 8: Character Memory repositories (InMemory + Postgres).

Provides create() + get_by_story_id() with the same interface as
the Postgres repository, enabling true save+reload tests without a DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .repository import StoryRecord


# ═══════════════════════════════════════════════════════════════════
# Phase 6.5: InMemoryRecordRepo (StoryRecord)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class InMemoryRecordRepo:
    """In-memory StoryRecord repository for testing."""

    _store: dict[str, StoryRecord] = field(default_factory=dict)

    def create(self, record: StoryRecord) -> StoryRecord:
        self._store[record.story_id] = record
        return record

    def get_by_story_id(self, story_id: str) -> StoryRecord | None:
        return self._store.get(story_id)

    def list_all(self) -> list[StoryRecord]:
        return list(self._store.values())


# ═══════════════════════════════════════════════════════════════════
# Phase 8: Character Memory Repositories
# ═══════════════════════════════════════════════════════════════════
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryCharacterMemoryRepository:
    """In-memory store for tests."""

    def __init__(self):
        self._memories: dict[str, dict[str, Any]] = {}

    def upsert(self, memory: Any) -> dict[str, Any]:
        """Idempotent upsert by stable key."""
        key = _stable_key(memory)
        existing = self._find_by_key(memory.character_id, key)
        if existing:
            return existing
        d = memory.to_dict()
        d.setdefault("created_at", _utcnow().isoformat())
        self._memories[memory.memory_id] = d
        return d

    def upsert_many(self, memories: list) -> list[dict[str, Any]]:
        return [self.upsert(m) for m in memories]

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return self._memories.get(memory_id)

    def list_active_by_character(
        self, character_id: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        now = _utcnow()
        results = []
        for m in self._memories.values():
            if m.get("character_id") != character_id:
                continue
            if m.get("expires_at"):
                try:
                    exp_str = str(m["expires_at"]).replace("Z", "+00:00")
                    exp = datetime.fromisoformat(exp_str)
                    if exp < now:
                        continue
                except (ValueError, TypeError):
                    continue
            results.append(m)
        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results[:limit]

    def list_by_source_story(self, source_story_id: str) -> list[dict[str, Any]]:
        return [
            m for m in self._memories.values()
            if m.get("source_story_id") == source_story_id
        ]

    def count_by_source_story(self, source_story_id: str) -> int:
        return len(self.list_by_source_story(source_story_id))

    def _find_by_key(self, character_id: str, key: str) -> dict[str, Any] | None:
        for m in self._memories.values():
            if m["character_id"] == character_id and _stable_key_from_dict(m) == key:
                return m
        return None


class PostgresCharacterMemoryRepository:
    """PostgreSQL / SQLite Character Memory repository."""

    def __init__(self, session):
        self._session = session
        from .models import CharacterMemoryModel
        self._model = CharacterMemoryModel

    def upsert(self, memory: Any) -> dict[str, Any]:
        key = _stable_key(memory)
        existing = (
            self._session.query(self._model)
            .filter_by(character_id=memory.character_id)
            .all()
        )
        for row in existing:
            if _stable_key_from_row(row) == key:
                return _row_to_dict(row)

        row = self._model(
            memory_id=memory.memory_id,
            character_id=memory.character_id,
            memory_type=memory.memory_type,
            content=memory.content,
            importance=memory.importance,
            source_story_id=memory.source_story_id,
            created_at=memory.created_at or _utcnow(),
            expires_at=memory.expires_at,
        )
        self._session.add(row)
        self._session.flush()
        return _row_to_dict(row)

    def upsert_many(self, memories: list) -> list[dict[str, Any]]:
        return [self.upsert(m) for m in memories]

    def get(self, memory_id: str) -> dict[str, Any] | None:
        row = self._session.query(self._model).filter_by(memory_id=memory_id).first()
        return _row_to_dict(row) if row else None

    def list_active_by_character(
        self, character_id: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        now = _utcnow()
        q = self._session.query(self._model).filter_by(character_id=character_id)
        rows = q.order_by(self._model.created_at.desc()).limit(limit).all()
        results = []
        for row in rows:
            if row.expires_at and row.expires_at < now:
                continue
            results.append(_row_to_dict(row))
            if len(results) >= limit:
                break
        return results

    def list_by_source_story(self, source_story_id: str) -> list[dict[str, Any]]:
        rows = (
            self._session.query(self._model)
            .filter_by(source_story_id=source_story_id)
            .all()
        )
        return [_row_to_dict(r) for r in rows]

    def count_by_source_story(self, source_story_id: str) -> int:
        return (
            self._session.query(self._model)
            .filter_by(source_story_id=source_story_id)
            .count()
        )


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════
def _stable_key(memory) -> str:
    return f"{memory.character_id}|{memory.source_story_id}|{memory.memory_type}|{memory.content}"


def _stable_key_from_dict(d: dict[str, Any]) -> str:
    return f"{d.get('character_id','')}|{d.get('source_story_id','')}|{d.get('memory_type','')}|{d.get('content','')}"


def _stable_key_from_row(row) -> str:
    return f"{row.character_id}|{row.source_story_id}|{row.memory_type}|{row.content}"


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "memory_id": row.memory_id,
        "character_id": row.character_id,
        "memory_type": row.memory_type,
        "content": row.content,
        "importance": row.importance or 0.0,
        "source_story_id": row.source_story_id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }