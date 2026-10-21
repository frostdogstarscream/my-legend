"""Public schemas for the approved-plan story workflow."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class StoryIntent(str, Enum):
    CREATE = "create"
    CONTINUE = "continue"
    REVISE = "revise"
    REMIX = "remix"


class AudienceBand(str, Enum):
    TEEN_14_17 = "14-17"
    ADULT_18_35 = "18-35"
    ADULT_36_55 = "36-55"


class ContentRating(str, Enum):
    TEEN = "teen"
    MATURE_NON_EXPLICIT = "mature_non_explicit"


class StoryModeChoice(str, Enum):
    AUTO = "auto"
    GENERAL = "general"
    MYSTERY = "mystery"
    SERIALIZED = "serialized"
    VIRAL_DRAMA = "viral_drama"


class PlanStatus(str, Enum):
    PREPARING = "PREPARING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVISING = "REVISING"
    APPROVED = "APPROVED"
    STALE = "STALE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class WorkflowJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class StoryVersionStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    READY = "READY"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class SourceSelector(WorkflowModel):
    type: Literal["last_generated", "last_played", "series_latest", "by_id"]
    series_id: str | None = None
    story_id: str | None = None
    story_version_id: str | None = None
    episode_id: str | None = None
    playback_position_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_selector(self):
        if self.type == "series_latest" and not self.series_id:
            raise ValueError("series_latest requires series_id")
        if self.type == "by_id" and not self.story_version_id:
            raise ValueError("by_id requires story_version_id")
        return self


class StoryTemplateRef(WorkflowModel):
    template_id: str
    template_revision: int = Field(ge=1)


class SafetyProfile(WorkflowModel):
    content_rating: ContentRating = ContentRating.TEEN
    blocked_topics: list[str] = Field(default_factory=list)


class CreationPreferences(WorkflowModel):
    story_mode: StoryModeChoice = StoryModeChoice.AUTO
    content_form: Literal["audio_drama"] = "audio_drama"
    genre: str = "auto"
    recipe_id: str | None = None
    tone_style: str = "warm"
    audience_band: AudienceBand = AudienceBand.ADULT_18_35
    content_rating: ContentRating = ContentRating.TEEN
    target_duration_sec: Literal[90, 120, 150, 180, 300] = 180
    target_episodes: int = Field(default=1, ge=1, le=100)
    language: str = "zh-CN"
    narration_pov: Literal["auto", "first", "third"] = "auto"
    dialogue_density: Literal["low", "medium", "high"] = "medium"
    pacing: Literal["slow", "normal", "fast"] = "normal"
    ending_preference: Literal["auto", "happy", "closed", "open", "cliffhanger"] = "auto"
    theme_keywords: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    additional_instructions: str = ""

    @model_validator(mode="after")
    def validate_policy(self):
        if (
            self.audience_band == AudienceBand.TEEN_14_17
            and self.content_rating == ContentRating.MATURE_NON_EXPLICIT
        ):
            raise ValueError("14-17 audience only supports teen content")
        include = {item.strip().casefold() for item in self.must_include if item.strip()}
        avoid = {item.strip().casefold() for item in self.must_avoid if item.strip()}
        conflicts = sorted(include & avoid)
        if conflicts:
            raise ValueError(f"must_include conflicts with must_avoid: {conflicts}")
        return self


class CharacterSelection(WorkflowModel):
    selected_character_ids: list[str] = Field(default_factory=list)
    character_profile_revisions: dict[str, int] = Field(default_factory=dict)
    temporary_profile_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    new_fictional_roles: list[dict[str, Any]] = Field(default_factory=list)
    role_bindings: list[dict[str, Any]] = Field(default_factory=list)


class EditPolicy(WorkflowModel):
    timing: Literal["pre_playback", "historical"] = "historical"
    scope: Literal["line", "scene", "episode", "arc", "ending", "whole_story"]
    target: dict[str, Any] = Field(default_factory=dict)
    instruction: str = Field(min_length=1)
    preserve: dict[str, bool] = Field(default_factory=dict)
    downstream_policy: Literal["replan_affected", "branch_from_here"] = "replan_affected"


class RemixPolicy(WorkflowModel):
    instruction: str = Field(min_length=1)
    retain_elements: list[str] = Field(min_length=1)
    transform_elements: list[str] = Field(min_length=1)
    relation_to_source: Literal["alternate_version", "adaptation", "parallel_world", "new_story"] = "alternate_version"


class BaseStoryRequest(WorkflowModel):
    request_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    session_id: str | None = None
    user_instruction: str = Field(min_length=1)
    creation_preferences: CreationPreferences = Field(default_factory=CreationPreferences)
    characters: CharacterSelection = Field(default_factory=CharacterSelection)
    template_ref: StoryTemplateRef | None = None
    safety_profile: SafetyProfile | None = None
    client_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_safety_profile(self):
        if self.safety_profile and self.safety_profile.content_rating != self.creation_preferences.content_rating:
            raise ValueError("safety_profile rating must match creation_preferences rating")
        return self


class CreateStoryRequest(BaseStoryRequest):
    intent: Literal[StoryIntent.CREATE] = StoryIntent.CREATE
    source_selector: None = None


class ContinueStoryRequest(BaseStoryRequest):
    intent: Literal[StoryIntent.CONTINUE] = StoryIntent.CONTINUE
    source_selector: SourceSelector
    continuation_kind: Literal["next_episode", "resume_playback"] = "next_episode"


class ReviseStoryRequest(BaseStoryRequest):
    intent: Literal[StoryIntent.REVISE] = StoryIntent.REVISE
    source_selector: SourceSelector
    edit: EditPolicy


class RemixStoryRequest(BaseStoryRequest):
    intent: Literal[StoryIntent.REMIX] = StoryIntent.REMIX
    source_selector: SourceSelector
    remix: RemixPolicy


StoryOperationRequest = Annotated[
    Union[CreateStoryRequest, ContinueStoryRequest, ReviseStoryRequest, RemixStoryRequest],
    Field(discriminator="intent"),
]


class OperationContext(WorkflowModel):
    principal_id: str = "local"
    character_snapshots: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ResolvedSourceReference(WorkflowModel):
    story_id: str
    story_version_id: str
    version_no: int = 1
    episode_id: str | None = None
    series_id: str | None = None
    resolved_reason: str
    last_played_position_ms: int | None = None
    story_summary: str = ""
    continuity_snapshot: dict[str, Any] = Field(default_factory=dict)
    source_content_snapshot: dict[str, Any] = Field(default_factory=dict)


class EpisodeOutline(WorkflowModel):
    index: int = Field(ge=1)
    title: str
    core_goal: str
    major_beats: list[str] = Field(min_length=1)
    ending: str
    target_duration_sec: int


class CharacterSnapshot(WorkflowModel):
    character_id: str
    canon_revision: int = 1
    profile_revision: int = 1
    canon: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    role_id: str | None = None
    performer_id: str | None = None


class CharacterPortrayal(WorkflowModel):
    """An approved, story-local portrayal; it never edits a toy's Canon."""

    role_id: str = Field(min_length=1)
    story_name: str = Field(min_length=1)
    external_goal: str = Field(min_length=1)
    emotional_avoidance: str = Field(min_length=1)
    speech_style: str = Field(min_length=1)
    relationship_stance: str = Field(min_length=1)


