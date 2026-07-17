"""Intégration Stripe (sans SDK) — trois niveaux, du plus automatique au repli :

1. **API Checkout Sessions** (`STRIPE_SECRET_KEY` configurée) : la session de
   paiement est créée à la volée avec le montant EXACT calculé par la
   plateforme (prix admin, frais de service, code promo déduit) via
   ``price_data`` inline. Rien à créer ni synchroniser côté Stripe : tout
   changement dans l'admin s'applique immédiatement. Les abonnements mensuels
   utilisent ``mode=subscription`` (prélèvement récurrent automatique).
2. **Payment Links** collés dans l'admin (sans clé secrète) : redirection vers
   le lien avec ``client_reference_id``.
3. **Page de paiement simulée** : sans clé ni lien — pour tester.

Dans les trois cas, ``client_reference_id`` = l'id du Checkout local ; Stripe le
renvoie dans ``checkout.session.completed`` et le webhook crédite le bon compte.
La vérification de signature du webhook utilise `STRIPE_WEBHOOK_SECRET`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Setting

_log = logging.getLogger("stripe")

_API_SESSIONS = "https://api.stripe.com/v1/checkout/sessions"
# Montant minimum accepté par Stripe (~0,50 € en EUR) : en-dessous, on retombe
# sur la page simulée plutôt que d'essuyer un refus d'API.
_MIN_CENTS = 50


def api_enabled() -> bool:
    """Mode API utilisable : clé présente ET du bon type (secrète sk_/rk_)."""
    key = get_settings().stripe_secret_key
    return bool(key) and key.startswith(("sk_", "rk_"))


def api_status() -> dict:
    """Diagnostic complet de la clé API pour l'admin : absente, mauvais type
    (clé publique pk_ collée à la place de la secrète — erreur classique),
    refusée par Stripe, ou valide (live/test). Vérification EN DIRECT contre
    l'API quand la clé a la bonne forme."""
    key = get_settings().stripe_secret_key
    if not key:
        return {"enabled": False, "state": "absent",
                "detail": "STRIPE_SECRET_KEY absente — mode liens/simulation"}
    if not key.startswith(("sk_", "rk_")):
        return {"enabled": False, "state": "wrong_key_type",
                "detail": "La clé fournie n'est PAS une clé secrète (elle commence "
                          "par « " + key[:8] + "… ») : c'est probablement la clé "
                          "PUBLIQUE pk_. Mettez la clé SECRÈTE sk_live_… "
                          "(Stripe → Developers → API keys)"}
    try:
        resp = httpx.get("https://api.stripe.com/v1/balance", auth=(key, ""), timeout=10)
        if resp.status_code == 200:
            live = "_live" in key[:8]
            return {"enabled": True, "state": "ok",
                    "detail": f"clé {'LIVE' if live else 'TEST'} valide — sessions générées par l'API"}
        if resp.status_code == 401:
            return {"enabled": False, "state": "invalid",
                    "detail": "clé refusée par Stripe (401) — valeur erronée ou révoquée"}
        return {"enabled": True, "state": "unexpected",
                "detail": f"réponse Stripe inattendue ({resp.status_code})"}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "state": "unreachable",
                "detail": f"API Stripe injoignable ({exc.__class__.__name__})"}


def create_checkout_session(
    *,
    checkout_id: str,
    product_name: str,
    amount_eur: float,
    email: str | None = None,
    recurring_monthly: bool = False,
) -> str | None:
    """Crée une Checkout Session Stripe avec le montant fourni (price_data
    inline) et retourne son ``url``. ``None`` si l'API n'est pas configurée,
    si le montant est sous le minimum Stripe, ou en cas d'erreur (l'appelant
    retombe alors sur les Payment Links puis la page simulée)."""
    s = get_settings()
    # Une clé absente OU du mauvais type (pk_ publique) → pas d'appel API :
    # repli immédiat sur les liens, sans latence d'un 401 garanti.
    if not api_enabled():
        return None
    cents = int(round(amount_eur * 100))
    if cents < _MIN_CENTS:
        return None
    base = _public_base()
    data = {
        "mode": "subscription" if recurring_monthly else "payment",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(cents),
        "line_items[0][price_data][product_data][name]": product_name,
        "line_items[0][quantity]": "1",
        "client_reference_id": checkout_id,
        "success_url": f"{base}/?paiement=ok",
        "cancel_url": f"{base}/?paiement=annule",
    }
    if recurring_monthly:
        data["line_items[0][price_data][recurring][interval]"] = "month"
    if email:
        data["customer_email"] = email
    try:
        resp = httpx.post(
            _API_SESSIONS, data=data, auth=(s.stripe_secret_key, ""), timeout=20
        )
        resp.raise_for_status()
        url = resp.json().get("url")
        if url:
            return url
        _log.error("Session Stripe créée mais sans URL — repli sur les liens")
    except Exception as exc:  # noqa: BLE001 — tout échec bascule sur le repli
        _log.error("Création de session Stripe échouée : %s — repli sur les liens", exc)
    return None


def _public_base() -> str:
    s = get_settings()
    return (s.public_base_url or s.site_url or "").rstrip("/")

# Clés de réglages (table settings) pour les liens Stripe.
LINK_KEYS = {
    "deploy": "stripe_link_deploy",
    "hosting_manual": "stripe_link_hosting_manual",   # sans engagement (29€)
    "hosting_sub": "stripe_link_hosting_sub",         # abonnement auto (19€/mois, mode subscription)
    "hosting_annual": "stripe_link_hosting_annual",   # 12 mois payés en une fois (209€)
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
        "hosting_manual": _get(db, LINK_KEYS["hosting_manual"]),
        "hosting_sub": _get(db, LINK_KEYS["hosting_sub"]),
        "hosting_annual": _get(db, LINK_KEYS["hosting_annual"]),
        "topup": topup,  # {montant(str): url}
    }


def _link_for(db: Session, kind: str, amount_eur: float | None, plan: str | None) -> str:
    links = get_links(db)
    if kind == "deploy":
        return links["deploy"]
    if kind == "hosting":
        if plan == "sub_annual":
            return links["hosting_annual"]
        if plan == "sub_monthly":
            return links["hosting_sub"]
        return links["hosting_manual"]
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
