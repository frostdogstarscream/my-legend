"""Adapters connecting the workflow to the existing graph and LLM provider."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Protocol, Literal

from pydantic import BaseModel, Field, create_model, model_validator

from ..config import RuleLibrary
from ..llm.usage_context import usage_scope
from ..llm.router import ModelTierMap, resolve_spec
from .creative_context import (
    build_episode_creative_packet, ending_contract_for, is_closed_short_story,
)
from .performance import normalize_utterance, validate_story_delivery
from .operations import source_delivery, fold_continuity, operation_requirements, evidence_kind_errors
from .timeline import timeline_errors, required_clock_mentions
from .storycraft_prompts import (
    PLAN_SYSTEM_V2,
    EPISODE_SYSTEM_V2,
    PERFORMANCE_SYSTEM_V2,
    JUDGE_SYSTEM_V2,
    OPERATION_JUDGE_INSTRUCTIONS,
)
from .schemas import (
    ApprovedPlanSnapshot,
    CharacterSnapshot,
    Emotion,
    OperationCheck,
    TimelineClaim,
    EmphasisSpan,
    EpisodeDelivery,
    EpisodeOutline,
    PlanContent,
    ResolvedSourceReference,
    SceneDelivery,
    StoryDelivery,
    StoryOperationRequest,
    StoryVersionStatus,
    Utterance,
)


class PlanGenerator(Protocol):
    def generate(
        self,
        request: StoryOperationRequest,
        source: ResolvedSourceReference | None,
        characters: list[CharacterSnapshot],
        previous: PlanContent | None = None,
        feedback: str = "",
        template: dict[str, Any] | None = None,
        strategy: Any | None = None,
    ) -> PlanContent: ...


class StoryExecutor(Protocol):
    def execute(self, snapshot: ApprovedPlanSnapshot) -> StoryDelivery: ...


class LLMPlanGenerator:
    """Strong-tier structured Plan generator; never falls back to Mock itself."""

    def __init__(self, provider):
        self.provider = provider
        self.lib = RuleLibrary.load()

    def generate(self, request, source, characters, previous=None, feedback="", template=None, strategy=None) -> PlanContent:
        preferences = request.creation_preferences.model_dump(mode="json")
        system = PLAN_SYSTEM_V2
        system += "\n各集剧情时间必须一致。未给定的精确时刻或倒计时不要随意添加；确需安排时须在全部剧集保持一致，不得混淆维修截止时间与播出时间。"
        payload = {
            "intent": request.intent,
            "operation_requirements": operation_requirements(request.model_dump(mode="json")),
            "user_instruction": request.user_instruction,
            "preferences": preferences,
            "ending_contract": ending_contract_for(request.creation_preferences),
            "beat_sheet": self.lib.beat_sheet_for(request.creation_preferences.target_duration_sec),
            "characters": [item.model_dump(mode="json") for item in characters],
            "source": source.model_dump(mode="json") if source else None,
            "template_ref": request.template_ref.model_dump(mode="json") if request.template_ref else None,
            "template_snapshot": template,
            "safety_profile": request.safety_profile.model_dump(mode="json") if request.safety_profile else None,
            "edit_policy": request.edit.model_dump(mode="json") if hasattr(request, "edit") else None,
            "remix_policy": request.remix.model_dump(mode="json") if hasattr(request, "remix") else None,
            "previous_plan": previous.model_dump(mode="json") if previous else None,
            "revision_feedback": feedback or None,
        }

        # Inject strategy context into system prompt if available
        strategy_context = ""
        if strategy is not None:
            strategy_context = "\n\n【故事创作策略 — 必须遵守以下创作机制】\n" + strategy.plan_context()
            system = PLAN_SYSTEM_V2 + strategy_context
        user = (
            "根据以下冻结参数生成完整结构化大纲。episode_outlines 数量必须等于 target_episodes，"
            "每集 target_duration_sec 必须等于请求值。\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        outline_schema = create_model("BoundEpisodeOutline", __base__=EpisodeOutline,
            target_duration_sec=(Literal[request.creation_preferences.target_duration_sec], ...))
        plan_schema = create_model("BoundPlanContent", __base__=PlanContent,
            episode_outlines=(list[outline_schema], Field(min_length=request.creation_preferences.target_episodes,
                max_length=request.creation_preferences.target_episodes,
                description="Only the requested generation/revision scope, not every episode in the source story")))
        content = self.provider.complete_structured(resolve_spec("outline"), system, user, plan_schema)
        production_policy = getattr(self.provider, 'production_plan_policy', None)
        if production_policy is not None:
            content = production_policy(
                content, creative_context=payload, schema=plan_schema, writer_spec=resolve_spec('outline'),
                review_spec=resolve_spec('storycraft_operation_judge').model_copy(
                    update={'role': 'production_plan_auditor'}),
            )
        # Keep the public type stable after the request-bound contract has passed.
        content = PlanContent.model_validate(content.model_dump(mode="json"))
        validate_generated_plan_cast(content, characters)
        validate_generated_plan_ending(content, request.creation_preferences)
        validate_generated_plan_causality(content, request.creation_preferences)
        return content


_PLACEHOLDER_NAME = re.compile(r"^(?:角色|人物|主角|配角|role|character)[\s_\-]*[0-9一二三四五六七八九十]+$", re.I)


def validate_generated_plan_cast(content: PlanContent, characters: list[CharacterSnapshot]) -> None:
    """Keep approved story names and non-toy voices explicit in a model plan."""
    # When no characters are selected, the model is free to invent portrayals
    if not characters:
        return
    selected = {item.role_id for item in characters if item.role_id}
    portrayed = set(item.role_id for item in content.character_portrayals)
    # Every selected character must have a portrayal; additional portrayals are allowed
    if not selected.issubset(portrayed):
        missing = selected - portrayed
        raise ValueError(f"PLAN_CHARACTER_PORTRAYALS_MISMATCH: missing portrayals for {missing}")
    if any(_PLACEHOLDER_NAME.fullmatch(item.story_name.strip()) for item in content.character_portrayals):
        raise ValueError("PLAN_PLACEHOLDER_STORY_NAME")
    plan_prose = [
        content.title, content.premise, content.beginning, content.development,
        content.climax, content.ending,
        *(text for episode in content.episode_outlines for text in (
            episode.title, episode.core_goal, *episode.major_beats, episode.ending,
        )),
    ]
    if any(re.search(r"(?:角色|人物)[\s_\-]*[0-9一二三四五六七八九十]+", text) for text in plan_prose):
        raise ValueError("PLAN_PLACEHOLDER_IN_STORY")
    fiction = content.fictional_voice_roles
    fiction_ids = [item.role_id for item in fiction]
    if len(fiction_ids) != len(set(fiction_ids)) or selected.intersection(fiction_ids):
        raise ValueError("PLAN_VOICE_ROLE_CONFLICT")
    if any(_PLACEHOLDER_NAME.fullmatch(item.story_name.strip()) for item in fiction):
        raise ValueError("PLAN_PLACEHOLDER_VOICE_NAME")
    if any(not item.performer_id.strip() for item in fiction):
        raise ValueError("PLAN_VOICE_PERFORMER_MISSING")


_TAIL_REVEAL = re.compile(
    r"隐藏留言|新留言|新的留言|新人物|新危机|第二个秘密|另一[封段条位].{0,12}留言|"
    r"突然.{0,12}(?:留言|秘密|危机)|戛然而止"
)


def validate_generated_plan_ending(content: PlanContent, preferences) -> None:
    """Reject explicit late reveal devices in a promised closed short story."""
    if not is_closed_short_story(preferences):
        return
    for episode in content.episode_outlines[-1:]:
        if _TAIL_REVEAL.search(episode.major_beats[-1]):
            raise ValueError(f"PLAN_CLOSED_ENDING_TAIL_REVEAL: episode {episode.index}")
    if re.search(r"Closed\s*/\s*Open|闭合.{0,8}开放|开放.{0,8}闭合", content.ending, re.I):
        raise ValueError("PLAN_AMBIGUOUS_ENDING_TYPE")


def validate_generated_plan_causality(content: PlanContent, preferences) -> None:
    """A newly generated mystery must expose its causal account for approval."""
    if str(preferences.story_mode) == "mystery" and content.mystery_causal_proof is None:
        raise ValueError("PLAN_MYSTERY_CAUSAL_PROOF_MISSING")


class PerformanceAnnotation(BaseModel):
    line_id: str
    emotion: Emotion
    tone_instruction: str = Field(min_length=1)
    emphasis: list[EmphasisSpan] = Field(min_length=1)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    delivery_note: str | None = None


class PerformanceAnnotationBatch(BaseModel):
    annotations: list[PerformanceAnnotation]


class PerformanceAnnotator(Protocol):
    def annotate(self, lines: list[dict[str, Any]], context: dict[str, Any]) -> list[Utterance]: ...


class LLMPerformanceAnnotator:
    """Balanced-tier cue annotator for already-generated spoken lines."""

    def __init__(self, provider):
        self.provider = provider

    def annotate(self, lines, context) -> list[Utterance]:
        aliases = {
            "audio_cue": "sfx", "sound_effect": "sfx", "sound": "sfx",
            "effect": "sfx", "bgm": "music", "background_music": "music",
            "dialog": "dialogue", "voice_over": "narration", "narrative": "narration",
            "scene_direction": "action", "stage_direction": "action", "silence": "action",
        }
        lines = [{**line, "kind": aliases.get(line.get("kind"), line.get("kind"))} for line in lines]
        invalid = [line["kind"] for line in lines if line["kind"] not in (
            "dialogue", "narration", "sfx", "music", "action"
        )]
        if invalid:
            raise ValueError(f"unsupported utterance kinds: {invalid}")
        spoken = [line for line in lines if line.get("kind") in ("dialogue", "narration")]
        annotations: dict[str, PerformanceAnnotation] = {}
        if spoken:
            tier_map = ModelTierMap(mapping=context["model_mapping_snapshot"]) if context.get("model_mapping_snapshot") else None
            system = PERFORMANCE_SYSTEM_V2
            user = json.dumps({"context": context, "spoken_lines": spoken}, ensure_ascii=False, indent=2)
            pending = spoken
            for attempt in range(3):
                with usage_scope(stage='story_generation', node='performance_annotation',
                                 attempt_kind='performance_annotation' if attempt == 0 else 'performance_annotation_repair'):
                    batch = self.provider.complete_structured(
                        resolve_spec("performance_annotation", tier_map=tier_map, temperature=0.3), system,
                        user if attempt == 0 else json.dumps({
                            "repair_only_lines": pending,
                            "instruction": "只修复这些行；重音短语必须在原文中逐字出现。",
                        }, ensure_ascii=False),
                        PerformanceAnnotationBatch,
                    )
                for item in batch.annotations:
                    raw = next((line for line in pending if line["line_id"] == item.line_id), None)
                    if raw is None:
                        continue
                    try:
                        for span in item.emphasis:
                            normalize_utterance(Utterance(
                                line_id=item.line_id, kind=raw["kind"], text=raw["text"],
                                emphasis=[span],
                            ))
                    except ValueError:
                        continue
                    annotations[item.line_id] = item
                pending = [line for line in spoken if line["line_id"] not in annotations]
                if not pending:
                    break
            if pending:
                raise ValueError(f"performance annotations missing or invalid lines: {[line['line_id'] for line in pending]}")

        output: list[Utterance] = []
        for raw in lines:
            kind = raw.get("kind", "action")
            if kind == "silence":
                kind = "action"
            annotation = annotations.get(raw["line_id"])
            utterance = Utterance(
                line_id=raw["line_id"],
                kind=kind,
                speaker_role_id=raw.get("speaker") if kind == "dialogue" else None,
                narrator_id="narrator" if kind == "narration" else None,
                performer_id=raw.get("performer_id") or (raw.get("speaker") if kind == "dialogue" else "narrator"),
                text=raw.get("text", ""),
                emotion=annotation.emotion if annotation else None,
                tone_instruction=annotation.tone_instruction if annotation else None,
                emphasis=annotation.emphasis if annotation else [],
                speech_rate=annotation.speech_rate if annotation else 1.0,
                delivery_note="；".join(filter(None, (
                    raw.get("performance_hint"), annotation.delivery_note if annotation else None,
                ))) or None,
                audio_cue_ref=raw.get("sfx_category"),
            )
            output.append(normalize_utterance(utterance))
        return output


class DraftLine(BaseModel):
    kind: str
    speaker_role_id: str | None = None
    text: str
    audio_cue_ref: str | None = None
    performance_hint: str | None = None


class DraftScene(BaseModel):
    title: str
    location: str = ""
    dramatic_goal: str
    lines: list[DraftLine] = Field(min_length=1)


class DraftEpisode(BaseModel):
    title: str
    synopsis: str
    preview_blurb: str = ""
    scenes: list[DraftScene] = Field(min_length=1)
    episode_summary: str
    ending_hook: str = ""
    fact_delta: list[dict[str, Any]] = Field(default_factory=list)
    relationship_delta: list[dict[str, Any]] = Field(default_factory=list)
    new_open_threads: list[str] = Field(default_factory=list)
    closed_threads: list[str] = Field(default_factory=list)


class DraftScenePatch(BaseModel):
    """One replacement scene used for a bounded duration repair."""

    scene_index: int = Field(ge=1)
    lines: list[DraftLine] = Field(min_length=1)


class DraftScenePatchBatch(BaseModel):
    patches: list[DraftScenePatch] = Field(min_length=1, max_length=1)


_LEADING_STAGE_DIRECTION = re.compile(r"^\s*[（(]([^（）()。！？]{1,16})[）)]\s*")


def normalize_draft_stage_directions(draft: DraftEpisode) -> DraftEpisode:
    """Keep unspoken parenthetical cues out of TTS text without rewriting prose."""
    scenes = []
    for scene in draft.scenes:
        lines = []
        for line in scene.lines:
            if line.kind not in ("dialogue", "narration"):
                lines.append(line)
                continue
            match = _LEADING_STAGE_DIRECTION.match(line.text)
            if match and line.text[match.end():].strip():
                lines.append(line.model_copy(update={
                    "text": line.text[match.end():].lstrip(),
                    "performance_hint": "；".join(filter(None, (line.performance_hint, match.group(1)))),
                }))
            else:
                lines.append(line)
        scenes.append(scene.model_copy(update={"lines": lines}))
    return draft.model_copy(update={"scenes": scenes})


def draft_episode_contract_errors(
    draft: DraftEpisode, target_duration_sec: int, approved_role_ids: set[str],
    *, duration_bounds: tuple[float, float] | None = None,
) -> tuple[int, list[str]]:
    """Check duration and dialogue cast before performance annotation begins."""
    spoken_chars = sum(
        len(line.text) for scene in draft.scenes for line in scene.lines
        if line.kind in ("dialogue", "narration")
    )
    estimated = round(spoken_chars / 3.5)
    errors = []
    if duration_bounds is not None:
        low, _, high = spoken_character_budget(target_duration_sec, duration_bounds=duration_bounds)
        if not low <= spoken_chars <= high:
            errors.append(f"duration estimate {estimated}s outside configured spoken-character bounds {low}-{high}")
    elif not 0.85 * target_duration_sec <= estimated <= 1.15 * target_duration_sec:
        errors.append(
            f"duration estimate {estimated}s outside 85%-115% of {target_duration_sec}s"
        )
    invalid_roles = sorted({
        line.speaker_role_id or "<missing>"
        for scene in draft.scenes for line in scene.lines
        if line.kind == "dialogue" and (
            not line.speaker_role_id or (
                line.speaker_role_id not in approved_role_ids
            )
        )
    })
    if invalid_roles:
        errors.append(f"unapproved dialogue roles: {invalid_roles}")
    # A quoted recording or call has an identifiable character speaker.  It
    # cannot be smuggled into the narrator track to bypass approved casting.
    attributed_narration = [
        line.text[:48] for scene in draft.scenes for line in scene.lines
        if line.kind == "narration" and (
            re.search(r"[（(][^）)]{0,24}(?:录音|留言|广播|电话)[）)]", line.text)
            or re.search(r"(?:录音|留言|广播|电话|预录语音)[^。！？]{0,24}[：:]\s*[“‘\"']", line.text)
        )
    ]
    if attributed_narration:
        errors.append(f"identified recorded speech miscast as narration: {attributed_narration}")
    return estimated, errors


def spoken_character_budget(target_duration_sec: int, *, duration_bounds: tuple[float, float] | None = None) -> tuple[int, int, int]:
    """Return the exact count window used by the duration quality gate."""
    ideal = round(target_duration_sec * 3.5)
    if duration_bounds is not None:
        import math
        low, high = duration_bounds
        if not 0 < low <= 1 <= high <= 3:
            raise ValueError('INVALID_DURATION_BOUNDS')
        return math.ceil(ideal * low), ideal, math.floor(ideal * high)
    return (ideal * 17 + 19) // 20, ideal, ideal * 23 // 20


def resolve_line_cast(line: DraftLine, preview) -> tuple[str, str | None]:
    """Resolve an approved speaker and its recording medium without renaming Canon."""
    if line.kind != "dialogue":
        return "narrator", line.audio_cue_ref
    for character in preview.character_snapshots:
        if character.role_id == line.speaker_role_id:
            return character.performer_id or character.role_id, line.audio_cue_ref
    for role in preview.plan.fictional_voice_roles:
        if role.role_id == line.speaker_role_id:
            return role.performer_id, line.audio_cue_ref or (
                role.medium if role.medium != "live" else None
            )
    # Plan-generated character portrayals (e.g., when no pre-existing characters)
    for portrayal in preview.plan.character_portrayals:
        if portrayal.role_id == line.speaker_role_id:
            return portrayal.role_id, line.audio_cue_ref
    raise ValueError(f"UNAPPROVED_SPEAKER: {line.speaker_role_id}")


class QualityDecision(BaseModel):
    outline_alignment_passed: bool
    continuity_passed: bool
    content_rating_passed: bool
    reasons: list[str] = Field(default_factory=list)
    operation_checks: list[OperationCheck] = Field(default_factory=list)
    timeline_claims: list[TimelineClaim] = Field(default_factory=list)


def bound_quality_schema(requirements, line_ids=None):
    """Make omitted/renamed requirements schema errors, eligible for one JSON repair."""
    check_schema = create_model("BoundOperationCheck", __base__=OperationCheck,
        requirement_id=(Literal[tuple(requirements)], Field(description="Copy one exact required_checks key; do not invent IDs")),
        **({"evidence_line_ids": (list[Literal[tuple(line_ids)]], Field(default_factory=list,
            description="Select actual utterance line_id values proving this check; prefer IDs over formatted quotations"))} if line_ids else {}))
    def complete_checks(self):
        if {c.requirement_id for c in self.operation_checks} != set(requirements):
            raise ValueError("operation_checks must cover every exact required_checks key once")
        return self
    return create_model("BoundQualityDecision", __base__=QualityDecision,
        operation_checks=(list[check_schema], Field(min_length=len(requirements), max_length=len(requirements))),
        **({"timeline_claims": (list[create_model("BoundTimelineClaim", __base__=TimelineClaim,
            line_id=(Literal[tuple(line_ids)], ...))], Field(default_factory=list))} if line_ids else {}),
        __validators__={"complete_checks": model_validator(mode="after")(complete_checks)})


def bound_thread_schema(base, continuity):
    names = tuple(continuity.get("unresolved_threads") or [])
    field_type = list[Literal[names]] if names else list[str]
    field = (Field(default_factory=list, description="Only exact existing unresolved thread names; put closure explanation in episode_summary")
             if names else Field(default_factory=list, max_length=0,
                description="No prior unresolved threads exist; closures within this episode belong in its summary"))
    return create_model("Bound" + base.__name__, __base__=base, closed_threads=(field_type, field))


def canonical_check_evidence(checks, episodes):
    """Resolve citations without paraphrasing or editing any story text."""
    lines = {u.line_id: u.text for ep in episodes for scene in ep.scenes for u in scene.utterances}
    rendered = []
    for check in checks:
        raw = check.model_dump(mode="json")
        raw["raw_evidence_quotes"] = list(raw["evidence_quotes"])
        if raw["evidence_line_ids"]:
            if any(key not in lines for key in raw["evidence_line_ids"]):
                raw["evidence_quotes"] = []
            else:
                raw["evidence_quotes"] = [lines[key] for key in raw["evidence_line_ids"]]
        else:
            normalized = []
            for quote in raw["evidence_quotes"]:
                found = False
                for key, text in lines.items():
                    match = re.match(r"^(?:\[" + re.escape(key) + r"\]|" + re.escape(key)
                                     + r"(?:\s+\([^)]*\))?:)\s*", quote)
                    if match:
                        body = quote[match.end():]
                        if len(body) >= 2 and (body[0], body[-1]) in (("'", "'"), ('"', '"'), ("“", "”"), ("‘", "’")):
                            body = body[1:-1]
                        if body and body in text:
                            normalized.append(body)
                            found = True
                            break
                if not found:
                    normalized.append(quote)
            raw["evidence_quotes"] = normalized
        rendered.append(raw)
    return rendered


class RevisionLine(DraftLine):
    line_id: str


class RevisionScene(BaseModel):
    scene_id: str
    lines: list[RevisionLine] = Field(min_length=1)


class EpisodeRevision(BaseModel):
    scenes: list[RevisionScene] = Field(min_length=1)
    episode_summary: str
    fact_delta: list[dict[str, Any]]
    relationship_delta: list[dict[str, Any]]
    new_open_threads: list[str]
    closed_threads: list[str]


class ApprovedPlanLLMExecutor:
    """Generate episodes against the frozen outline, with real structured calls."""

    def __init__(self, provider, annotator: PerformanceAnnotator | None = None):
        self.provider = provider
        self.annotator = annotator or LLMPerformanceAnnotator(provider)
        self.lib = RuleLibrary.load()

    def execute(self, snapshot: ApprovedPlanSnapshot) -> StoryDelivery:
        preview = snapshot.preview
        trace_start = len(getattr(self.provider, "call_history", []))
        prefs = preview.resolved_preferences
        source = preview.source_reference
        original = source_delivery(source)
        intent = snapshot.request["intent"]
        inherited_episodes = list(original.episodes) if original and intent in ("continue", "revise") else []
        continuity = source.continuity_snapshot if source and intent == "continue" else {}
        tier_map = ModelTierMap(mapping=snapshot.model_mapping_snapshot) if snapshot.model_mapping_snapshot else None
        episodes: list[EpisodeDelivery] = []
        first_preview_blurb = ""
        for outline in preview.plan.episode_outlines:
            if intent == "revise":
                continuity = fold_continuity([ep for ep in original.episodes if ep.index < outline.index])
                # Replace prior affected episodes with the actual revised results.
                prefix = [next((new for new in episodes if new.index == old.index), old)
                          for old in original.episodes if old.index < outline.index]
                continuity = fold_continuity(prefix)
            prior_episode = next((ep for ep in inherited_episodes if ep.index == outline.index), None)
            impact = preview.impact_analysis
            if (intent == "revise" and impact["scope"] in ("line", "scene", "ending")
                    and outline.index not in impact["downstream_episode_indexes"]):
                episode = self._revise_local_episode(snapshot, prior_episode, outline, continuity, tier_map)
                episodes.append(episode)
                continuity = fold_continuity([episode], continuity)
                continue
            duration_bounds = getattr(self.provider, 'production_duration_bounds', None)
            min_chars, ideal_chars, max_chars = spoken_character_budget(prefs.target_duration_sec, duration_bounds=duration_bounds)
            creative_packet = build_episode_creative_packet(
                snapshot, outline, min_chars, ideal_chars, max_chars, continuity_override=continuity,
            )
            system = EPISODE_SYSTEM_V2
            system += "\n逐项核对previous_generated_episodes和current_continuity：继承已经写出的角色、事实、关系、时间安排。不得混淆任务截止时刻与约定事件时刻。未获批或来源未明确的精确时刻和倒计时不要随意新增。"
            system += "\n连续性增量必须对应正文证据：正文已提出但尚未兑现的约定、承诺和伏笔应逐项写入new_open_threads，不能仅记录设备问题或主要冲突。只有本集正文实际完成或明确撤销的剧情线才可写入closed_threads。"
            strategy_hint_text = ""
            if creative_packet.strategy_hints:
                strategy_hint_text = (
                    "\n\n【故事套路策略 — 本集的创作机制约束】\n"
                    + "\n".join(f"- {h}" for h in creative_packet.strategy_hints)
                )
            system = system + strategy_hint_text if strategy_hint_text else system
            draft = None
            estimated = 0
            contract_errors: list[str] = []
            approved_role_ids = {item.role_id for item in preview.character_snapshots}
            approved_role_ids.update(role.role_id for role in preview.plan.fictional_voice_roles)
            # Plan-generated portrayals are also approved (when no pre-existing characters)
            approved_role_ids.update(p.role_id for p in preview.plan.character_portrayals)
            portrayal_map = {p.role_id: p.story_name for p in preview.plan.character_portrayals}
            user = json.dumps({
                "approved_plan_fingerprint": snapshot.plan_fingerprint,
                "story_plan": preview.plan.model_dump(mode="json"),
                "episode_outline": outline.model_dump(mode="json"),
                "beat_sheet": self.lib.beat_sheet_for(prefs.target_duration_sec),
                "preferences": prefs.model_dump(mode="json"),
                "characters": [c.model_dump(mode="json") for c in preview.character_snapshots],
                "source": source.model_dump(mode="json") if source else None,
                "operation_request": snapshot.request,
                "current_continuity": continuity,
                "previous_generated_episodes": [ep.model_dump(mode="json") for ep in episodes],
                "template_snapshot": preview.template_snapshot,
                "creative_packet": creative_packet.model_dump(mode="json"),
                "instruction": (
                    f"创作第{outline.index}集，目标时长{prefs.target_duration_sec}秒。"
                    f"对白与旁白的文字总量（含标点，按 Unicode 字符计）必须在{min_chars}至{max_chars}字，"
                    f"尽量接近{ideal_chars}字；音效、音乐、动作说明不计入这个字数。"
                    "每个 major_beat 都要在场景中可辨认地实现；请写足适配时长的对白与旁白。"
                    f"dialogue 的 speaker_role_id 只能使用以下获批角色ID之一：{sorted(approved_role_ids)}。"
                    "不能使用未列出的ID；narration 行无需填写 speaker_role_id。"
                ),
            }, ensure_ascii=False)
            with usage_scope(stage="story_generation", node="episode_writer", attempt_kind="first_draft"):
                draft = self.provider.complete_structured(
                    resolve_spec("storycraft_episode_writer", tier_map=tier_map), system, user,
                    bound_thread_schema(DraftEpisode, continuity),
                )
            draft = normalize_draft_stage_directions(draft)
            production_policy = getattr(self.provider, 'production_draft_policy', None)
            if production_policy is not None:
                draft = production_policy(
                    draft, creative_context=user, schema=bound_thread_schema(DraftEpisode, continuity),
                    writer_spec=resolve_spec('storycraft_episode_writer', tier_map=tier_map),
                    review_spec=resolve_spec('storycraft_operation_judge', tier_map=tier_map).model_copy(
                        update={'role': 'production_readiness_auditor'}),
                    approved_roles=approved_role_ids, target_seconds=prefs.target_duration_sec,
                )
            estimated, contract_errors = draft_episode_contract_errors(
                draft, prefs.target_duration_sec, approved_role_ids,
                duration_bounds=duration_bounds,
            )
            # One bounded scene repair is allowed for a deterministic length miss.
            # Cast/schema/fact failures are not hidden behind a full-episode rewrite.
            if contract_errors and all(error.startswith("duration estimate") for error in contract_errors):
                draft = self._repair_duration_scene(
                    draft=draft,
                    creative_packet=creative_packet,
                    target_duration_sec=prefs.target_duration_sec,
                    observed_sec=estimated,
                    min_chars=min_chars,
                    ideal_chars=ideal_chars,
                    max_chars=max_chars,
                    approved_role_ids=approved_role_ids,
                    tier_map=tier_map,
                )
                draft = normalize_draft_stage_directions(draft)
                estimated, contract_errors = draft_episode_contract_errors(
                    draft, prefs.target_duration_sec, approved_role_ids,
                    duration_bounds=duration_bounds,
                )
            if contract_errors:
                raise ValueError(
                    f"EPISODE_CONTRACT_FAILED: episode {outline.index}: "
                    + "; ".join(contract_errors)
                )
            if not episodes:
                first_preview_blurb = draft.preview_blurb.strip() or draft.synopsis.strip()
            scenes: list[SceneDelivery] = []
            for scene_index, scene in enumerate(draft.scenes, 1):
                lines = [{
                    "line_id": f"ep{outline.index}-s{scene_index}-l{line_index}",
                    "kind": line.kind,
                    "speaker": line.speaker_role_id,
                    "text": line.text,
                    "performance_hint": line.performance_hint,
                    "sfx_category": resolve_line_cast(line, preview)[1],
                    "performer_id": resolve_line_cast(line, preview)[0],
                } for line_index, line in enumerate(scene.lines, 1)]
                utterances = self.annotator.annotate(lines, {
                    "approved_outline": outline.model_dump(mode="json"),
                    "scene": scene.title,
                    "content_rating": prefs.content_rating,
                    "model_mapping_snapshot": snapshot.model_mapping_snapshot,
                })
                scenes.append(SceneDelivery(
                    scene_id=f"ep{outline.index}-scene{scene_index}",
                    scene_index=scene_index,
                    title=scene.title, location=scene.location,
                    dramatic_goal=scene.dramatic_goal, utterances=utterances,
                ))
            episodes.append(EpisodeDelivery(
                episode_id=prior_episode.episode_id if prior_episode else f"ep-{outline.index}-{uuid.uuid4().hex[:8]}",
                index=outline.index, title=draft.title, synopsis=draft.synopsis,
                duration_target_sec=outline.target_duration_sec,
                duration_estimate_sec=estimated, scenes=scenes,
                episode_summary=draft.episode_summary, ending_hook=draft.ending_hook,
                fact_delta=draft.fact_delta, relationship_delta=draft.relationship_delta,
                new_open_threads=draft.new_open_threads, closed_threads=draft.closed_threads,
                validation_status="PASSED", ready_for_playback=True,
            ))
            continuity = fold_continuity([episodes[-1]], continuity)
        generated_episode_ids = [ep.episode_id for ep in episodes]
        if intent == "continue":
            episodes = [*inherited_episodes, *episodes]
        elif intent == "revise":
            replacements = {ep.index: ep for ep in episodes}
            episodes = [replacements.get(ep.index, ep) for ep in inherited_episodes]
        same_story = source and snapshot.request["intent"] in ("continue", "revise")
        delivery = StoryDelivery(
            story_id=source.story_id if same_story else uuid.uuid4().hex,
            story_version_id=uuid.uuid4().hex,
            version_no=source.version_no + 1 if same_story else 1,
            parent_story_version_id=source.story_version_id if source else None,
            lineage_type=snapshot.request["intent"],
            series_id=source.series_id if source else None,
            approved_plan_id=snapshot.plan_id,
            approved_plan_revision=snapshot.plan_revision,
            title=preview.plan.title, summary=preview.plan.premise,
            preview_blurb=first_preview_blurb or (original.preview_blurb if original else preview.plan.premise),
            tags=[prefs.genre, prefs.tone_style, prefs.audience_band, prefs.content_rating],
            story_mode=prefs.story_mode,
            character_snapshot=preview.character_snapshots,
            character_portrayals=[p.model_dump(mode="json") for p in preview.plan.character_portrayals],
            fictional_voice_roles=[v.model_dump(mode="json") for v in preview.plan.fictional_voice_roles],
            outline=preview.plan, episodes=episodes,
            generated_episode_ids=generated_episode_ids,
            rule_versions=preview.rule_versions,
            continuity_state={**fold_continuity(episodes), "source_version_id": source.story_version_id if source else None},
            quality_report={"validation_status": "FAILED"},
        )
        if getattr(self.provider, 'production_review_policy', None) == 'listen_first':
            # Publish a technically usable draft without claiming semantic approval.
            report = validate_story_delivery(delivery)
            report = report.model_copy(update={
                "outline_alignment_passed": False,
                "continuity_passed": False,
                "content_rating_passed": False,
                "warnings": [*report.warnings, "LISTEN_FIRST_SEMANTIC_REVIEW_SKIPPED: 待试听，未执行内容质量审核"],
            })
            return delivery.model_copy(update={
                "quality_report": report,
                "model_trace": [*preview.model_trace, *getattr(self.provider, "call_history", [])[trace_start:]],
            })
        judgement = self.provider.complete_structured(
            resolve_spec("storycraft_operation_judge", tier_map=tier_map, temperature=0),
            JUDGE_SYSTEM_V2 + OPERATION_JUDGE_INSTRUCTIONS,
            json.dumps({
                "approved_plan": preview.plan.model_dump(mode="json"),
                "operation_request": snapshot.request,
                "source": source.model_dump(mode="json") if source else None,
                "approved_impact": preview.impact_analysis,
                "required_checks": operation_requirements(snapshot.request),
                "required_clock_mentions": required_clock_mentions(episodes),
                "clock_coverage_instruction": "timeline_claims必须逐项覆盖required_clock_mentions的每个行号和每个时刻（同一时刻重复出现也须逐行列出）。清单只是字面提取，不是时间一致性结论；请根据正文判定event_id及kind，计划、实际、期限分开；无依据不得假设改期。",
                "generated_episode_ids": generated_episode_ids,
                "audience_band": prefs.audience_band,
                "content_rating": prefs.content_rating,
                "episodes": [episode.model_dump(mode="json") for episode in episodes],
            }, ensure_ascii=False),
            bound_quality_schema(operation_requirements(snapshot.request),
                [u.line_id for ep in episodes for scene in ep.scenes for u in scene.utterances]),
        )
        checks = canonical_check_evidence(judgement.operation_checks, episodes)
        kind_errors = evidence_kind_errors(snapshot.request, checks, episodes)
        if kind_errors:
            judgement = judgement.model_copy(update={"continuity_passed": False,
                "reasons": [*judgement.reasons, *kind_errors]})
        time_errors = timeline_errors(judgement.timeline_claims, episodes)
        if time_errors:
            for check in checks:
                if check["requirement_id"] == "timeline":
                    check.update(model_passed=check["passed"], passed=False, deterministic_errors=time_errors)
            judgement = judgement.model_copy(update={"continuity_passed": False,
                "reasons": [*judgement.reasons, *time_errors]})
        if checks or intent != "create":
            text_evidence = "\n".join(u.text for ep in episodes for s in ep.scenes for u in s.utterances)
            changed_text = "\n".join(u.text for ep in episodes if ep.episode_id in generated_episode_ids
                                      for s in ep.scenes for u in s.utterances)
            expected_checks = operation_requirements(snapshot.request)
            evidence_valid = ({c.get("requirement_id") for c in checks} == set(expected_checks)
                              and len(checks) == len(expected_checks)) and all(
                c.get("passed") is True and c.get("requirement") and c.get("evidence_quotes")
                and all(isinstance(q, str) and q.strip() and q in text_evidence for q in c["evidence_quotes"])
                and (not (c.get("requirement_id") in ("instruction", "edit_instruction", "remix_instruction")
                          or str(c.get("requirement_id", "")).startswith("transform:"))
                     or any(q in changed_text for q in c["evidence_quotes"]))
                for c in checks
            )
            if not evidence_valid:
                judgement = judgement.model_copy(update={"continuity_passed": False,
                    "reasons": [*judgement.reasons, "OPERATION_REQUIREMENTS_NOT_PROVEN"]})
        report = validate_story_delivery(delivery).model_copy(update={
            "outline_alignment_passed": judgement.outline_alignment_passed,
            "continuity_passed": judgement.continuity_passed,
            "content_rating_passed": judgement.content_rating_passed,
            "warnings": judgement.reasons,
            "operation_checks": checks,
            "timeline_claims": [c.model_dump(mode="json") for c in judgement.timeline_claims],
        })
        if not judgement.content_rating_passed:
            report = report.model_copy(update={
                "validation_status": "FAILED",
                "warnings": [*report.warnings, "content rating validation failed"],
            })
        if not judgement.outline_alignment_passed or not judgement.continuity_passed:
            report = report.model_copy(update={"validation_status": "FAILED"})
        return delivery.model_copy(update={
            "quality_report": report,
            "model_trace": [
                *preview.model_trace,
                *getattr(self.provider, "call_history", [])[trace_start:],
            ],
        })

    def _revise_local_episode(self, snapshot, episode, outline, continuity, tier_map):
        impact = snapshot.preview.impact_analysis
        scene_ids = impact["target_scene_ids"]
        with usage_scope(stage="story_generation", node="episode_writer", attempt_kind="local_revision"):
            patch = self.provider.complete_structured(
                resolve_spec("storycraft_episode_writer", tier_map=tier_map),
                "你是局部故事修订器。只返回获准目标的替换，禁止修改其他内容。"
                "保留所有已有line_id；新台词使用不重复的新ID。line范围仅返回指定一行，保留kind和speaker_role_id。"
                "scene/ending范围返回目标场景完整台词。episode_summary与事实关系伏笔增量须重新概括整集，"
                "不得保留已被修改内容产生的过时增量。closed_threads必须逐字引用来源线程。",
                json.dumps({"operation_request": snapshot.request, "approved_outline": outline.model_dump(mode="json"),
                    "approved_scope": impact, "source_episode": episode.model_dump(mode="json"),
                    "current_continuity": continuity,
                    "approved_cast": snapshot.preview.plan.model_dump(mode="json")}, ensure_ascii=False),
                bound_thread_schema(EpisodeRevision, continuity),
            )
        if len(patch.scenes) != len(scene_ids) or {s.scene_id for s in patch.scenes} != set(scene_ids):
            raise ValueError("REVISION_SCOPE_VIOLATION: scenes")
        scenes = []
        for scene in episode.scenes:
            revised = next((s for s in patch.scenes if s.scene_id == scene.scene_id), None)
            if revised is None:
                scenes.append(scene)
                continue
            if impact["scope"] == "line" and [u.line_id for u in revised.lines] != impact["target_line_ids"]:
                raise ValueError("REVISION_SCOPE_VIOLATION: lines")
            old_lines = {u.line_id: u for u in scene.utterances}
            replacements = {}
            pending_annotations = []
            for line in revised.lines:
                old = old_lines.get(line.line_id)
                if impact["scope"] == "line" and (line.kind != old.kind or line.speaker_role_id != old.speaker_role_id):
                    raise ValueError("REVISION_SCOPE_VIOLATION: line identity")
                if old and (line.kind, line.speaker_role_id, line.text) == (old.kind, old.speaker_role_id, old.text):
                    replacements[line.line_id] = old
                    continue
                performer, cue = resolve_line_cast(line, snapshot.preview)
                pending_annotations.append({
                    "line_id": line.line_id, "kind": line.kind, "speaker": line.speaker_role_id,
                    "text": line.text, "performance_hint": line.performance_hint,
                    "performer_id": performer, "sfx_category": cue,
                })
            if pending_annotations:
                annotated = self.annotator.annotate(pending_annotations, {
                    "approved_outline": outline.model_dump(mode="json"), "scene": scene.title,
                    "model_mapping_snapshot": snapshot.model_mapping_snapshot})
                replacements.update({u.line_id: u for u in annotated})
            utterances = ([replacements.get(u.line_id, u) for u in scene.utterances] if impact["scope"] == "line"
                          else [replacements[u.line_id] for u in revised.lines])
            scenes.append(scene.model_copy(update={"utterances": utterances}))
        estimated = round(sum(len(u.text) for s in scenes for u in s.utterances if u.kind in ("dialogue", "narration")) / 3.5)
        revision_bounds = getattr(self.provider, 'production_duration_bounds', None)
        if revision_bounds is not None:
            revision_min, _, revision_max = spoken_character_budget(episode.duration_target_sec, duration_bounds=revision_bounds)
            revision_count = sum(len(u.text) for s in scenes for u in s.utterances if u.kind in ('dialogue', 'narration'))
            duration_ok = revision_min <= revision_count <= revision_max
        else:
            duration_ok = 0.85 * episode.duration_target_sec <= estimated <= 1.15 * episode.duration_target_sec
        if not duration_ok:
            raise ValueError("REVISION_DURATION_OUT_OF_RANGE")
        return episode.model_copy(update={"scenes": scenes, "duration_estimate_sec": estimated,
            "episode_summary": patch.episode_summary, "fact_delta": patch.fact_delta,
            "relationship_delta": patch.relationship_delta, "new_open_threads": patch.new_open_threads,
            "closed_threads": patch.closed_threads})

    def _repair_duration_scene(
        self, *, draft: DraftEpisode, creative_packet, target_duration_sec: int,
        observed_sec: int, min_chars: int, ideal_chars: int, max_chars: int,
        approved_role_ids: set[str], tier_map: ModelTierMap | None,
    ) -> DraftEpisode:
        """Replace only one scene; never rewrite the already-valid whole episode."""
        scene_counts = [
            sum(len(line.text) for line in scene.lines if line.kind in ("dialogue", "narration"))
            for scene in draft.scenes
        ]
        scene_index = max(range(len(scene_counts)), key=scene_counts.__getitem__)
        current_total = sum(scene_counts)
        desired_scene_chars = max(40, scene_counts[scene_index] + ideal_chars - current_total)
        card = next(
            (item for item in creative_packet.scene_cards if item.scene_index == scene_index + 1),
            None,
        )
        repair_user = json.dumps({
            "repair_type": "duration_only_single_scene",
            "target_episode_duration_sec": target_duration_sec,
            "observed_episode_duration_sec": observed_sec,
            "episode_spoken_character_window": {"min": min_chars, "ideal": ideal_chars, "max": max_chars},
            "replace_scene_index": scene_index + 1,
            "replacement_scene_spoken_character_target": desired_scene_chars,
            "scene_card": card.model_dump(mode="json") if card else None,
            "approved_role_ids": sorted(role for role in approved_role_ids if role),
            "original_scene": draft.scenes[scene_index].model_dump(mode="json"),
            "instruction": (
                "只返回这一场的替换 lines，不改其他场、标题、简介、事实增量或结局。"
                "保留本场 required_beats、因果依据与人物选择；压缩时删重复解释，"
                "扩写时增加验证信息或有代价的行动，不得增加新人物或新反转。"
            ),
        }, ensure_ascii=False)
        with usage_scope(stage="story_generation", node="episode_writer", attempt_kind="targeted_duration_repair"):
            result = self.provider.complete_structured(
                resolve_spec("storycraft_episode_writer", tier_map=tier_map, temperature=0.45),
                EPISODE_SYSTEM_V2,
                repair_user,
                DraftScenePatchBatch,
            )
        patch = result.patches[0]
        if patch.scene_index != scene_index + 1:
            raise ValueError("EPISODE_DURATION_REPAIR_WRONG_SCENE")
        replacement = draft.scenes[scene_index].model_copy(update={"lines": patch.lines})
        scenes = list(draft.scenes)
        scenes[scene_index] = replacement
        return draft.model_copy(update={"scenes": scenes})


class DeterministicPerformanceAnnotator:
    """Deterministic test adapter. Production acceptance must use the LLM adapter."""

    def annotate(self, lines, context) -> list[Utterance]:
        output: list[Utterance] = []
        for raw in lines:
            kind = raw.get("kind", "action")
            if kind == "silence":
                kind = "action"
            text = raw.get("text", "")
            spoken = kind in ("dialogue", "narration")
            anchor = text[: min(6, len(text))] if text else ""
            utterance = Utterance(
                line_id=raw["line_id"], kind=kind,
                speaker_role_id=raw.get("speaker") if kind == "dialogue" else None,
                narrator_id="narrator" if kind == "narration" else None,
                performer_id=raw.get("performer_id") or (raw.get("speaker") if kind == "dialogue" else "narrator"),
                text=text,
                emotion=Emotion.NEUTRAL if spoken else None,
                tone_instruction="自然清晰，突出关键信息" if spoken else None,
                emphasis=[EmphasisSpan(span_text=anchor, strength="medium")] if spoken and anchor else [],
                delivery_note=raw.get("performance_hint"),
                audio_cue_ref=raw.get("sfx_category"),
            )
            output.append(normalize_utterance(utterance))
        return output


class GraphStoryExecutor:
    """Approved-plan adapter around the existing synchronous graph."""

    def __init__(self, workspace: str, lib: Any, runtime: Any, annotator: PerformanceAnnotator):
        self.workspace = workspace
        self.lib = lib
        self.runtime = runtime
        self.annotator = annotator

    def execute(self, snapshot: ApprovedPlanSnapshot) -> StoryDelivery:
        from ..graph import run_pipeline

        request = snapshot.request
        if request["intent"] != "create":
            raise ValueError("GRAPH_EXECUTOR_DOES_NOT_SUPPORT_STORY_OPERATIONS")
        preview = snapshot.preview
        prefs = preview.resolved_preferences
        characters = [
            {
                "character_id": item.character_id,
                "role_id": item.role_id,
                "canon": item.canon,
                "runtime": item.profile,
            }
            for item in preview.character_snapshots
        ] or None
        result = run_pipeline(
            idea=request["user_instruction"],
            workspace=self.workspace,
            lib=self.lib,
            runtime=self.runtime,
            target_episodes=prefs.target_episodes,
            target_duration_sec=prefs.target_duration_sec,
            story_mode=prefs.story_mode,
            content_form="audio_drama",
            characters=characters,
            thread_id=request["request_id"],
        )
        episodes = self._to_episode_deliveries(result, snapshot)
        source = preview.source_reference
        story_id = source.story_id if source and request["intent"] in ("continue", "revise") else uuid.uuid4().hex
        version_no = (source.version_no + 1) if source and story_id == source.story_id else 1
        delivery = StoryDelivery(
            story_id=story_id,
            story_version_id=uuid.uuid4().hex,
            version_no=version_no,
            parent_story_version_id=source.story_version_id if source else None,
            lineage_type=request["intent"],
            series_id=source.series_id if source else request.get("series_id"),
            approved_plan_id=snapshot.plan_id,
            approved_plan_revision=snapshot.plan_revision,
            title=preview.plan.title,
            summary=preview.plan.premise,
            preview_blurb=preview.plan.premise,
            tags=[prefs.genre, prefs.tone_style, prefs.audience_band, prefs.content_rating],
            story_mode=prefs.story_mode,
            character_snapshot=preview.character_snapshots,
            outline=preview.plan,
            episodes=episodes,
            generated_episode_ids=[ep.episode_id for ep in episodes],
            continuity_state={
                "source": source.model_dump(mode="json") if source else None,
                "unresolved_threads": [thread for ep in episodes for thread in ep.new_open_threads],
            },
            quality_report={"validation_status": "FAILED"},
        )
        report = validate_story_delivery(delivery)
        ready = report.validation_status == "PASSED" and all(ep.validation_status == "PASSED" for ep in episodes)
        return delivery.model_copy(update={
            "quality_report": report,
            "ready_for_playback": ready,
            "status": StoryVersionStatus.READY if ready else StoryVersionStatus.CANDIDATE,
        })

    def _to_episode_deliveries(self, result: dict[str, Any], snapshot: ApprovedPlanSnapshot) -> list[EpisodeDelivery]:
        raw_results = result.get("episode_results") or []
        output: list[EpisodeDelivery] = []
        for index, raw_result in enumerate(raw_results, start=1):
            if hasattr(raw_result, "model_dump"):
                raw_result = raw_result.model_dump(mode="json")
            audio = raw_result.get("audio_episode") or {}
            scenes: list[SceneDelivery] = []
            for scene_index, beat in enumerate(audio.get("beats") or [], start=1):
                raw_lines = []
                for line in beat.get("lines") or []:
                    raw_lines.append({
                        "line_id": line.get("id") or uuid.uuid4().hex,
                        "kind": line.get("kind", "action"),
                        "speaker": line.get("speaker"),
                        "text": line.get("text", ""),
                        "sfx_category": line.get("sfx_category"),
                    })
                utterances = self.annotator.annotate(raw_lines, {
                    "story_title": snapshot.preview.plan.title,
                    "episode": index,
                    "scene": beat.get("label", ""),
                    "audience_band": snapshot.preview.resolved_preferences.audience_band,
                    "content_rating": snapshot.preview.resolved_preferences.content_rating,
                })
                scenes.append(SceneDelivery(
                    scene_id=f"scene_{index}_{scene_index}",
                    scene_index=scene_index,
                    title=beat.get("label") or f"场景{scene_index}",
                    dramatic_goal=beat.get("event") or "",
                    utterances=utterances,
                ))
            duration_estimate = int(max(
                (line.get("end_sec", 0) for beat in audio.get("beats") or [] for line in beat.get("lines") or []),
                default=0,
            ))
            validation_passed = bool(raw_result.get("validation_passed"))
            guard_passed = bool(raw_result.get("output_guard_passed"))
            output.append(EpisodeDelivery(
                episode_id=f"ep_{index}_{uuid.uuid4().hex[:8]}",
                index=index,
                title=audio.get("title") or snapshot.preview.plan.episode_outlines[index - 1].title,
                synopsis=snapshot.preview.plan.episode_outlines[index - 1].core_goal,
                duration_target_sec=snapshot.preview.resolved_preferences.target_duration_sec,
                duration_estimate_sec=duration_estimate,
                scenes=scenes,
                episode_summary=snapshot.preview.plan.episode_outlines[index - 1].ending,
                new_open_threads=audio.get("threads") or [],
                validation_status="PASSED" if validation_passed and guard_passed and scenes else "FAILED",
                ready_for_playback=validation_passed and guard_passed and bool(scenes),
            ))
        return output
