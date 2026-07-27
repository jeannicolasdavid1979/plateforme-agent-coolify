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

UPDATE_STEPS = [
    "update_service",
    "health_check",
    "read_versions",
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
    # L'image officielle a pour ENTRYPOINT [/init, /opt/hermes/docker/main-wrapper.sh]
    # (vérifié : `docker inspect nousresearch/hermes-agent`). /init est
    # s6-overlay ; main-wrapper.sh est le SEUL maillon qui sait résoudre
    # « gateway run » en un exec du binaire hermes réel. Un /init nu ne le
    # comprend pas et cherche un binaire littéral nommé « gateway »
    # (« rc.init: 91: gateway: not found », constaté en production). Et sans
    # passer par lui du tout, rien ne démarre le gateway : l'API agent reste
    # muette et l'interface affiche « Agent: not detected » — constaté depuis
    # le tout premier déploiement, bien avant qu'on ne touche à quoi que ce
    # soit ici. Notre entrypoint (nécessaire pour écrire config.yaml avant
    # démarrage) doit donc PRÉSERVER toute la chaîne d'origine, arguments
    # compris, plutôt que la remplacer par un /init nu.
    "exec /init /opt/hermes/docker/main-wrapper.sh gateway run"
)

_FQDN_KEY_RE = re.compile(r"^SERVICE_(?:FQDN|URL)_HERMESWEBUI(?:_\d+)?$")

# L'interface installe les dépendances du moteur au démarrage, en copiant la
# source partagée (volume hermes-agent-src) puis `uv pip install`. Or les
# versions récentes du moteur refusent cette construction :
#   « RuntimeError: Building wheels or sdists for hermes-agent is not supported »
# et l'interface repart alors en boucle de redémarrage (constaté en production
# dès qu'on est passé de 0.15 à la version courante). Le message d'erreur du
# moteur désigne lui-même la sortie prévue : l'installateur officiel Nix pose
# HERMES_NIX_BUILD=1 pour autoriser ce cas. Sans cette variable, l'interface
# ne démarre au mieux qu'en mode réduit (pas de détection des modèles, pas de
# routage de personnalité, pas d'import des sessions CLI).
_WEBUI_ENV = {"HERMES_NIX_BUILD": "1"}


def _set_env(svc: dict, key: str, value: str) -> bool:
    """Pose une variable d'environnement sur un service du compose, quelle que
    soit la forme utilisée (liste `CLE=valeur` ou dictionnaire). Retourne True
    si le compose a changé."""
    env = svc.get("environment")
    if isinstance(env, list):
        for i, entry in enumerate(env):
            if str(entry).split("=", 1)[0].strip() == key:
                if str(entry) == f"{key}={value}":
                    return False
                env[i] = f"{key}={value}"
                return True
        env.append(f"{key}={value}")
        return True
    if not isinstance(env, dict):
        env = {}
        svc["environment"] = env
    if str(env.get(key, "")) == value:
        return False
    env[key] = value
    return True


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

        # 3. Autorisation de construction pour l'interface (cf. _WEBUI_ENV)
        if "hermes-webui" in image or "webui" in str(svc_name):
            for key, value in _WEBUI_ENV.items():
                if _set_env(svc, key, value):
                    changes.append(f"{key}={value} sur {svc_name}")

    if not changes:
        return None, ["aucune variable SERVICE_FQDN_HERMESWEBUI ni service agent trouvés"]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), changes


