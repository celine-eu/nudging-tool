from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    family: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)

    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    templates: Mapped[list["Template"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    lang: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    title_jinja: Mapped[str] = mapped_column(Text, nullable=False)
    body_jinja: Mapped[str] = mapped_column(Text, nullable=False)

    rule: Mapped["Rule"] = relationship(back_populates="templates")
    __table_args__ = (
        UniqueConstraint("rule_id", "lang", name="uq_template_rule_lang"),
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid4().hex
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    community_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lang: Mapped[str] = mapped_column(String(64), default="en", nullable=False)

    channel_web: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    channel_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    channel_telegram: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    channel_whatsapp: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    max_per_day: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    consents: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    __table_args__ = (
        UniqueConstraint("user_id", "community_id", name="uq_user_pref_user_community"),
    )

class NudgeLog(Base):
    """Engine audit log. One row per engine evaluation outcome (created, suppressed, etc.).
    Not user-facing. read_at / deleted_at do NOT belong here.
    """

    __tablename__ = "nudges_log"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    community_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # EngineResultStatus value: created | not_triggered | missing_facts |
    #                           unknown_scenario | suppressed_dedup
    status: Mapped[str] = mapped_column(String(30), default="created", nullable=False)

    # Audit payload: scenario, facts_version, facts, details
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (UniqueConstraint("dedup_key", name="uq_nudges_dedup_key"),)

    notification: Mapped["Notification | None"] = relationship(
        back_populates="nudge_log", uselist=False
    )


class Notification(Base):
    """User-facing notification. Created only when the engine produces a CREATED nudge
    and it passes orchestration. Tracks delivery status and user lifecycle (read, deleted).
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )  # own uuid, not nudge_log.id

    # Traceability back to the engine audit row
    nudge_log_id: Mapped[str] = mapped_column(
        ForeignKey("nudges_log.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Denormalised from NudgeLog / Rule for query convenience
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Rule metadata (flat – avoids joins on hot read path)
    family: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # informative | opportunity | alert
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # info | warning | critical

    # Rendered content
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Delivery status: pending | sent | suppressed | failed
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)

    # User lifecycle
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    nudge_log: Mapped["NudgeLog | None"] = relationship(back_populates="notification")

    __table_args__ = (
        UniqueConstraint("nudge_log_id", name="uq_notification_nudge_log"),
    )


class DeliveryLog(Base):
    __tablename__ = "delivery_log"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nudge_id: Mapped[str] = mapped_column(String(64), nullable=False)

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WebPushSubscription(Base):
    __tablename__ = "web_push_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "endpoint",
            "community_id",
            name="uq_webpush_user_endpoint_community",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    community_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
