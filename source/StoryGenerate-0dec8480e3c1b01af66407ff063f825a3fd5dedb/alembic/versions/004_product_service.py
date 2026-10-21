"""Durable planning tasks, generation leases and frozen character assets."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    j = JSONB().with_variant(sa.JSON(), "sqlite")
    # 001 creates selected tables from current ORM metadata. Fresh databases may
    # already have these columns; an existing003 database won't. Support both.
    inspector = sa.inspect(op.get_bind())
    characters = {c["name"] for c in inspector.get_columns("character_templates")}
    jobs = {c["name"] for c in inspector.get_columns("story_jobs")}
    if "assets_json" not in characters:
        op.add_column(
            "character_templates",
            sa.Column("assets_json", j, nullable=False, server_default=sa.text("'{}'")),
        )
    for column in (
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("execution_usage_json", j),
    ):
        if column.name not in jobs:
            op.add_column("story_jobs", column)
    if "ix_story_jobs_lease_expires_at" not in {
        i["name"] for i in inspector.get_indexes("story_jobs")
    }:
        op.create_index(
            "ix_story_jobs_lease_expires_at", "story_jobs", ["lease_expires_at"]
        )
    if inspector.has_table("service_plan_tasks"):
        return
    op.create_table(
        "service_plan_tasks",
        sa.Column("task_id", sa.String(64), primary_key=True),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload_json", j, nullable=False),
        sa.Column("result_json", j, nullable=False),
        sa.Column("usage_json", j, nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("execution_token", sa.String(64)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "principal_id", "idempotency_key", name="uq_service_plan_task_key"
        ),
    )
    op.create_index(
        "ix_service_plan_tasks_principal_id", "service_plan_tasks", ["principal_id"]
    )
    op.create_index("ix_service_plan_tasks_status", "service_plan_tasks", ["status"])
    op.create_index(
        "ix_service_plan_tasks_lease_expires_at",
        "service_plan_tasks",
        ["lease_expires_at"],
    )


def downgrade():
    op.drop_table("service_plan_tasks")
    op.drop_index("ix_story_jobs_lease_expires_at", table_name="story_jobs")
    for name in ("execution_usage_json", "lease_expires_at", "lease_owner"):
        op.drop_column("story_jobs", name)
    op.drop_column("character_templates", "assets_json")
