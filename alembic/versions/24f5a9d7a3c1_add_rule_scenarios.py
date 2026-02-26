"""

Revision ID: 24f5a9d7a3c1
Revises: 73a5308230a8
Create Date: 2026-02-24 10:45:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "24f5a9d7a3c1"
down_revision: Union[str, None] = "73a5308230a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rules",
        sa.Column(
            "scenarios",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("rules", "scenarios")
