"""Database models — users, tenants (agents), provisioning jobs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    tenants: Mapped[list["Tenant"]] = relationship(back_populates="owner")


class Tenant(Base):
    """An agent deployed (or being deployed) for a user."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128))
    subdomain: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(128), default="openai/gpt-4o")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="awaiting_payment")
    # "awaiting_payment" → "pending" → "deploying" → "running" | "failed"
    balance_eur: Mapped[float] = mapped_column(Float, default=0.0)

    coolify_service_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    instance_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instance_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Clé OpenRouter dédiée (créée via la clé maître de provisioning)
    openrouter_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    openrouter_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    owner: Mapped[User] = relationship(back_populates="tenants")
    jobs: Mapped[list["ProvisioningJob"]] = relationship(back_populates="tenant")


class Setting(Base):
    """Variables business modifiables par l'admin (prix, recharges, crédit)
    et petites données de plateforme (cache du compose template Coolify)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Checkout(Base):
    """Session de paiement simulée (même cycle de vie que Stripe Checkout)."""

    __tablename__ = "checkouts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(16))  # "deploy" | "topup"
    amount_eur: Mapped[float] = mapped_column(Float)
    credit_eur: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # "pending" | "paid"
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ProvisioningJob(Base):
    __tablename__ = "provisioning_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    tenant: Mapped[Tenant] = relationship(back_populates="jobs")
