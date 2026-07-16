"""Envoi d'e-mails transactionnels (vérification d'adresse, réinitialisation).

Stratégie volontairement simple et sans dépendance : si `SMTP_HOST` est
configuré, l'e-mail part réellement via `smtplib` ; sinon, il est **journalisé**
(avec le lien) — repli de développement qui laisse l'application fonctionner
sans fournisseur d'e-mail. À brancher sur un vrai SMTP avant la production.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import get_settings

_log = logging.getLogger("mailer")


def public_base_url() -> str:
    s = get_settings()
    return (s.public_base_url or s.site_url or "").rstrip("/")


def send_email(to: str, subject: str, text: str) -> bool:
    """Envoie un e-mail texte. Retourne True si réellement expédié (SMTP), False
    si journalisé en repli. Ne lève pas : un échec d'e-mail ne doit pas casser
    le parcours utilisateur."""
    s = get_settings()
    sender = s.smtp_from or s.legal_contact_email
    if not s.smtp_host:
        _log.warning(
            "[E-MAIL NON ENVOYÉ — SMTP non configuré] À: %s | Sujet: %s\n%s",
            to, subject, text,
        )
        return False
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as srv:
            if s.smtp_starttls:
                srv.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                srv.login(s.smtp_user, s.smtp_password)
            srv.send_message(msg)
        _log.info("E-mail envoyé à %s (%s)", to, subject)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.error("Envoi e-mail échoué à %s : %s — lien : voir le message ci-dessous\n%s",
                   to, exc, text)
        return False


def send_verification(to: str, token: str) -> bool:
    link = f"{public_base_url()}/api/auth/verify?token={token}"
    s = get_settings()
    return send_email(
        to,
        f"Vérifiez votre adresse — {s.site_name}",
        f"Bienvenue sur {s.site_name}.\n\n"
        f"Confirmez votre adresse e-mail en ouvrant ce lien :\n{link}\n\n"
        "Si vous n'êtes pas à l'origine de cette inscription, ignorez ce message.",
    )


def send_password_reset(to: str, token: str) -> bool:
    link = f"{public_base_url()}/reset-password?token={token}"
    s = get_settings()
    return send_email(
        to,
        f"Réinitialisation de votre mot de passe — {s.site_name}",
        f"Vous avez demandé à réinitialiser votre mot de passe sur {s.site_name}.\n\n"
        f"Choisissez un nouveau mot de passe via ce lien (valable 1 heure) :\n{link}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message : "
        "votre mot de passe reste inchangé.",
    )