class FictionalVoiceRole(WorkflowModel):
    """A non-toy voice proposed in the Plan, then frozen at approval."""

    role_id: str = Field(min_length=1)
    story_name: str = Field(min_length=1)
    performer_id: str = Field(min_length=1)
    medium: Literal["live", "recording", "telephone", "broadcast"]
    dramatic_purpose: str = Field(min_length=1)


class MysteryCausalProof(WorkflowModel):
    """Approved explanation of an apparent impossibility, not a literary score."""

    actual_event_timeline: str = Field(min_length=8)
    witnessed_event: str = Field(min_length=8)
    actor_opportunity: str = Field(min_length=8)
    audible_trace_before_reveal: str = Field(min_length=8)
    onstage_verification: str = Field(min_length=8)


class PlanContent(WorkflowModel):
    title: str
    premise: str
    theme: str
    beginning: str
    development: str
    climax: str
    ending: str
    episode_outlines: list[EpisodeOutline] = Field(min_length=1)
    character_portrayals: list[CharacterPortrayal] = Field(default_factory=list)
    fictional_voice_roles: list[FictionalVoiceRole] = Field(default_factory=list)
    mystery_causal_proof: MysteryCausalProof | None = None
    performance_plan: dict[str, Any] = Field(default_factory=dict)
    continuity_constraints: list[str] = Field(default_factory=list)
    change_summary: list[str] = Field(default_factory=list)


