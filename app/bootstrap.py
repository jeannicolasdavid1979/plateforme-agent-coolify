"""Levier de secours : reprendre la main sur le compte administrateur.

Le parcours normal de récupération passe par l'e-mail. Quand aucun SMTP
n'est configuré — ou que le fournisseur d'e-mail est en panne — l'exploitant
se retrouve enfermé dehors, sans aucun moyen de revenir : c'est exactement la
situation qu'un SaaS ne peut pas se permettre.

D'où ce levier, actionné par variable d'environnement (le seul canal dont
l'exploitant garde toujours le contrôle via Coolify) :

    ADMIN_EMAILS=vous@exemple.fr
    ADMIN_BOOTSTRAP_PASSWORD=un-mot-de-passe-solide

Au démarrage, chaque adresse d'ADMIN_EMAILS reçoit ce mot de passe : le compte
est créé s'il n'existe pas, sinon son mot de passe est réinitialisé. Le compte
est marqué administrateur et son adresse considérée vérifiée (sans quoi le
levier dépendrait à nouveau de l'e-mail).

⚠️ Le mot de passe est appliqué à CHAQUE démarrage tant que la variable est
présente : on la retire une fois la main reprise. C'est un levier de secours,
pas un mode de configuration permanent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory
from .models import User
from .security import hash_password

logger = logging.getLogger("bootstrap")

MIN_PASSWORD_LEN = 8


def admin_emails() -> list[str]:
    return [e.strip().lower() for e in get_settings().admin_emails.split(",") if e.strip()]


def apply_admin_bootstrap() -> list[str]:
    """Applique le mot de passe de secours. Retourne les adresses traitées.

    Ne lève jamais : un échec ici ne doit pas empêcher l'application de
    démarrer (sinon la panne d'accès devient une panne totale)."""
    settings = get_settings()
    password = (getattr(settings, "admin_bootstrap_password", "") or "").strip()
    if not password:
        return []

    emails = admin_emails()
    if not emails:
        logger.error(
            "ADMIN_BOOTSTRAP_PASSWORD est défini mais ADMIN_EMAILS est vide : "
            "aucune adresse à qui rendre l'accès. Renseignez ADMIN_EMAILS.",
        )
        return []
    if len(password) < MIN_PASSWORD_LEN:
        logger.error(
            "ADMIN_BOOTSTRAP_PASSWORD trop court (%d caractères, minimum %d) — ignoré.",
            len(password), MIN_PASSWORD_LEN,
        )
        return []

    treated: list[str] = []
    try:
        with SessionFactory() as db:
            for email in emails:
                user = db.scalar(select(User).where(User.email == email))
                if user is None:
                    user = User(
                        email=email,
                        password_hash=hash_password(password),
                        is_admin=True,
                        email_verified=True,
                        consent_at=datetime.now(timezone.utc),
                        consent_version=settings.terms_version,
                    )
                    db.add(user)
                    action = "créé"
                else:
                    user.password_hash = hash_password(password)
                    user.is_admin = True
                    user.email_verified = True
                    user.reset_token = None
                    user.reset_expires = None
                    action = "réinitialisé"
                treated.append(email)
                logger.warning(
                    "ACCÈS ADMIN %s pour %s via ADMIN_BOOTSTRAP_PASSWORD. "
                    "Connectez-vous, puis RETIREZ cette variable d'environnement "
                    "et redéployez.", action, email,
                )
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Levier d'accès admin non appliqué : %s", exc)
        return []
    return treated
