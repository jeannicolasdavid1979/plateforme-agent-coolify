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


def use_implicit_tls() -> bool:
    """TLS implicite (SMTPS) ? Réglage explicite, sinon déduit du port 465."""
    s = get_settings()
    if s.smtp_ssl is not None:
        return bool(s.smtp_ssl)
    return s.smtp_port == 465


def send_email(to: str, subject: str, text: str) -> bool:
    """Envoie un e-mail texte. Retourne True si réellement expédié (SMTP), False
    si journalisé en repli. Ne lève pas : un échec d'e-mail ne doit pas casser
    le parcours utilisateur."""
    try:
        _send_or_raise(to, subject, text)
        _log.info("E-mail envoyé à %s (%s)", to, subject)
        return True
    except NotConfigured:
        _log.warning(
            "[E-MAIL NON ENVOYÉ — SMTP non configuré] À: %s | Sujet: %s\n%s",
            to, subject, text,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        _log.error("Envoi e-mail échoué à %s : %s — contenu (avec le lien) ci-dessous\n%s",
                   to, exc, text)
        return False


class NotConfigured(RuntimeError):
    """Aucun serveur SMTP renseigné — repli sur la journalisation."""


def _send_or_raise(to: str, subject: str, text: str) -> None:
    """Expédie réellement, en laissant remonter l'erreur exacte du serveur.
    Utilisé par le test SMTP de l'admin, qui doit afficher la cause du refus
    (authentification, port, certificat) plutôt qu'un échec silencieux."""
    s = get_settings()
    if not s.smtp_host:
        raise NotConfigured("SMTP_HOST non renseigné")

    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.legal_contact_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    if use_implicit_tls():
        # Port 465 : la connexion est chiffrée d'emblée, pas de STARTTLS.
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15,
                              context=ssl.create_default_context()) as srv:
            if s.smtp_user:
                srv.login(s.smtp_user, s.smtp_password)
            srv.send_message(msg)
        return

    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as srv:
        if s.smtp_starttls:
            srv.starttls(context=ssl.create_default_context())
        if s.smtp_user:
            srv.login(s.smtp_user, s.smtp_password)
        srv.send_message(msg)


def diagnostics() -> dict:
    """État de la configuration e-mail, pour l'admin. Ne révèle aucun secret."""
    s = get_settings()
    configured = bool(s.smtp_host)
    return {
        "configured": configured,
        "host": s.smtp_host or None,
        "port": s.smtp_port,
        "mode": "TLS implicite (465)" if use_implicit_tls() else
                ("STARTTLS" if s.smtp_starttls else "aucun chiffrement"),
        "user": s.smtp_user or None,
        "has_password": bool(s.smtp_password),
        "from": s.smtp_from or s.legal_contact_email,
        "public_base_url": public_base_url() or None,
        "advice": (
            "Aucun SMTP configuré : les e-mails de vérification et de "
            "réinitialisation ne partent pas — leur contenu est écrit dans les "
            "journaux du conteneur. Renseignez SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD et SMTP_FROM."
            if not configured else
            "SMTP configuré — utilisez « Envoyer un e-mail de test » pour vérifier."
        ),
    }


def send_test(to: str) -> tuple[bool, str]:
    """E-mail de test pour l'admin. Retourne (succès, message explicite)."""
    s = get_settings()
    try:
        _send_or_raise(
            to,
            f"Test d'envoi — {s.site_name}",
            "Cet e-mail confirme que la configuration SMTP de votre plateforme "
            "fonctionne.\n\nLes messages de vérification d'adresse et de "
            "réinitialisation de mot de passe partiront désormais normalement.",
        )
        return True, f"E-mail de test envoyé à {to}."
    except NotConfigured:
        return False, ("Aucun serveur SMTP configuré (SMTP_HOST vide) : "
                       "les e-mails ne peuvent pas partir.")
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"Authentification refusée par {s.smtp_host} : {exc}"
    except (smtplib.SMTPException, OSError) as exc:
        hint = ""
        if not use_implicit_tls() and s.smtp_port == 465:
            hint = " — le port 465 exige un TLS implicite (SMTP_SSL=true)."
        elif use_implicit_tls() and s.smtp_port != 465:
            hint = " — TLS implicite demandé sur un port qui attend STARTTLS."
        return False, f"Échec de l'envoi via {s.smtp_host}:{s.smtp_port} : {exc}{hint}"


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
