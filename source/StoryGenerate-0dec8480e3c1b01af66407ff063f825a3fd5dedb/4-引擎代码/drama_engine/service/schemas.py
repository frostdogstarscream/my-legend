from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..characters.models import CharacterCanon, CharacterRuntimeInput


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonInput(CharacterCanon):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    age: int | None = Field(default=None, ge=0)


class RuntimeInput(CharacterRuntimeInput):
    model_config = ConfigDict(extra="forbid")


class AssetReference(APIModel):
    asset_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(default="1", min_length=1, max_length=64)
    uri: str | None = None
    performer_id: str | None = None


class CharacterImport(APIModel):
    character_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=0)
    canon: CanonInput
    runtime_defaults: RuntimeInput = Field(default_factory=RuntimeInput)
    assets: dict[Literal["image", "voice", "expression", "action"], AssetReference] = (
        Field(default_factory=dict)
    )


class CharacterBatch(APIModel):
    characters: list[CharacterImport] = Field(min_length=1, max_length=100)


class CharacterView(APIModel):
    character_id: str
    version: int
    canon: CanonInput
    runtime_defaults: RuntimeInput
    assets: dict[str, AssetReference]


class RuntimeUpdate(APIModel):
    expected_revision: int = Field(ge=0)
    patch: RuntimeInput


class RuntimeView(APIModel):
    character_id: str
    revision: int
    profile: dict[str, Any]


class PlanRevisionRequest(APIModel):
    expected_revision: int = Field(ge=1)
    feedback: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class PlanApprovalRequest(APIModel):
    expected_revision: int = Field(ge=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class TaskView(APIModel):
    task_id: str
    kind: Literal["prepare", "revise"]
    status: Literal[
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"
    ]
    progress_pct: int
    stage: str
    result: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class JobView(APIModel):
    job_id: str
    request_id: str
    status: str
    stage: str
    progress_pct: int
    approved_plan_id: str
    approved_plan_revision: int
    result_story_version_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    story_state: Literal[
        "NOT_GENERATED", "GENERATING", "SUCCEEDED", "FAILED", "CANCELLED"
    ]
    audio_state: Literal["NOT_SYNTHESIZED"] = "NOT_SYNTHESIZED"
    playback_state: Literal["NOT_REPORTED"] = "NOT_REPORTED"


class ErrorInfo(APIModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(APIModel):
    error: ErrorInfo
