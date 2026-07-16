"""API routes — auth, agent CRUD, paiement simulé, provisioning."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .coolify import get_client
from .db import get_db
from .hosting import extend_period, hosting_status
from .models import Checkout, ProvisioningJob, PromoCode, Setting, Tenant, User
from .openrouter import get_keys_client
from .provisioning import ProvisioningEngine
from . import mailer, promo as promo_mod, stripe_pay
from .security import (
    _is_admin_email,
    create_token,
    current_user,
    hash_password,
    new_token,
    require_admin,
    verify_password,
)

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str
    password: str
    accept_terms: bool = False  # acceptation CGV + politique de confidentialité


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


class PromoUpsert(BaseModel):
    code: str
    kind: str = "percent"          # "percent" | "amount"
    value: float = 0.0
    scope: str = "all"             # all|deploy|topup|hosting
    active: bool = True
    max_uses: int | None = None
    expires_at: str | None = None  # ISO (optionnel)


class PromoApply(BaseModel):
    code: str
    scope: str = "all"
    amount_eur: float = 0.0


class AdminExtend(BaseModel):
    months: int = 0
    days: int = 0


class CreateAgentRequest(BaseModel):
    name: str
    subdomain: str
    model: str = "openai/gpt-4o"
    system_prompt: str = ""
    promo_code: str | None = None


class PricingUpdate(BaseModel):
    deploy_price_eur: float | None = None
    topup_amount_eur: float | None = None
    initial_credit_eur: float | None = None
    service_fee_rate: float | None = None  # 0.10 = +10 % sur les recharges
    hosting_manual_eur: float | None = None
    hosting_sub_monthly_eur: float | None = None
    hosting_annual_eur: float | None = None
    hosting_grace_days: float | None = None
    hosting_retention_days: float | None = None
    topup_amounts_eur: str | None = None  # liste "5,10,20,50,100"


class TopupRequest(BaseModel):
    amount_eur: float | None = None  # None → montant par défaut
    promo_code: str | None = None


class HostingRequest(BaseModel):
    plan: str = "manual"  # "manual" | "sub_monthly" | "sub_annual"
    promo_code: str | None = None


class DeployCheckoutRequest(BaseModel):
    promo_code: str | None = None


class StripeLinksUpdate(BaseModel):
    deploy: str | None = None
    hosting_manual: str | None = None
    hosting_sub: str | None = None
    hosting_annual: str | None = None
    topup: dict[str, str] | None = None  # {"10": "https://buy.stripe.com/...", ...}


# ── Pricing (variables business) ─────────────────────────────────────

PRICING_KEYS = (
    "deploy_price_eur",
    "topup_amount_eur",
    "initial_credit_eur",
    "service_fee_rate",
    "hosting_manual_eur",
    "hosting_sub_monthly_eur",
    "hosting_annual_eur",
    "hosting_grace_days",
    "hosting_retention_days",
)

# Plans d'hébergement → (clé de prix, nombre de mois crédités)
HOSTING_PLANS = {
    "manual": ("hosting_manual_eur", 1),       # sans engagement, prolongation d'un mois
    "sub_monthly": ("hosting_sub_monthly_eur", 1),  # abonnement auto, 1 mois par prélèvement
    "sub_annual": ("hosting_annual_eur", 12),  # 12 mois payés en une fois
}


def _promo_apply(db: Session, code: str | None, scope: str, amount: float) -> tuple[str | None, float, float]:
    """(code_normalisé|None, remise €, montant net). Lève HTTPException si invalide."""
    if not code or not code.strip():
        return None, 0.0, round(amount, 2)
    try:
        return promo_mod.apply(db, code, scope, amount)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _redeem_promo(db: Session, checkout: Checkout) -> None:
    """Incrémente le compteur d'usage du code promo au moment du paiement."""
    if not checkout.promo_code:
        return
    promo = db.get(PromoCode, checkout.promo_code)
    if promo:
        promo.used_count = (promo.used_count or 0) + 1


def topup_charge(credit_eur: float, fee_rate: float) -> float:
    """Montant à régler pour `credit_eur` de crédit IA, frais de service inclus.
    Le crédit reçu par le client (= plafond OpenRouter relevé) reste `credit_eur` ;
    la plateforme facture `credit_eur × (1 + fee_rate)`."""
    return round(credit_eur * (1 + max(fee_rate, 0.0)), 2)


def get_pricing(db: Session) -> dict[str, float]:
    """Valeurs business : défauts de config, surchargés par la table settings."""
    s = get_settings()
    pricing = {k: float(getattr(s, k)) for k in PRICING_KEYS}
    for row in db.scalars(select(Setting).where(Setting.key.in_(PRICING_KEYS))):
        try:
            pricing[row.key] = float(row.value)
        except ValueError:
            pass
    return pricing


TOPUP_AMOUNTS_KEY = "topup_amounts_eur"


def get_topup_amounts(db: Session) -> list[float]:
    """Montants de recharge proposés (config, surchargée par la table settings).
    Toujours trié, dédoublonné, positif — repli sur le montant par défaut."""
    raw = get_settings().topup_amounts_eur
    row = db.get(Setting, TOPUP_AMOUNTS_KEY)
    if row and row.value.strip():
        raw = row.value
    amounts: set[float] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = round(float(part), 2)
            if v > 0:
                amounts.add(v)
        except ValueError:
            pass
    return sorted(amounts) or [float(get_pricing(db)["topup_amount_eur"])]


@router.get("/api/pricing")
def pricing(db: Session = Depends(get_db)):
    p = get_pricing(db)
    p["topup_amounts_eur"] = get_topup_amounts(db)
    return p


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/legal/terms-version")
def terms_version():
    """Version des CGV/confidentialité en vigueur — affichée à l'inscription."""
    return {"terms_version": get_settings().terms_version}


