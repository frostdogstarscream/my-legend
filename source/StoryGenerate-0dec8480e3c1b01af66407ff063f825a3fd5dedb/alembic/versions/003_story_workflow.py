"""Approved-plan story workflow.

Revision ID: 003
Revises: 002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "story_plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False, server_default="local"),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_snapshot", _json_type(), nullable=False),
        sa.Column("source_reference", _json_type(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("plan_id", name="uq_story_plans_plan_id"),
    )
    op.create_index("ix_story_plans_request_id", "story_plans", ["request_id"])
    op.create_index("ix_story_plans_principal_id", "story_plans", ["principal_id"])

    op.create_table(
        "story_plan_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan_json", _json_type(), nullable=False),
        sa.Column("character_snapshot", _json_type(), nullable=True),
        sa.Column("source_snapshot", _json_type(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("plan_id", "revision", name="uq_story_plan_revision"),
    )
    op.create_index("ix_story_plan_revisions_plan_id", "story_plan_revisions", ["plan_id"])

    op.create_table(
        "story_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("story_version_id", sa.String(64), nullable=False),
        sa.Column("story_id", sa.String(64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_story_version_id", sa.String(64), nullable=True),
        sa.Column("lineage_type", sa.String(32), nullable=False),
        sa.Column("series_id", sa.String(64), nullable=True),
        sa.Column("principal_id", sa.String(128), nullable=False, server_default="local"),
        sa.Column("approved_plan_id", sa.String(64), nullable=False),
        sa.Column("approved_plan_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="CANDIDATE"),
        sa.Column("ready_for_playback", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("delivery_json", _json_type(), nullable=False),
        sa.Column("continuity_json", _json_type(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("story_version_id", name="uq_story_versions_story_version_id"),
        sa.UniqueConstraint("story_id", "version_no", name="uq_story_version_number"),
    )
    op.create_index("ix_story_versions_story_id", "story_versions", ["story_id"])
    op.create_index("ix_story_versions_principal_id", "story_versions", ["principal_id"])
    op.create_index("ix_story_versions_series_id", "story_versions", ["series_id"])

    op.create_table(
        "character_profile_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("principal_id", sa.String(128), nullable=False, server_default="local"),
        sa.Column("character_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("profile_json", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("principal_id", "character_id", "revision", name="uq_character_profile_revision"),
    )
    op.create_index("ix_character_profile_character_id", "character_profile_revisions", ["character_id"])

    op.create_table(
        "playback_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False, server_default="local"),
        sa.Column("story_version_id", sa.String(64), nullable=False),
        sa.Column("episode_id", sa.String(64), nullable=True),
        sa.Column("playback_position_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(32), nullable=False, server_default="progress"),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", name="uq_playback_events_event_id"),
    )
    op.create_index("ix_playback_events_principal_id", "playback_events", ["principal_id"])
    op.create_index("ix_playback_events_story_version_id", "playback_events", ["story_version_id"])

    with op.batch_alter_table("story_jobs") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        batch.add_column(sa.Column("approved_plan_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("approved_plan_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("input_snapshot", _json_type(), nullable=True))
        batch.add_column(sa.Column("result_story_version_id", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_story_jobs_job_id", ["job_id"])
        batch.create_unique_constraint("uq_story_jobs_idempotency_key", ["idempotency_key"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE FUNCTION prevent_story_version_content_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.story_id IS DISTINCT FROM OLD.story_id
               OR NEW.version_no IS DISTINCT FROM OLD.version_no
               OR NEW.parent_story_version_id IS DISTINCT FROM OLD.parent_story_version_id
               OR NEW.approved_plan_id IS DISTINCT FROM OLD.approved_plan_id
               OR NEW.approved_plan_revision IS DISTINCT FROM OLD.approved_plan_revision
               OR NEW.delivery_json::jsonb IS DISTINCT FROM OLD.delivery_json::jsonb THEN
                RAISE EXCEPTION 'story version content is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)
        op.execute("""
        CREATE TRIGGER trg_story_version_immutable
        BEFORE UPDATE ON story_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_story_version_content_update();
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_story_version_immutable ON story_versions")
        op.execute("DROP FUNCTION IF EXISTS prevent_story_version_content_update()")
    with op.batch_alter_table("story_jobs") as batch:
        batch.drop_constraint("uq_story_jobs_idempotency_key", type_="unique")
        batch.drop_constraint("uq_story_jobs_job_id", type_="unique")
        for column in (
            "result_story_version_id", "input_snapshot", "approved_plan_revision",
            "approved_plan_id", "idempotency_key",
        ):
            batch.drop_column(column)
    op.drop_table("playback_events")
    op.drop_table("character_profile_revisions")
    op.drop_table("story_versions")
    op.drop_table("story_plan_revisions")
    op.drop_table("story_plans")
