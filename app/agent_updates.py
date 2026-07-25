"""Suivi des versions amont des deux briques d'un agent Hermes.

Un agent déployé, c'est deux images : l'interface (`nesquena/hermes-webui`,
versionnée par releases) et le moteur (`NousResearch/hermes-agent`, qui suit
sa branche principale au commit). Ce module relève les versions publiées,
les met en cache, et dit pour un agent donné ce qui a bougé depuis son
dernier déploiement.

Règle de conduite : le réseau n'est jamais bloquant. Si GitHub est
injoignable, on sert le dernier relevé connu — jamais d'erreur 500 sur un
parcours client à cause d'une API tierce.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Setting, Tenant, User

logger = logging.getLogger("agent_updates")

# Les deux briques suivies, telles que déployées par le template Coolify.
WEBUI_REPO = "nesquena/hermes-webui"
AGENT_REPO = "NousResearch/hermes-agent"

WEBUI_LATEST_KEY = "hermes_webui_latest"
AGENT_LATEST_KEY = "hermes_agent_latest"
LATEST_CHECKED_KEY = "hermes_latest_checked_at"


def fetch_latest_versions(timeout: float = 6.0) -> dict[str, str | None]:
    """Relève les versions publiées en amont. Toute panne réseau/API rend
    None pour la brique concernée (l'appelant conserve alors son cache)."""
    versions: dict[str, str | None] = {"webui": None, "agent": None}
    try:
        with httpx.Client(timeout=timeout, headers={"Accept": "application/vnd.github+json"}) as client:
            try:
                r = client.get(f"https://api.github.com/repos/{WEBUI_REPO}/releases/latest")
                if r.status_code == 200:
                    versions["webui"] = (r.json().get("tag_name") or "").lstrip("v") or None
            except Exception as exc:
                logger.warning("Relevé de version webui impossible : %s", exc)

            try:
                r = client.get(f"https://api.github.com/repos/{AGENT_REPO}/commits/main")
                if r.status_code == 200:
                    versions["agent"] = ((r.json().get("sha") or "")[:8]) or None
            except Exception as exc:
                logger.warning("Relevé de version agent impossible : %s", exc)
    except Exception as exc:  # création du client (proxy, DNS…)
        logger.warning("Relevé des versions amont impossible : %s", exc)
    return versions


def get_cached_latest_versions(db: Session) -> dict[str, str | None]:
    """Dernier relevé connu des versions amont."""
    webui = db.get(Setting, WEBUI_LATEST_KEY)
    agent = db.get(Setting, AGENT_LATEST_KEY)
    return {
        "webui": webui.value if webui else None,
        "agent": agent.value if agent else None,
    }


def refresh_latest_versions(db: Session, max_age_s: float = 3600) -> dict[str, str | None]:
    """Renvoie les versions amont, en interrogeant GitHub au plus une fois
    par `max_age_s`. Toujours servi depuis le cache en cas d'échec réseau."""
    now = datetime.now(timezone.utc)
    stamp = db.get(Setting, LATEST_CHECKED_KEY)
    if stamp:
        try:
            last = datetime.fromisoformat(stamp.value)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < max_age_s:
                return get_cached_latest_versions(db)
        except ValueError:
            pass

    try:
        fresh = fetch_latest_versions()
        cache_latest_versions(db, fresh)
        db.merge(Setting(key=LATEST_CHECKED_KEY, value=now.isoformat()))
        db.commit()
    except Exception as exc:
        # Un incident chez GitHub ne doit jamais casser un parcours client :
        # on sert le dernier relevé connu et on retentera au prochain appel.
        logger.warning("Rafraîchissement des versions amont abandonné : %s", exc)
        db.rollback()
    return get_cached_latest_versions(db)


def cache_latest_versions(db: Session, versions: dict[str, str | None]) -> None:
    """Mémorise le relevé. Une valeur absente (panne réseau) ne fait jamais
    perdre la valeur déjà connue."""
    for key, value in ((WEBUI_LATEST_KEY, versions.get("webui")),
                       (AGENT_LATEST_KEY, versions.get("agent"))):
        if value:
            db.merge(Setting(key=key, value=value))
    db.commit()


COMPONENT_LABELS = {"webui": "Interface de discussion", "agent": "Moteur de l'agent"}


def check_updates_available(tenant: Tenant, latest_versions: dict[str, str | None]) -> list[dict]:
    """Écart entre les versions déployées pour cet agent et l'amont.

    Un agent dont la version n'a jamais été relevée (déployé avant la mise en
    place du suivi) n'est PAS considéré comme en retard : on ne facture pas
    une mise à jour qu'on est incapable de constater. Sa version est alignée
    sur l'amont au premier relevé, et les écarts suivants seront réels."""
    updates: list[dict] = []
    for component, current in (("webui", tenant.hermes_webui_version),
                               ("agent", tenant.hermes_agent_version)):
        upstream = latest_versions.get(component)
        if not upstream or not current or current == upstream:
            continue
        updates.append({
            "component": component,
            "label": COMPONENT_LABELS[component],
            "from": current,
            "to": upstream,
        })
    return updates


def adopt_versions_if_unknown(tenant: Tenant, latest_versions: dict[str, str | None]) -> bool:
    """Aligne un agent sans version connue sur le relevé amont (référence de
    départ). Retourne True si quelque chose a été posé."""
    changed = False
    if not tenant.hermes_webui_version and latest_versions.get("webui"):
        tenant.hermes_webui_version = latest_versions["webui"]
        changed = True
    if not tenant.hermes_agent_version and latest_versions.get("agent"):
        tenant.hermes_agent_version = latest_versions["agent"]
        changed = True
    return changed


def _next_month_start(dt: datetime) -> datetime:
    """1er du mois suivant, à minuit UTC."""
    return (dt.replace(day=1) + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite rend des datetime naïfs — on les rattache à UTC pour comparer."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def free_updates_left(user: User, quota: int) -> int:
    """Mises à jour offertes restantes ce mois-ci. Remet le compteur à zéro
    (en mémoire — l'appelant commite) quand la date de reset est dépassée."""
    now = datetime.now(timezone.utc)
    reset_at = _aware(user.free_updates_reset_at)

    if reset_at is None:
        user.free_updates_reset_at = _next_month_start(now)
    elif now >= reset_at:
        user.free_updates_used = 0
        user.free_updates_reset_at = _next_month_start(now)

    return max(0, quota - (user.free_updates_used or 0))


def has_free_updates_available(user: User, quota: int) -> bool:
    """Le client a-t-il encore une mise à jour offerte ce mois-ci ?"""
    return free_updates_left(user, quota) > 0


def consume_free_update(user: User, quota: int) -> bool:
    """Consomme une mise à jour offerte. False si le quota est épuisé."""
    if not has_free_updates_available(user, quota):
        return False
    user.free_updates_used = (user.free_updates_used or 0) + 1
    return True


# Les deux réglages sont stockés dans `settings` sous les MÊMES clés que le
# reste de la tarification (PRICING_KEYS), donc pilotables depuis l'admin.
UPDATE_COST_KEY = "update_cost_eur"
FREE_UPDATES_KEY = "free_updates_per_month"


def _setting_float(db: Session, key: str, default: float) -> float:
    row = db.get(Setting, key)
    if row is None:
        return default
    try:
        return float(row.value)
    except (ValueError, TypeError):
        return default


def get_update_cost_eur(db: Session) -> float:
    """Prix d'une mise à jour : réglage admin, sinon valeur par défaut."""
    return round(_setting_float(db, UPDATE_COST_KEY, get_settings().update_cost_eur), 2)


def get_free_updates_quota(db: Session) -> int:
    """Mises à jour offertes par mois : réglage admin, sinon valeur par défaut."""
    return int(_setting_float(db, FREE_UPDATES_KEY, get_settings().free_updates_per_month))
