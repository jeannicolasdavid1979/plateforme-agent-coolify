"""Intégration Stripe par **Payment Links** (approche sans SDK).

L'admin colle des liens de paiement Stripe dans les réglages. Au moment de
payer, on redirige le client vers le lien en y attachant ``client_reference_id``
(= l'id du Checkout local) : Stripe le renvoie tel quel dans l'événement
``checkout.session.completed``, ce qui permet de créditer automatiquement le bon
paiement via le webhook, sans rapprochement manuel.

Aucune clé secrète Stripe n'est requise pour rediriger ; seule la vérification
de signature du webhook utilise le *signing secret* (`STRIPE_WEBHOOK_SECRET`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import quote

from sqlalchemy.orm import Session

from .models import Setting

# Clés de réglages (table settings) pour les liens Stripe.
LINK_KEYS = {
    "deploy": "stripe_link_deploy",
    "hosting_monthly": "stripe_link_hosting_monthly",
    "hosting_annual": "stripe_link_hosting_annual",
}
TOPUP_LINKS_KEY = "stripe_links_topup"  # JSON : {"10": "https://buy.stripe.com/...", ...}


def _get(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return (row.value or "").strip() if row else ""


def get_links(db: Session) -> dict:
    """Tous les liens configurés, tels quels (pour l'admin)."""
    topup_raw = _get(db, TOPUP_LINKS_KEY)
    try:
        topup = json.loads(topup_raw) if topup_raw else {}
    except json.JSONDecodeError:
        topup = {}
    return {
        "deploy": _get(db, LINK_KEYS["deploy"]),
        "hosting_monthly": _get(db, LINK_KEYS["hosting_monthly"]),
        "hosting_annual": _get(db, LINK_KEYS["hosting_annual"]),
        "topup": topup,  # {montant(str): url}
    }


def _link_for(db: Session, kind: str, amount_eur: float | None, plan: str | None) -> str:
    links = get_links(db)
    if kind == "deploy":
        return links["deploy"]
    if kind == "hosting":
        return links["hosting_annual"] if plan == "annual" else links["hosting_monthly"]
    if kind == "topup" and amount_eur is not None:
        # clé indexée sur le montant entier si possible (« 10 »), sinon brut
        key = str(int(amount_eur)) if float(amount_eur).is_integer() else str(amount_eur)
        return links["topup"].get(key, "")
    return ""


def checkout_redirect_url(
    db: Session,
    *,
    checkout_id: str,
    kind: str,
    amount_eur: float | None = None,
    plan: str | None = None,
    email: str | None = None,
) -> str | None:
    """URL Stripe à ouvrir pour ce Checkout, ou ``None`` si aucun lien n'est
    configuré (l'appelant retombe alors sur la page de paiement simulée)."""
    base = _link_for(db, kind, amount_eur, plan)
    if not base:
        return None
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}client_reference_id={quote(checkout_id)}"
    if email:
        url += f"&prefilled_email={quote(email)}"
    return url


def verify_signature(payload: bytes, sig_header: str, secret: str, tolerance: int = 300) -> bool:
    """Vérifie l'en-tête ``Stripe-Signature`` (schéma v1 = HMAC-SHA256 de
    ``{timestamp}.{payload}``). Sans secret configuré, la vérification est
    ignorée (utile en développement) — à ne pas laisser en production."""
    if not secret:
        return True
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(","))
        ts = parts["t"]
        sent = parts["v1"]
    except (ValueError, KeyError):
        return False
    signed = f"{ts}.{payload.decode()}".encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sent):
        return False
    try:
        if tolerance and abs(time.time() - int(ts)) > tolerance:
            return False
    except ValueError:
        return False
    return True
