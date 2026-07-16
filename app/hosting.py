"""Cycle de vie de l'hébergement récurrent d'un agent.

Un agent déployé doit rester « payé » jusqu'à `hosting_paid_until`. Passé cette
date (+ éventuelle grâce), il est **suspendu** (conteneurs arrêtés, accès
bloqué) ; les données restent **restaurables par l'admin** pendant la période de
rétention, après quoi l'agent est **supprimé définitivement**.

États exposés au front (chrono FOMO) :
- ``none``      : pas d'abonnement (agent non déployé / jamais payé)
- ``active``    : payé, échéance dans le futur
- ``grace``     : échéance dépassée mais dans la fenêtre de grâce
- ``suspended`` : suspendu, restaurable jusqu'à ``delete_at``
- ``deletable`` : rétention dépassée — à supprimer définitivement
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def _aware(dt: datetime | None) -> datetime | None:
    """Normalise en UTC-aware (SQLite peut rendre des datetimes naïfs)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class HostingStatus:
    state: str
    paid_until: datetime | None
    suspend_at: datetime | None   # date de suspension (échéance + grâce)
    delete_at: datetime | None    # date de suppression définitive
    seconds_left: int | None      # secondes avant la prochaine bascule (FOMO)


def hosting_status(
    paid_until: datetime | None,
    suspended_at: datetime | None,
    grace_days: int,
    retention_days: int,
    now: datetime | None = None,
) -> HostingStatus:
    now = now or datetime.now(timezone.utc)
    paid_until = _aware(paid_until)
    suspended_at = _aware(suspended_at)

    if paid_until is None and suspended_at is None:
        return HostingStatus("none", None, None, None, None)

    # Déjà suspendu : compte à rebours vers la suppression définitive.
    if suspended_at is not None:
        delete_at = suspended_at + timedelta(days=retention_days)
        state = "deletable" if now >= delete_at else "suspended"
        return HostingStatus(state, paid_until, suspended_at, delete_at,
                             max(0, int((delete_at - now).total_seconds())))

    suspend_at = paid_until + timedelta(days=grace_days)
    delete_at = suspend_at + timedelta(days=retention_days)
    if now < paid_until:
        return HostingStatus("active", paid_until, suspend_at, delete_at,
                             int((paid_until - now).total_seconds()))
    if now < suspend_at:
        return HostingStatus("grace", paid_until, suspend_at, delete_at,
                             int((suspend_at - now).total_seconds()))
    # Échéance + grâce dépassées mais pas encore marqué suspendu : à suspendre.
    return HostingStatus("suspended", paid_until, suspend_at, delete_at,
                         max(0, int((delete_at - now).total_seconds())))


def extend_period(paid_until: datetime | None, months: int, now: datetime | None = None) -> datetime:
    """Prolonge l'abonnement de ``months`` mois. On repart de l'échéance si elle
    est encore dans le futur (on ne perd pas les jours restants), sinon de
    maintenant. Approximation d'un mois = 30 jours (suffisant et lisible)."""
    now = now or datetime.now(timezone.utc)
    base = _aware(paid_until)
    if base is None or base < now:
        base = now
    return base + timedelta(days=30 * months)
