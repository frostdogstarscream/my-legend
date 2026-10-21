from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(Contract):
    role_id: str
    name: str
    description: str = ""


class Scene(Contract):
    scene_id: str
    description: str = ""
    performance: dict[str, Any] = Field(default_factory=dict)


class Point(Contract):
    name: str
    kind: str = "contact"
    frame: int = Field(ge=0)
    sample_rate: int = Field(default=48000, gt=0)
    method: str
    status: Literal["candidate", "confirmed"] = "candidate"


class Relation(Contract):
    anchor: str | None = None
    anchor_point: str = "end"
    self_point: str = "start"
    offset_ms: float = 0
    mode: Literal["equal", "after"] = "equal"
    provenance: str = "explicit"


class Event(Contract):
    block_id: str
    scene_id: str
    kind: Literal["speech", "sfx", "ambience", "music", "pause", "action", "transition"]
    role_id: str | None = None
    text: str = ""
    target: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    performance: dict[str, Any] = Field(default_factory=dict)
    sound: dict[str, Any] = Field(default_factory=dict)
    media: dict[str, Any] = Field(default_factory=dict)
    timing: list[Relation] = Field(default_factory=list)
    keypoints: list[Point] = Field(default_factory=list)
    lineage: list[str] = Field(default_factory=list)
    source_ref: dict[str, Any] = Field(default_factory=dict)


class StandardScript(Contract):
    schema_version: Literal["lingban.script/1"] = "lingban.script/1"
    source_version_id: str
    artifact_revision: str = "r1"
    title: str
    roles: list[Role]
    scenes: list[Scene]
    events: list[Event]
    source: dict[str, Any] = Field(default_factory=dict)
    boundaries: dict[str, Any] = Field(default_factory=lambda: {"allow_new_plot": False, "preserve_dialogue": True})
    issues: list[dict[str, Any]] = Field(default_factory=list)
    id_mapping: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self):
        for field, key in [(self.roles, "role_id"), (self.scenes, "scene_id"), (self.events, "block_id")]:
            ids = [getattr(x, key) for x in field]
            if len(set(ids)) != len(ids):
                raise ValueError(f"Duplicate {key}")
        roles = {x.role_id for x in self.roles}
        scenes = {x.scene_id for x in self.scenes}
        events = {x.block_id for x in self.events}
        for e in self.events:
            if e.scene_id not in scenes:
                raise ValueError(f"{e.block_id}: unknown scene")
            if e.role_id and e.role_id not in roles:
                raise ValueError(f"{e.block_id}: unknown role")
            for t in e.timing:
                if t.anchor is not None and t.anchor not in events:
                    raise ValueError(f"{e.block_id}: unknown timing anchor {t.anchor}")
        return self


class PerformanceDocument(Contract):
    schema_version: Literal["lingban.performance/1"] = "lingban.performance/1"
    source_version_id: str
    script_sha256: str
    artifact_revision: str
    template_version: str
    project: dict[str, Any]
    roles: dict[str, Any]
    scenes: dict[str, Any]
    lines: dict[str, Any]
    sounds: dict[str, Any]
    visual_semantics: list[dict[str, Any]]
    timing_constraints: list[dict[str, Any]]
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    generation_record: dict[str, Any] = Field(default_factory=dict)


class ProductionTask(Contract):
    schema_version: Literal["lingban.task/1"] = "lingban.task/1"
    task_id: str
    block_id: str
    kind: Literal["speech", "sfx", "ambience", "music"]
    source_version_id: str
    artifact_revision: str
    input_sha256: str
    candidate_version: int = Field(default=1, ge=1)
    adapter: str
    payload: dict[str, Any]
    dependencies: list[str]
    status: str = "planned"


class AssetRecord(Contract):
    schema_version: Literal["lingban.asset/1"] = "lingban.asset/1"
    asset_id: str
    artifact_revision: str
    task_id: str
    block_id: str
    raw: dict[str, Any]
    processed: dict[str, Any]
    processing: dict[str, Any]
    keypoints: list[Point]
    duration_basis: Literal["measured"] = "measured"
    semantic_review: str = "pending"
    generation: dict[str, Any]


class Timeline(Contract):
    schema_version: Literal["lingban.timeline/1"] = "lingban.timeline/1"
    timeline_version: str
    source_version_id: str
    script_sha256: str
    sample_rate: int
    total_frames: int
    stage: Literal["draft", "audio_locked", "audiovisual_locked"] = "draft"
    placements: list[dict[str, Any]]
    subtitles: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    budget: dict[str, Any]
    visual: dict[str, Any]


class ReviewRecord(Contract):
    schema_version: Literal["lingban.review/1"] = "lingban.review/1"
    review_id: str
    bundle_sha256: str
    source_version_id: str
    timeline_version: str
    decision: Literal["approve", "changes", "cancel"]
    reviewer: str
    scope: list[str]
    notes: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_points: list[str] = Field(default_factory=list)
    delivery: dict[str, Any] = Field(default_factory=dict)
