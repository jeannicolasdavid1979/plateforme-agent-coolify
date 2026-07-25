"""Lire la version réellement installée sur un agent déployé.

Le template Coolify tire les images sur des tags FLOTTANTS
(`nousresearch/hermes-agent`, `ghcr.io/nesquena/hermes-webui:latest`) : le tag
ne dit donc pas quelle version tourne, et deux agents déployés le même jour
peuvent porter des versions différentes selon ce que l'hôte avait en cache.

La seule source fiable est l'agent lui-même : son interface expose sa version
derrière une authentification par mot de passe — que la plateforme possède
(`Tenant.instance_password`). On ouvre donc une session comme le ferait le
client, et on lit `/api/version`.

Prudence assumée : les points d'entrée d'un logiciel tiers peuvent changer.
Toute la sonde est tolérante — un échec rend « version inconnue », jamais une
erreur. L'interface le dit alors franchement plutôt que d'affirmer à tort
qu'un agent est à jour.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("agent_probe")

# Points d'entrée essayés dans l'ordre, avec plusieurs formes de connexion :
# on ne contrôle pas ce logiciel, on ne suppose donc pas une seule variante.
VERSION_PATHS = ("/api/version", "/api/system", "/api/system/info")
LOGIN_PATHS = ("/login", "/api/login", "/api/auth/login")
PASSWORD_FIELDS = ("password", "pass", "access_password")

_VERSION_RE = re.compile(r"\bv?(\d+\.\d+\.\d+(?:[-.\w]*)?)\b")
_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")


def _extract(payload: Any, keys: tuple[str, ...]) -> str | None:
    """Cherche une valeur de version dans un JSON de forme inconnue."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            k = str(key).lower()
            if any(want in k for want in keys) and isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text and text.lower() not in ("unknown", "none", "not detected"):
                    return text.lstrip("v")
        for value in payload.values():  # descente récursive
            found = _extract(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract(item, keys)
            if found:
                return found
    return None


def _parse_versions(payload: Any) -> dict[str, str | None]:
    """Extrait (webui, agent) d'une réponse quelconque."""
    webui = _extract(payload, ("webui", "web_ui", "ui_version"))
    agent = _extract(payload, ("agent", "core", "engine"))
    if webui is None:
        # Réponse plate du type {"version": "0.51.92"} : c'est l'interface.
        webui = _extract(payload, ("version",))
    for name, value in (("webui", webui), ("agent", agent)):
        if value and not (_VERSION_RE.fullmatch(value) or _SHA_RE.fullmatch(value)):
            # Valeur inexploitable (texte libre) — on préfère « inconnue ».
            if name == "webui":
                webui = None
            else:
                agent = None
    return {"webui": webui, "agent": agent}


def detect_versions(base_url: str, password: str | None, timeout: float = 8.0) -> dict[str, str | None]:
    """Versions installées sur cet agent, ou {} si elles restent indéterminées."""
    if not base_url:
        return {}
    base = base_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # Certaines instances laissent la version en accès libre.
            found = _read_version(client, base)
            if found:
                return found
            if not password:
                return {}
            if not _login(client, base, password):
                return {}
            return _read_version(client, base) or {}
    except Exception as exc:  # réseau, TLS, agent éteint…
        logger.info("Version de %s non lisible : %s", base_url, exc)
        return {}


def _login(client: httpx.Client, base: str, password: str) -> bool:
    """Ouvre une session sur l'interface de l'agent."""
    for path in LOGIN_PATHS:
        for field in PASSWORD_FIELDS:
            for send in ("data", "json"):
                try:
                    resp = client.post(f"{base}{path}", **{send: {field: password}})
                except Exception:
                    continue
                if resp.status_code < 400 and "login" not in str(resp.url):
                    return True
    return False


def _read_version(client: httpx.Client, base: str) -> dict[str, str | None] | None:
    for path in VERSION_PATHS:
        try:
            resp = client.get(f"{base}{path}")
        except Exception:
            continue
        if resp.status_code != 200:
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        versions = _parse_versions(payload)
        if versions.get("webui") or versions.get("agent"):
            return versions
    return None
