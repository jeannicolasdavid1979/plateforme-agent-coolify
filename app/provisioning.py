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
from .models import ProvisioningJob, Setting, Tenant
from .openrouter import get_keys_client

logger = logging.getLogger("provisioning")

STEPS = [
    "deploy_service",
    "create_api_key",
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


def find_web_services(compose_yaml: str | None) -> list[str]:
    """Noms (clés du compose) des services exposés en HTTP : ceux qui portent
    une variable magique SERVICE_FQDN/URL_* ou une image/un nom webui. C'est
    à EUX que l'API Coolify attribue un domaine (champ `urls`)."""
    if not compose_yaml:
        return []
    try:
        doc = yaml.safe_load(compose_yaml)
        services = doc["services"]
        assert isinstance(services, dict)
    except Exception:
        return []
    found: list[str] = []
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        env = svc.get("environment") or []
        entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
        keys = [str(e).split("=", 1)[0].strip() for e in entries]
        if any(_FQDN_KEY_RE.match(k) for k in keys) or "webui" in str(svc.get("image", "")) or "webui" in str(name):
            found.append(str(name))
    return found


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
        matched: list[str] = []
        if isinstance(env, list):
            for i, entry in enumerate(env):
                key = str(entry).split("=", 1)[0].strip()
                if _FQDN_KEY_RE.match(key):
                    env[i] = f"{key}={fqdn_url}"
                    matched.append(key)
        elif isinstance(env, dict):
            for key in list(env):
                if _FQDN_KEY_RE.match(str(key)):
                    env[key] = fqdn_url
                    matched.append(str(key))
        changes.extend(f"{k} → {fqdn_url}" for k in matched)

        # Le template peut ne déclarer que SERVICE_URL_* (constaté en réel) :
        # or c'est SERVICE_FQDN_* que le parseur Coolify lit pour générer le
        # domaine. On l'ajoute alors explicitement, même suffixe de port.
        url_keys = [k for k in matched if k.startswith("SERVICE_URL_")]
        has_fqdn = any(k.startswith("SERVICE_FQDN_") for k in matched)
        if url_keys and not has_fqdn:
            suffix = url_keys[0].removeprefix("SERVICE_URL_HERMESWEBUI")
            new_key = f"SERVICE_FQDN_HERMESWEBUI{suffix}"
            if isinstance(env, list):
                env.append(f"{new_key}={fqdn_url}")
            elif isinstance(env, dict):
                env[new_key] = fqdn_url
            changes.append(f"{new_key} ajouté → {fqdn_url}")

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

    TEMPLATE_CACHE_KEY = "coolify_template_compose"

    def _get_template_compose(self, client) -> str | None:
        """Le compose du template hermes-agent-with-webui, sondé UNE fois :
        service jetable créé depuis le template → compose lu → service
        supprimé → YAML mis en cache en DB (table settings)."""
        row = self.db.get(Setting, self.TEMPLATE_CACHE_KEY)
        if row and row.value.strip():
            return row.value
        import uuid as _uuid
        probe_uuid = None
        try:
            probe_uuid = client.create_service(f"hermes-probe-{_uuid.uuid4().hex[:8]}")
            compose = client.get_compose_raw(probe_uuid)
            if compose and "services" in compose:
                self.db.merge(Setting(key=self.TEMPLATE_CACHE_KEY, value=compose))
                self.db.commit()
                return compose
        except Exception as exc:
            logger.warning("Sonde du template échouée : %s", exc)
        finally:
            if probe_uuid:
                try:
                    client.delete_service(probe_uuid)
                except Exception:
                    logger.warning("Service sonde %s non supprimé — à nettoyer dans Coolify", probe_uuid)
        return None

    def _step_deploy_service(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        if not client:
            raise RuntimeError("Service d'hébergement non configuré")

        fqdn_url = f"https://{tenant.subdomain}.{self.settings.base_domain}"
        svc_name = f"hermes-{tenant.subdomain}"

        # Le domaine du client se transmet par le champ officiel `urls` de
        # l'API ({nom du service compose} → URL) : c'est lui qui remplit
        # service_applications.fqdn, la source des labels Traefik. Une valeur
        # posée dans SERVICE_FQDN_* du YAML est ignorée par le parseur.
        svc_uuid, how = None, ""
        template = self._get_template_compose(client)
        web_svcs = find_web_services(template) or ["hermes-webui"]
        urls = [{"name": s, "url": fqdn_url} for s in web_svcs]

        # Voie royale : créer le service avec notre compose (entrypoint
        # config.yaml, variables magiques) ET le domaine, dès la création.
        if template:
            patched, _changes = customize_compose(template, fqdn_url)
            if patched:
                svc_uuid = client.create_service_from_compose(svc_name, patched, urls=urls)
                how = " (configuration personnalisée appliquée)"

        # Repli : création depuis le template, domaine transmis quand même
        if not svc_uuid:
            svc_uuid = client.create_service(svc_name, urls=urls)
            how = ""

        tenant.coolify_service_uuid = svc_uuid
        tenant.instance_url = fqdn_url
        tenant.instance_password = client.get_password(svc_uuid)
        self.db.commit()
        return f"agent configuré → {fqdn_url}{how}"

    def _step_create_api_key(self, tenant: Tenant, job: ProvisioningJob) -> str:
        """Une clé OpenRouter PAR AGENT, nommée et plafonnée au crédit payé —
        sinon impossible de savoir quel client consomme quoi."""
        keys = get_keys_client()
        if not keys:
            return "clé partagée utilisée (OPENROUTER_PROVISIONING_KEY non configurée)"
        if tenant.openrouter_api_key:
            return "clé dédiée existante réutilisée"

        limit_usd = round((tenant.balance_eur or 0.0) * self.settings.eur_usd_rate, 2)
        key, key_hash = keys.create(name=f"hermes-{tenant.subdomain}", limit_usd=limit_usd)
        tenant.openrouter_api_key = key
        tenant.openrouter_key_hash = key_hash
        self.db.commit()
        return f"clé hermes-{tenant.subdomain} créée — plafond {limit_usd:.2f} $"

    def _step_configure_env(self, tenant: Tenant, job: ProvisioningJob) -> str:
        client = get_client()
        svc_uuid = tenant.coolify_service_uuid
        fqdn_host = f"{tenant.subdomain}.{self.settings.base_domain}"

        # hermes-agent lit le modèle dans HERMES_MODEL. Clé dédiée à l'agent
        # si le provisioning OpenRouter est configuré, partagée sinon.
        api_key = tenant.openrouter_api_key or self.settings.openrouter_api_key
        client.set_env(svc_uuid, "OPENROUTER_API_KEY", api_key)
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
        details: list[str] = []

        # Mécanisme OFFICIEL (spec OpenAPI Coolify) : PATCH du champ `urls`
        # — écrit service_applications.fqdn, d'où sont générés les labels
        # Traefik au déploiement. Toujours exécuté, même si le domaine figure
        # déjà dans le compose : la valeur d'une variable SERVICE_FQDN_* du
        # YAML est ignorée par le parseur (constaté en réel : sslip.io gardé).
        compose = client.get_compose_raw(svc_uuid)
        web_svcs = find_web_services(compose) or ["hermes-webui"]
        domains = client.set_service_urls(
            svc_uuid, [{"name": s, "url": tenant.instance_url} for s in web_svcs]
        )
        if domains is None:
            details.append(
                "adresse à confirmer manuellement"
            )
        elif domains:
            details.append("adresse attribuée : " + ", ".join(domains))
        else:
            details.append(f"adresse {tenant.instance_url} attribuée")

        # Ceinture : le compose porte aussi le domaine (variables magiques)
        # et l'entrypoint config.yaml de l'agent.
        if compose and f"={tenant.instance_url}" not in compose:
            patched, changes = customize_compose(compose, tenant.instance_url)
            if patched and client.update_compose_raw(svc_uuid, patched):
                details.append("configuration adaptée")
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
                f"votre agent ne démarre pas (statut : {status or 'inconnu'})"
            )

        # Vérité terrain : le domaine que Coolify a réellement retenu.
        # S'il a gardé le sien (sslip.io), on ADOPTE cette adresse : l'agent
        # doit être joignable tout de suite — le domaine personnalisé se
        # règle ensuite dans Coolify (Domains) puis Redémarrer.
        want = tenant.instance_url.replace("https://", "")
        fqdns = client.service_fqdns(svc_uuid)
        if any(want in f for f in fqdns):
            return f"agent démarré ({status}), adresse {want} appliquée"
        effective = next((f.strip() for f in fqdns if f and f.strip()), None)
        if effective:
            effective = re.sub(r":\d+$", "", effective)  # :8787 = port interne Coolify
            if not effective.startswith("http"):
                effective = f"http://{effective}"
            tenant.instance_url = effective
            self.db.commit()
            return (
                f"agent démarré ({status}) — adresse effective adoptée : {effective}"
            )
        return f"agent démarré ({status}), adresse en cours d'attribution"

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
            return f"agent actif ({state}) — adresse publique en cours de propagation (SSL)"
        raise RuntimeError(
            "votre agent n'a pas répondu à la vérification finale"
            + (f" (statut : {state})" if state else "")
        )

    def _step_done(self, tenant: Tenant, job: ProvisioningJob) -> str:
        return "agent prêt"
