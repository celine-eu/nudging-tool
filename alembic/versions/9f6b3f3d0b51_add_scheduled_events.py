"""

Revision ID: 9f6b3f3d0b51
Revises: 3c4f4a2e7d9b
Create Date: 2026-04-15 11:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f6b3f3d0b51"
down_revision: Union[str, None] = "3c4f4a2e7d9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("community_id", sa.String(length=128), nullable=True),
        sa.Column("external_key", sa.String(length=255), nullable=True),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "facts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="pending"
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_key", name="uq_scheduled_events_external_key"),
    )
    op.create_index(
        op.f("ix_scheduled_events_user_id"),
        "scheduled_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_events_trigger_at"),
        "scheduled_events",
        ["trigger_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduled_events_trigger_at"), table_name="scheduled_events")
    op.drop_index(op.f("ix_scheduled_events_user_id"), table_name="scheduled_events")
    op.drop_table("scheduled_events")
