"""API routes — auth, agent CRUD, provisioning."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ProvisioningJob, Tenant, User
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
    model: str = "openai/gpt-4o-mini"
    system_prompt: str = ""


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
    agents = db.scalars(select(Tenant).where(Tenant.user_id == user.id)).all()
    return {"agents": [_agent_dict(a) for a in agents]}


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

    # Create the tenant
    tenant = Tenant(
        user_id=user.id,
        name=body.name,
        subdomain=body.subdomain,
        model=body.model,
        system_prompt=body.system_prompt or f"Tu es {body.name}, l'agent IA personnel de ton propriétaire.",
        status="pending",
    )
    db.add(tenant)
    db.commit()

    # Start provisioning in background
    engine = ProvisioningEngine(db)
    job = engine.create_job(tenant)
    engine.run_job_async(job.id)

    return {"agent": _agent_dict(tenant), "job_id": job.id}


@router.get("/api/agents/{agent_id}")
def get_agent(agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    agent = db.get(Tenant, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent non trouvé")
    return _agent_dict(agent, include_secrets=True)


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
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    if include_secrets:
        d["password"] = a.instance_password
        d["coolify_service_uuid"] = a.coolify_service_uuid
    return d
