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
