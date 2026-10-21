"""Story Strategy Resolver — data-driven, 0-LLM.

Unifies Story Mode + Genre + Recipe + Template into a single
ResolvedStoryStrategy that the Plan and Writer stages can consume.

Design principles:
- Data-driven: recipe mechanisms, genre mappings, and mode affinities
  are read from the RuleLibrary (JSON), not hardcoded Python constants.
- Explicit-first: user-specified values always win; auto-match only fills gaps.
- Conflict-aware: incompatible combinations produce clear errors.
- Extensible: R6–R13 (and beyond) are loaded as library data — adding a
  new recipe only requires a JSON entry, not a Python code change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..config import RuleLibrary
from ..core.errors import EngineErrorCode, StoryEngineError


class StrategyConflictError(StoryEngineError):
    """Raised when the user's explicit choices are incompatible."""

    def __init__(self, message: str) -> None:
        super().__init__(
            code=EngineErrorCode.MODE_NOT_SUPPORTED,
            message=message,
        )


# ---------------------------------------------------------------------------
# Resolved Story Strategy
# ---------------------------------------------------------------------------

class ResolvedStoryStrategy(BaseModel):
    """The frozen strategy snapshot that enters Plan and Writer stages."""

    story_mode: str = Field(description="Final resolved story mode key")
    mode_label: str = Field(default="", description="Human-readable mode name")

    genre_id: str = Field(description="Genre from formula library, e.g. G01")
    genre_name: str = Field(default="", description="Human-readable genre name")
    genre_mechanism: str = Field(default="", description="Core conflict/emotional mechanism")
    genre_emotion: str = Field(default="", description="Emotional arc, e.g. '怒→爽→暖'")

    recipe_id: str = Field(default="", description="Recipe id, e.g. R1")
    recipe_name: str = Field(default="", description="Human-readable recipe name")
    recipe_slots: dict[str, str] = Field(default_factory=dict, description="Slot assignments A→E")
    recipe_landing_note: str = Field(default="", description="Audio-adaptation guidance")
    recipe_version: str = Field(default="", description="Recipe version from library data")
    recipe_enabled: bool = Field(default=True, description="Whether the recipe is enabled")

    template_ref: str = Field(default="", description="Resolved template id")
    template_version: int = Field(default=1, description="Template version")

    explicit_fields: list[str] = Field(default_factory=list)
    auto_filled_fields: list[str] = Field(default_factory=list)

    conflict_mechanism: str = Field(default="", description="How conflict escalates")
    causal_driver: str = Field(default="", description="Causal progression driver")
    ending_strategy: str = Field(default="", description="How the story should end")
    tone_guidance: str = Field(default="", description="Tone hints for the writer")

    genre_data: dict[str, Any] = Field(default_factory=dict)
    recipe_data: dict[str, Any] = Field(default_factory=dict)

    def plan_context(self) -> str:
        """Compact context for injection into the Plan system prompt."""
        parts = []
        if self.story_mode:
            parts.append(f"故事模式：{self.mode_label}（{self.story_mode}）")
        if self.genre_id:
            parts.append(f"题材赛道：{self.genre_name}（{self.genre_id}）")
            if self.genre_mechanism:
                parts.append(f"题材机制：{self.genre_mechanism}")
            if self.genre_emotion:
                parts.append(f"情绪弧线：{self.genre_emotion}")
        if self.recipe_id:
            parts.append(f"角色配方：{self.recipe_name}（{self.recipe_id}）")
            if self.recipe_landing_note:
                parts.append(f"配方落地提醒：{self.recipe_landing_note}")
        if self.conflict_mechanism:
            parts.append(f"核心冲突机制：{self.conflict_mechanism}")
        if self.causal_driver:
            parts.append(f"因果推进方式：{self.causal_driver}")
        if self.ending_strategy:
            parts.append(f"结局策略：{self.ending_strategy}")
        if self.tone_guidance:
            parts.append(f"基调指导：{self.tone_guidance}")
        return "\n".join(parts)

    def writer_context(self) -> list[str]:
        """Hints for injection into the Writer stage via creative_packet.strategy_hints."""
        hints = []
        if self.conflict_mechanism:
            hints.append(f"冲突机制：{self.conflict_mechanism}")
        if self.causal_driver:
            hints.append(f"因果驱动：{self.causal_driver}")
        if self.ending_strategy:
            hints.append(f"结局策略：{self.ending_strategy}")
        if self.tone_guidance:
            hints.append(f"基调：{self.tone_guidance}")
        return hints


# ---------------------------------------------------------------------------
# Keyword Helpers
# ---------------------------------------------------------------------------