@router.put("/api/admin/pricing")
def update_pricing(
    body: PricingUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Aucune valeur à mettre à jour")

    # La liste des montants de recharge est une chaîne "5,10,20,…" : on la
    # valide (au moins un montant positif) et on la stocke telle quelle.
    amounts_raw = updates.pop(TOPUP_AMOUNTS_KEY, None)
    if amounts_raw is not None:
        parsed = [p.strip() for p in str(amounts_raw).split(",") if p.strip()]
        valid = []
        for p in parsed:
            try:
                if float(p) > 0:
                    valid.append(str(round(float(p), 2)).rstrip("0").rstrip("."))
            except ValueError:
                raise HTTPException(400, f"Montant invalide dans la liste : « {p} »")
        if not valid:
            raise HTTPException(400, "La liste des montants doit contenir au moins un montant positif")
        _upsert_setting(db, TOPUP_AMOUNTS_KEY, ",".join(valid))

    for key, value in updates.items():
        if value < 0:
            raise HTTPException(400, f"{key} doit être positif ou nul")
        if key == "service_fee_rate" and value > 1:
            raise HTTPException(400, "Le taux de frais doit être entre 0 et 1 (0–100 %)")
        _upsert_setting(db, key, str(value))
    db.commit()
    result = get_pricing(db)
    result["topup_amounts_eur"] = get_topup_amounts(db)
    return result


def _upsert_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


# ── Auth ─────────────────────────────────────────────────────────────


@router.post("/api/auth/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.lower()
    # Consentement obligatoire (RGPD) : on ne crée pas de compte sans
    # acceptation explicite des CGV et de la politique de confidentialité,
    # et on horodate cette acceptation comme preuve.
    if not body.accept_terms:
        raise HTTPException(
            400, "Vous devez accepter les CGV et la politique de confidentialité"
        )
    if len(body.password) < 8:
        raise HTTPException(400, "Le mot de passe doit faire au moins 8 caractères")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Cet email est déjà enregistré")
    from datetime import datetime, timezone
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        consent_at=datetime.now(timezone.utc),
        consent_version=get_settings().terms_version,
        verification_token=new_token(),
    )
    # Promotion admin dès l'inscription si l'email est autorisé (le tout
    # premier compte créé avec un email d'ADMIN_EMAILS est admin immédiatement).
    if _is_admin_email(user.email):
        user.is_admin = True
    db.add(user)
    db.commit()
    # Envoi (ou journalisation) du lien de vérification — best effort.
    mailer.send_verification(user.email, user.verification_token)
    return {"token": create_token(user), "email": user.email, "email_verified": user.email_verified}


@router.get("/api/auth/verify")
def verify_email(token: str, db: Session = Depends(get_db)):
    """Valide l'adresse e-mail depuis le lien reçu. Renvoie une petite page HTML."""
    user = db.scalar(select(User).where(User.verification_token == token)) if token else None
    if not user:
        return HTMLResponse(_auth_page("Lien invalide",
            "Ce lien de vérification est invalide ou déjà utilisé."), status_code=400)
    user.email_verified = True
    user.verification_token = None
    db.commit()
    return HTMLResponse(_auth_page("Adresse vérifiée ✓",
        "Merci, votre adresse e-mail est confirmée. Vous pouvez revenir à votre tableau de bord."))


@router.post("/api/auth/resend-verification")
def resend_verification(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.email_verified:
        return {"status": "already_verified"}
    if not user.verification_token:
        user.verification_token = new_token()
        db.commit()
    mailer.send_verification(user.email, user.verification_token)
    return {"status": "sent"}


@router.post("/api/auth/forgot")
def forgot_password(body: ForgotRequest, db: Session = Depends(get_db)):
    """Envoie un lien de réinitialisation. Réponse toujours 200 (pas de
    divulgation de l'existence d'un compte)."""
    from datetime import datetime, timedelta, timezone

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user:
        user.reset_token = new_token()
        user.reset_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        mailer.send_password_reset(user.email, user.reset_token)
    return {"status": "ok"}


@router.post("/api/auth/reset")
def reset_password(body: ResetRequest, db: Session = Depends(get_db)):
    from datetime import datetime, timezone

    if len(body.password) < 8:
        raise HTTPException(400, "Le mot de passe doit faire au moins 8 caractères")
    user = db.scalar(select(User).where(User.reset_token == body.token)) if body.token else None
    if not user or not user.reset_expires:
        raise HTTPException(400, "Lien de réinitialisation invalide")
    exp = user.reset_expires if user.reset_expires.tzinfo else user.reset_expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > exp:
        raise HTTPException(400, "Lien de réinitialisation expiré — refaites une demande")
    user.password_hash = hash_password(body.password)
    user.reset_token = None
    user.reset_expires = None
    db.commit()
    return {"status": "password_reset"}


@router.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Identifiants invalides")
    # Promotion admin si l'email figure dans ADMIN_EMAILS (le jeton émis
    # porte alors la revendication admin dès la connexion).
    if not user.is_admin and _is_admin_email(user.email):
        user.is_admin = True
        db.commit()
    return {"token": create_token(user), "email": user.email}


@router.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin,
            "email_verified": user.email_verified}


def _auth_page(title: str, message: str) -> str:
    """Petite page HTML autonome (vérification e-mail, confirmation reset)."""
    s = get_settings()
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} — {s.site_name}</title>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#050507;color:#f2f3f7;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.box{{max-width:440px;padding:40px;text-align:center}}
h1{{font-size:22px;margin:0 0 12px}}p{{color:#aeb2bd;line-height:1.6}}
a{{display:inline-block;margin-top:20px;color:#8aa2ff;text-decoration:none;font-weight:600}}</style>
</head><body><div class="box"><h1>{title}</h1><p>{message}</p>
<a href="/">← Retour au tableau de bord</a></div></body></html>"""


# ── Agents ───────────────────────────────────────────────────────────


@router.get("/api/agents")
def list_agents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    # include_secrets : la liste ne renvoie que les agents de l'utilisateur,
    # le mot de passe est affiché (masqué) sur sa carte avec bouton copier.
    agents = db.scalars(select(Tenant).where(Tenant.user_id == user.id)).all()
    cfg = _hosting_cfg(db)
    return {"agents": [_agent_dict(a, include_secrets=True, hosting_cfg=cfg) for a in agents]}


@router.post("/api/agents")
def create_agent(
    body: CreateAgentRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    # Un domaine DNS est insensible à la casse : on normalise en minuscules
    # au lieu de rejeter une majuscule sans explication.
    subdomain = body.subdomain.strip().lower()
    if not re.match(r"^[a-z0-9-]+$", subdomain):
        raise HTTPException(
            400,
            "Le sous-domaine ne peut contenir que des lettres minuscules, "
            "des chiffres et des tirets (ex. : mon-agent)",
        )

    # Check uniqueness
    if db.scalar(select(Tenant).where(Tenant.subdomain == subdomain)):
        raise HTTPException(409, "Ce sous-domaine est déjà pris")

    # Create the tenant — le déploiement ne démarre qu'après paiement
    tenant = Tenant(
        user_id=user.id,
        name=body.name,
        subdomain=subdomain,
        model=body.model,
        system_prompt=body.system_prompt or f"Tu es {body.name}, l'agent IA personnel de ton propriétaire.",
        status="awaiting_payment",
    )
    db.add(tenant)
    db.commit()

    p = get_pricing(db)
    code, disc, net = _promo_apply(db, body.promo_code, "deploy", p["deploy_price_eur"])
    checkout = Checkout(
        user_id=user.id,
        tenant_id=tenant.id,
        kind="deploy",
        amount_eur=net,
        credit_eur=p["initial_credit_eur"],
        promo_code=code, discount_eur=disc,
    )
    db.add(checkout)
    db.commit()

    return {"agent": _agent_dict(tenant),
            "checkout_url": _checkout_url(db, checkout, email=user.email),
            "discount_eur": disc}


@router.get("/api/agents/{agent_id}")
def get_agent(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    d = _agent_dict(agent, include_secrets=True, hosting_cfg=_hosting_cfg(db))
    # Consommation réelle de la clé OpenRouter dédiée (qui paye quoi)
    if agent.openrouter_key_hash:
        keys = get_keys_client()
        if keys:
            try:
                info = keys.info(agent.openrouter_key_hash)
                d["api_key_limit_usd"] = info.get("limit")
                d["api_key_usage_usd"] = info.get("usage")
            except Exception:
                pass
    return d


@router.get("/api/agents/{agent_id}/jobs")
def get_jobs(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    jobs = db.scalars(
        select(ProvisioningJob)
        .where(ProvisioningJob.tenant_id == agent_id)
        .order_by(ProvisioningJob.created_at.desc())
    ).all()
    return {"jobs": [{"id": j.id, "status": j.status, "steps": j.steps, "error": j.error} for j in jobs]}


# ── Paiement simulé (même cycle de vie que Stripe Checkout) ──────────


@router.post("/api/agents/{agent_id}/checkout")
def get_or_create_deploy_checkout(
    agent_id: str,
    body: DeployCheckoutRequest | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Retrouve (ou recrée) la session de paiement d'un agent à payer."""
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    if agent.status != "awaiting_payment":
        raise HTTPException(409, "Cet agent est déjà payé")
    p = get_pricing(db)
    code, disc, net = _promo_apply(db, body.promo_code if body else None, "deploy", p["deploy_price_eur"])
    # Avec un code promo, on force un nouveau checkout (montant remisé) ; sinon on
    # réutilise une session en attente pour ne pas en empiler.
    checkout = None
    if not code:
        checkout = db.scalar(
            select(Checkout)
            .where(Checkout.tenant_id == agent_id, Checkout.kind == "deploy",
                   Checkout.status == "pending", Checkout.promo_code.is_(None))
            .order_by(Checkout.created_at.desc())
        )
    if not checkout:
        checkout = Checkout(
            user_id=user.id, tenant_id=agent_id, kind="deploy",
            amount_eur=net, credit_eur=p["initial_credit_eur"],
            promo_code=code, discount_eur=disc,
        )
        db.add(checkout)
        db.commit()
    return {"checkout_url": _checkout_url(db, checkout, email=user.email),
            "amount_eur": checkout.amount_eur, "discount_eur": checkout.discount_eur}


@router.post("/api/agents/{agent_id}/topup")
def create_topup_checkout(
    agent_id: str,
    body: TopupRequest | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")

    allowed = get_topup_amounts(db)
    # Montant choisi par le client, validé contre la liste proposée (on ne
    # crédite pas un montant arbitraire). Sans choix : le montant par défaut.
    amount = body.amount_eur if body and body.amount_eur is not None else get_pricing(db)["topup_amount_eur"]
    amount = round(float(amount), 2)
    if amount not in allowed:
        raise HTTPException(
            400,
            "Montant de recharge invalide. Montants proposés : "
            + ", ".join(f"{a:.0f} €" for a in allowed),
        )
    # Le client reçoit `amount` € de crédit IA (plafond OpenRouter) et règle
    # ce montant majoré des frais de service. amount_eur = ce qui est payé,
    # credit_eur = ce qui est crédité — la page de paiement détaille les deux.
    fee_rate = float(get_pricing(db)["service_fee_rate"])
    charged = topup_charge(amount, fee_rate)
    code, disc, net = _promo_apply(db, body.promo_code if body else None, "topup", charged)
    checkout = Checkout(
        user_id=user.id, tenant_id=agent_id, kind="topup",
        amount_eur=net, credit_eur=amount,
        promo_code=code, discount_eur=disc,
    )
    db.add(checkout)
    db.commit()
    return {
        "checkout_url": _checkout_url(db, checkout, email=user.email, amount_eur=amount),
        "amount_eur": net,          # à payer, frais inclus, remise déduite
        "credit_eur": amount,       # crédit IA reçu (inchangé)
        "fee_eur": round(charged - amount, 2),
        "fee_rate": fee_rate,
        "discount_eur": disc,
    }


@router.post("/api/agents/{agent_id}/hosting")
def create_hosting_checkout(
    agent_id: str,
    body: HostingRequest | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Souscrit/renouvelle l'hébergement de l'agent. Trois plans :
    - `manual`     : location sans engagement (29 €), prolonge d'un mois ;
    - `sub_monthly`: abonnement auto prélevé Stripe (19 €/mois) ;
    - `sub_annual` : 12 mois payés en une fois (209 €, un mois offert).
    Crée un Checkout `hosting` et renvoie le lien de paiement (Stripe si
    configuré, sinon page simulée)."""
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    plan = (body.plan if body else "manual") or "manual"
    if plan not in HOSTING_PLANS:
        raise HTTPException(400, "Plan invalide (manual, sub_monthly ou sub_annual)")
    price_key, _months = HOSTING_PLANS[plan]
    price = round(float(get_pricing(db)[price_key]), 2)
    code, disc, net = _promo_apply(db, body.promo_code if body else None, "hosting", price)
    checkout = Checkout(
        user_id=user.id, tenant_id=agent_id, kind="hosting", plan=plan,
        amount_eur=net, credit_eur=0.0,
        promo_code=code, discount_eur=disc,
    )
    db.add(checkout)
    db.commit()
    return {
        "checkout_url": _checkout_url(db, checkout, email=user.email, plan=plan),
        "plan": plan,
        "amount_eur": net,
        "discount_eur": disc,
    }


def _checkout_url(db: Session, checkout: Checkout, *, email: str | None,
                  amount_eur: float | None = None, plan: str | None = None) -> str:
    """Lien Stripe configuré pour ce paiement, sinon page de paiement simulée."""
    url = stripe_pay.checkout_redirect_url(
        db, checkout_id=checkout.id, kind=checkout.kind,
        amount_eur=amount_eur, plan=plan, email=email,
    )
    return url or f"/pay/{checkout.id}"


def _apply_paid_checkout(db: Session, checkout: Checkout) -> Tenant | None:
    """Applique un paiement réglé (page simulée ou webhook Stripe). Idempotent :
    ne fait rien si le checkout est déjà payé."""
    if checkout.status == "paid":
        return db.get(Tenant, checkout.tenant_id)
    tenant = db.get(Tenant, checkout.tenant_id)
    if not tenant:
        return None

    checkout.status = "paid"
    _redeem_promo(db, checkout)
    tenant.balance_eur = (tenant.balance_eur or 0.0) + (checkout.credit_eur or 0.0)

    if checkout.kind == "deploy" and tenant.status == "awaiting_payment":
        # Le déploiement inclut le premier mois — sans engagement par défaut :
        # le chrono FOMO poussera ensuite vers l'abonnement (moins cher).
        tenant.hosting_plan = "manual"
        tenant.hosting_paid_until = extend_period(None, 1)
        tenant.suspended_at = None
        tenant.status = "pending"
        db.commit()
        engine = ProvisioningEngine(db)
        job = engine.create_job(tenant)
        engine.run_job_async(job.id)
        return tenant

    if checkout.kind == "hosting":
        plan = checkout.plan or "manual"
        _months = HOSTING_PLANS.get(plan, ("", 1))[1]
        tenant.hosting_plan = plan
        tenant.hosting_paid_until = extend_period(tenant.hosting_paid_until, _months)
        _resume_if_suspended(db, tenant)
        db.commit()
        return tenant

    db.commit()
    # Recharge : relever le plafond de la clé OpenRouter dédiée du même montant.
    if checkout.kind == "topup" and tenant.openrouter_key_hash:
        keys = get_keys_client()
        if keys:
            try:
                keys.add_credit(
                    tenant.openrouter_key_hash,
                    checkout.credit_eur * get_settings().eur_usd_rate,
                )
            except Exception as exc:
                import logging
                logging.getLogger("api").warning(
                    "Recharge OpenRouter échouée pour %s : %s — solde local crédité, "
                    "relever le plafond manuellement", tenant.name, exc,
                )
    return tenant


def _resume_if_suspended(db: Session, tenant: Tenant) -> None:
    """Réactive un agent suspendu après paiement d'hébergement (relance Coolify)."""
    if tenant.suspended_at is None:
        return
    tenant.suspended_at = None
    if tenant.coolify_service_uuid:
        client = get_client()
        if client:
            client.start_service(tenant.coolify_service_uuid)


@router.post("/api/pay/{checkout_id}")
def pay(checkout_id: str, db: Session = Depends(get_db)):
    """Règle une session de paiement simulée (le « paiement carte »)."""
    checkout = db.get(Checkout, checkout_id)
    if not checkout:
        raise HTTPException(404, "Session de paiement inconnue ou expirée")
    if checkout.status == "paid":
        raise HTTPException(409, "Session déjà payée")
    tenant = _apply_paid_checkout(db, checkout)
    if not tenant:
        raise HTTPException(404, "Agent introuvable")
    return {"status": "paid", "kind": checkout.kind, "credited_eur": checkout.credit_eur}


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Reçoit les événements Stripe. `checkout.session.completed` crédite le
    Checkout local identifié par `client_reference_id` (déploiement, recharge,
    hébergement). `invoice.paid` prolonge un abonnement auto-débité, retrouvé
    par `stripe_subscription_id`. Signature vérifiée si un secret est configuré."""
    import json as _json

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = get_settings().stripe_webhook_secret
    if not stripe_pay.verify_signature(payload, sig, secret):
        raise HTTPException(400, "Signature Stripe invalide")
    try:
        event = _json.loads(payload)
    except _json.JSONDecodeError:
        raise HTTPException(400, "Corps d'événement illisible")

    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if etype == "checkout.session.completed":
        ref = obj.get("client_reference_id")
        if ref:
            checkout = db.get(Checkout, ref)
            if checkout and checkout.status != "paid":
                tenant = _apply_paid_checkout(db, checkout)
                # Mémorise l'abonnement Stripe (mode auto) pour les renouvellements.
                sub = obj.get("subscription")
                if tenant and sub and checkout.kind == "hosting":
                    tenant.stripe_subscription_id = sub
                    db.commit()
        return {"received": True}

    if etype == "invoice.paid":
        sub = obj.get("subscription")
        if sub:
            tenant = db.scalar(select(Tenant).where(Tenant.stripe_subscription_id == sub))
            if tenant:
                # Renouvellement auto (abonnement Stripe) : prolonge d'un mois
                # et réactive si suspendu.
                tenant.hosting_plan = "sub_monthly"
                tenant.hosting_paid_until = extend_period(tenant.hosting_paid_until, 1)
                _resume_if_suspended(db, tenant)
                db.commit()
        return {"received": True}

    return {"received": True, "ignored": etype}


@router.get("/pay/{checkout_id}", response_class=HTMLResponse)
def pay_page(checkout_id: str, db: Session = Depends(get_db)):
    """Page de paiement de la démo — même emplacement et même geste que le
    futur Stripe Checkout : le client voit le montant, clique PAYER, le
    déploiement (ou la recharge) se lance. Sera remplacée telle quelle par
    la page hébergée Stripe en production."""
    checkout = db.get(Checkout, checkout_id)
    tenant = db.get(Tenant, checkout.tenant_id) if checkout else None
    if not checkout or not tenant or checkout.status == "paid":
        return HTMLResponse(
            "<h2 style='font-family:system-ui'>Session de paiement inconnue ou expirée.</h2>",
            status_code=404,
        )
    amount = f"{checkout.amount_eur:.2f}".replace(".", ",")
    if checkout.kind == "deploy":
        description = f"Déploiement de l'agent « {tenant.name} »"
        note_credit = f"{checkout.credit_eur:.2f}".replace(".", ",") + " € de crédit IA offerts au lancement (1er mois d'hébergement inclus)"
    elif checkout.kind == "hosting":
        labels = {
            "manual": ("Hébergement sans engagement", "Hébergement prolongé d'un mois"),
            "sub_monthly": ("Abonnement hébergement mensuel", "Abonnement mensuel — prolongé d'un mois"),
            "sub_annual": ("Abonnement hébergement annuel", "Hébergement prolongé de 12 mois (1 mois offert)"),
        }
        title, note_credit = labels.get(checkout.plan or "manual", labels["manual"])
        description = f"{title} — {tenant.name}"
    else:
        description = f"Recharge de crédit IA — {tenant.name}"
        # amount_eur est net (remise déduite) ; on récupère les frais réels en
        # réintégrant la remise : frais = (net + remise) − crédit.
        fee = round(checkout.amount_eur + (checkout.discount_eur or 0.0) - checkout.credit_eur, 2)
        credit_fr = f"{checkout.credit_eur:.2f}".replace(".", ",")
        if fee > 0:
            fee_fr = f"{fee:.2f}".replace(".", ",")
            note_credit = (
                f"{credit_fr} € de crédit IA crédités sur votre agent "
                f"(+ {fee_fr} € de frais de service inclus)"
            )
        else:
            note_credit = f"{credit_fr} € crédités sur votre agent"

    # Ligne de remise (tous types de paiement) si un code promo a été appliqué.
    promo_note = ""
    if checkout.promo_code and (checkout.discount_eur or 0) > 0:
        disc_fr = f"{checkout.discount_eur:.2f}".replace(".", ",")
        promo_note = f'<div class="promo">🏷️ Code {checkout.promo_code} — remise de {disc_fr} €</div>'

    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paiement — Hermes</title>
<style>
  body {{ font-family:Inter,"Helvetica Neue",system-ui,sans-serif; background:#050507; color:#f2f3f7;
         display:grid; place-items:center; min-height:100vh; margin:0 }}
  .card {{ background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.14);
          box-shadow:0 20px 60px rgba(0,0,0,0.45); backdrop-filter:blur(18px);
          border-radius:14px; padding:40px; width:min(400px,90vw); text-align:center }}
  .badge {{ font-size:13px; letter-spacing:.18em; text-transform:uppercase; color:#8a90a3; margin-bottom:16px }}
  .amount {{ font-size:40px; font-weight:600; letter-spacing:-0.02em; margin:8px 0 }}
  .desc {{ color:#8a90a3; margin-bottom:8px }}
  .credit {{ color:#e0a458; font-size:13px; margin-bottom:12px }}
  .promo {{ color:#4caf7d; font-size:13px; font-weight:600; margin-bottom:20px }}
  button {{ width:100%; padding:16px; border:0; border-radius:9px; background:#635bff; color:#fff;
           font-size:17px; font-weight:600; cursor:pointer }}
  button:disabled {{ opacity:.6; cursor:wait }}
  .ok {{ color:#4caf7d; font-weight:600; margin-top:16px; display:none }}
  .note {{ color:#8a90a3; font-size:13px; margin-top:16px }}
  a {{ color:#8a90a3; font-size:13px; display:inline-block; margin-top:12px }}
</style></head><body>
<div class="card">
  <div class="badge">Paiement sécurisé · simulation Stripe</div>
  <div class="amount">{amount} €</div>
  <div class="desc">{description}</div>
  <div class="credit">{note_credit}</div>
  {promo_note}
  <button id="pay" onclick="pay()">PAYER {amount} €</button>
  <p class="ok" id="ok">✔ Paiement accepté — retour au tableau de bord…</p>
  <p class="note">Aucun débit réel. En production, cette page est remplacée par Stripe Checkout.</p>
  <a href="/">Annuler et revenir</a>
</div>
<script>
async function pay() {{
  const btn = document.getElementById('pay');
  btn.disabled = true; btn.textContent = 'Paiement en cours…';
  const res = await fetch('/api/pay/{checkout_id}', {{method:'POST'}});
  if (res.ok) {{
    document.getElementById('ok').style.display = 'block';
    btn.style.display = 'none';
    setTimeout(() => location.href = '/', 1200);
  }} else {{
    const body = await res.json().catch(() => ({{}}));
    btn.disabled = false; btn.textContent = 'PAYER {amount} €';
    alert(body.detail || 'Échec du paiement');
  }}
}}
</script></body></html>"""


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(token: str = ""):
    """Page de saisie du nouveau mot de passe (ouverte depuis l'e-mail)."""
    s = get_settings()
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nouveau mot de passe — {s.site_name}</title>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#050507;color:#f2f3f7;
display:grid;place-items:center;min-height:100vh;margin:0}}
.card{{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);border-radius:14px;
padding:36px;width:min(400px,90vw)}}h1{{font-size:20px;margin:0 0 18px}}
input{{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid rgba(255,255,255,.2);
background:rgba(255,255,255,.05);color:#f2f3f7;box-sizing:border-box}}
button{{width:100%;padding:14px;border:0;border-radius:9px;background:#635bff;color:#fff;font-weight:600;cursor:pointer;margin-top:8px}}
.msg{{margin-top:14px;font-size:14px}}a{{color:#8aa2ff;font-size:13px;display:inline-block;margin-top:14px}}</style>
</head><body><div class="card"><h1>Choisir un nouveau mot de passe</h1>
<input id="pw" type="password" placeholder="Nouveau mot de passe (8 caractères min.)" autocomplete="new-password">
<input id="pw2" type="password" placeholder="Confirmer le mot de passe" autocomplete="new-password">
<button id="go" onclick="reset()">Réinitialiser</button>
<div class="msg" id="msg"></div><a href="/">← Retour</a></div>
<script>
const token = new URLSearchParams(location.search).get('token') || '{token}';
async function reset() {{
  const pw = document.getElementById('pw').value, pw2 = document.getElementById('pw2').value;
  const msg = document.getElementById('msg');
  if (pw.length < 8) {{ msg.textContent = 'Mot de passe trop court (8 caractères min.).'; return; }}
  if (pw !== pw2) {{ msg.textContent = 'Les deux mots de passe ne correspondent pas.'; return; }}
  const r = await fetch('/api/auth/reset', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{token, password: pw}})}});
  const b = await r.json().catch(() => ({{}}));
  if (r.ok) {{ msg.style.color = '#4caf7d'; msg.textContent = '✔ Mot de passe changé. Vous pouvez vous connecter.';
    setTimeout(() => location.href = '/', 1800); }}
  else {{ msg.style.color = '#e0665a'; msg.textContent = b.detail || 'Échec de la réinitialisation.'; }}
}}
</script></body></html>"""


# ── Suppression ──────────────────────────────────────────────────────


@router.post("/api/agents/{agent_id}/restart")
def restart_agent(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Redémarre les conteneurs — utile après avoir réglé le domaine dans Coolify."""
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    if not agent.coolify_service_uuid:
        raise HTTPException(409, "Cet agent n'a pas encore de service Coolify")
    client = get_client()
    if not client:
        raise HTTPException(503, "Coolify API non configurée")
    if not client.restart_service(agent.coolify_service_uuid):
        raise HTTPException(502, "Redémarrage refusé par Coolify")
    return {"status": "restarting"}


def _purge_tenant(db: Session, agent: Tenant) -> None:
    """Détruit un agent partout : clé OpenRouter dédiée, service Coolify
    (conteneurs + volumes), puis toutes ses lignes en base. Réutilisé par la
    suppression d'un agent et par l'effacement complet du compte (RGPD)."""
    if agent.openrouter_key_hash:
        keys = get_keys_client()
        if keys:
            keys.delete(agent.openrouter_key_hash)

    if agent.coolify_service_uuid:
        client = get_client()
        if client and not client.delete_service(agent.coolify_service_uuid):
            # Non-fatal : le service a pu être supprimé à la main dans Coolify
            import logging
            logging.getLogger("api").warning(
                "Suppression Coolify échouée pour %s (service %s) — on retire quand même l'agent",
                agent.name, agent.coolify_service_uuid,
            )

    db.execute(sa_delete(ProvisioningJob).where(ProvisioningJob.tenant_id == agent.id))
    db.execute(sa_delete(Checkout).where(Checkout.tenant_id == agent.id))
    db.delete(agent)


@router.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Détruit l'agent : service Coolify (conteneurs, volumes), clé API, puis DB."""
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")

    # La clé OpenRouter dédiée part avec l'agent (le crédit restant est perdu,
    # c'est annoncé dans la confirmation de suppression)
    _purge_tenant(db, agent)
    db.commit()
    return {"status": "deleted"}


# ── RGPD : accès/portabilité (Art. 15/20) et effacement (Art. 17) ────


@router.get("/api/account/export")
def export_account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Exporte toutes les données personnelles de l'utilisateur (portabilité).

    Le mot de passe haché (un secret d'authentification, pas une donnée à
    porter) est exclu. Tout le reste — compte, consentement, agents, paiements
    — est fourni dans un format structuré et lisible."""
    agents = db.scalars(select(Tenant).where(Tenant.user_id == user.id)).all()
    agent_ids = [a.id for a in agents]
    checkouts = (
        db.scalars(select(Checkout).where(Checkout.tenant_id.in_(agent_ids))).all()
        if agent_ids else []
    )
    return {
        "exported_at": _now_iso(),
        "account": {
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "is_admin": user.is_admin,
            "consent": {
                "accepted_at": user.consent_at.isoformat() if user.consent_at else None,
                "version": user.consent_version,
            },
        },
        "agents": [
            {
                "id": a.id, "name": a.name, "subdomain": a.subdomain,
                "model": a.model, "system_prompt": a.system_prompt,
                "status": a.status, "balance_eur": a.balance_eur or 0.0,
                "url": a.instance_url,
                "hosting_plan": a.hosting_plan or "none",
                "hosting_paid_until": a.hosting_paid_until.isoformat() if a.hosting_paid_until else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in agents
        ],
        "payments": [
            {
                "id": c.id, "tenant_id": c.tenant_id, "kind": c.kind,
                "amount_eur": c.amount_eur, "credit_eur": c.credit_eur,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in checkouts
        ],
    }


@router.delete("/api/account")
def delete_account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Efface définitivement le compte et TOUTES ses données (droit à
    l'effacement) : chaque agent est détruit partout (Coolify + OpenRouter),
    puis le compte lui-même. Action irréversible."""
    agents = db.scalars(select(Tenant).where(Tenant.user_id == user.id)).all()
    for agent in agents:
        _purge_tenant(db, agent)
    db.delete(user)
    db.commit()
    return {"status": "account_deleted", "agents_removed": len(agents)}


# ── Admin ────────────────────────────────────────────────────────────


@router.get("/api/admin/agents")
def list_all_agents(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    agents = db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()
    cfg = _hosting_cfg(db)
    # email du propriétaire (une seule requête)
    owners = {u.id: u.email for u in db.scalars(select(User)).all()}
    out = []
    for a in agents:
        d = _agent_dict(a, include_secrets=True, hosting_cfg=cfg)
        d["owner_email"] = owners.get(a.user_id, "?")
        out.append(d)
    return {"agents": out}


@router.post("/api/admin/agents/{agent_id}/suspend")
def admin_suspend(agent_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Suspend immédiatement un agent (stop Coolify), sans attendre l'échéance."""
    from datetime import datetime, timezone

    agent = db.get(Tenant, agent_id)
    if not agent:
        raise HTTPException(404, "Agent non trouvé")
    if agent.suspended_at is None:
        agent.suspended_at = datetime.now(timezone.utc)
        if agent.coolify_service_uuid:
            client = get_client()
            if client:
                client.stop_service(agent.coolify_service_uuid)
        db.commit()
    return {"status": "suspended", "hosting": _agent_dict(agent, hosting_cfg=_hosting_cfg(db))["hosting"]}


@router.post("/api/admin/agents/{agent_id}/extend")
def admin_extend(agent_id: str, body: AdminExtend, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """Offre du temps d'hébergement (ajustement commercial) : prolonge l'échéance
    de `months` mois et/ou `days` jours, et réactive si l'agent était suspendu."""
    from datetime import timedelta

    agent = db.get(Tenant, agent_id)
    if not agent:
        raise HTTPException(404, "Agent non trouvé")
    base = extend_period(agent.hosting_paid_until, max(int(body.months), 0))
    if body.days:
        base = base + timedelta(days=int(body.days))
    agent.hosting_paid_until = base
    if agent.hosting_plan == "none":
        agent.hosting_plan = "manual"
    _resume_if_suspended(db, agent)
    db.commit()
    return {"status": "extended", "hosting": _agent_dict(agent, hosting_cfg=_hosting_cfg(db))["hosting"]}


@router.delete("/api/admin/agents/{agent_id}")
def admin_delete_agent(agent_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Supprime définitivement n'importe quel agent (Coolify + clé + DB)."""
    agent = db.get(Tenant, agent_id)
    if not agent:
        raise HTTPException(404, "Agent non trouvé")
    _purge_tenant(db, agent)
    db.commit()
    return {"status": "deleted"}


# ── Codes promo ──────────────────────────────────────────────────────


def _promo_dict(p: PromoCode) -> dict:
    return {
        "code": p.code, "kind": p.kind, "value": p.value, "scope": p.scope,
        "active": p.active, "max_uses": p.max_uses, "used_count": p.used_count,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
    }


@router.post("/api/promo/validate")
def validate_promo(body: PromoApply, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Prévisualise la remise d'un code pour le client (avant paiement)."""
    try:
        code, disc, net = promo_mod.apply(db, body.code, body.scope, body.amount_eur)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"code": code, "discount_eur": disc, "net_eur": net}


@router.get("/api/admin/promos")
def list_promos(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    promos = db.scalars(select(PromoCode).order_by(PromoCode.created_at.desc())).all()
    return {"promos": [_promo_dict(p) for p in promos]}


@router.post("/api/admin/promos")
def upsert_promo(body: PromoUpsert, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Crée ou met à jour un code promo."""
    from datetime import datetime

    code = promo_mod.normalize(body.code)
    if not code:
        raise HTTPException(400, "Code requis")
    if body.kind not in ("percent", "amount"):
        raise HTTPException(400, "Type invalide (percent ou amount)")
    if body.scope not in ("all", "deploy", "topup", "hosting"):
        raise HTTPException(400, "Périmètre invalide")
    if body.value <= 0:
        raise HTTPException(400, "La valeur doit être positive")
    if body.kind == "percent" and body.value > 100:
        raise HTTPException(400, "Un pourcentage ne peut dépasser 100")
    expires = None
    if body.expires_at:
        try:
            expires = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Date d'expiration invalide (format ISO)")
    promo = db.get(PromoCode, code)
    if not promo:
        promo = PromoCode(code=code)
        db.add(promo)
    promo.kind = body.kind
    promo.value = round(float(body.value), 2)
    promo.scope = body.scope
    promo.active = body.active
    promo.max_uses = body.max_uses
    promo.expires_at = expires
    db.commit()
    return _promo_dict(promo)


@router.delete("/api/admin/promos/{code}")
def delete_promo(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    promo = db.get(PromoCode, promo_mod.normalize(code))
    if not promo:
        raise HTTPException(404, "Code inconnu")
    db.delete(promo)
    db.commit()
    return {"status": "deleted"}


@router.get("/api/admin/stripe")
def get_stripe_links(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Liens de paiement Stripe configurés + état du secret de webhook."""
    return {
        "links": stripe_pay.get_links(db),
        "webhook_configured": bool(get_settings().stripe_webhook_secret),
        "webhook_url": "/api/stripe/webhook",
    }


@router.put("/api/admin/stripe")
def update_stripe_links(
    body: StripeLinksUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enregistre les liens Stripe (déploiement, hébergement mensuel/annuel,
    recharges par montant). Un champ absent (None) n'est pas modifié."""
    import json as _json

    if body.deploy is not None:
        _upsert_setting(db, stripe_pay.LINK_KEYS["deploy"], body.deploy.strip())
    if body.hosting_manual is not None:
        _upsert_setting(db, stripe_pay.LINK_KEYS["hosting_manual"], body.hosting_manual.strip())
    if body.hosting_sub is not None:
        _upsert_setting(db, stripe_pay.LINK_KEYS["hosting_sub"], body.hosting_sub.strip())
    if body.hosting_annual is not None:
        _upsert_setting(db, stripe_pay.LINK_KEYS["hosting_annual"], body.hosting_annual.strip())
    if body.topup is not None:
        clean = {str(k): str(v).strip() for k, v in body.topup.items() if str(v).strip()}
        _upsert_setting(db, stripe_pay.TOPUP_LINKS_KEY, _json.dumps(clean))
    db.commit()
    return {"links": stripe_pay.get_links(db)}


@router.post("/api/admin/agents/{agent_id}/restore")
def restore_agent(agent_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Restaure un agent suspendu (moins d'un mois de retard) : relance les
    conteneurs et repousse l'échéance d'un mois offert le temps de régulariser."""
    agent = db.get(Tenant, agent_id)
    if not agent:
        raise HTTPException(404, "Agent non trouvé")
    if agent.suspended_at is None:
        raise HTTPException(409, "Cet agent n'est pas suspendu")
    agent.hosting_paid_until = extend_period(None, 1)
    _resume_if_suspended(db, agent)
    db.commit()
    return {"status": "restored", "hosting": _agent_dict(agent, hosting_cfg=_hosting_cfg(db))["hosting"]}


@router.post("/api/admin/enforce-hosting")
def admin_enforce_hosting(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Déclenche manuellement le balayage (suspension des échus, suppression des
    dépassements de rétention). Utile pour vérifier sans attendre la tâche de fond."""
    return {"changed": enforce_hosting(db)}


def enforce_hosting(db: Session, now=None) -> int:
    """Applique le cycle de vie de l'hébergement sur tous les agents :
    - échéance (+grâce) dépassée et non encore suspendu → suspend (stop Coolify) ;
    - rétention dépassée depuis la suspension → suppression définitive.
    Retourne le nombre d'agents modifiés."""
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    grace, retention = _hosting_cfg(db)
    changed = 0
    for agent in list(db.scalars(select(Tenant)).all()):
        if agent.hosting_paid_until is None and agent.suspended_at is None:
            continue
        st = hosting_status(agent.hosting_paid_until, agent.suspended_at, grace, retention, now)
        if st.state in ("suspended", "deletable") and agent.suspended_at is None:
            # Ancre la suspension à la date d'échéance réelle (pas « maintenant »),
            # pour ne pas rallonger la fenêtre de restauration si le balayage tarde.
            agent.suspended_at = st.suspend_at
            if agent.coolify_service_uuid:
                client = get_client()
                if client:
                    client.stop_service(agent.coolify_service_uuid)
            changed += 1
            st = hosting_status(agent.hosting_paid_until, agent.suspended_at, grace, retention, now)
        if st.state == "deletable":
            _purge_tenant(db, agent)
            changed += 1
    if changed:
        db.commit()
    return changed


@router.post("/api/admin/agents/{agent_id}/redeploy")
def redeploy(agent_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    agent = db.get(Tenant, agent_id)
    if not agent:
        raise HTTPException(404, "Agent non trouvé")
    agent.status = "pending"
    db.commit()
    engine = ProvisioningEngine(db)
    job = engine.create_job(agent)
    engine.run_job_async(job.id)
    return {"job_id": job.id}


# ── Helpers ──────────────────────────────────────────────────────────


def _hosting_cfg(db: Session) -> tuple[int, int]:
    """(jours de grâce, jours de rétention) — défauts config surchargés par l'admin."""
    p = get_pricing(db)
    return int(p["hosting_grace_days"]), int(p["hosting_retention_days"])


def _agent_dict(a: Tenant, include_secrets: bool = False,
                hosting_cfg: tuple[int, int] | None = None) -> dict:
    d = {
        "id": a.id,
        "name": a.name,
        "subdomain": a.subdomain,
        "model": a.model,
        "status": a.status,
        "url": a.instance_url,
        "balance_eur": a.balance_eur or 0.0,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    grace, retention = hosting_cfg or (0, 30)
    st = hosting_status(a.hosting_paid_until, a.suspended_at, grace, retention)
    d["hosting"] = {
        "plan": a.hosting_plan or "none",
        "state": st.state,
        "paid_until": st.paid_until.isoformat() if st.paid_until else None,
        "suspend_at": st.suspend_at.isoformat() if st.suspend_at else None,
        "delete_at": st.delete_at.isoformat() if st.delete_at else None,
        "seconds_left": st.seconds_left,
        "suspended": a.suspended_at is not None,
    }
    if include_secrets:
        d["password"] = a.instance_password
        d["coolify_service_uuid"] = a.coolify_service_uuid
    return d
