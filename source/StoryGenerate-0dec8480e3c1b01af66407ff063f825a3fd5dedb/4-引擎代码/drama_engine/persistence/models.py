"""Phase 4 SQLAlchemy ORM 模型。

所有表：character_templates, story_templates, story_jobs,
         story_records, generation_traces, quality_results。

JSONB 字段用于 semi-structured 数据（Canon, Runtime, Snapshot）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    Float,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# ---- constraint naming convention for Alembic ---- #
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    metadata = metadata


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ============================================================================
# character_templates
# ============================================================================
class CharacterTemplateModel(Base):
    __tablename__ = "character_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(
        Enum("draft", "active", "archived", name="character_template_status"),
        default="active",
        nullable=False,
    )
    name = Column(String(128), nullable=False)

    # JSONB: Phase 3 CharacterCanon (semi-structured)
    canon_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    # JSONB: CharacterRuntimeInput-compatible defaults
    runtime_defaults_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    assets_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("character_id", "version", name="uq_ct_charid_version"),
    )


# ============================================================================
# story_templates
# ============================================================================
class StoryTemplateModel(Base):
    __tablename__ = "story_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(
        Enum("draft", "active", "archived", name="story_template_status"),
        default="active",
        nullable=False,
    )
    name = Column(String(256), nullable=False)
    genre_key = Column(String(32), nullable=True)

    supported_modes = Column(JSONB().with_variant(JSON, "sqlite"), default=list)
    supported_forms = Column(JSONB().with_variant(JSON, "sqlite"), default=list)
    tags = Column(JSONB().with_variant(JSON, "sqlite"), default=list)

    # JSONB: 模板本体（semi-structured）
    template_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)

    quality_score = Column(Integer, default=0)
    usage_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_st_tid_version"),
    )


# ============================================================================
# story_jobs
# ============================================================================
class StoryJobModel(Base):
    __tablename__ = "story_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), nullable=False, unique=True, index=True)
    request_id = Column(String(64), nullable=False, unique=True)

    # Phase 7: long-form linking
    run_id = Column(String(64), nullable=True, index=True)
    story_id = Column(String(64), nullable=True, index=True)

    status = Column(String(32), default="pending")
    current_stage = Column(String(64), nullable=True)

    input_json = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    approved_plan_id = Column(String(64), nullable=True, index=True)
    approved_plan_revision = Column(Integer, nullable=True)
    input_snapshot = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    result_story_version_id = Column(String(64), nullable=True, index=True)

    story_mode = Column(String(32), nullable=True)
    mode_version = Column(String(16), nullable=True)

    # Phase 7: additional config
    template_id = Column(String(64), nullable=True)
    content_form = Column(String(32), nullable=True)
    generation_scale = Column(String(32), nullable=True)

    # Phase 7: progress tracking
    progress_pct = Column(Integer, default=0)
    current_chapter = Column(Integer, default=0)
    total_chapters = Column(Integer, default=0)
    message = Column(Text, nullable=True)

    # Phase 7: cancellation
    cancel_requested = Column(Integer, default=0)  # 0/1 boolean

    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    execution_usage_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=True)


# ============================================================================
# story_records
# ============================================================================
class StoryRecordModel(Base):
    __tablename__ = "story_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(String(64), nullable=False, index=True)
    job_id = Column(String(64), nullable=True, index=True)
    request_id = Column(String(64), nullable=False)

    story_mode = Column(String(32), nullable=True)
    mode_version = Column(String(16), nullable=True)

    title = Column(String(512), nullable=True)
    summary = Column(Text, nullable=True)

    # JSONB: 正文 + 完整输出
    content_json = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    output_json = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)

    # Phase 3/4 关键：角色快照（JSONB）
    resolved_character_snapshot = Column(JSONB().with_variant(JSON, "sqlite"), default=list)

    engine_version = Column(String(32), nullable=True)
    model_mapping_snapshot = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    rule_versions = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    template_ids = Column(JSONB().with_variant(JSON, "sqlite"), default=list)

    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ============================================================================
# generation_traces
# ============================================================================
class GenerationTraceModel(Base):
    __tablename__ = "generation_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(64), nullable=False, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    job_id = Column(String(64), nullable=True)

    node = Column(String(64), nullable=False)
    llm_role = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    model_tier = Column(String(16), nullable=True)

    attempt = Column(Integer, default=0)

    latency_ms = Column(Integer, default=0)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)

    status = Column(String(16), default="ok")
    error_code = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ============================================================================
# quality_results
# ============================================================================
class QualityResultModel(Base):
    __tablename__ = "quality_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(String(64), nullable=False, index=True)

    validator = Column(String(64), nullable=True)
    severity = Column(String(16), nullable=True)  # "error" | "warning" | "info"
    rule_id = Column(String(32), nullable=True)
    passed = Column(Integer, default=1)  # 1=pass, 0=fail

    details_json = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)

    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ============================================================================
# generation_checkpoints  (Phase 7)
# ============================================================================
class GenerationCheckpointModel(Base):
    __tablename__ = "generation_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    story_id = Column(String(64), nullable=True)

    generation_scale = Column(String(32), default="long_form")
    stage = Column(String(64), nullable=False)

    last_completed_chapter = Column(Integer, default=0)
    next_chapter = Column(Integer, default=1)

    # JSONB: 完整的可恢复状态 (plan, context, etc.)
    state_json = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)

    config_fingerprint = Column(String(64), nullable=True)
    status = Column(String(32), default="resumable")
    version = Column(String(16), default="1.0")

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "stage", name="uq_gcp_runid_stage"),
    )


# ============================================================================
# character_memory  (Phase 8)
# ============================================================================
class CharacterMemoryModel(Base):
    __tablename__ = "character_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(String(64), nullable=False, unique=True, index=True)
    character_id = Column(String(64), nullable=False, index=True)

    memory_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    importance = Column(Float, default=0.0)

    source_story_id = Column(String(64), nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("character_id", "source_story_id", "memory_type", "content",
                         name="uq_cm_char_source_type_content"),
    )


# ============================================================================
# Approved-plan story workflow (Phase 10)
# ============================================================================
class StoryPlanModel(Base):
    __tablename__ = "story_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(String(64), nullable=False, unique=True, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    principal_id = Column(String(128), nullable=False, default="local", index=True)
    intent = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    current_revision = Column(Integer, nullable=False, default=1)
    request_snapshot = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    source_reference = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class StoryPlanRevisionModel(Base):
    __tablename__ = "story_plan_revisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(String(64), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    plan_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    character_snapshot = Column(JSONB().with_variant(JSON, "sqlite"), default=list)
    source_snapshot = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    fingerprint = Column(String(64), nullable=False)
    feedback = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("plan_id", "revision", name="uq_story_plan_revision"),
    )


class StoryVersionModel(Base):
    __tablename__ = "story_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_version_id = Column(String(64), nullable=False, unique=True, index=True)
    story_id = Column(String(64), nullable=False, index=True)
    version_no = Column(Integer, nullable=False, default=1)
    parent_story_version_id = Column(String(64), nullable=True, index=True)
    lineage_type = Column(String(32), nullable=False)
    series_id = Column(String(64), nullable=True, index=True)
    principal_id = Column(String(128), nullable=False, default="local", index=True)
    approved_plan_id = Column(String(64), nullable=False, index=True)
    approved_plan_revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="CANDIDATE")
    ready_for_playback = Column(Integer, nullable=False, default=0)
    title = Column(String(512), nullable=True)
    summary = Column(Text, nullable=True)
    delivery_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    continuity_json = Column(JSONB().with_variant(JSON, "sqlite"), default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("story_id", "version_no", name="uq_story_version_number"),
    )


class CharacterProfileRevisionModel(Base):
    __tablename__ = "character_profile_revisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    principal_id = Column(String(128), nullable=False, default="local", index=True)
    character_id = Column(String(64), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="active")
    profile_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("principal_id", "character_id", "revision", name="uq_character_profile_revision"),
    )


class PlaybackEventModel(Base):
    __tablename__ = "playback_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False, unique=True, index=True, default=_new_id)
    principal_id = Column(String(128), nullable=False, default="local", index=True)
    story_version_id = Column(String(64), nullable=False, index=True)
    episode_id = Column(String(64), nullable=True)
    playback_position_ms = Column(Integer, nullable=False, default=0)
    event_type = Column(String(32), nullable=False, default="progress")
    occurred_at = Column(DateTime(timezone=True), default=_utcnow, index=True)


class ServicePlanTaskModel(Base):
    """Durable transport tasks for existing prepare/revise operations only."""
    __tablename__ = "service_plan_tasks"
    task_id = Column(String(64), primary_key=True, default=_new_id)
    principal_id = Column(String(128), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="QUEUED", index=True)
    payload_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False)
    result_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    usage_json = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False, default=dict)
    progress_pct = Column(Integer, nullable=False, default=0)
    stage = Column(String(64), nullable=False, default="queued")
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    execution_token = Column(String(64), nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    __table_args__ = (UniqueConstraint("principal_id", "idempotency_key", name="uq_service_plan_task_key"),)
