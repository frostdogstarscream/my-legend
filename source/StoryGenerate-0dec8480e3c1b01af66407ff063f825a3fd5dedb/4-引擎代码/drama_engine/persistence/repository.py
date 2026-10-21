"""Phase 4 Repository 实现层。

PostgresRepository —— 生产级 SQLAlchemy 实现
MemoryRepository  —— dev/test/regression 内存实现

业务代码不直接引用这些类，通过 build_repositories() 选择后端。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..characters.models import CharacterCanon, CharacterRuntimeInput
from .database import get_session  # noqa: F401
from .models import (
    CharacterTemplateModel,
    GenerationTraceModel,
    QualityResultModel,
    StoryJobModel,
    StoryRecordModel,
    StoryTemplateModel,
)
from .settings import PersistenceSettings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ============================================================================
# Domain Objects（纯业务对象，不含库导入）
# ============================================================================
@dataclass
class CharacterTemplate:
    """角色模板领域对象。canon / runtime_defaults 使用 Phase 3 类型。"""
    character_id: str
    version: int
    status: str
    name: str = ""
    canon: dict[str, Any] = field(default_factory=dict)
    runtime_defaults: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_canon(self) -> CharacterCanon:
        return CharacterCanon(character_id=self.character_id, **self.canon)

    def to_runtime_defaults(self) -> CharacterRuntimeInput:
        return CharacterRuntimeInput(**self.runtime_defaults) if self.runtime_defaults else CharacterRuntimeInput()


@dataclass
class StoryTemplate:
    """故事模板领域对象。Phase 4 只存储，不改变生成流程。"""
    template_id: str
    version: int
    status: str
    name: str = ""
    genre_key: str = ""
    supported_modes: list[str] = field(default_factory=list)
    supported_forms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    template_json: dict[str, Any] = field(default_factory=dict)
    quality_score: int = 0
    usage_count: int = 0
    created_at: datetime | None = None


@dataclass
class StoryJob:
    job_id: str
    request_id: str
    status: str = "pending"
    current_stage: str = ""
    input_json: dict[str, Any] = field(default_factory=dict)
    story_mode: str = ""
    mode_version: str = ""
    error_code: str = ""
    error_message: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class StoryRecord:
    story_id: str
    request_id: str
    job_id: str = ""
    story_mode: str = ""
    title: str = ""
    summary: str = ""
    content_json: dict[str, Any] = field(default_factory=dict)
    output_json: dict[str, Any] = field(default_factory=dict)
    resolved_character_snapshot: list[dict[str, Any]] = field(default_factory=list)
    engine_version: str = ""
    model_mapping_snapshot: dict[str, Any] = field(default_factory=dict)
    rule_versions: dict[str, Any] = field(default_factory=dict)
    template_ids: list[str] = field(default_factory=list)
    content_form: str = ""          # Phase 6: audio_drama | prose_story
    renderer_version: str = ""      # Phase 6: content form renderer version
    created_at: datetime | None = None


@dataclass
class GenerationTrace:
    trace_id: str
    request_id: str
    node: str
    job_id: str = ""
    llm_role: str = ""
    model: str = ""
    model_tier: str = ""
    attempt: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "ok"
    error_code: str = ""
    created_at: datetime | None = None


@dataclass
class QualityResult:
    story_id: str
    validator: str = ""
    severity: str = ""
    rule_id: str = ""
    passed: bool = True
    details_json: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


# ============================================================================
# Postgres Repositories
# ============================================================================
class PostgresCharacterTemplateRepository:
    """生产级 PostgreSQL Character Template 仓库。"""

    def __init__(self, session: Session):
        self._session = session

    def get_active(self, character_id: str) -> CharacterTemplate | None:
        row = (
            self._session.query(CharacterTemplateModel)
            .filter_by(character_id=character_id, status="active")
            .order_by(CharacterTemplateModel.version.desc())
            .first()
        )
        return self._to_domain(row) if row else None

    def get_version(self, character_id: str, version: int) -> CharacterTemplate | None:
        row = (
            self._session.query(CharacterTemplateModel)
            .filter_by(character_id=character_id, version=version)
            .first()
        )
        return self._to_domain(row) if row else None

    def create(self, ct: CharacterTemplate) -> CharacterTemplate:
        row = CharacterTemplateModel(
            character_id=ct.character_id,
            version=ct.version,
            status=ct.status,
            name=ct.name,
            canon_json=ct.canon,
            runtime_defaults_json=ct.runtime_defaults,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def list_active(self) -> list[CharacterTemplate]:
        rows = (
            self._session.query(CharacterTemplateModel)
            .filter_by(status="active")
            .order_by(CharacterTemplateModel.character_id)
            .all()
        )
        return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row: CharacterTemplateModel) -> CharacterTemplate:
        return CharacterTemplate(
            character_id=row.character_id,
            version=row.version,
            status=row.status or "active",
            name=row.name,
            canon=row.canon_json or {},
            runtime_defaults=row.runtime_defaults_json or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class PostgresStoryJobRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, job: StoryJob) -> StoryJob:
        row = StoryJobModel(
            job_id=job.job_id or _new_id(),
            request_id=job.request_id,
            status=job.status,
            current_stage=job.current_stage,
            input_json=job.input_json,
            story_mode=job.story_mode,
            mode_version=job.mode_version,
            created_at=_utcnow(),
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def update_status(self, job_id: str, status: str, stage: str = "",
                      error_code: str = "", error_message: str = "") -> None:
        updates = {"status": status}
        if stage:
            updates["current_stage"] = stage
        if status == "running":
            updates["started_at"] = _utcnow()
        elif status in ("completed", "failed"):
            updates["finished_at"] = _utcnow()
        if error_code:
            updates["error_code"] = error_code
        if error_message:
            updates["error_message"] = error_message
        (
            self._session.query(StoryJobModel)
            .filter_by(job_id=job_id)
            .update(updates)
        )

    def get_by_request_id(self, request_id: str) -> StoryJob | None:
        row = self._session.query(StoryJobModel).filter_by(request_id=request_id).first()
        return self._to_domain(row) if row else None

    @staticmethod
    def _to_domain(row: StoryJobModel) -> StoryJob:
        return StoryJob(
            job_id=row.job_id, request_id=row.request_id or "",
            status=row.status or "pending",
            current_stage=row.current_stage or "",
            story_mode=row.story_mode or "",
            mode_version=row.mode_version or "",
            error_code=row.error_code or "",
            error_message=row.error_message or "",
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


class PostgresStoryRecordRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, record: StoryRecord) -> StoryRecord:
        row = StoryRecordModel(
            story_id=record.story_id or _new_id(),
            request_id=record.request_id,
            job_id=record.job_id,
            story_mode=record.story_mode,
            title=record.title,
            summary=record.summary,
            content_json=record.content_json,
            output_json=record.output_json,
            resolved_character_snapshot=record.resolved_character_snapshot,
            engine_version=record.engine_version,
            model_mapping_snapshot=record.model_mapping_snapshot,
            rule_versions=record.rule_versions,
            template_ids=record.template_ids,
            created_at=_utcnow(),
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def get_by_story_id(self, story_id: str) -> StoryRecord | None:
        row = self._session.query(StoryRecordModel).filter_by(story_id=story_id).first()
        return self._to_domain(row) if row else None

    @staticmethod
    def _to_domain(row: StoryRecordModel) -> StoryRecord:
        return StoryRecord(
            story_id=row.story_id, request_id=row.request_id or "",
            job_id=row.job_id or "", story_mode=row.story_mode or "",
            title=row.title or "", summary=row.summary or "",
            content_json=row.content_json or {},
            output_json=row.output_json or {},
            resolved_character_snapshot=row.resolved_character_snapshot or [],
            engine_version=row.engine_version or "",
            model_mapping_snapshot=row.model_mapping_snapshot or {},
            rule_versions=row.rule_versions or {},
            template_ids=row.template_ids or [],
            created_at=row.created_at,
        )


class PostgresGenerationTraceRepository:
    def __init__(self, session: Session):
        self._session = session

    def batch_create(self, traces: list[GenerationTrace]) -> list[GenerationTrace]:
        for t in traces:
            row = GenerationTraceModel(
                trace_id=t.trace_id or _new_id(),
                request_id=t.request_id, job_id=t.job_id,
                node=t.node, llm_role=t.llm_role, model=t.model,
                model_tier=t.model_tier, attempt=t.attempt,
                latency_ms=t.latency_ms, input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                status=t.status, error_code=t.error_code,
                created_at=_utcnow(),
            )
            self._session.add(row)
        self._session.flush()
        return traces


class PostgresQualityResultRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, qr: QualityResult) -> QualityResult:
        row = QualityResultModel(
            story_id=qr.story_id,
            validator=qr.validator, severity=qr.severity,
            rule_id=qr.rule_id, passed=1 if qr.passed else 0,
            details_json=qr.details_json, created_at=_utcnow(),
        )
        self._session.add(row)
        self._session.flush()
        return qr


class PostgresStoryTemplateRepository:
    def __init__(self, session: Session):
        self._session = session

    def get_active(self, template_id: str) -> StoryTemplate | None:
        row = (
            self._session.query(StoryTemplateModel)
            .filter_by(template_id=template_id, status="active")
            .order_by(StoryTemplateModel.version.desc())
            .first()
        )
        return self._to_domain(row) if row else None

    def create(self, st: StoryTemplate) -> StoryTemplate:
        row = StoryTemplateModel(
            template_id=st.template_id, version=st.version,
            status=st.status, name=st.name, genre_key=st.genre_key,
            supported_modes=st.supported_modes,
            supported_forms=st.supported_forms, tags=st.tags,
            template_json=st.template_json,
            quality_score=st.quality_score, usage_count=st.usage_count,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def list_active(self) -> list[StoryTemplate]:
        rows = (
            self._session.query(StoryTemplateModel)
            .filter_by(status="active").all()
        )
        return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row: StoryTemplateModel) -> StoryTemplate:
        return StoryTemplate(
            template_id=row.template_id, version=row.version,
            status=row.status or "active", name=row.name,
            genre_key=row.genre_key or "",
            supported_modes=row.supported_modes or [],
            supported_forms=row.supported_forms or [],
            tags=row.tags or [], template_json=row.template_json or {},
            quality_score=row.quality_score or 0,
            usage_count=row.usage_count or 0,
            created_at=row.created_at,
        )


# ============================================================================
# Memory Repositories (dev / test / regression)
# ============================================================================
class MemoryCharacterTemplateRepository:
    """内存实现：用于 dev/test/legacy regression。"""

    def __init__(self):
        self._store: dict[str, CharacterTemplate] = {}
        self._versions: dict[str, dict[int, CharacterTemplate]] = {}

    def get_active(self, character_id: str) -> CharacterTemplate | None:
        return self._store.get(character_id)

    def get_version(self, character_id: str, version: int) -> CharacterTemplate | None:
        return self._versions.get(character_id, {}).get(version)

    def create(self, ct: CharacterTemplate) -> CharacterTemplate:
        if ct.status == "active":
            self._store[ct.character_id] = ct
        self._versions.setdefault(ct.character_id, {})[ct.version] = ct
        return ct

    def list_active(self) -> list[CharacterTemplate]:
        return list(self._store.values())


class MemoryStoryJobRepository:
    def __init__(self):
        self._jobs: dict[str, StoryJob] = {}

    def create(self, job: StoryJob) -> StoryJob:
        self._jobs[job.job_id] = job
        return job

    def update_status(self, job_id: str, status: str, stage: str = "",
                      error_code: str = "", error_message: str = "") -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = status
            if stage:
                job.current_stage = stage
            if status == "running" and not job.started_at:
                job.started_at = _utcnow()
            elif status in ("completed", "failed"):
                job.finished_at = _utcnow()
            if error_code:
                job.error_code = error_code
            if error_message:
                job.error_message = error_message

    def get_by_request_id(self, request_id: str) -> StoryJob | None:
        for j in self._jobs.values():
            if j.request_id == request_id:
                return j
        return None


class MemoryStoryRecordRepository:
    def __init__(self):
        self._records: dict[str, StoryRecord] = {}

    def create(self, record: StoryRecord) -> StoryRecord:
        self._records[record.story_id] = record
        return record

    def get_by_story_id(self, story_id: str) -> StoryRecord | None:
        return self._records.get(story_id)


class MemoryGenerationTraceRepository:
    def __init__(self):
        self._traces: list[GenerationTrace] = []

    def batch_create(self, traces: list[GenerationTrace]) -> list[GenerationTrace]:
        self._traces.extend(traces)
        return traces


class MemoryQualityResultRepository:
    def __init__(self):
        self._results: list[QualityResult] = []

    def create(self, qr: QualityResult) -> QualityResult:
        self._results.append(qr)
        return qr


class MemoryStoryTemplateRepository:
    def __init__(self):
        self._store: dict[str, StoryTemplate] = {}

    def get_active(self, template_id: str) -> StoryTemplate | None:
        return self._store.get(template_id)

    def create(self, st: StoryTemplate) -> StoryTemplate:
        if st.status == "active":
            self._store[st.template_id] = st
        return st

    def list_active(self) -> list[StoryTemplate]:
        return list(self._store.values())


# ============================================================================
# Repository Factory
# ============================================================================
@dataclass
class Repositories:
    character_template: MemoryCharacterTemplateRepository | PostgresCharacterTemplateRepository
    story_template: MemoryStoryTemplateRepository | PostgresStoryTemplateRepository
    story_job: MemoryStoryJobRepository | PostgresStoryJobRepository
    story_record: MemoryStoryRecordRepository | PostgresStoryRecordRepository
    generation_trace: MemoryGenerationTraceRepository | PostgresGenerationTraceRepository
    quality_result: MemoryQualityResultRepository | PostgresQualityResultRepository


def build_repositories(settings: PersistenceSettings | None = None) -> Repositories:
    """根据 PERSISTENCE_BACKEND 选择 Repository 实现。

    memory → 全量内存实现（dev/test/regression）
    postgres → SQLAlchemy + 真正 PostgreSQL
    """
    if settings is None:
        settings = PersistenceSettings.from_env()
    if settings.backend == "memory":
        return Repositories(
            character_template=MemoryCharacterTemplateRepository(),
            story_template=MemoryStoryTemplateRepository(),
            story_job=MemoryStoryJobRepository(),
            story_record=MemoryStoryRecordRepository(),
            generation_trace=MemoryGenerationTraceRepository(),
            quality_result=MemoryQualityResultRepository(),
        )
    session = get_session(settings)
    if session is None:
        raise RuntimeError(
            "PERSISTENCE_BACKEND=postgres 但无法创建数据库连接"
        )
    return Repositories(
        character_template=PostgresCharacterTemplateRepository(session),
        story_template=PostgresStoryTemplateRepository(session),
        story_job=PostgresStoryJobRepository(session),
        story_record=PostgresStoryRecordRepository(session),
        generation_trace=PostgresGenerationTraceRepository(session),
        quality_result=PostgresQualityResultRepository(session),
    )