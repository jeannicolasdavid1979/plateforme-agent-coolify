"""Coolify Service API client — deploys agents as native Coolify Services.

Uses the "hermes-agent-with-webui" template (same as manual deploy from
the Coolify dashboard). Each agent gets two containers:
  - hermes-agent: the LLM brain (nousresearch/hermes-agent)
  - hermes-webui: the web interface (ghcr.io/nesquena/hermes-webui)

They communicate via shared Docker volumes — the webui can see the agent
code without needing pip install. This is the proven, working setup.
"""
from __future__ import annotations

import logging

import httpx

from .config import get_settings

logger = logging.getLogger("coolify")

SERVICE_TYPE = "hermes-agent-with-webui"


class CoolifyClient:
    """Thin REST client for the Coolify v4 Services API."""

    def __init__(self, timeout: int = 60):
        settings = get_settings()
        self.base_url = settings.coolify_api_url.rstrip("/")
        self.api_token = settings.coolify_api_token
        self.project_uuid = settings.coolify_project_uuid
        self.environment_name = settings.coolify_environment
        self.server_uuid = settings.coolify_server_uuid
        self.destination_uuid = settings.coolify_destination_uuid
        self.timeout = timeout

        self._headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, path: str, json_data: dict | None = None) -> dict:
        resp = httpx.post(
            f"{self.base_url}{path}",
            json=json_data or {},
            headers=self._headers,
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"POST {path} → {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.text else {}

    def _patch(self, path: str, json_data: dict | None = None) -> dict:
        resp = httpx.patch(
            f"{self.base_url}{path}",
            json=json_data or {},
            headers=self._headers,
            timeout=self.timeout,
        )
        # Coolify returns 200, 201, or 202 for successful PATCHes
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"PATCH {path} → {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.text else {}

    def _get(self, path: str) -> dict | list:
        resp = httpx.get(f"{self.base_url}{path}", headers=self._headers, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"GET {path} → {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.text else {}

    def _delete(self, path: str) -> bool:
        resp = httpx.delete(f"{self.base_url}{path}", headers=self._headers, timeout=self.timeout)
        return resp.status_code in (200, 202)

    # ── Create / configure / start (une méthode par étape du journal) ─

    def create_service(self, name: str) -> str:
        """Create a Coolify Service from the hermes-agent-with-webui template."""
        created = self._post("/api/v1/services", {
            "project_uuid": self.project_uuid,
            "environment_name": self.environment_name,
            "server_uuid": self.server_uuid,
            "destination_uuid": self.destination_uuid,
            "type": SERVICE_TYPE,
            "name": name,
        })
        svc_uuid = created["uuid"]
        logger.info("Service %s created (uuid=%s)", name, svc_uuid)
        return svc_uuid

    def create_service_from_compose(self, name: str, compose_yaml: str) -> str | None:
        """Crée le service avec NOTRE compose — le domaine du client y est déjà
        inscrit, donc le PREMIER parse de Coolify l'enregistre directement.
        (Modifier le domaine après coup est refusé par l'API : le fqdn se fige
        au premier parse.) Retourne None si l'API refuse ce mode de création."""
        import base64
        try:
            created = self._post("/api/v1/services", {
                "project_uuid": self.project_uuid,
                "environment_name": self.environment_name,
                "server_uuid": self.server_uuid,
                "destination_uuid": self.destination_uuid,
                "name": name,
                "docker_compose_raw": base64.b64encode(compose_yaml.encode("utf-8")).decode("ascii"),
            })
            uuid = created.get("uuid")
            logger.info("Service %s créé depuis compose personnalisé (uuid=%s)", name, uuid)
            return uuid
        except RuntimeError as exc:
            logger.warning("Création par compose refusée : %s", exc)
            return None

    def get_service(self, svc_uuid: str) -> dict:
        data = self._get(f"/api/v1/services/{svc_uuid}")
        return data if isinstance(data, dict) else {}

    def service_fqdns(self, svc_uuid: str) -> list[str]:
        """Domaines réellement enregistrés par Coolify pour ce service."""
        try:
            apps = self.get_service(svc_uuid).get("applications") or []
            return [a.get("fqdn") or "" for a in apps if a.get("fqdn")]
        except Exception:
            return []

    def patch_service_fqdn(self, svc_uuid: str, service_name: str, fqdn: str) -> bool:
        try:
            self._patch(f"/api/v1/services/{svc_uuid}", {
                "service_name": service_name,
                "fqdn": fqdn,
            })
            return True
        except RuntimeError as exc:
            logger.warning("FQDN patch refusé : %s", exc)
            return False

    def trigger_deploy(self, svc_uuid: str) -> bool:
        """Force un déploiement complet — contrairement à /start, Coolify
        re-parse le compose et régénère les labels Traefik (donc le domaine)."""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/v1/deploy",
                params={"uuid": svc_uuid},
                headers=self._headers,
                timeout=self.timeout,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("trigger_deploy: %s", exc)
            return False

    def start_service(self, svc_uuid: str) -> bool:
        try:
            self._post(f"/api/v1/services/{svc_uuid}/start")
            return True
        except RuntimeError as exc:
            logger.warning("start refusé : %s", exc)
            return False

    def restart_service(self, svc_uuid: str) -> bool:
        try:
            self._post(f"/api/v1/services/{svc_uuid}/restart")
            return True
        except RuntimeError as exc:
            logger.warning("restart refusé : %s", exc)
            return False

    def wait_running(self, svc_uuid: str, timeout: int = 180) -> str:
        """Poll le statut Coolify jusqu'à 'running' (ou expiration).

        Retourne le dernier statut vu (ex. 'running:healthy', 'exited').
        Un 'exited' précoce est normal pendant le pull/démarrage — on
        continue de guetter jusqu'au bout du délai.
        """
        import time
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = self.service_status(svc_uuid) or last
            if last and "running" in last:
                return last
            time.sleep(5)
        return last

    def get_password(self, svc_uuid: str) -> str | None:
        return self._get_env_value(svc_uuid, "SERVICE_PASSWORD_HERMESWEBUI")

    # ── Compose raw (source des labels Traefik et des entrypoints) ────

    def get_compose_raw(self, svc_uuid: str) -> str | None:
        """Le docker-compose du service, décodé si Coolify le renvoie en base64."""
        import base64
        raw = self.get_service(svc_uuid).get("docker_compose_raw")
        if not raw:
            return None
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
            # Un YAML plausible contient forcément 'services'
            if "services" in decoded:
                return decoded
        except Exception:
            pass
        return raw

    def update_compose_raw(self, svc_uuid: str, compose_yaml: str) -> bool:
        """PATCH le compose du service — essaie en base64 (format du POST de
        création) puis en clair si Coolify refuse."""
        import base64
        encoded = base64.b64encode(compose_yaml.encode("utf-8")).decode("ascii")
        for payload in (encoded, compose_yaml):
            try:
                self._patch(f"/api/v1/services/{svc_uuid}", {"docker_compose_raw": payload})
                return True
            except RuntimeError as exc:
                logger.warning("update_compose_raw: %s", exc)
        return False

    # ── Environment variables ────────────────────────────────────────

    @staticmethod
    def _sanitize_env_value(value: str) -> str:
        """Le .env généré par Coolify entoure les valeurs de quotes simples
        SANS échapper celles contenues dedans : une apostrophe dans la valeur
        casse le parsing du .env et les conteneurs ne démarrent plus
        (statut exited). On remplace par l'apostrophe typographique, et les
        retours à la ligne par des espaces (non plus supportés par .env)."""
        return value.replace("'", "’").replace("\r\n", " ").replace("\n", " ")

    def set_env(self, svc_uuid: str, key: str, value: str) -> None:
        self._set_env(svc_uuid, key, self._sanitize_env_value(value))

    def _set_env(self, svc_uuid: str, key: str, value: str) -> None:
        """Set or update an environment variable on a service."""
        try:
            self._post(f"/api/v1/services/{svc_uuid}/envs", {
                "key": key, "value": value, "is_literal": True,
            })
        except RuntimeError:
            # Variable already exists — update via PATCH
            self._patch(f"/api/v1/services/{svc_uuid}/envs", {
                "key": key, "value": value, "is_literal": True,
            })

    def _get_env_value(self, svc_uuid: str, key: str) -> str | None:
        """Retrieve the value of an environment variable."""
        try:
            envs = self._get(f"/api/v1/services/{svc_uuid}/envs")
            for env in envs:
                if env.get("key") == key and not env.get("is_preview"):
                    return env.get("real_value") or env.get("value", "")
        except Exception:
            pass
        return None

    # ── Delete a service ─────────────────────────────────────────────

    def delete_service(self, svc_uuid: str) -> bool:
        return self._delete(f"/api/v1/services/{svc_uuid}")

    # ── Service status ───────────────────────────────────────────────

    def service_status(self, svc_uuid: str) -> str | None:
        """Return Coolify's status string for a service (e.g. 'running:healthy')."""
        try:
            data = self._get(f"/api/v1/services/{svc_uuid}")
            return str(data.get("status") or "") or None
        except Exception:
            return None

    # ── Health check ─────────────────────────────────────────────────

    def is_healthy(self, fqdn: str, timeout: int = 120) -> bool:
        """Poll the webui until the public URL answers through Traefik.

        On accepte 200-403 : un 200 sur /health prouve que la webui est en
        ligne, un 401/403 prouve au moins que Traefik route vers elle (page
        protégée). Un 404 vient de Traefik lui-même (pas encore de route)
        et un 5xx d'un conteneur pas prêt — on continue d'attendre.
        """
        import time
        deadline = time.monotonic() + timeout
        health_url = fqdn + "/health"
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(health_url, timeout=5, verify=False)
                if 200 <= resp.status_code < 404:
                    return True
            except Exception:
                pass
            time.sleep(5)
        return False


_client: CoolifyClient | None = None


def get_client() -> CoolifyClient | None:
    """Get the singleton CoolifyClient. Returns None if not configured."""
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    if not settings.coolify_api_token or not settings.coolify_api_url:
        return None
    _client = CoolifyClient()
    return _client
