from __future__ import annotations
from datetime import datetime

from sqlalchemy import (String, DateTime, Boolean, Integer, ForeignKey, Text, JSON, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    family: Mapped[str] = mapped_column(String(50), nullable=False)     # energy/price/...
    type: Mapped[str] = mapped_column(String(20), nullable=False)       # informative/opportunity/alert
    severity: Mapped[str] = mapped_column(String(20), nullable=False)   # info/warning/critical

    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    templates: Mapped[list["Template"]] = relationship(back_populates="rule", cascade="all, delete-orphan")
    #TODO: dedup rule

class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), nullable=False)

    lang: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    title_jinja: Mapped[str] = mapped_column(Text, nullable=False)
    body_jinja: Mapped[str] = mapped_column(Text, nullable=False)

    rule: Mapped["Rule"] = relationship(back_populates="templates")
    __table_args__ = (UniqueConstraint("rule_id", "lang", name="uq_template_rule_lang"),)

class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    lang: Mapped[str] = mapped_column(String(64), default="en", nullable=False)

    channel_web: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    channel_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    channel_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    channel_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    max_per_day: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    consents: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class NudgeLog(Base):
    __tablename__ = "nudges_log"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(30), default="created", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("dedup_key", name="uq_nudges_dedup_key"),)

class DeliveryLog(Base):
    __tablename__ = "delivery_log"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nudge_id: Mapped[str] = mapped_column(String(64), nullable=False)

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebPushSubscription(Base):
    __tablename__ = "web_push_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", name="uq_webpush_user_endpoint"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)  # uuid
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    community_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)