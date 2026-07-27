"""Lire la version réellement installée sur un agent déployé.

Deux sources, aucune n'exigeant d'authentification :

1. **Le compose du service Coolify** — les images y sont ÉPINGLÉES par le
   template (`ghcr.io/nesquena/hermes-webui:0.51.92`, l'agent par digest).
   C'est la source qui fait foi : c'est littéralement ce que l'hôte lance.
2. **La page de connexion de l'agent** — elle référence ses fichiers statiques
   avec la version en paramètre (`/static/login.js?v=v0.51.92`). Utile pour
   confirmer ce qui tourne vraiment, l'agent étant seul juge.

Une version antérieure de ce module tentait d'ouvrir une session sur l'agent
pour interroger son API. C'était à la fois inutile (la version est publique)
et néfaste : les essais de mots de passe emplissaient le journal du client
d'une rafale de connexions refusées, impossible à distinguer d'une attaque.
"""
from __future__ import annotations

import logging
import re

import httpx
import yaml

logger = logging.getLogger("agent_probe")

# `/static/login.js?v=v0.51.92` sur la page de connexion, sans authentification.
_ASSET_VERSION_RE = re.compile(r"[?&]v=v?(\d+\.\d+\.\d+[\w.-]*)")

WEBUI_IMAGE_HINT = "hermes-webui"
AGENT_IMAGE_HINT = "hermes-agent"


def version_from_login_page(base_url: str, timeout: float = 8.0) -> str | None:
    """Version de l'interface, lue sur sa page de connexion. None si muette."""
    if not base_url:
        return None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(f"{base_url.rstrip('/')}/login")
        if resp.status_code != 200:
            return None
        found = _ASSET_VERSION_RE.search(resp.text)
        return found.group(1) if found else None
    except Exception as exc:  # agent éteint, DNS, TLS…
        logger.info("Page de connexion de %s illisible : %s", base_url, exc)
        return None


def _image_of(compose_yaml: str, hint: str) -> str | None:
    try:
        services = yaml.safe_load(compose_yaml)["services"]
        assert isinstance(services, dict)
    except Exception:
        return None
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        image = str(svc.get("image", ""))
        if hint in image or hint in str(name):
            return image or None
    return None


def _tag_of(image: str | None) -> str | None:
    """Version portée par une référence d'image.

    `…/hermes-webui:0.51.92` → « 0.51.92 » ; `…@sha256:2f1f…` → « sha256:2f1f… »
    abrégé, qui n'est pas un numéro de version mais identifie l'image sans
    ambiguïté ; `…:latest` → None, un tag flottant ne dit rien de ce qui tourne.
    """
    if not image:
        return None
    if "@sha256:" in image:
        return "sha256:" + image.split("@sha256:", 1)[1][:12]
    ref = image.rsplit("/", 1)[-1]
    if ":" not in ref:
        return None
    tag = ref.rsplit(":", 1)[1]
    return None if tag in ("latest", "main", "edge") else tag


FLOATING_TAGS = ("latest", "main", "edge")


def follows_floating_tag(compose_yaml: str | None,
                         hint: str = AGENT_IMAGE_HINT) -> bool:
    """L'image de ce service suit-elle un tag mouvant (`latest`, `main`…) ?

    C'est nous qui épinglons le moteur sur `latest` — le seul moyen de le
    faire avancer, faute de tags de version publiés côté image. Conséquence :
    le compose ne porte plus AUCUN numéro pour lui, et la fiche de l'agent
    affichait une ligne « Agent » vide. Savoir qu'on suit un tag mouvant
    permet de rétablir le numéro depuis la version publiée du paquet.
    """
    image = _image_of(compose_yaml, hint) if compose_yaml else None
    if not image or "@sha256:" in image:
        return False
    ref = image.rsplit("/", 1)[-1]
    tag = ref.rsplit(":", 1)[1] if ":" in ref else "latest"
    return tag in FLOATING_TAGS


def versions_from_compose(compose_yaml: str | None) -> dict[str, str | None]:
    """Versions épinglées dans le compose — ce que l'hôte lance réellement."""
    if not compose_yaml:
        return {"webui": None, "agent": None}
    return {
        "webui": _tag_of(_image_of(compose_yaml, WEBUI_IMAGE_HINT)),
        "agent": _tag_of(_image_of(compose_yaml, AGENT_IMAGE_HINT)),
    }


def detect_versions(base_url: str, compose_yaml: str | None = None,
                    timeout: float = 8.0) -> dict[str, str | None]:
    """Versions installées. Le compose fait foi ; la page de connexion
    confirme l'interface (et la corrige si le compose suit un tag flottant)."""
    found = versions_from_compose(compose_yaml)
    live = version_from_login_page(base_url, timeout=timeout)
    if live:
        found["webui"] = live
    return {k: v for k, v in found.items() if v} or {}