class PlanPreview(WorkflowModel):
    plan_id: str
    plan_revision: int = Field(ge=1)
    status: PlanStatus
    intent: StoryIntent
    requires_confirmation: bool = True
    source_reference: ResolvedSourceReference | None = None
    template_snapshot: dict[str, Any] | None = None
    strategy_snapshot: dict[str, Any] | None = None
    plan: PlanContent
    resolved_preferences: CreationPreferences
    character_snapshots: list[CharacterSnapshot] = Field(default_factory=list)
    character_profile_revision_map: dict[str, int] = Field(default_factory=dict)
    auto_filled_fields: list[str] = Field(default_factory=list)
    inherited_fields: list[str] = Field(default_factory=list)
    impact_analysis: dict[str, Any] = Field(default_factory=dict)
    model_trace: list[dict[str, Any]] = Field(default_factory=list)
    rule_versions: dict[str, str] = Field(default_factory=dict)
    explicit_fields: list[str] = Field(default_factory=list)
    plan_fingerprint: str
    allowed_actions: list[str] = Field(default_factory=lambda: ["revise_plan", "approve_plan", "cancel_plan"])
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ApprovedPlanSnapshot(WorkflowModel):
    plan_id: str
    plan_revision: int
    plan_fingerprint: str
    request: dict[str, Any]
    preview: PlanPreview
    model_mapping_snapshot: dict[str, str] = Field(default_factory=dict)
    approved_at: datetime = Field(default_factory=utcnow)


class GenerationJob(WorkflowModel):
    job_id: str
    request_id: str
    idempotency_key: str
    approved_plan_id: str
    approved_plan_revision: int
    status: WorkflowJobStatus = WorkflowJobStatus.QUEUED
    stage: str = "queued"
    progress_pct: int = Field(default=0, ge=0, le=100)
    result_story_version_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    execution_token: str | None = None
    input_snapshot: ApprovedPlanSnapshot
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Emotion(str, Enum):
    NEUTRAL = "neutral"
    CALM = "calm"
    HAPPY = "happy"
    EXCITED = "excited"
    SAD = "sad"
    WORRIED = "worried"
    ANGRY = "angry"
    SURPRISED = "surprised"
    FEARFUL = "fearful"
    REASSURING = "reassuring"
    SERIOUS = "serious"
    PLAYFUL = "playful"
    MYSTERIOUS = "mysterious"
    TENDER = "tender"


class EmphasisSpan(WorkflowModel):
    span_text: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=1)
    strength: Literal["medium", "strong"] = "medium"


class PauseCue(WorkflowModel):
    after_char: int = Field(ge=0)
    duration_ms: int = Field(ge=0, le=5000)


class Utterance(WorkflowModel):
    line_id: str
    kind: Literal["dialogue", "narration", "sfx", "music", "action"]
    speaker_role_id: str | None = None
    narrator_id: str | None = None
    performer_id: str | None = None
    text: str = ""
    emotion: Emotion | None = None
    tone_instruction: str | None = None
    emphasis: list[EmphasisSpan] = Field(default_factory=list)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: str | None = None
    volume: str | None = None
    pauses: list[PauseCue] = Field(default_factory=list)
    delivery_note: str | None = None
    audio_cue_ref: str | None = None


