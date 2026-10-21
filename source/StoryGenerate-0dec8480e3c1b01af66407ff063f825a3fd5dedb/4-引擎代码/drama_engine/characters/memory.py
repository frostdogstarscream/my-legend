"""Phase 8: Character Memory domain model.

Character Memory is cross-story long-term memory for persistent characters.
It is NOT:
  - Current story FactLedger (Phase 7)
  - Character Canon (immutable identity)
  - Per-chapter checkpoint state

Design rules:
  - Memory NEVER overrides Canon
  - Memory only comes from successfully persisted StoryRecords
  - Temporary characters (character_id=None) never receive memory
  - Memory is idempotent per (character_id, source_story_id, memory_type, content)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════
# CharacterMemory
# ═══════════════════════════════════════════════════════════════════
class CharacterMemory(BaseModel):
    """A single cross-story memory entry for a persistent character."""

    memory_id: str = Field(default_factory=_new_id)
    character_id: str
    memory_type: str  # experience | relationship | preference | goal_outcome | belief_or_attitude
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source_story_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CharacterMemory:
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════
# CharacterMemoryCandidate  (summarizer output)
# ═══════════════════════════════════════════════════════════════════
class CharacterMemoryCandidate(BaseModel):
    """Proposed memory entry from the summarizer (not yet persisted)."""

    character_id: str
    memory_type: str
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source_story_id: str | None = None  # filled by service if missing


# ═══════════════════════════════════════════════════════════════════
# SelectedMemory  (selector output)
# ═══════════════════════════════════════════════════════════════════
class SelectedMemory(BaseModel):
    """A memory entry that passed selection and is ready for injection."""

    memory_id: str
    character_id: str
    memory_type: str
    content: str
    importance: float
    source_story_id: str
    created_at: datetime
    # Scoring breakdown (for observability)
    importance_score: float = 0.0
    recency_score: float = 0.0
    keyword_score: float = 0.0
    final_score: float = 0.0

    def summary(self) -> str:
        age_label = ""
        if self.created_at:
            from datetime import timezone as tz
            days = (datetime.now(tz.utc) - self.created_at).days
            age_label = f" ({days}天前)"
        return f"[{self.memory_type}]{age_label} {self.content}"


# ═══════════════════════════════════════════════════════════════════
# MemorySelectorConfig
# ═══════════════════════════════════════════════════════════════════
class MemorySelectorConfig(BaseModel):
    """Configuration for the deterministic Memory Selector."""

    importance_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    recency_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.25, ge=0.0, le=1.0)

    # Recency decay half-life in days
    recency_half_life_days: float = Field(default=60.0, gt=0.0)

    # Injection budget
    memory_top_n: int = Field(default=5, ge=1)
    memory_max_chars: int = Field(default=2400, ge=1)
    candidate_pool_limit: int = Field(default=50, ge=1)