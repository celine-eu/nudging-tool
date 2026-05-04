"""add notification click tracking

Revision ID: bf5f5ec8a1d1
Revises: 9f6b3f3d0b51
Create Date: 2026-04-30 16:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bf5f5ec8a1d1"
down_revision: Union[str, Sequence[str], None] = "9f6b3f3d0b51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("click_action", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "click_action")
    op.drop_column("notifications", "clicked_at")