class SceneDelivery(WorkflowModel):
    scene_id: str
    scene_index: int = Field(default=1, ge=1, description="1-based scene index within the episode")
    title: str
    location: str = ""
    dramatic_goal: str = ""
    utterances: list[Utterance] = Field(default_factory=list)


class EpisodeDelivery(WorkflowModel):
    episode_id: str
    index: int
    title: str
    synopsis: str = ""
    duration_target_sec: int
    duration_estimate_sec: int = 0
    scenes: list[SceneDelivery] = Field(default_factory=list)
    episode_summary: str = ""
    ending_hook: str = ""
    fact_delta: list[dict[str, Any]] = Field(default_factory=list)
    relationship_delta: list[dict[str, Any]] = Field(default_factory=list)
    new_open_threads: list[str] = Field(default_factory=list)
    closed_threads: list[str] = Field(default_factory=list)
    validation_status: Literal["PENDING", "PASSED", "FAILED"] = "PENDING"
    ready_for_playback: bool = False


class OperationCheck(WorkflowModel):
    requirement_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    passed: bool = Field(strict=True)
    evidence_quotes: list[str] = Field(default_factory=list)
    evidence_line_ids: list[str] = Field(default_factory=list)


class TimelineClaim(WorkflowModel):
    event_id: str = Field(min_length=1, description="Same concrete occurrence uses one ID; programme opening is its start unless explicit warm-up/trial")
    kind: Literal["scheduled", "actual", "deadline", "clock", "explicit_change"]
    normalized_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    line_id: str
    quote: str = Field(min_length=1)


class PerformanceValidationReport(WorkflowModel):
    validation_status: Literal["PASSED", "FAILED"]
    spoken_line_count: int = 0
    emotion_coverage: float = 0.0
    tone_coverage: float = 0.0
    emphasis_coverage: float = 0.0
    emphasis_span_validity: float = 0.0
    continuity_passed: bool = True
    outline_alignment_passed: bool = True
    content_rating_passed: bool = True
    warnings: list[str] = Field(default_factory=list)
    operation_checks: list[dict[str, Any]] = Field(default_factory=list)
    timeline_claims: list[dict[str, Any]] = Field(default_factory=list)


class StoryDelivery(WorkflowModel):
    story_id: str
    story_version_id: str
    version_no: int = 1
    parent_story_version_id: str | None = None
    lineage_type: Literal["create", "continue", "revise", "remix"] = "create"
    series_id: str | None = None
    approved_plan_id: str
    approved_plan_revision: int
    title: str
    summary: str
    preview_blurb: str = ""
    tags: list[str] = Field(default_factory=list)
    story_mode: str
    content_form: Literal["audio_drama"] = "audio_drama"
    character_snapshot: list[CharacterSnapshot] = Field(default_factory=list)
    character_portrayals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Plan-generated character portrayals: role_id→story_name, goal, avoidance, speech_style, relationship_stance",
    )
    fictional_voice_roles: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Non-toy voice roles from Plan: recording, broadcast, telephone voices with role_id, story_name, performer_id, medium, dramatic_purpose",
    )
    outline: PlanContent
    episodes: list[EpisodeDelivery] = Field(default_factory=list)
    generated_episode_ids: list[str] = Field(default_factory=list)
    continuity_state: dict[str, Any] = Field(default_factory=dict)
    quality_report: PerformanceValidationReport
    ready_for_playback: bool = False
    status: StoryVersionStatus = StoryVersionStatus.CANDIDATE
    engine_version: str = "0.3.0"
    delivery_format_version: str = Field(
        default="1.0",
        description="Format version for downstream compatibility; increment when field semantics change",
    )
    rule_versions: dict[str, Any] = Field(default_factory=dict)
    model_trace: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


def plan_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
