"""API routes — auth, agent CRUD, paiement simulé, provisioning."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .coolify import get_client
from .db import get_db
from .models import Checkout, ProvisioningJob, Setting, Tenant, User
from .openrouter import get_keys_client
from .provisioning import ProvisioningEngine
from .security import create_token, current_user, hash_password, require_admin, verify_password

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateAgentRequest(BaseModel):
    name: str
    subdomain: str
    model: str = "openai/gpt-4o"
    system_prompt: str = ""


class PricingUpdate(BaseModel):
    deploy_price_eur: float | None = None
    topup_amount_eur: float | None = None
    initial_credit_eur: float | None = None


# ── Pricing (variables business) ─────────────────────────────────────

PRICING_KEYS = ("deploy_price_eur", "topup_amount_eur", "initial_credit_eur")


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


@router.get("/api/pricing")
def pricing(db: Session = Depends(get_db)):
    return get_pricing(db)


@router.put("/api/admin/pricing")
def update_pricing(
    body: PricingUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Aucune valeur à mettre à jour")
    for key, value in updates.items():
        if value < 0:
            raise HTTPException(400, f"{key} doit être positif ou nul")
        row = db.get(Setting, key)
        if row:
            row.value = str(value)
        else:
            db.add(Setting(key=key, value=str(value)))
    db.commit()
    return get_pricing(db)


# ── Auth ─────────────────────────────────────────────────────────────


@router.post("/api/auth/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Cet email est déjà enregistré")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return {"token": create_token(user), "email": user.email}


@router.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Identifiants invalides")
    # Promote admin if email is in the allowed list
    from .config import get_settings
    allowed = {e.strip().lower() for e in get_settings().admin_emails.split(",") if e.strip()}
    if email_lower := user.email.lower() in allowed and not user.is_admin:
        user.is_admin = True
        db.commit()
    return {"token": create_token(user), "email": user.email}


@router.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin}


# ── Agents ───────────────────────────────────────────────────────────


@router.get("/api/agents")
def list_agents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    # include_secrets : la liste ne renvoie que les agents de l'utilisateur,
    # le mot de passe est affiché (masqué) sur sa carte avec bouton copier.
    agents = db.scalars(select(Tenant).where(Tenant.user_id == user.id)).all()
    return {"agents": [_agent_dict(a, include_secrets=True) for a in agents]}


@router.post("/api/agents")
def create_agent(
    body: CreateAgentRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    # Validate subdomain (alphanumeric + hyphens only)
    if not re.match(r"^[a-z0-9-]+$", body.subdomain):
        raise HTTPException(400, "Le sous-domaine ne doit contenir que des lettres, chiffres et tirets")

    # Check uniqueness
    if db.scalar(select(Tenant).where(Tenant.subdomain == body.subdomain)):
        raise HTTPException(409, "Ce sous-domaine est déjà pris")

    # Create the tenant — le déploiement ne démarre qu'après paiement
    tenant = Tenant(
        user_id=user.id,
        name=body.name,
        subdomain=body.subdomain,
        model=body.model,
        system_prompt=body.system_prompt or f"Tu es {body.name}, l'agent IA personnel de ton propriétaire.",
        status="awaiting_payment",
    )
    db.add(tenant)
    db.commit()

    p = get_pricing(db)
    checkout = Checkout(
        user_id=user.id,
        tenant_id=tenant.id,
        kind="deploy",
        amount_eur=p["deploy_price_eur"],
        credit_eur=p["initial_credit_eur"],
    )
    db.add(checkout)
    db.commit()

    return {"agent": _agent_dict(tenant), "checkout_url": f"/pay/{checkout.id}"}


@router.get("/api/agents/{agent_id}")
def get_agent(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    d = _agent_dict(agent, include_secrets=True)
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
    agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Retrouve (ou recrée) la session de paiement d'un agent à payer."""
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    if agent.status != "awaiting_payment":
        raise HTTPException(409, "Cet agent est déjà payé")
    checkout = db.scalar(
        select(Checkout)
        .where(Checkout.tenant_id == agent_id, Checkout.kind == "deploy", Checkout.status == "pending")
        .order_by(Checkout.created_at.desc())
    )
    if not checkout:
        p = get_pricing(db)
        checkout = Checkout(
            user_id=user.id, tenant_id=agent_id, kind="deploy",
            amount_eur=p["deploy_price_eur"], credit_eur=p["initial_credit_eur"],
        )
        db.add(checkout)
        db.commit()
    return {"checkout_url": f"/pay/{checkout.id}"}


@router.post("/api/agents/{agent_id}/topup")
def create_topup_checkout(
    agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    p = get_pricing(db)
    checkout = Checkout(
        user_id=user.id, tenant_id=agent_id, kind="topup",
        amount_eur=p["topup_amount_eur"], credit_eur=p["topup_amount_eur"],
    )
    db.add(checkout)
    db.commit()
    return {"checkout_url": f"/pay/{checkout.id}"}


@router.post("/api/pay/{checkout_id}")
def pay(checkout_id: str, db: Session = Depends(get_db)):
    """Règle une session de paiement simulée (le « paiement carte »)."""
    checkout = db.get(Checkout, checkout_id)
    if not checkout:
        raise HTTPException(404, "Session de paiement inconnue ou expirée")
    if checkout.status == "paid":
        raise HTTPException(409, "Session déjà payée")
    tenant = db.get(Tenant, checkout.tenant_id)
    if not tenant:
        raise HTTPException(404, "Agent introuvable")

    checkout.status = "paid"
    tenant.balance_eur = (tenant.balance_eur or 0.0) + checkout.credit_eur
    if checkout.kind == "deploy" and tenant.status == "awaiting_payment":
        tenant.status = "pending"
        db.commit()
        engine = ProvisioningEngine(db)
        job = engine.create_job(tenant)
        engine.run_job_async(job.id)
    else:
        db.commit()
        # Recharge : relever le plafond de la clé OpenRouter dédiée du même
        # montant — c'est elle qui matérialise le crédit du client.
        if checkout.kind == "topup" and tenant.openrouter_key_hash:
            keys = get_keys_client()
            if keys:
                from .config import get_settings
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
    return {"status": "paid", "kind": checkout.kind, "credited_eur": checkout.credit_eur}


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
        note_credit = f"{checkout.credit_eur:.2f}".replace(".", ",") + " € de crédit IA offerts au lancement"
    else:
        description = f"Recharge de crédit IA — {tenant.name}"
        note_credit = f"{checkout.credit_eur:.2f}".replace(".", ",") + " € crédités sur votre agent"
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
  .credit {{ color:#e0a458; font-size:13px; margin-bottom:28px }}
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


@router.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Détruit l'agent : service Coolify (conteneurs, volumes), clé API, puis DB."""
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")

    # La clé OpenRouter dédiée part avec l'agent (le crédit restant est perdu,
    # c'est annoncé dans la confirmation de suppression)
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

    db.execute(sa_delete(ProvisioningJob).where(ProvisioningJob.tenant_id == agent_id))
    db.execute(sa_delete(Checkout).where(Checkout.tenant_id == agent_id))
    db.delete(agent)
    db.commit()
    return {"status": "deleted"}


# ── Admin ────────────────────────────────────────────────────────────


@router.get("/api/admin/agents")
def list_all_agents(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    agents = db.scalars(select(Tenant)).all()
    return {"agents": [_agent_dict(a) for a in agents]}


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


def _agent_dict(a: Tenant, include_secrets: bool = False) -> dict:
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
    if include_secrets:
        d["password"] = a.instance_password
        d["coolify_service_uuid"] = a.coolify_service_uuid
    return d