def retag_compose(compose_yaml: str, webui_version: str | None) -> tuple[str | None, list[str]]:
    """Pointe les images du compose sur les versions à installer.

    C'est LE geste qui met à jour. Le template Coolify épingle les versions —
    `ghcr.io/nesquena/hermes-webui:0.51.92`, et l'agent par digest
    (`@sha256:…`). Un `docker compose pull` sur une référence épinglée retire
    exactement la MÊME image : sans réécrire ces références, aucune mise à
    jour n'est possible, quel que soit l'appel d'API utilisé.

    L'interface reçoit le numéro de version publié. Le moteur n'a pas de
    versions publiées (il suit sa branche principale), on le bascule donc sur
    `latest` — ce qui lève au passage son épinglage par digest.

    Retourne (yaml modifié | None si rien à changer, liste des changements).
    """
    try:
        doc = yaml.safe_load(compose_yaml)
        services = doc["services"]
        assert isinstance(services, dict)
    except Exception as exc:
        logger.warning("compose illisible : %s", exc)
        return None, [f"compose illisible ({exc})"]

    changes: list[str] = []
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        image = str(svc.get("image", ""))
        if not image:
            continue
        base = image.split("@", 1)[0]           # retire un éventuel digest
        repo = base.rsplit(":", 1)[0] if ":" in base.rsplit("/", 1)[-1] else base

        if "hermes-webui" in image or "webui" in str(name):
            if not webui_version:
                continue
            wanted = f"{repo}:{webui_version}"
        elif "hermes-agent" in image or "hermes-agent" in str(name):
            wanted = f"{repo}:latest"
        else:
            continue

        if wanted != image:
            svc["image"] = wanted
            changes.append(f"{name} : {image} → {wanted}")

    if not changes:
        return None, ["images déjà aux versions demandées"]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), changes


