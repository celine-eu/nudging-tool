"""

Revision ID: 3c4f4a2e7d9b
Revises: 24f5a9d7a3c1
Create Date: 2026-02-24 11:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3c4f4a2e7d9b"
down_revision: Union[str, None] = "24f5a9d7a3c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rule_overrides",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("community_id", sa.String(length=128), nullable=False),
        sa.Column("enabled_override", sa.Boolean(), nullable=True),
        sa.Column("definition_override", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_unique_constraint("uq_rule_override", "rule_overrides", ["rule_id", "community_id"])
    op.create_index(op.f("ix_rule_overrides_rule_id"), "rule_overrides", ["rule_id"], unique=False)
    op.create_index(op.f("ix_rule_overrides_community_id"), "rule_overrides", ["community_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_rule_overrides_community_id"), table_name="rule_overrides")
    op.drop_index(op.f("ix_rule_overrides_rule_id"), table_name="rule_overrides")
    op.drop_constraint("uq_rule_override", "rule_overrides", type_="unique")
    op.drop_table("rule_overrides")
