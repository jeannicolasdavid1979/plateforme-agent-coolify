"""Codes promo : validation et calcul de remise."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import PromoCode


def normalize(code: str) -> str:
    return (code or "").strip().upper()


def compute_discount(promo: PromoCode, amount_eur: float) -> float:
    """Remise en euros pour un montant donné, plafonnée au montant (jamais
    négatif). % pour kind='percent', euros pour kind='amount'."""
    if promo.kind == "percent":
        disc = amount_eur * (promo.value / 100.0)
    else:
        disc = promo.value
    return round(min(max(disc, 0.0), amount_eur), 2)


def validate(db: Session, code: str, scope: str, now: datetime | None = None) -> PromoCode:
    """Retourne le PromoCode valide pour ce périmètre, ou lève ValueError avec un
    message clair (inconnu, inactif, expiré, épuisé, hors périmètre)."""
    now = now or datetime.now(timezone.utc)
    promo = db.get(PromoCode, normalize(code))
    if not promo or not promo.active:
        raise ValueError("Code promo inconnu ou inactif")
    if promo.expires_at is not None:
        exp = promo.expires_at if promo.expires_at.tzinfo else promo.expires_at.replace(tzinfo=timezone.utc)
        if now > exp:
            raise ValueError("Code promo expiré")
    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        raise ValueError("Code promo épuisé")
    if promo.scope not in ("all", scope):
        raise ValueError("Code promo non valable pour ce paiement")
    return promo


def apply(db: Session, code: str, scope: str, amount_eur: float) -> tuple[str, float, float]:
    """Valide et calcule. Retourne (code_normalisé, remise_eur, montant_net).
    Lève ValueError si invalide."""
    promo = validate(db, code, scope)
    disc = compute_discount(promo, amount_eur)
    return promo.code, disc, round(amount_eur - disc, 2)