class ProvisioningEngine:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def create_job(self, tenant: Tenant, kind: str = "deploy") -> ProvisioningJob:
        steps_to_use = UPDATE_STEPS if kind == "update" else STEPS
        job = ProvisioningJob(
            tenant_id=tenant.id,
            status="queued",
            steps=[{"name": s, "status": "pending", "detail": ""} for s in steps_to_use],
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

        # Les étapes SONT celles enregistrées dans le job : une mise à jour en
        # a moins qu'un premier déploiement. Rejouer STEPS ici recréerait un
        # second service Coolify pour un agent qui en a déjà un.
        steps = [s["name"] for s in job.steps] or STEPS

        completed: list[str] = []
        try:
            for name in steps:
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
            failed_step = next((s for s in steps if s not in completed), "?")
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

        # Gateway : sans lui, les tâches planifiées de l'agent ne se
        # déclenchent JAMAIS — l'interface ne fait pas tourner l'horloge
        # elle-même, c'est le démon du conteneur moteur qui bat la seconde
        # (toutes les 60 s). Le client verrait « Gateway not configured » et
        # ses tâches resteraient inertes, sans message d'erreur. Le démon est
        # déjà là : il ne manquait que l'adresse pour l'atteindre, sur le
        # réseau interne du compose.
        # Coolify nomme les conteneurs « {service}-{uuid} » : c'est ce nom-là
        # que le DNS de Docker résout à coup sûr sur le réseau du projet.
        agent_host = f"{self._agent_service_name(client, svc_uuid)}-{svc_uuid}"
        gateway_url = f"http://{agent_host}:8642"
        client.set_env(svc_uuid, "HERMES_API_URL", gateway_url)
        client.set_env(svc_uuid, "HERMES_WEBUI_GATEWAY_BASE_URL", gateway_url)
        # L'interface cite aussi ces deux variables dans son message d'erreur :
        # on les pose pour ne dépendre d'aucune de ses versions.
        client.set_env(svc_uuid, "GATEWAY_HEALTH_URL", f"{gateway_url}/health")
        client.set_env(svc_uuid, "HERMES_GATEWAY_HEALTH_URL", f"{gateway_url}/health")

        # Variables magiques Coolify : c'est ELLES que le parseur de compose
        # lit pour générer les labels Traefik. Sans ça, Coolify garde le
        # domaine sslip.io généré à la création du service.
        client.set_env(svc_uuid, "SERVICE_FQDN_HERMESWEBUI", fqdn_host)
        client.set_env(svc_uuid, "SERVICE_URL_HERMESWEBUI", f"https://{fqdn_host}")
        return f"variables, domaine et gateway ({gateway_url}) poussés"

    @staticmethod
    def _agent_service_name(client, svc_uuid: str) -> str:
        """Nom du service moteur dans le compose — c'est lui qui fait office
        d'hôte sur le réseau interne. Repli sur le nom du template."""
        try:
            doc = yaml.safe_load(client.get_compose_raw(svc_uuid) or "") or {}
            for name, svc in (doc.get("services") or {}).items():
                if "hermes-agent" in str(name) or "hermes-agent" in str(
                    (svc or {}).get("image", "")
                ):
                    return str(name)
        except Exception:
            pass
        return "hermes-agent"

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

        # Livrer un agent NEUF déjà à jour. Le template épingle ses versions
        # (tag figé pour l'interface, digest pour le moteur) : sans repointer
        # ces références, le tirage rapporte exactement les mêmes images et
        # l'agent naît en retard. Toute cette étape est NON BLOQUANTE — un
        # agent livré sur une version un peu ancienne reste un agent qui
        # fonctionne, et son client pourra le mettre à jour d'un clic.
        from . import agent_updates

        try:
            latest = agent_updates.refresh_latest_versions(self.db, max_age_s=3600)
            compose = client.get_compose_raw(svc_uuid)
            if compose and latest.get("webui"):
                patched, changes = retag_compose(compose, latest["webui"])
                if patched and client.update_compose_raw(svc_uuid, patched):
                    logger.info("Versions de %s : %s", tenant.subdomain, "; ".join(changes))
        except Exception as exc:
            logger.warning(
                "Versions de %s non repointées (%s) — agent livré sur les "
                "versions du modèle", tenant.subdomain, exc,
            )

        if not client.restart_service(svc_uuid, pull_latest=True):
            logger.warning(
                "Tirage des images refusé pour %s — agent démarré sur les "
                "images en cache de l'hôte", tenant.subdomain,
            )

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

    # ── Update steps ─────────────────────────────────────────────────

    def _step_update_service(self, tenant: Tenant, job: ProvisioningJob) -> str:
        """Redéploie le service Coolify avec les versions mises à jour."""
        client = get_client()
        if not client or not tenant.coolify_service_uuid:
            raise RuntimeError("Service Coolify non trouvé")

        svc_uuid = tenant.coolify_service_uuid
        details: list[str] = []

        # 1. Pointer le compose sur les versions à installer. Indispensable :
        #    le template ÉPINGLE les versions (tag figé pour l'interface,
        #    digest pour le moteur) — sans cette réécriture, le tirage
        #    récupérerait exactement les mêmes images.
        from . import agent_updates

        latest = agent_updates.get_cached_latest_versions(self.db)
        compose = client.get_compose_raw(svc_uuid)
        if compose:
            patched, changes = retag_compose(compose, latest.get("webui"))
            # La mise à jour remet aussi la configuration d'aplomb : un agent
            # déployé avant que le gateway soit posé le récupère ici, sans quoi
            # ses tâches planifiées resteraient inertes à jamais.
            fixed, fixes = customize_compose(patched or compose, tenant.instance_url or "")
            if fixed:
                patched, changes = fixed, changes + fixes
            if patched and client.update_compose_raw(svc_uuid, patched):
                details.extend(changes)
            elif patched:
                raise RuntimeError(
                    "votre hébergeur a refusé de changer de version"
                )
        # Les variables d'environnement suivent le même chemin (gateway).
        try:
            self._step_configure_env(tenant, job)
        except Exception as exc:
            logger.warning("Variables non repoussées pour %s : %s", tenant.subdomain, exc)

        # 2. Tirer les images ainsi désignées : Coolify exécute alors un
        #    `docker compose pull` — donc les DEUX conteneurs, le moteur de
        #    l'agent comme son interface — puis recrée les conteneurs.
        if not client.restart_service(svc_uuid, pull_latest=True):
            raise RuntimeError(
                "votre hébergeur a refusé la mise à jour (récupération des "
                "nouvelles versions impossible)"
            )

        status = client.wait_running(svc_uuid, timeout=300)
        if not status or "running" not in status:
            raise RuntimeError(
                f"votre agent ne redémarre pas (statut : {status or 'inconnu'})"
            )
        return " — ".join(details) if details else "dernières images récupérées"

    def _step_read_versions(self, tenant: Tenant, job: ProvisioningJob) -> str:
        """Relit la version installée SUR l'agent après le redéploiement —
        on constate le résultat au lieu de le présumer."""
        from . import agent_updates

        if agent_updates.detect_installed_versions(tenant):
            self.db.commit()
            return f"version installée : {tenant.hermes_webui_version or '?'}"
        return "version non lisible pour le moment (agent en cours de démarrage)"
