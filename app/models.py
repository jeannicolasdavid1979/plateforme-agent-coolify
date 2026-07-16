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

    # Vérification d'e-mail & réinitialisation de mot de passe
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reset_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reset_expires: Mapped[datetime | None] = mapped_column(nullable=True)

    # RGPD — preuve du consentement aux CGV/politique de confidentialité,
    # recueilli à l'inscription (base légale : exécution du contrat + consentement).
    consent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    consent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

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

    # Abonnement d'hébergement (revenu récurrent). L'agent doit être « payé »
    # jusqu'à hosting_paid_until ; passé cette date sans renouvellement, il est
    # suspendu (conteneurs arrêtés) puis supprimé après la période de rétention.
    # none | manual (sans engagement 29€) | sub_monthly (abo auto 19€) | sub_annual (209€/an)
    hosting_plan: Mapped[str] = mapped_column(String(16), default="none")
    hosting_paid_until: Mapped[datetime | None] = mapped_column(nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Abonnement Stripe auto-débité (mode auto) : sert à prolonger sur invoice.paid
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

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
    kind: Mapped[str] = mapped_column(String(16))  # "deploy" | "topup" | "hosting"
    # Pour un checkout d'hébergement : manual | sub_monthly | sub_annual
    plan: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amount_eur: Mapped[float] = mapped_column(Float)          # à payer (remise déduite)
    credit_eur: Mapped[float] = mapped_column(Float, default=0.0)
    promo_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discount_eur: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # "pending" | "paid"
    created_at: Mapped[datetime] = mapped_column(default=_now)


class PromoCode(Base):
    """Code promo appliquant une remise (en % ou en montant) sur un paiement."""

    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)  # stocké en MAJUSCULES
    kind: Mapped[str] = mapped_column(String(8))     # "percent" | "amount"
    value: Mapped[float] = mapped_column(Float)      # 20 => -20% ou -20 €
    scope: Mapped[str] = mapped_column(String(16), default="all")  # all|deploy|topup|hosting
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = illimité
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
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
