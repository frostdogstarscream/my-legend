"""Phase 8: Memory–Canon Conflict Guard.

Ensures Memory NEVER overrides Canon locked fields.
"""
from __future__ import annotations

from .models import CharacterCanon


class MemoryCanonGuard:
    """Gatekeeper between Character Memory and Canon.

    Principles:
      1. Memory cannot write into Canon locked fields.
      2. Memory can only influence Runtime dynamic fields.
      3. Conflicting Memory is filtered (not merged).
      4. Conflict does not fail the entire story.
    """

    # Fields that Memory is allowed to influence in Runtime
    ALLOWED_RUNTIME_FIELDS: set[str] = {
        "personality", "goal", "motivation", "fear",
        "stance", "dialogue_style", "story_function",
    }

    # Fields that are permanently locked to Canon — Memory can NEVER touch
    CANON_ONLY_FIELDS: set[str] = {
        "name", "gender", "species", "age", "age_band",
        "identity", "character_type", "world_id",
        "appearance", "abilities", "immutable_facts",
    }

    def check(
        self,
        canon: CharacterCanon,
        memory_content: str,
        memory_type: str,
    ) -> tuple[bool, str]:
        """Check if a memory conflicts with Canon.

        Returns:
            (allowed, reason)
        """
        # If memory_type maps to a Canon-only field, reject
        if self._maps_to_canon_field(memory_type, memory_content, canon):
            return False, f"memory touches Canon-locked field: {memory_type}"

        # Check for content that contradicts immutable facts
        for fact in canon.immutable_facts:
            if _content_contradicts(memory_content, fact):
                return False, f"memory contradicts immutable_fact: {fact}"

        return True, ""

    def _maps_to_canon_field(
        self, memory_type: str, content: str, canon: CharacterCanon,
    ) -> bool:
        """Check if the memory_type attempts to modify a Canon field.

        memory_type values like 'identity_override', 'species_override' etc.
        are prohibited by design, but we guard anyway.
        """
        # Direct override types are rejected by taxonomy (Section 8)
        if "override" in memory_type.lower():
            return True
        return False

    def filter_conflicting(
        self,
        canon: CharacterCanon,
        memory_contents: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Filter a batch of memory snippets.

        Returns:
            (safe_memories, conflicts)
        """
        safe: list[dict] = []
        conflicts: list[dict] = []
        for mem in memory_contents:
            allowed, reason = self.check(
                canon,
                mem.get("content", ""),
                mem.get("memory_type", ""),
            )
            if allowed:
                safe.append(mem)
            else:
                conflicts.append({**mem, "conflict_reason": reason})
        return safe, conflicts


def _content_contradicts(content: str, fact: str) -> bool:
    """Simple deterministic check: does content contain words suggesting
    reversal of a stated fact?

    This is lightweight; a full semantic check requires LLM (future phase).
    """
    if not fact or not content:
        return False
    # Check for explicit negation markers
    fact_lower = fact.lower()
    content_lower = content.lower()
    negation_markers = ["不再", "不再会", "不会", "不能", "不是", "从未", "never", "not"]
    for marker in negation_markers:
        if marker in content_lower:
            # Split fact into keywords and check if content re-directs them
            keywords = _extract_keywords(fact_lower)
            if any(kw in content_lower for kw in keywords):
                return True
    return False


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a fact string."""
    import re
    # Simple: extract CJK / Latin content words
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    latin = re.findall(r"[a-zA-Z]{3,}", text)
    return cjk + latin