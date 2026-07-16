"""Application settings — reads from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite:///./data/orchestrator.db"

    # Auth
    jwt_secret: str = "change-me"
    jwt_ttl_hours: int = 72
    admin_emails: str = ""

    # Coolify API
    coolify_api_url: str = "https://coolify.kechlab.com"
    coolify_api_token: str = ""
    coolify_project_uuid: str = "tso4ocs4k0g0oso8wg04k4c8"
    coolify_environment: str = "production"
    coolify_server_uuid: str = "mggo8cg8kokwcgk48sw0o4c4"
    coolify_destination_uuid: str = "xl2ufkvx4pjnjpj8hgsoq9wd"

    # LLM
    openrouter_api_key: str = ""  # clé partagée — repli si pas de provisioning
    openrouter_provisioning_key: str = ""  # clé maître : crée les clés par agent
    eur_usd_rate: float = 1.0  # conversion crédit € → plafond $ OpenRouter
    base_domain: str = "kechlab.com"

    # Plan
    plan_price_cents: int = 2900
    plan_monthly_credits_eur: float = 20.0
    default_model: str = "openai/gpt-4o"

    # Business — valeurs par défaut, modifiables par l'admin (table settings)
    deploy_price_eur: float = 29.0
    topup_amount_eur: float = 10.0  # montant proposé par défaut
    initial_credit_eur: float = 5.0
    # Frais de service prélevés sur les recharges (finance l'exploitation de la
    # plateforme). 0.10 = +10 % : le client reçoit 10 € de crédit IA et règle
    # 11 €. Modifiable par l'admin ; borné à 0–100 %.
    service_fee_rate: float = 0.10

    # ── Hébergement récurrent (revenu mensuel) ────────────────────────
    # Deux offres :
    #  • SANS ENGAGEMENT : location au mois, prolongation MANUELLE avant la date
    #    anniversaire (chrono FOMO). Plus chère car sans engagement.
    #  • ABONNEMENT (engagé 12 mois) : soit prélèvement Stripe automatique au
    #    mois, soit payé en une fois à l'année avec un mois offert. Moins cher.
    hosting_manual_eur: float = 29.0        # sans engagement, 1 mois, renouvellement manuel
    hosting_sub_monthly_eur: float = 19.0   # abonnement auto (prélevé Stripe), par mois
    hosting_annual_eur: float = 209.0       # 12 mois payés en 1 fois (19 × 11, 1 mois offert)
    hosting_grace_days: int = 0             # jours de tolérance après l'échéance avant suspension
    hosting_retention_days: int = 30        # jours de rétention (restaurable) avant suppression définitive

    # Stripe — les liens de paiement sont saisis dans l'admin (table settings).
    # Le secret de webhook, lui, est un secret d'infra : variable d'environnement.
    stripe_webhook_secret: str = ""

    # ── E-mail (vérification d'adresse & réinitialisation de mot de passe) ──
    # Si SMTP_HOST est renseigné, les e-mails partent réellement ; sinon, le
    # lien est JOURNALISÉ (repli de développement) — à configurer avant prod.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""          # défaut : legal_contact_email si vide
    smtp_starttls: bool = True
    # Base publique pour les liens des e-mails (défaut : site_url).
    public_base_url: str = ""
    # Montants de recharge proposés au client (liste, en euros). Chaque euro
    # rechargé relève d'autant le plafond de la clé OpenRouter dédiée.
    topup_amounts_eur: str = "5,10,20,50,100"

    # ── Mentions légales & RGPD ───────────────────────────────────────
    # À RENSEIGNER avant la mise en production (via variables d'environnement
    # ou .env). Les mentions légales et la politique de confidentialité sont
    # obligatoires en France/UE ; les valeurs ci-dessous sont des marqueurs
    # explicites pour ne pas publier un site sans les avoir remplies.
    site_name: str = "Plateforme Agent Hermes"
    site_url: str = "https://plateformeagentcoolify.kechlab.com"
    legal_publisher: str = "[À RENSEIGNER — nom de l'éditeur]"
    legal_status: str = "[À RENSEIGNER — forme juridique, ex. SASU / auto-entrepreneur]"
    legal_siret: str = "[À RENSEIGNER — SIRET / RCS]"
    legal_capital: str = ""  # capital social, si société
    legal_address: str = "[À RENSEIGNER — adresse postale]"
    legal_director: str = "[À RENSEIGNER — directeur de la publication]"
    legal_contact_email: str = "contact@kechlab.com"
    legal_vat: str = ""  # numéro de TVA intracommunautaire, si assujetti
    # Délégué / contact RGPD (peut être l'éditeur lui-même s'il n'y a pas de DPO)
    dpo_email: str = "privacy@kechlab.com"
    # Hébergeur (obligatoire dans les mentions légales) — infra Coolify/Hetzner
    host_name: str = "Hetzner Online GmbH"
    host_address: str = "Industriestr. 25, 91710 Gunzenhausen, Allemagne"
    host_contact: str = "https://www.hetzner.com — +49 (0)9831 505-0"
    # Version des CGV/confidentialité acceptée à l'inscription (à incrémenter
    # à chaque évolution substantielle pour re-solliciter le consentement)
    terms_version: str = "2026-07-16"


@lru_cache
def get_settings() -> Settings:
    return Settings()
