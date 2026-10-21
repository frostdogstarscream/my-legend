"""Phase 8: Character Memory Summarizer.

Extracts candidate memories from a completed StoryRecord.
Uses LLM through the standard Provider tier.
"""
from __future__ import annotations

from typing import Any

from .memory import CharacterMemoryCandidate
from ..contracts import Runtime


class CharacterMemorySummarizer:
    """Summarize a completed StoryRecord into memory candidates.

    Input: StoryRecord + character canon + story context
    Output: CharacterMemoryCandidate[] (not yet persisted)

    Design:
      - Each character gets ≤ max_new_memories_per_character
      - Memories are short, character-centric, long-term valuable
      - Does NOT save full story text or per-chapter trivia
    """

    def __init__(
        self,
        *,
        max_new_memories_per_character: int = 3,
    ):
        self._max_per_char = max_new_memories_per_character

    # ------------------------------------------------------------------
    def summarize(
        self,
        story_record: dict[str, Any],
        characters: list[dict[str, Any]],
        runtime: Runtime | None = None,
    ) -> list[CharacterMemoryCandidate]:
        """Extract memory candidates from a completed story.

        Args:
            story_record: The persisted StoryRecord dict.
            characters: List of character dicts with character_id, name, canon fields.
            runtime: Optional LLM Runtime (if None, produces no LLM memories).
        Returns:
            List of CharacterMemoryCandidate (may be empty).
        """
        candidates: list[CharacterMemoryCandidate] = []

        for char in characters:
            char_id = char.get("character_id")
            if not char_id:
                # Temporary character — never write long-term memory
                continue

            char_candidates = self._summarize_for_character(
                char_id, char, story_record, runtime,
            )
            # Enforce per-character cap
            sorted_by_importance = sorted(
                char_candidates, key=lambda c: c.importance, reverse=True,
            )
            candidates.extend(sorted_by_importance[:self._max_per_char])

        return candidates

    # ------------------------------------------------------------------
    def _summarize_for_character(
        self,
        character_id: str,
        character: dict[str, Any],
        story_record: dict[str, Any],
        runtime: Runtime | None,
    ) -> list[CharacterMemoryCandidate]:
        source_story_id = story_record.get("story_id", "")
        source_content = _extract_story_text(story_record)

        if runtime is not None:
            return self._llm_summarize(
                character_id, character, source_content, source_story_id, runtime,
            )
        else:
            return self._mock_summarize(
                character_id, character, source_content, source_story_id,
            )

    # ------------------------------------------------------------------
    def _mock_summarize(
        self,
        character_id: str,
        character: dict[str, Any],
        story_text: str,
        source_story_id: str,
    ) -> list[CharacterMemoryCandidate]:
        """Deterministic mock summarizer for tests (no LLM)."""
        name = character.get("name", character_id)
        if not story_text.strip():
            return []

        # Simple heuristic: extract one memory per meaningful sentence
        sentences = [s.strip() for s in story_text.replace("。", ".\n").split("\n") if s.strip()]
        if not sentences:
            sentences = [story_text[:120]]

        candidates: list[CharacterMemoryCandidate] = []
        for i, sent in enumerate(sentences[:self._max_per_char]):
            if name in sent or character_id in sent:
                candidates.append(CharacterMemoryCandidate(
                    character_id=character_id,
                    memory_type="experience",
                    content=sent[:200],
                    importance=0.5 + 0.15 * (self._max_per_char - i) / self._max_per_char,
                    source_story_id=source_story_id,
                ))

        # If no character mention found, take first sentence as fallback
        if not candidates and sentences:
            candidates.append(CharacterMemoryCandidate(
                character_id=character_id,
                memory_type="experience",
                content=f"{name}参与了：{sentences[0][:200]}",
                importance=0.5,
                source_story_id=source_story_id,
            ))

        return candidates[:self._max_per_char]

    # ------------------------------------------------------------------
    def _llm_summarize(
        self,
        character_id: str,
        character: dict[str, Any],
        story_text: str,
        source_story_id: str,
        runtime: Runtime,
    ) -> list[CharacterMemoryCandidate]:
        """LLM-based summarizer (production path).

        Uses BALANCED tier. Structured output with retry budget.
        """
        # TODO: Phase 8 LLM summarizer integration
        # For now, fall back to mock
        return self._mock_summarize(character_id, character, story_text, source_story_id)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════
def _extract_story_text(story_record: dict[str, Any]) -> str:
    """Extract prose text from a StoryRecord for summarization."""
    content_json = story_record.get("content_json", {})
    chapter_results = content_json.get("chapter_results", [])
    if chapter_results:
        parts = []
        for ch in chapter_results:
            if isinstance(ch, dict):
                render = ch.get("render_result", ch.get("text", ""))
                if render:
                    parts.append(str(render))
        return "\n".join(parts)

    output = story_record.get("output_json", {})
    prose = output.get("prose", "")
    return str(prose) if prose else str(content_json.get("plan", ""))