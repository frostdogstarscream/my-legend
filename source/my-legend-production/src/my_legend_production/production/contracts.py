"""Public production contracts. Automatic execution never implies human approval."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreativeSource(Contract):
    kind: Literal["creative"] = "creative"
    title: str = Field(min_length=1, max_length=80)
    idea: str = Field(min_length=1, max_length=8000)
    genre: str = Field(min_length=1, max_length=80)
    character_ids: list[str] = Field(min_length=1, max_length=6)
    target_duration_seconds: Literal[90, 120, 150, 180, 300] = 90


class ArtifactRef(Contract):
    kind: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_version_id: str = Field(min_length=1, max_length=128)
    media_type: str = Field(default="application/json", max_length=100)
    size_bytes: int = Field(ge=1)


class DeliverySource(Contract):
    kind: Literal["delivery"]
    source_version_id: str = Field(min_length=1, max_length=128)
    delivery: ArtifactRef
    manifest: ArtifactRef

    @model_validator(mode="after")
    def same_source(self):
        if self.delivery.kind != "StoryDelivery" or self.manifest.kind != "RenderManifest":
            raise ValueError("SOURCE_ARTIFACT_KIND_MISMATCH")
        if {self.delivery.source_version_id, self.manifest.source_version_id} != {self.source_version_id}:
            raise ValueError("SOURCE_VERSION_MISMATCH")
        return self


class StoryEdit(Contract):
    scope: Literal["line", "scene", "episode", "arc", "ending", "whole_story"]
    target: dict = Field(default_factory=dict)
    preserve: dict[str, bool] = Field(default_factory=dict)
    downstream_policy: Literal["replan_affected", "branch_from_here"] = "replan_affected"


class StoryRemix(Contract):
    retain_elements: list[str] = Field(min_length=1, max_length=30)
    transform_elements: list[str] = Field(min_length=1, max_length=30)
    relation_to_source: Literal["alternate_version", "adaptation", "parallel_world", "new_story"] = "adaptation"


class StoryOperationSource(DeliverySource):
    """Owned immutable source, processed into a new native story version."""
    kind: Literal["operation"] = "operation"
    intent: Literal["revise", "continue", "remix"]
    instruction: str = Field(min_length=1, max_length=8000)
    target_duration_seconds: Literal[90, 120, 150, 180, 300] = 90
    edit: StoryEdit | None = None
    remix: StoryRemix | None = None

    @model_validator(mode="after")
    def operation_fields(self):
        if (self.intent == "revise") != (self.edit is not None):
            raise ValueError("STORY_EDIT_REQUIRED_ONLY_FOR_REVISE")
        if (self.intent == "remix") != (self.remix is not None):
            raise ValueError("STORY_REMIX_REQUIRED_ONLY_FOR_REMIX")
        if not self.instruction.strip():
            raise ValueError("STORY_INSTRUCTION_REQUIRED")
        return self


class Budget(Contract):
    max_provider_calls: int = Field(default=80, ge=1, le=500)
    max_elapsed_seconds: int = Field(default=1800, ge=10, le=14400)
    max_repairs_per_stage: int = Field(default=1, ge=0, le=2)


class VoicePolicy(Contract):
    mode: Literal["configured", "catalog_policy"] = "catalog_policy"
    bindings: dict[str, str] = Field(default_factory=dict)
    policy_version: Literal["stable-role/1"] = "stable-role/1"

    @model_validator(mode="after")
    def explicit_bindings(self):
        if self.mode == "configured" and not self.bindings:
            raise ValueError("VOICE_BINDINGS_REQUIRED")
        if self.mode == "catalog_policy" and self.bindings:
            raise ValueError("SELECT_CONFIGURED_VOICE_POLICY")
        return self


class AudioEventRef(Contract):
    episode_id: str = Field(min_length=1, max_length=128)
    scene_id: str = Field(min_length=1, max_length=128)
    line_id: str = Field(min_length=1, max_length=128)


class AudioRemakeSelection(AudioEventRef):
    operation_id: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')


def distinct_audio_selections(events):
    selected = {}
    for e in events:
        key = (e.episode_id, e.scene_id, e.line_id)
        previous = selected.setdefault(key, set())
        if e.operation_id in previous or (previous and (None in previous or e.operation_id is None)):
            raise ValueError('DUPLICATE_AUDIO_EVENT')
        previous.add(e.operation_id)


class AudioRevision(Contract):
    cache: ArtifactRef
    regenerate: list[AudioRemakeSelection] = Field(default_factory=list, max_length=1000)
    arrangement: ArtifactRef | None = None
    review: ArtifactRef | None = None

    @model_validator(mode="after")
    def valid_cache(self):
        if self.cache.kind != "AudioCache" or self.cache.media_type != "application/zip":
            raise ValueError("AUDIO_CACHE_REQUIRED")
        distinct_audio_selections(self.regenerate)
        if (self.arrangement is None) != (self.review is None):
            raise ValueError('AUDIO_REVISION_PLAN_REVIEW_REQUIRED')
        for ref, kind in ((self.arrangement, 'AudioArrangement'), (self.review, 'AudioArrangementReview')):
            if ref is not None and (ref.kind != kind or ref.media_type != 'application/json' or
                                    ref.source_version_id != self.cache.source_version_id):
                raise ValueError('AUDIO_REVISION_PLAN_BINDING_INVALID')
        if any(e.operation_id is not None for e in self.regenerate) and self.arrangement is None:
            raise ValueError('AUDIO_REVISION_PLAN_REQUIRED')
        return self


class RemakeAudioRequest(Contract):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    regenerate: list[AudioRemakeSelection] = Field(default_factory=list, max_length=1000)
    voices: dict[str, str] | None = None
    outputs: list[Literal["audio", "visual"]] = Field(default_factory=lambda: ["audio"], min_length=1, max_length=2)
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def distinct_inputs(self):
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError("DUPLICATE_OUTPUT")
        distinct_audio_selections(self.regenerate)
        if self.voices is not None and not self.voices:
            raise ValueError("VOICE_BINDING_REQUIRED")
        return self


class ProductionRequest(Contract):
    schema_version: Literal["production_request/1.0"] = "production_request/1.0"
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    work_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: Annotated[CreativeSource | DeliverySource | StoryOperationSource, Field(discriminator="kind")]
    outputs: list[Literal["script", "audio", "visual"]] = Field(min_length=1, max_length=3)
    mode: Literal["automatic", "reviewed"] = "automatic"
    voices: VoicePolicy = Field(default_factory=VoicePolicy)
    visual_profile: Literal["standing_performance/1", "pixel_performance/1", "basic_storyboard/1"] = "standing_performance/1"
    budget: Budget = Field(default_factory=Budget)
    audio_revision: AudioRevision | None = None
    sound_workflow: Literal['sound_bridge/1'] | None = None
    audio_delivery_policy: Literal['strict','audio_first'] = 'strict'
    review_policy: Literal['standard', 'listen_first'] = 'standard'

    @model_validator(mode="after")
    def unique_outputs(self):
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError("DUPLICATE_OUTPUT")
        self.outputs = sorted(self.outputs)
        if self.review_policy == 'listen_first':
            if self.mode != 'automatic' or 'visual' in self.outputs or self.audio_revision:
                raise ValueError('LISTEN_FIRST_REQUIRES_AUTOMATIC_NONVISUAL')
            if 'audio' in self.outputs and (self.sound_workflow != 'sound_bridge/1' or self.audio_delivery_policy != 'audio_first'):
                raise ValueError('LISTEN_FIRST_REQUIRES_AUDIO_FIRST_BRIDGE')
        if self.audio_delivery_policy == 'audio_first' and (not self.sound_workflow or self.audio_revision or 'visual' in self.outputs):
            raise ValueError('AUDIO_FIRST_REQUIRES_FRESH_SOUND_BRIDGE')
        if self.sound_workflow and ('audio' not in self.outputs or 'visual' in self.outputs or self.audio_revision):
            raise ValueError('SOUND_BRIDGE_REQUIRES_FRESH_AUDIO_ONLY')
        if isinstance(self.source, CreativeSource) and len(set(self.source.character_ids)) != len(self.source.character_ids):
            raise ValueError("DUPLICATE_CHARACTER")
        if self.audio_revision and (self.source.kind != "delivery" or
                self.audio_revision.cache.source_version_id != self.source.source_version_id or
                not {"audio", "visual"}.intersection(self.outputs)):
            raise ValueError("AUDIO_REVISION_SOURCE_REQUIRED")
        return self


class StageResult(Contract):
    schema_version: Literal["stage_result/1.0"] = "stage_result/1.0"
    status: Literal["running", "succeeded", "failed", "needs_input"]
    checkpoint: dict = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    source_version_id: str | None = None
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
    technical_quality: Literal["not_checked", "passed", "failed"] = "not_checked"
    human_review: Literal["pending", "not_required"] = "not_required"
    poll_after_seconds: int = Field(default=1, ge=0, le=60)

    @model_validator(mode="after")
    def consistent_result(self):
        if self.status in ("failed", "needs_input") and not self.error_code:
            raise ValueError("ERROR_CODE_REQUIRED")
        if self.status == "succeeded" and (not self.artifacts or self.technical_quality != "passed"):
            raise ValueError("SUCCESS_REQUIRES_VERIFIED_ARTIFACTS")
        if self.source_version_id and any(a.source_version_id != self.source_version_id for a in self.artifacts):
            raise ValueError("SOURCE_VERSION_MISMATCH")
        return self


class ResumeRequest(Contract):
    expected_revision: int = Field(ge=1)
    resolution: dict = Field(default_factory=dict)


class ReconcileRequest(Contract):
    expected_revision: int = Field(ge=1)


class RunEvidence(Contract):
    schema_version: Literal["run_evidence/1.0"] = "run_evidence/1.0"
    job_id: str
    frozen_files: dict[str, str]
    session_interventions: int | None = Field(default=None, ge=0)
    manual_artifact_edits: int | None = Field(default=None, ge=0)
    out_of_band_context_injections: int | None = Field(default=None, ge=0)
    policy_repairs: int = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    evidence_basis: Literal["unattested", "isolated_runner"] = "unattested"
    human_content_acceptance: Literal["pending", "passed", "failed"] = "pending"


REQUIRED_ARTIFACTS = {
    "story": {"StoryDelivery", "RenderManifest"},
    "sound_plan": {"SoundProductionPackage", "SoundSourceClassification", "AudioArrangement", "AudioArrangementReview", "AudioArrangementEvidence"},
    "audio": {"AudioMix", "MeasuredTimeline"},
    "visual_plan": {"VisualContext", "VisualPlan"},
    "visual_render": {"VisualTrack"},
    "integrate": {"Video", "PlaybackManifest"},
}


def stage_graph(request: ProductionRequest) -> dict[str, list[str]]:
    graph = {"story": []}
    if "audio" in request.outputs or "visual" in request.outputs:
        graph["audio"] = ["story"]
        if request.sound_workflow:
            graph['sound_plan'] = ['story']
            graph['audio'] = ['story', 'sound_plan']
    if "visual" in request.outputs:
        graph.update(visual_plan=["story"], visual_render=["audio", "visual_plan"],
                     integrate=["audio", "visual_render"])
    return graph


def fingerprint(value) -> str:
    if isinstance(value, ProductionRequest) and value.audio_revision is None:
        # Keep pre-revision idempotency keys compatible with existing stored requests.
        value = value.model_dump(mode="json", exclude={"audio_revision"})
    elif isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict) and value.get('sound_workflow') is None:
        value = {k:v for k,v in value.items() if k != 'sound_workflow'}
    if isinstance(value, dict) and value.get('audio_delivery_policy') == 'strict':
        value = {k:v for k,v in value.items() if k != 'audio_delivery_policy'}
    if isinstance(value, dict) and value.get('review_policy') == 'standard':
        value = {k:v for k,v in value.items() if k != 'review_policy'}
    if isinstance(value, dict) and value.get('audio_revision'):
        # New optional planning/operation references must not change legacy request fingerprints.
        revision = value['audio_revision']
        for key in ('arrangement', 'review'):
            if revision.get(key) is None:
                revision.pop(key, None)
        for event in revision.get('regenerate', []):
            if event.get('operation_id') is None:
                event.pop('operation_id', None)
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
