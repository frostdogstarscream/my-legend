"""Phase 8: Deterministic Memory Selector.

No LLM. No embedding. No pgvector.
Scoring: importance + recency + keyword relevance.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .memory import MemorySelectorConfig, SelectedMemory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemorySelector:
    """Deterministic memory selector: select Top N relevant memories
    for a given character and story context.

    Scoring:
      score = w_imp * importance + w_rec * recency + w_kw * keyword_relevance
    """

    def __init__(self, config: MemorySelectorConfig | None = None):
        self.cfg = config or MemorySelectorConfig()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def select(
        self,
        character_id: str,
        memory_dicts: list[dict[str, Any]],
        story_query: str,
    ) -> list[SelectedMemory]:
        """Select relevant memories for injection.

        Args:
            character_id: The character to select memories for.
            memory_dicts: Active (non-expired) memory dicts for this character.
            story_query: Natural-language description of the current story.
        Returns:
            Ordered list of SelectedMemory (best first).
        """
        now = _utcnow()

        # 1. Filter: character_id match + non-expired
        candidates = self._filter_candidates(memory_dicts, character_id, now)

        # 2. Score
        scored = self._score_all(candidates, story_query, now)

        # 3. Stable sort: score desc → created_at desc → memory_id asc
        scored.sort(
            key=lambda x: (-x.final_score, -x.created_at.timestamp(), x.memory_id)
        )

        # 4. Top N + budget
        selected = self._apply_budget(scored)

        return selected

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------
    def _filter_candidates(
        self,
        memory_dicts: list[dict[str, Any]],
        character_id: str,
        now: datetime,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for m in memory_dicts:
            if m.get("character_id") != character_id:
                continue
            if m.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(m["expires_at"])
                    if exp < now:
                        continue
                except (ValueError, TypeError):
                    pass
            result.append(m)

        # Limit candidate pool for performance
        if len(result) > self.cfg.candidate_pool_limit:
            result.sort(key=lambda x: x.get("importance", 0.0), reverse=True)
            result = result[:self.cfg.candidate_pool_limit]

        return result

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _score_all(
        self,
        candidates: list[dict[str, Any]],
        story_query: str,
        now: datetime,
    ) -> list[SelectedMemory]:
        results: list[SelectedMemory] = []
        for m in candidates:
            imp_score = self._importance_score(m)
            rec_score = self._recency_score(m, now)
            kw_score = self._keyword_score(m, story_query)
            final = (
                self.cfg.importance_weight * imp_score
                + self.cfg.recency_weight * rec_score
                + self.cfg.keyword_weight * kw_score
            )
            results.append(SelectedMemory(
                memory_id=m["memory_id"],
                character_id=m["character_id"],
                memory_type=m.get("memory_type", ""),
                content=m.get("content", ""),
                importance=m.get("importance", 0.0),
                source_story_id=m.get("source_story_id", ""),
                created_at=_parse_dt(m.get("created_at"), now),
                importance_score=imp_score,
                recency_score=rec_score,
                keyword_score=kw_score,
                final_score=final,
            ))
        return results

    def _importance_score(self, m: dict[str, Any]) -> float:
        return float(m.get("importance", 0.5))

    def _recency_score(self, m: dict[str, Any], now: datetime) -> float:
        created = _parse_dt(m.get("created_at"), now)
        days = max(0, (now - created).total_seconds() / 86400.0)
        hl = self.cfg.recency_half_life_days
        if hl <= 0:
            return 1.0
        return 2.0 ** (-days / hl)

    def _keyword_score(self, m: dict[str, Any], story_query: str) -> float:
        content = m.get("content", "")
        if not story_query or not content:
            return 0.0
        # Extract tokens (CJK-friendly: 2-gram + Latin word tokenizer)
        q_tokens = _tokenize(story_query)
        c_tokens = _tokenize(content)
        if not q_tokens or not c_tokens:
            return 0.0
        matched = sum(1 for t in q_tokens if t in c_tokens)
        return min(1.0, matched / len(q_tokens))

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------
    def _apply_budget(self, scored: list[SelectedMemory]) -> list[SelectedMemory]:
        selected: list[SelectedMemory] = []
        total_chars = 0
        for sm in scored[:self.cfg.memory_top_n]:
            new_total = total_chars + len(sm.content)
            if new_total > self.cfg.memory_max_chars:
                break
            selected.append(sm)
            total_chars = new_total
        return selected


# ═══════════════════════════════════════════════════════════════════
# Tokenizer (CJK-friendly)
# ═══════════════════════════════════════════════════════════════════
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_LATIN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Basic tokenizer: CJK 2-grams + Latin word tokens."""
    text = text.lower().strip()
    tokens: list[str] = []

    # Extract CJK blocks and produce 2-grams
    for cjk_block in _CJK_RE.findall(text):
        clean = cjk_block
        for i in range(len(clean) - 1):
            tokens.append(clean[i:i + 2])
        if len(clean) == 1:
            tokens.append(clean)

    # Extract Latin/numbers
    for lat in _LATIN_RE.findall(text):
        tokens.append(lat)

    return tokens


def _parse_dt(val: Any, fallback: datetime) -> datetime:
    if not val:
        return fallback
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return fallback