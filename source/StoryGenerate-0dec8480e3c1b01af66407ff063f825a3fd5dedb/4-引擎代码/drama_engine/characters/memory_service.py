"""Phase 8: Character Memory Service.

Orchestrates the memory lifecycle:
  capture_from_story  → summarizer → upsert → (done)
  select_for_character → selector → guard → (safe list)
"""
from __future__ import annotations

import logging
from typing import Any

from .memory import (
    CharacterMemory,
    CharacterMemoryCandidate,
    MemorySelectorConfig,
    SelectedMemory,
)
from .memory_selector import MemorySelector
from .memory_guard import MemoryCanonGuard
from .memory_summarizer import CharacterMemorySummarizer

log = logging.getLogger(__name__)


class CharacterMemoryService:
    """Central service for Character Memory operations."""

    def __init__(
        self,
        memory_repo: Any,
        *,
        selector_config: MemorySelectorConfig | None = None,
        max_new_memories_per_character: int = 3,
        enabled: bool = True,
    ):
        self._repo = memory_repo
        self._selector = MemorySelector(selector_config)
        self._guard = MemoryCanonGuard()
        self._summarizer = CharacterMemorySummarizer(
            max_new_memories_per_character=max_new_memories_per_character,
        )
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Capture ────────────────────────────────────────────────────
    def capture_from_story(
        self,
        story_record: dict[str, Any],
        characters: list[dict[str, Any]],
        runtime: Any = None,
    ) -> int:
        """Capture and persist memories from a completed StoryRecord.

        Only called after StoryRecord is successfully persisted.

        Returns:
            Number of new memories created (0 if disabled, empty, or all duplicates).
        """
        if not self._enabled:
            return 0

        if not story_record:
            return 0

        # Summarize
        candidates = self._summarizer.summarize(story_record, characters, runtime)
        if not candidates:
            return 0

        # Persist (idempotent)
        count = 0
        source_story_id = story_record.get("story_id", "")
        for cand in candidates:
            memory = CharacterMemory(
                character_id=cand.character_id,
                memory_type=cand.memory_type,
                content=cand.content,
                importance=cand.importance,
                source_story_id=cand.source_story_id or source_story_id,
            )
            try:
                self._repo.upsert(memory)
                count += 1
            except Exception:
                log.warning(
                    "CharacterMemory upsert failed for %s / %s",
                    cand.character_id, cand.memory_type, exc_info=True,
                )

        return count

    # ── Select ────────────────────────────────────────────────────
    def select_for_character(
        self,
        character_id: str,
        story_query: str,
    ) -> list[SelectedMemory]:
        """Select relevant memories for a character in the context of a story.

        Returns:
            Safe, scored, budget-constrained list of SelectedMemory.
        """
        if not self._enabled or not character_id:
            return []

        try:
            candidates = self._repo.list_active_by_character(character_id)
        except Exception:
            log.warning("Memory repo unavailable for %s", character_id, exc_info=True)
            return []

        if not candidates:
            return []

        return self._selector.select(character_id, candidates, story_query)

    # ── Guard ─────────────────────────────────────────────────────
    def filter_by_canon(
        self,
        canon: Any,  # CharacterCanon
        selected: list[SelectedMemory],
    ) -> tuple[list[SelectedMemory], list[dict]]:
        """Filter selected memories through Canon conflict guard.

        Returns:
            (safe_memories, conflict_reports)
        """
        if not selected:
            return [], []

        safe: list[SelectedMemory] = []
        conflicts: list[dict] = []

        for sm in selected:
            allowed, reason = self._guard.check(
                canon, sm.content, sm.memory_type,
            )
            if allowed:
                safe.append(sm)
            else:
                conflicts.append({
                    "memory_id": sm.memory_id,
                    "content": sm.content,
                    "conflict_reason": reason,
                })
                log.info("Memory %s filtered: %s", sm.memory_id, reason)

        return safe, conflicts