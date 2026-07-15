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
from dataclasses import dataclass

import httpx

from .config import get_settings

logger = logging.getLogger("coolify")

SERVICE_TYPE = "hermes-agent-with-webui"


@dataclass
class DeployedService:
    uuid: str
    name: str
    fqdn: str
    password: str | None = None


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
        if resp.status_code not in (200, 202):
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

    # ── Deploy an agent ──────────────────────────────────────────────

    def deploy_agent(
        self,
        name: str,
        subdomain: str,
        openrouter_api_key: str,
        model: str = "meta-llama/llama-3.3-70b-instruct:free",
        system_prompt: str = "",
    ) -> DeployedService:
        """Create a Coolify Service from the hermes-agent-with-webui template.

        Returns DeployedService with the service UUID, FQDN, and auto-generated
        password.
        """
        service_name = f"hermes-{subdomain}"
        fqdn = f"https://{subdomain}.{get_settings().base_domain}"

        # 1. Create the service from the template
        created = self._post("/api/v1/services", {
            "project_uuid": self.project_uuid,
            "environment_name": self.environment_name,
            "server_uuid": self.server_uuid,
            "destination_uuid": self.destination_uuid,
            "type": SERVICE_TYPE,
            "name": service_name,
        })
        svc_uuid = created["uuid"]
        logger.info("Service %s created (uuid=%s)", service_name, svc_uuid)

        # 2. Set environment variables
        self._set_env(svc_uuid, "OPENROUTER_API_KEY", openrouter_api_key)
        self._set_env(svc_uuid, "HERMES_INSTANCE_MODEL", model)
        if system_prompt:
            self._set_env(svc_uuid, "HERMES_SYSTEM_PROMPT", system_prompt)

        # 3. Set the FQDN for the webui service
        try:
            self._patch(f"/api/v1/services/{svc_uuid}", {
                "service_name": "hermes-webui",
                "fqdn": fqdn,
            })
            logger.info("FQDN set: %s", fqdn)
        except RuntimeError as exc:
            logger.warning("FQDN patch failed (non-fatal): %s", exc)

        # 4. Retrieve the auto-generated webui password
        password = self._get_env_value(svc_uuid, "SERVICE_PASSWORD_HERMESWEBUI")

        # 5. Start the service
        try:
            self._post(f"/api/v1/services/{svc_uuid}/start")
            logger.info("Service %s started", service_name)
        except RuntimeError as exc:
            logger.warning("Start failed (non-fatal, Coolify may auto-start): %s", exc)

        return DeployedService(
            uuid=svc_uuid,
            name=service_name,
            fqdn=fqdn,
            password=password,
        )

    # ── Environment variables ────────────────────────────────────────

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

    # ── Health check ─────────────────────────────────────────────────

    def is_healthy(self, fqdn: str, timeout: int = 120) -> bool:
        """Poll the webui /health endpoint until it responds."""
        import time
        deadline = time.monotonic() + timeout
        health_url = fqdn.replace("https://", "https://") + "/health"
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(health_url, timeout=5, verify=False)
                if resp.status_code == 200:
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
