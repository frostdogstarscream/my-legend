"""Deterministic pre-writing context for an approved episode.

This module deliberately does not call a model.  It turns the approved snapshot
into a compact writing packet so the episode writer can spend its token budget
on craft instead of rediscovering constraints that the application already
knows.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .schemas import ApprovedPlanSnapshot, EpisodeOutline


class SceneWritingCard(BaseModel):
    scene_index: int
    objective: str
    required_beats: list[str] = Field(default_factory=list)
    spoken_character_budget: int
    dialogue_character_budget: int
    narration_character_budget: int
    audible_change: str


class EpisodeCreativePacket(BaseModel):
    episode_index: int
    story_promise: str
    theme: str
    episode_goal: str
    ending_condition: str
    ending_contract: str
    mode_strategy: list[str]
    strategy_hints: list[str]
    character_logic: list[dict[str, Any]]
    voice_cast: list[dict[str, str]]
    mystery_causal_proof: dict[str, str] | None = None
    known_facts: list[Any]
    unresolved_threads: list[Any]
    immutable_constraints: list[str]
    scene_cards: list[SceneWritingCard]
    spoken_character_budget: dict[str, int]
    audio_time_budget_sec: dict[str, int]


_MODE_STRATEGIES = {
    "mystery": [
        "揭晓前至少出现一个可听、可复核的关键线索",
        "区分事实、人物推测与误导；结论必须有获取路径",
        "反转重新解释既有证据，不凭空增加决定性证据",
    ],
    "serialized": [
        "完成本集局部行动目标并留下具体长期问题",
        "本集结尾改变人物处境，不能只在高潮处突然截断",
        "长期悬念沿用 continuity 中已有事实和未解线程",
    ],
    "viral_drama": [
        "开场尽快让利害关系落到具体人物选择",
        "强情绪来自目标冲突和代价，不依赖口号或连续打脸",
        "反转必须由人物此前的行动触发",
    ],
    "general": [
        "优先人物关系或认知的可信变化",
        "用具体动作与生活细节承载主题",
        "不强套悬疑反转或夸张冲突",
    ],
}


def is_closed_short_story(preferences) -> bool:
    """A one-shot auto ending resolves its own promise unless explicitly serialized."""
    serialized = str(preferences.story_mode) == "serialized" or preferences.target_episodes > 1
    return preferences.ending_preference in ("closed", "happy") or (
        preferences.ending_preference == "auto" and not serialized
    )


def ending_contract_for(preferences) -> str:
    if is_closed_short_story(preferences):
        return (
            "完整短篇：在最后一场之前交代核心真相或情感信息；最后约15%只演人物选择、"
            "可听见的后果和前文物件/声音的回响。不得首次揭示隐藏留言、新人物、"
            "新危机或第二个秘密，不得以戛然而止代替收束。"
        )
    return (
        "连载或明确开放结局：先完成本集局部目标与人物选择；长线悬念必须从本集"
        "已经听到的证据自然延伸，最后一场不得突然引入新人物或无前因危机。"
    )


def build_episode_creative_packet(
    snapshot: ApprovedPlanSnapshot,
    outline: EpisodeOutline,
    min_chars: int,
    ideal_chars: int,
    max_chars: int,
    continuity_override: dict | None = None,
) -> EpisodeCreativePacket:
    """Build the writer-facing packet without changing the approved plan."""
    preview = snapshot.preview
    prefs = preview.resolved_preferences
    beats = list(outline.major_beats)
    scene_count = _scene_count(prefs.target_duration_sec, len(beats))
    beat_groups = _partition(beats, scene_count)
    scene_budgets = _allocate(ideal_chars, [max(1, len(group)) for group in beat_groups])
    dialogue_ratio = {"low": 0.52, "medium": 0.68, "high": 0.80}[prefs.dialogue_density]

    scene_cards = []
    for index, (group, budget) in enumerate(zip(beat_groups, scene_budgets), 1):
        dialogue = round(budget * dialogue_ratio)
        scene_cards.append(SceneWritingCard(
            scene_index=index,
            objective=group[0] if group else outline.core_goal,
            required_beats=group,
            spoken_character_budget=budget,
            dialogue_character_budget=dialogue,
            narration_character_budget=budget - dialogue,
            audible_change=(
                "本场结束时必须让事实、关系、处境或下一步行动发生一项可听见的变化"
            ),
        ))

    source = preview.source_reference
    continuity = continuity_override if continuity_override is not None else (source.continuity_snapshot if source else {})
    portrayals = {item.role_id: item for item in preview.plan.character_portrayals}
    character_logic = []
    for item in preview.character_snapshots:
        canon, profile = item.canon, item.profile
        portrayal = portrayals.get(item.role_id)
        character_logic.append({
            "role_id": item.role_id,
            "performer_id": item.performer_id,
            "canon_name": canon.get("name") or item.character_id,
            "story_name": portrayal.story_name if portrayal else (canon.get("name") or profile.get("name") or item.character_id),
            "immutable_facts": canon.get("immutable_facts", []),
            "current_goal": portrayal.external_goal if portrayal else (profile.get("goal") or profile.get("current_goal") or "由获批大纲限定"),
            "motivation": profile.get("motivation") or profile.get("desire") or "仅按获批剧情推断",
            "fear_or_avoidance": portrayal.emotional_avoidance if portrayal else (profile.get("fear") or profile.get("avoidance") or "未知"),
            "speech_style": portrayal.speech_style if portrayal else (profile.get("speech_style") or profile.get("voice") or "根据人物关系保持稳定且可区分"),
            "relationship_stance": portrayal.relationship_stance if portrayal else "以获批大纲为准",
            "known_facts": profile.get("known_facts", []),
            "behavior_boundaries": profile.get("behavior_boundaries", []),
        })

    transition_sec = max(12, round(prefs.target_duration_sec * 0.12))
    strategy_hints: list[str] = []
    strategy_snapshot = getattr(preview, "strategy_snapshot", None) or {}
    if strategy_snapshot:
        cm = strategy_snapshot.get("conflict_mechanism", "")
        cd = strategy_snapshot.get("causal_driver", "")
        es = strategy_snapshot.get("ending_strategy", "")
        tg = strategy_snapshot.get("tone_guidance", "")
        if cm:
            strategy_hints.append(f"冲突机制：{cm}")
        if cd:
            strategy_hints.append(f"因果驱动：{cd}")
        if es:
            strategy_hints.append(f"结局策略：{es}")
        if tg:
            strategy_hints.append(f"基调：{tg}")
    return EpisodeCreativePacket(
        episode_index=outline.index,
        story_promise=preview.plan.premise,
        theme=preview.plan.theme,
        episode_goal=outline.core_goal,
        ending_condition=outline.ending,
        ending_contract=ending_contract_for(prefs),
        mode_strategy=_MODE_STRATEGIES.get(str(prefs.story_mode), _MODE_STRATEGIES["general"]),
        strategy_hints=strategy_hints,
        character_logic=character_logic,
        voice_cast=[
            {"role_id": role.role_id, "story_name": role.story_name,
             "performer_id": role.performer_id, "medium": role.medium}
            for role in preview.plan.fictional_voice_roles
        ],
        mystery_causal_proof=(
            preview.plan.mystery_causal_proof.model_dump(mode="json")
            if preview.plan.mystery_causal_proof else None
        ),
        known_facts=list(continuity.get("facts") or []),
        unresolved_threads=list(continuity.get("unresolved_threads") or []),
        immutable_constraints=[
            *preview.plan.continuity_constraints,
            *prefs.must_include,
            *(f"不得出现：{item}" for item in prefs.must_avoid),
        ],
        scene_cards=scene_cards,
        spoken_character_budget={"minimum": min_chars, "ideal": ideal_chars, "maximum": max_chars},
        audio_time_budget_sec={
            "total": prefs.target_duration_sec,
            # The release gate still estimates spoken text at 3.5 chars/sec.  Cue
            # time is advisory and may overlap speech; it is not additive proof of
            # real audio duration.  A TTS dry-run must replace this heuristic later.
            "spoken_text_gate_estimate": prefs.target_duration_sec,
            "independent_transition_advisory": transition_sec,
        },
    )


def _scene_count(duration_sec: int, beat_count: int) -> int:
    preferred = 3 if duration_sec <= 120 else 4 if duration_sec <= 180 else 5
    return max(1, min(preferred, max(1, beat_count)))


def _partition(items: list[str], group_count: int) -> list[list[str]]:
    groups: list[list[str]] = [[] for _ in range(group_count)]
    for index, item in enumerate(items):
        groups[min(group_count - 1, index * group_count // max(1, len(items)))].append(item)
    return groups


def _allocate(total: int, weights: list[int]) -> list[int]:
    weight_total = sum(weights)
    raw = [total * weight // weight_total for weight in weights]
    for index in range(total - sum(raw)):
        raw[index % len(raw)] += 1
    return raw
