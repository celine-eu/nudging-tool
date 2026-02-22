"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-02-22

Full schema for a clean database. Includes all tables as of the initial
Alembic setup, including the notification lifecycle fields (read_at,
deleted_at) on nudges_log that were added prior to this migration setup.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------
    op.create_table(
        "rules",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("family", sa.String(50), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ------------------------------------------------------------------
    # templates
    # ------------------------------------------------------------------
    op.create_table(
        "templates",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "rule_id",
            sa.String(64),
            sa.ForeignKey("rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lang", sa.String(10), nullable=False, server_default="en"),
        sa.Column("title_jinja", sa.Text(), nullable=False),
        sa.Column("body_jinja", sa.Text(), nullable=False),
        sa.UniqueConstraint("rule_id", "lang", name="uq_template_rule_lang"),
    )

    # ------------------------------------------------------------------
    # user_preferences
    # ------------------------------------------------------------------
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("lang", sa.String(64), nullable=False, server_default="en"),
        sa.Column("channel_web", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("channel_email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channel_telegram", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channel_whatsapp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("telegram_chat_id", sa.String(64), nullable=True),
        sa.Column("whatsapp_phone", sa.String(64), nullable=True),
        sa.Column("max_per_day", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("consents", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ------------------------------------------------------------------
    # nudges_log  (includes notification lifecycle fields from the start)
    # ------------------------------------------------------------------
    op.create_table(
        "nudges_log",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("dedup_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="created"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # notification lifecycle
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("dedup_key", name="uq_nudges_dedup_key"),
    )
    op.create_index(
        "ix_nudges_log_user_deleted",
        "nudges_log",
        ["user_id", "deleted_at"],
    )

    # ------------------------------------------------------------------
    # delivery_log
    # ------------------------------------------------------------------
    op.create_table(
        "delivery_log",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("nudge_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )

    # ------------------------------------------------------------------
    # web_push_subscriptions
    # ------------------------------------------------------------------
    op.create_table(
        "web_push_subscriptions",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("community_id", sa.String(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "endpoint", name="uq_webpush_user_endpoint"),
    )
    op.create_index("ix_web_push_user_id", "web_push_subscriptions", ["user_id"])
    op.create_index("ix_web_push_community_id", "web_push_subscriptions", ["community_id"])


def downgrade() -> None:
    op.drop_table("web_push_subscriptions")
    op.drop_index("ix_nudges_log_user_deleted", table_name="nudges_log")
    op.drop_table("nudges_log")
    op.drop_table("delivery_log")
    op.drop_table("user_preferences")
    op.drop_table("templates")
    op.drop_table("rules")