def _chinese_keywords(text: str) -> list[str]:
    """Extract CJK character 2-3 grams + significant single chars."""
    if not text:
        return []
    cjk = [c for c in text if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf"]
    if not cjk:
        return []
    grams = []
    for i in range(len(cjk)):
        grams.append(cjk[i])
        if i + 1 < len(cjk):
            grams.append(cjk[i] + cjk[i + 1])
        if i + 2 < len(cjk):
            grams.append(cjk[i] + cjk[i + 1] + cjk[i + 2])
    return sorted(set(grams), key=len, reverse=True)


def _is_significant_char(ch: str) -> bool:
    """Single CJK chars that carry meaning."""
    trivial = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
               "他", "她", "它", "们", "这", "那", "一", "个", "都", "也",
               "很", "到", "说", "要", "去", "你", "会", "着", "没", "看",
               "好", "自", "过", "对", "想", "来", "能", "多", "然", "心"}
    return ch not in trivial


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class StoryStrategyResolver:
    """Resolve story strategy: Mode + Genre + Recipe + Template.

    All mapping data comes from the RuleLibrary (JSON), enabling R1–R5
    and R6–R13 (and beyond) without code changes.
    """

    def __init__(self, lib: RuleLibrary) -> None:
        self.lib = lib

    # ── public API ──

    def resolve(
        self,
        *,
        story_mode: str,
        genre: str,
        recipe_id: str | None = None,
        template_ref: str | None = None,
        user_instruction: str = "",
        explicit_fields: list[str] | None = None,
    ) -> ResolvedStoryStrategy:
        explicit = set(explicit_fields or [])
        auto_filled: list[str] = []

        # --- Step 1: resolve genre ---
        resolved_genre_id = self._resolve_genre(genre, user_instruction, explicit, auto_filled)
        genre_data = self.lib.genre(resolved_genre_id) or {}

        # --- Step 2: resolve story mode ---
        resolved_mode = self._resolve_mode(story_mode, resolved_genre_id, genre_data, explicit, auto_filled)
        mode_label = self._mode_label(resolved_mode)

        # --- Step 3: resolve recipe ---
        resolved_recipe_id = self._resolve_recipe(
            recipe_id, resolved_genre_id, resolved_mode, explicit, auto_filled,
        )
        recipe_data = self.lib.recipe(resolved_recipe_id) or {}
        recipe_slots = {s: recipe_data.get(s, "") for s in ("A", "B", "C", "D", "E")}

        # --- Step 4: resolve mechanisms ---
        mechanisms = self.lib.recipe_mechanism(resolved_recipe_id) or {
            "conflict_mechanism": "",
            "causal_driver": "",
            "ending_strategy": "",
            "tone_guidance": "",
        }

        return ResolvedStoryStrategy(
            story_mode=resolved_mode,
            mode_label=mode_label,
            genre_id=resolved_genre_id,
            genre_name=genre_data.get("name", ""),
            genre_mechanism=genre_data.get("mechanism", ""),
            genre_emotion=genre_data.get("emotion", ""),
            recipe_id=resolved_recipe_id or "",
            recipe_name=recipe_data.get("name", ""),
            recipe_slots=recipe_slots,
            recipe_landing_note=recipe_data.get("landing_note", ""),
            recipe_version=recipe_data.get("version", ""),
            recipe_enabled=recipe_data.get("enabled", True),
            template_ref=template_ref or "",
            template_version=1,
            explicit_fields=sorted(explicit),
            auto_filled_fields=sorted(auto_filled),
            conflict_mechanism=mechanisms["conflict_mechanism"],
            causal_driver=mechanisms["causal_driver"],
            ending_strategy=mechanisms["ending_strategy"],
            tone_guidance=mechanisms["tone_guidance"],
            genre_data=genre_data,
            recipe_data=recipe_data,
        )

    # ── Step 1: Genre ──

    def _resolve_genre(self, genre: str, instruction: str,
                       explicit: set, auto_filled: list) -> str:
        if genre and genre != "auto":
            # Explicit genre: formula ID or free-text
            if genre.startswith("G") and genre[1:].isdigit():
                return genre
            # Free-text lookup (data-driven from library keywords)
            ft_map = self.lib.freetext_genre_map
            gid = self._match_free_text(genre, ft_map)
            if gid:
                return gid
            # Fallback: keyword match from genre text + instruction
            return self._match_or_fallback(genre + " " + instruction)
        # Auto: match from instruction keywords
        auto_filled.append("genre")
        return self._match_or_fallback(instruction)

    def _match_free_text(self, text: str, ft_map: dict[str, str]) -> str | None:
        """Exact match or substring match against free-text genre keywords."""
        if text in ft_map:
            return ft_map[text]
        for kw in sorted(ft_map, key=len, reverse=True):
            if kw in text:
                return ft_map[kw]
        return None

    def _match_or_fallback(self, text: str) -> str:
        """Keyword match from idea, fallback to best audio-fit genre."""
        matched = self._match_genre_from_idea(text)
        if matched:
            return matched
        # Best audio-fit as safe default
        best = max(self.lib.all_genres, key=lambda g: g.get("audio_fit_score", 0),
                   default={"genre_id": "G01"})
        return best.get("genre_id", "G01")

    # ── Step 2: Mode ──

    def _resolve_mode(self, mode: str, genre_id: str,
                      genre_data: dict, explicit: set, auto_filled: list) -> str:
        if mode and mode != "auto":
            return mode
        auto_filled.append("story_mode")
        # Infer from genre data or recipe affinity
        # Default: general is the safest fallback
        return genre_data.get("default_mode", "general")

    @staticmethod
    def _mode_label(mode: str) -> str:
        return {
            "general": "通用故事", "viral_drama": "爆款广播剧",
            "mystery": "悬疑", "serialized": "连载",
        }.get(mode, mode)

    # ── Step 3: Recipe ──

    def _resolve_recipe(self, recipe_id: str | None, genre_id: str,
                        mode: str, explicit: set, auto_filled: list) -> str | None:
        if recipe_id:
            # Validate the recipe exists
            r = self.lib.recipe(recipe_id)
            if not r:
                available = sorted(item.get("id", "?") for item in self.lib.all_recipes)
                raise StrategyConflictError(
                    f"配方 '{recipe_id}' 不存在。可用配方：{', '.join(available)}"
                )
            if not r.get("enabled", True):
                raise StrategyConflictError(
                    f"配方 '{recipe_id}'（{r.get('name', '?')}）已禁用（版本 {r.get('version', '?')}）"
                )
            # Check mode compatibility
            affinity = self.lib.recipe_mode_affinity()
            allowed_modes = affinity.get(recipe_id, [])
            if allowed_modes and mode not in allowed_modes:
                raise StrategyConflictError(
                    f"配方 '{recipe_id}'（{r.get('name', '?')}）不支持模式 '{mode}'。"
                    f"支持的公式模式：{', '.join(allowed_modes)}"
                )
            return recipe_id

        # Auto: derive from genre
        auto_filled.append("recipe_id")
        affinity = self.lib.recipe_mode_affinity()
        default = self.lib.genre_default_recipe(genre_id)
        if default:
            r = self.lib.recipe(default)
            if r and r.get("enabled", True):
                # Validate mode compatibility for auto-picked recipe
                allowed_modes = affinity.get(default, [])
                if not allowed_modes or mode in allowed_modes:
                    return default
                # Mode mismatch: skip this recipe, fall through to generic
        # Generic fallback: first enabled recipe compatible with mode
        for r in self.lib.all_recipes:
            if not r.get("enabled", True):
                continue
            rid = r.get("id", "")
            if mode in affinity.get(rid, [mode]):
                return rid
        return None

    # ── Keyword matching ──

    def _match_genre_from_idea(self, idea: str) -> str | None:
        idea_lower = idea.casefold()
        best_score = 0
        best_genre: str | None = None

        for genre in self.lib.all_genres:
            score = 0
            name = genre.get("name", "").casefold()
            mechanism = genre.get("mechanism", "").casefold()
            emotion = genre.get("emotion", "").casefold()
            keywords = genre.get("keywords", [])

            seen_single: set[str] = set()
            seen_multi: set[str] = set()

            # Name keywords (highest weight)
            for kw in _chinese_keywords(name):
                if len(kw) >= 2:
                    if kw not in seen_multi and kw in idea_lower:
                        score += 20
                        seen_multi.add(kw)
                elif len(kw) == 1 and _is_significant_char(kw):
                    if kw not in seen_single and kw in idea_lower:
                        score += 12
                        seen_single.add(kw)

            # Mechanism keywords (medium weight)
            for kw in _chinese_keywords(mechanism):
                if len(kw) >= 2:
                    if kw not in seen_multi and kw in idea_lower:
                        score += 10
                        seen_multi.add(kw)
                elif len(kw) == 1 and _is_significant_char(kw):
                    if kw not in seen_single and kw in idea_lower:
                        score += 8
                        seen_single.add(kw)

            # Genre keywords from data
            for kw in keywords:
                if kw in idea_lower:
                    score += 15

            # Emotion keywords (lowest weight)
            for kw in _chinese_keywords(emotion):
                if len(kw) >= 2:
                    if kw not in seen_multi and kw in idea_lower:
                        score += 8
                        seen_multi.add(kw)
                elif len(kw) == 1 and _is_significant_char(kw):
                    if kw not in seen_single and kw in idea_lower:
                        score += 5
                        seen_single.add(kw)

            if score > best_score:
                best_score = score
                best_genre = genre.get("genre_id")

        return best_genre if best_score >= 10 else None