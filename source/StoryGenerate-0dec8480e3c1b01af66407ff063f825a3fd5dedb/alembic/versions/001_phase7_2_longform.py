"""Phase 7.2: Long-form Job + Generation Checkpoint

Revision ID: 001
Revises: None (first migration)
Create Date: 2026-09-15

Changes:
  - Extend story_jobs: run_id, story_id, template_id, content_form,
    generation_scale, progress_pct, current_chapter, total_chapters,
    message, cancel_requested, updated_at
  - Add generation_checkpoints table

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, JSON, inspect,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from drama_engine.persistence.models import Base

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    legacy_tables = [
        Base.metadata.tables[name] for name in (
            "character_templates", "story_templates", "story_records",
            "generation_traces", "quality_results",
        )
    ]
    Base.metadata.create_all(bind, tables=legacy_tables, checkfirst=True)
    if not inspect(bind).has_table("story_jobs"):
        op.create_table(
            "story_jobs",
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("job_id", String(64), nullable=False),
            Column("request_id", String(64), nullable=False, unique=True),
            Column("status", String(32), nullable=True),
            Column("current_stage", String(64), nullable=True),
            Column("input_json", JSONB().with_variant(JSON(), "sqlite"), nullable=True),
            Column("story_mode", String(32), nullable=True),
            Column("mode_version", String(16), nullable=True),
            Column("error_code", String(64), nullable=True),
            Column("error_message", Text(), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=True),
            Column("started_at", DateTime(timezone=True), nullable=True),
            Column("finished_at", DateTime(timezone=True), nullable=True),
            Column("deadline_at", DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_story_jobs_job_id", "story_jobs", ["job_id"])
    # ── Extend story_jobs ──
    for col, col_type in [
        ("run_id", String(64)),
        ("story_id", String(64)),
        ("template_id", String(64)),
        ("content_form", String(32)),
        ("generation_scale", String(32)),
        ("progress_pct", Integer()),
        ("current_chapter", Integer()),
        ("total_chapters", Integer()),
        ("message", Text()),
        ("cancel_requested", Integer()),
        ("updated_at", DateTime(timezone=True)),
    ]:
        if col not in {column["name"] for column in inspect(bind).get_columns("story_jobs")}:
            op.add_column("story_jobs", Column(col, col_type, nullable=True))

    # ── Create generation_checkpoints ──
    op.create_table(
        "generation_checkpoints",
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("run_id", String(64), nullable=False, index=True),
        Column("story_id", String(64), nullable=True),
        Column("generation_scale", String(32), default="long_form"),
        Column("stage", String(64), nullable=False),
        Column("last_completed_chapter", Integer, default=0),
        Column("next_chapter", Integer, default=1),
        Column("state_json", JSON, default=dict),
        Column("config_fingerprint", String(64), nullable=True),
        Column("status", String(32), default="resumable"),
        Column("version", String(16), default="1.0"),
        Column("created_at", DateTime(timezone=True)),
        Column("updated_at", DateTime(timezone=True)),
        UniqueConstraint("run_id", "stage", name="uq_gcp_runid_stage"),
    )


def downgrade() -> None:
    # ── Remove generation_checkpoints ──
    op.drop_table("generation_checkpoints")

    # ── Remove extended columns from story_jobs ──
    for col in [
        "run_id", "story_id", "template_id", "content_form",
        "generation_scale", "progress_pct", "current_chapter",
        "total_chapters", "message", "cancel_requested", "updated_at",
    ]:
        try:
            op.drop_column("story_jobs", col)
        except Exception:
            pass
