"""Provisioning engine — deploys an agent via Coolify, step by step."""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.orm import Session

from .config import get_settings
from .coolify import get_client
from .db import SessionFactory
from .models import ProvisioningJob, Tenant

logger = logging.getLogger("provisioning")

STEPS = [
    "deploy_service",
    "configure_env",
    "set_fqdn",
    "start_service",
    "health_check",
    "done",
]


class ProvisioningEngine:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def create_job(self, tenant: Tenant) -> ProvisioningJob:
        job = ProvisioningJob(
            tenant_id=tenant.id,
            status="queued",
            steps=[{"name": s, "status": "pending", "detail": ""} for s in STEPS],
        )
        self.db.add(job)
        self.db.commit()
        return job

    def _mark_step(self, job: ProvisioningJob, name: str, status: str, detail: str = ""):
        steps = [dict(s) for s in job.steps]
        for step in steps:
            if step["name"] == name:
                step["status"] = status
                step["detail"] = detail
        job.steps = steps
        self.db.commit()

    def run_job(self, job_id: str) -> ProvisioningJob:
        job = self.db.get(ProvisioningJob, job_id)
        tenant = self.db.get(Tenant, job.tenant_id)
        job.status = "running"
        tenant.status = "deploying"
        self.db.commit()

        completed: list[str] = []
        try:
            for name in STEPS:
                self._mark_step(job, name, "running")
                detail = getattr(self, f"_step_{name}")(tenant, job)
                self._mark_step(job, name, "done", detail or "")
                completed.append(name)

            job.status = "succeeded"
            tenant.status = "running"
            self.db.commit()
            logger.info("Agent %s deployed at %s", tenant.name, tenant.instance_url)

        except Exception as exc:
            logger.exception("Provisioning failed for %s", tenant.name)
            failed_step = next((s for s in STEPS if s not in completed), "?")
            self._mark_step(job, failed_step, "failed", str(exc)[:500])
            job.error = f"{failed_step}: {exc}"[:1000]
            job.status = "failed"
            tenant.status = "failed"
            self.db.commit()
        return job

    def run_job_async(self, job_id: str) -> None:
        """Run in a background thread so the API returns immediately."""

        def _worker():
            session = SessionFactory()
            try:
                ProvisioningEngine(session).run_job(job_id)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

        threading.Thread(target=_worker, daemon=True, name=f"deploy-{job_id}").start()

    # ── Steps ────────────────────────────────────────────────────────

    def _step_deploy_service(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        if not client:
            raise RuntimeError("Coolify API non configurée")

        svc = client.deploy_agent(
            name=tenant.name,
            subdomain=tenant.subdomain,
            openrouter_api_key=self.settings.openrouter_api_key,
            model=tenant.model,
            system_prompt=tenant.system_prompt,
        )
        tenant.coolify_service_uuid = svc.uuid
        tenant.instance_url = svc.fqdn
        tenant.instance_password = svc.password
        self.db.commit()
        return f"service {svc.uuid[:12]} → {svc.fqdn}"

    def _step_configure_env(self, tenant: Tenant, job: ProvisioningJob) -> str:
        # Env vars are set during deploy_agent — nothing more to do
        return "variables poussées"

    def _step_set_fqdn(self, tenant: Tenant, job: ProvisioningJob) -> str:
        # FQDN is set during deploy_agent — nothing more to do
        return f"fqdn={tenant.instance_url}"

    def _step_start_service(self, tenant: Tenant, job: ProvisioningJob) -> str:
        # Service is started during deploy_agent — nothing more to do
        return "service démarré"

    def _step_health_check(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        if not client or not tenant.instance_url:
            return "skip (pas d'URL)"

        # Attendre que les conteneurs soient up avant d'injecter le modèle
        import time
        time.sleep(15)

        # Injecter le modèle dans config.yaml du conteneur hermes-agent
        # (l'agent ne lit pas HERMES_INSTANCE_MODEL, il lit config.yaml)
        self._inject_model(tenant)

        if client.is_healthy(tenant.instance_url, timeout=300):
            return "instance en ligne"

        # Fenêtre de convergence connue (docs Hermes) : Traefik n'ajoute le
        # conteneur à sa table de routage qu'une fois son statut Docker
        # `healthy`, et la première émission du certificat Let's Encrypt
        # peut prendre plusieurs minutes. Si Coolify confirme que les
        # conteneurs tournent, le déploiement a réussi — seule l'URL
        # publique converge encore.
        state = client.service_status(tenant.coolify_service_uuid)
        if state and "running" in state:
            return f"conteneurs actifs ({state}) — l'URL publique converge (SSL)"
        raise RuntimeError(
            "l'instance n'a pas répondu au health check"
            + (f" (statut Coolify : {state})" if state else "")
        )

    def _inject_model(self, tenant: Tenant) -> None:
        """Écrit le modèle dans config.yaml du conteneur hermes-agent via docker exec."""
        import subprocess
        agent_container = f"hermes-agent-{tenant.coolify_service_uuid}"
        config_path = "/home/hermes/.hermes/config.yaml"
        model = tenant.model or self.settings.default_model
        cmd = (
            f'docker exec {agent_container} bash -c '
            f'"mkdir -p /home/hermes/.hermes && '
            f'cat > {config_path} << EOF\\n'
            f'model:\\n'
            f'  default: \\"{model}\\"\\n'
            f'  provider: \\"auto\\"\\n'
            f'  base_url: \\"https://openrouter.ai/api/v1\\"\\n'
            f'EOF"'
        )
        try:
            subprocess.run(cmd, shell=True, timeout=30, capture_output=True)
            logger.info("Modèle %s injecté dans %s", model, agent_container)
        except Exception as exc:
            logger.warning("Injection modèle échouée (non-fatal): %s", exc)

    def _step_done(self, tenant: Tenant, job: ProvisioningJob) -> str:
        return "agent prêt"
