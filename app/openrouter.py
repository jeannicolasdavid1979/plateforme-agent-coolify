"""Clés API OpenRouter dédiées par agent, via l'API de provisioning.

La clé maître (OPENROUTER_PROVISIONING_KEY, créée dans le dashboard
OpenRouter → Settings → Provisioning Keys) sert UNIQUEMENT à fabriquer des
clés client : une par agent, nommée `hermes-<sous-domaine>`, plafonnée au
crédit payé. Chaque recharge relève le plafond de la clé. On sait ainsi
exactement qui consomme quoi — la clé partagée n'est plus qu'un repli si le
provisioning n'est pas configuré.

Les plafonds OpenRouter sont en dollars US ; la conversion depuis nos euros
utilise EUR_USD_RATE (défaut 1.0 — prudent, le crédit vaut alors un peu
moins que le montant payé quand l'euro est au-dessus du dollar).
"""
from __future__ import annotations

import logging

import httpx

from .config import get_settings

logger = logging.getLogger("openrouter")

BASE_URL = "https://openrouter.ai/api/v1/keys"


class OpenRouterKeyError(RuntimeError):
    pass


class OpenRouterKeys:
    def __init__(self, provisioning_key: str, timeout: int = 30):
        self._headers = {
            "Authorization": f"Bearer {provisioning_key}",
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def _request(self, method: str, path: str = "", json_data: dict | None = None) -> dict:
        resp = httpx.request(
            method,
            f"{BASE_URL}{path}",
            json=json_data,
            headers=self._headers,
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 201):
            raise OpenRouterKeyError(
                f"{method} /keys{path} → {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json() if resp.text else {}

    def create(self, name: str, limit_usd: float) -> tuple[str, str]:
        """Crée une clé nommée et plafonnée. Retourne (clé, hash).

        La clé complète (sk-or-v1-…) n'est renvoyée qu'à la création ; le
        hash sert ensuite d'identifiant pour lire/modifier/supprimer.
        """
        data = self._request("POST", json_data={"name": name, "limit": round(limit_usd, 2)})
        key = data.get("key")
        key_hash = (data.get("data") or {}).get("hash")
        if not key or not key_hash:
            raise OpenRouterKeyError(f"réponse inattendue à la création : {str(data)[:300]}")
        logger.info("Clé OpenRouter %s créée (limite %.2f $)", name, limit_usd)
        return key, key_hash

    def info(self, key_hash: str) -> dict:
        """Limite et consommation actuelles : {'limit': float, 'usage': float, ...}."""
        return self._request("GET", f"/{key_hash}").get("data") or {}

    def add_credit(self, key_hash: str, amount_usd: float) -> float:
        """Relève le plafond de la clé du montant rechargé. Retourne le nouveau plafond."""
        current = float(self.info(key_hash).get("limit") or 0.0)
        new_limit = round(current + amount_usd, 2)
        self._request("PATCH", f"/{key_hash}", {"limit": new_limit})
        logger.info("Clé %s : plafond %.2f → %.2f $", key_hash[:12], current, new_limit)
        return new_limit

    def delete(self, key_hash: str) -> bool:
        try:
            self._request("DELETE", f"/{key_hash}")
            return True
        except OpenRouterKeyError as exc:
            logger.warning("Suppression de clé refusée : %s", exc)
            return False


def get_keys_client() -> OpenRouterKeys | None:
    """Client de provisioning, ou None si la clé maître n'est pas configurée
    (la plateforme retombe alors sur la clé OpenRouter partagée)."""
    key = get_settings().openrouter_provisioning_key
    return OpenRouterKeys(key) if key else None


# ── Supervision du compte OpenRouter (crédits, statut, top modèles) ──
#
# Trois sources, toutes tolérantes aux pannes (jamais d'exception qui remonte) :
#  - crédits  : GET /api/v1/credits avec la clé maître (repli : clé partagée)
#  - statut   : la réponse HTTP de l'API sert de sonde (toute réponse = API
#               joignable), complétée par le flux d'incidents officiel
#               status.openrouter.ai/incidents.rss (page OnlineOrNot — il n'y a
#               pas de summary.json, vérifié)
#  - modèles  : GET /api/v1/models?category=programming — la catégorie
#               « agentic » n'existe pas chez OpenRouter ; « programming » est
#               son classement d'usage le plus proche (modèles outillés, tri
#               par usage réel dans les tâches de code/agents)

API_BASE = "https://openrouter.ai/api/v1"
STATUS_RSS = "https://status.openrouter.ai/incidents.rss"
AGENTIC_CATEGORY = "programming"

_SNAPSHOT_TTL = 300  # secondes — on ne martèle pas l'API à chaque ouverture d'admin
_snapshot_cache: dict = {}


def _fetch_credits() -> dict:
    """Crédit du compte : {'configured', 'total', 'usage', 'remaining', 'level', 'error'}.

    L'appel sert aussi de sonde API : toute réponse HTTP (même 401) prouve que
    l'API OpenRouter répond — le résultat 'reachable' est réutilisé par le statut.
    """
    s = get_settings()
    key = s.openrouter_provisioning_key or s.openrouter_api_key
    out = {"configured": bool(key), "total": None, "usage": None,
           "remaining": None, "level": "unknown", "error": None, "reachable": None}
    try:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        resp = httpx.get(f"{API_BASE}/credits", headers=headers, timeout=8)
        out["reachable"] = True
        if resp.status_code == 200:
            data = resp.json().get("data") or {}
            total = float(data.get("total_credits") or 0.0)
            usage = float(data.get("total_usage") or 0.0)
            remaining = round(total - usage, 2)
            out.update(total=round(total, 2), usage=round(usage, 2), remaining=remaining,
                       level="critical" if remaining < 3 else
                             "warning" if remaining < 10 else "ok")
        elif resp.status_code in (401, 403):
            out["error"] = ("Clé OpenRouter absente ou refusée — renseignez "
                            "OPENROUTER_PROVISIONING_KEY (ou OPENROUTER_API_KEY).")
        else:
            out["error"] = f"Réponse inattendue de l'API ({resp.status_code})."
    except Exception as exc:  # réseau coupé, timeout…
        out["reachable"] = False
        out["error"] = f"API OpenRouter injoignable : {type(exc).__name__}"
    return out


def _fetch_incident() -> dict | None:
    """Dernier incident du flux RSS officiel, ou None si le flux est muet.

    Un incident est « en cours » si son dernier point d'étape n'est ni
    COMPLETED ni RESOLVED et qu'il date de moins de 48 h.
    """
    import re
    import xml.etree.ElementTree as ET
    from datetime import datetime, timedelta, timezone
    from email.utils import parsedate_to_datetime

    try:
        resp = httpx.get(STATUS_RSS, timeout=8)
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.text)
        item = root.find("./channel/item")
        if item is None:
            return None
        title = (item.findtext("title") or "").strip()
        desc = item.findtext("description") or ""
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        m = re.search(r"<strong>([A-Z ]+)</strong>", desc)
        state = (m.group(1).strip() if m else "").upper()
        when = None
        try:
            when = parsedate_to_datetime(pub)
        except Exception:
            pass
        recent = bool(when and datetime.now(timezone.utc) - when < timedelta(hours=48))
        ongoing = recent and state not in ("COMPLETED", "RESOLVED")
        return {"title": title, "state": state or "?", "date": pub,
                "url": ("https://" + link) if link and not link.startswith("http") else link,
                "ongoing": ongoing}
    except Exception:
        return None


def _fetch_top_models() -> list[dict]:
    """Top 10 des modèles agentiques (classement d'usage `programming`),
    restreint aux modèles qui supportent l'appel d'outils (tools)."""
    try:
        resp = httpx.get(f"{API_BASE}/models", params={"category": AGENTIC_CATEGORY},
                         timeout=10)
        if resp.status_code != 200:
            return []
        models = resp.json().get("data") or []
        out = []
        for m in models:
            if "tools" not in (m.get("supported_parameters") or []):
                continue
            pricing = m.get("pricing") or {}

            def per_m(v):
                try:
                    return round(float(v) * 1_000_000, 2)
                except (TypeError, ValueError):
                    return None

            out.append({
                "rank": len(out) + 1,
                "id": m.get("id"),
                "name": m.get("name"),
                "context": m.get("context_length"),
                "prompt_usd_m": per_m(pricing.get("prompt")),
                "completion_usd_m": per_m(pricing.get("completion")),
            })
            if len(out) == 10:
                break
        return out
    except Exception:
        return []


def account_snapshot(force: bool = False) -> dict:
    """Photo complète du compte OpenRouter pour l'admin, cachée 5 minutes."""
    import time
    from datetime import datetime, timezone

    now = time.time()
    hit = _snapshot_cache.get("snap")
    if hit and not force and now - hit[0] < _SNAPSHOT_TTL:
        return hit[1]

    credits = _fetch_credits()
    incident = _fetch_incident()
    reachable = credits.pop("reachable")

    if reachable is False:
        level, message = "down", "L'API OpenRouter ne répond pas — vos agents peuvent être impactés."
    elif incident and incident["ongoing"]:
        level, message = "incident", f"Incident OpenRouter en cours : {incident['title']}"
    elif reachable is None:
        level, message = "unknown", "Statut OpenRouter indéterminé."
    else:
        level, message = "ok", "API OpenRouter opérationnelle."

    snap = {
        "credits": credits,
        "status": {"level": level, "message": message, "incident": incident},
        "models": _fetch_top_models(),
        "category": AGENTIC_CATEGORY,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _snapshot_cache["snap"] = (now, snap)
    return snap
