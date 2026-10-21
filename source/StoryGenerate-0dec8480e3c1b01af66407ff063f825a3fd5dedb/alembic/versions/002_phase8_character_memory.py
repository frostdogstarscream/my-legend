"""Phase 8: Character Memory table

Revision ID: 002
Revises: 001
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float,
    UniqueConstraint,
)

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "character_memory",
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("memory_id", String(64), nullable=False, unique=True, index=True),
        Column("character_id", String(64), nullable=False, index=True),
        Column("memory_type", String(32), nullable=False),
        Column("content", Text, nullable=False),
        Column("importance", Float, default=0.0),
        Column("source_story_id", String(64), nullable=False, index=True),
        Column("created_at", DateTime(timezone=True)),
        Column("expires_at", DateTime(timezone=True), nullable=True),
        UniqueConstraint("character_id", "source_story_id", "memory_type", "content",
                         name="uq_cm_char_source_type_content"),
    )


def downgrade() -> None:
    op.drop_table("character_memory")