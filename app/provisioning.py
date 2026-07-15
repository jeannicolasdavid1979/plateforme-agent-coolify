"""Provisioning engine — deploys an agent via Coolify, step by step."""
from __future__ import annotations

import logging
import re
import threading
import time

import yaml
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

# L'image nousresearch/hermes-agent ne lit pas HERMES_MODEL : elle lit
# ~/.hermes/config.yaml. On écrit ce fichier AU DÉMARRAGE DU CONTENEUR
# (entrypoint injecté dans le compose) — un docker exec depuis le conteneur
# de la plateforme est impossible (pas de CLI docker ni de socket).
# `$$` : les variables sont résolues par le shell du conteneur, pas par
# docker compose au parse.
_AGENT_BOOTSTRAP = (
    "mkdir -p /home/hermes/.hermes && "
    "printf 'model:\\n"
    "  default: \"%s\"\\n"
    "  provider: \"auto\"\\n"
    "  base_url: \"https://openrouter.ai/api/v1\"\\n' "
    '"$${HERMES_MODEL:-openai/gpt-4o}" > /home/hermes/.hermes/config.yaml; '
    "exec /init"
)

_FQDN_KEY_RE = re.compile(r"^SERVICE_(?:FQDN|URL)_HERMESWEBUI(?:_\d+)?$")


def customize_compose(compose_yaml: str, fqdn_url: str) -> tuple[str | None, list[str]]:
    """Adapte le compose du template au tenant.

    1. Fixe explicitement les variables magiques SERVICE_FQDN/URL_HERMESWEBUI
       au domaine du client — c'est CE que le parseur Coolify lit pour
       générer les labels Traefik (sinon il garde le sslip.io généré à la
       création du service).
    2. Injecte l'entrypoint du conteneur hermes-agent qui écrit le modèle
       dans config.yaml au boot.

    Retourne (yaml modifié | None si rien reconnu, liste des changements).
    """
    try:
        doc = yaml.safe_load(compose_yaml)
        services = doc["services"]
        assert isinstance(services, dict)
    except Exception as exc:
        logger.warning("compose illisible : %s", exc)
        return None, [f"compose illisible ({exc})"]

    changes: list[str] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue

        # 1. Domaine explicite sur les variables magiques de la webui
        env = svc.get("environment")
        if isinstance(env, list):
            for i, entry in enumerate(env):
                key = str(entry).split("=", 1)[0].strip()
                if _FQDN_KEY_RE.match(key):
                    env[i] = f"{key}={fqdn_url}"
                    changes.append(f"{key} → {fqdn_url}")
        elif isinstance(env, dict):
            for key in list(env):
                if _FQDN_KEY_RE.match(str(key)):
                    env[key] = fqdn_url
                    changes.append(f"{key} → {fqdn_url}")

        # 2. Entrypoint config.yaml sur le conteneur agent
        image = str(svc.get("image", ""))
        if "hermes-agent" in image or "hermes-agent" in str(svc_name):
            svc["entrypoint"] = ["/bin/bash", "-c", _AGENT_BOOTSTRAP]
            changes.append(f"entrypoint config.yaml sur {svc_name}")

    if not changes:
        return None, ["aucune variable SERVICE_FQDN_HERMESWEBUI ni service agent trouvés"]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), changes


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

        svc_uuid = client.create_service(f"hermes-{tenant.subdomain}")
        tenant.coolify_service_uuid = svc_uuid
        tenant.instance_url = f"https://{tenant.subdomain}.{self.settings.base_domain}"
        tenant.instance_password = client.get_password(svc_uuid)
        self.db.commit()
        return f"service {svc_uuid[:12]} → {tenant.instance_url}"

    def _step_configure_env(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        svc_uuid = tenant.coolify_service_uuid
        fqdn_host = f"{tenant.subdomain}.{self.settings.base_domain}"

        # hermes-agent lit le modèle dans HERMES_MODEL
        client.set_env(svc_uuid, "OPENROUTER_API_KEY", self.settings.openrouter_api_key)
        client.set_env(svc_uuid, "HERMES_MODEL", tenant.model)
        if tenant.system_prompt:
            client.set_env(svc_uuid, "HERMES_SYSTEM_PROMPT", tenant.system_prompt)

        # Variables magiques Coolify : c'est ELLES que le parseur de compose
        # lit pour générer les labels Traefik. Sans ça, Coolify garde le
        # domaine sslip.io généré à la création du service.
        client.set_env(svc_uuid, "SERVICE_FQDN_HERMESWEBUI", fqdn_host)
        client.set_env(svc_uuid, "SERVICE_URL_HERMESWEBUI", f"https://{fqdn_host}")
        return "variables + domaine poussés"

    def _step_set_fqdn(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        svc_uuid = tenant.coolify_service_uuid

        # La source de vérité des labels Traefik est le compose du service :
        # on y inscrit le domaine (et l'entrypoint config.yaml de l'agent),
        # le déploiement qui suit re-parse le tout.
        details: list[str] = []
        compose = client.get_compose_raw(svc_uuid)
        if compose:
            patched, changes = customize_compose(compose, tenant.instance_url)
            if patched and client.update_compose_raw(svc_uuid, patched):
                details.append("compose adapté : " + " ; ".join(changes))
            else:
                details.append("compose non modifié (" + " ; ".join(changes) + ")")
        else:
            details.append("compose introuvable via l'API")

        # Ceinture + bretelles : le PATCH fqdn direct, s'il est supporté
        if client.patch_service_fqdn(svc_uuid, "hermes-webui", tenant.instance_url):
            details.append("PATCH fqdn accepté")
        return " — ".join(details)

    def _step_start_service(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        svc_uuid = tenant.coolify_service_uuid

        # /deploy (et non /start) : force le re-parse du compose, donc les
        # labels Traefik avec le bon domaine. /start réutilisait le rendu
        # fait à la création → FQDN sslip.io et conteneurs invisibles.
        if not client.trigger_deploy(svc_uuid):
            client.start_service(svc_uuid)

        status = client.wait_running(svc_uuid, timeout=240)
        if not status or "running" not in status:
            # Un service resté 'exited' après création : on retente une fois
            client.restart_service(svc_uuid)
            status = client.wait_running(svc_uuid, timeout=120)
        if not status or "running" not in status:
            raise RuntimeError(
                f"les conteneurs ne démarrent pas (statut Coolify : {status or 'inconnu'})"
            )

        # Vérité terrain : le domaine que Coolify a réellement retenu
        want = tenant.instance_url.replace("https://", "")
        fqdns = client.service_fqdns(svc_uuid)
        domain_note = (
            f", domaine {want} appliqué" if any(want in f for f in fqdns)
            else f", ATTENTION domaine retenu : {', '.join(fqdns) or 'aucun'}"
        )
        return f"conteneurs démarrés ({status}){domain_note}"

    def _step_health_check(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        if not client or not tenant.instance_url:
            return "skip (pas d'URL)"

        # Laisser Traefik découvrir les nouveaux conteneurs
        time.sleep(15)

        # Le mot de passe webui est généré par Coolify au parse du compose —
        # s'il n'existait pas encore à la création, il existe forcément ici.
        if not tenant.instance_password:
            tenant.instance_password = client.get_password(tenant.coolify_service_uuid)
            self.db.commit()

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

    def _step_done(self, tenant: Tenant, job: ProvisioningJob) -> str:
        return "agent prêt"
