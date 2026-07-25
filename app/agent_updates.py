"""Version checking and updates for Hermes agent components."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models import Setting, Tenant, User


# GitHub repo refs
WEBUI_REPO = "nesquena/hermes-webui"
AGENT_REPO = "NousResearch/hermes-agent"


async def get_latest_versions() -> dict[str, str | None]:
    """Fetch latest versions from GitHub (webui release, agent commit)."""
    versions = {"webui": None, "agent": None}

    async with httpx.AsyncClient(timeout=10) as client:
        # WebUI: latest release tag
        try:
            r = await client.get(f"https://api.github.com/repos/{WEBUI_REPO}/releases/latest")
            if r.status_code == 200:
                data = r.json()
                versions["webui"] = data.get("tag_name", "").lstrip("v")
        except Exception:
            pass

        # Agent: latest commit hash
        try:
            r = await client.get(f"https://api.github.com/repos/{AGENT_REPO}/commits/main")
            if r.status_code == 200:
                data = r.json()
                versions["agent"] = data.get("sha", "")[:7]
        except Exception:
            pass

    return versions


def get_cached_latest_versions(db: Session) -> dict[str, str | None]:
    """Get latest versions from Settings cache."""
    webui = db.query(Setting).filter_by(key="HERMES_WEBUI_LATEST").first()
    agent = db.query(Setting).filter_by(key="HERMES_AGENT_LATEST").first()

    return {
        "webui": webui.value if webui else None,
        "agent": agent.value if agent else None,
    }


def cache_latest_versions(db: Session, versions: dict[str, str | None]) -> None:
    """Cache latest versions in Settings."""
    for key, value in [("HERMES_WEBUI_LATEST", versions.get("webui")),
                       ("HERMES_AGENT_LATEST", versions.get("agent"))]:
        if value:
            s = db.query(Setting).filter_by(key=key).first()
            if s:
                s.value = value
            else:
                db.add(Setting(key=key, value=value))
    db.commit()


def check_updates_available(tenant: Tenant, latest_versions: dict[str, str | None]) -> list[dict]:
    """Compare tenant versions vs latest, return list of available updates."""
    updates = []

    if latest_versions.get("webui") and tenant.hermes_webui_version != latest_versions["webui"]:
        updates.append({
            "component": "webui",
            "from": tenant.hermes_webui_version or "unknown",
            "to": latest_versions["webui"],
        })

    if latest_versions.get("agent") and tenant.hermes_agent_version != latest_versions["agent"]:
        updates.append({
            "component": "agent",
            "from": tenant.hermes_agent_version or "unknown",
            "to": latest_versions["agent"],
        })

    return updates


def has_free_updates_available(user: User) -> bool:
    """Check if user still has free updates this month."""
    now = datetime.now(timezone.utc)

    if user.free_updates_reset_at is None:
        # Initialize reset date to 1st of next month
        next_month = now.replace(day=1) + timedelta(days=32)
        user.free_updates_reset_at = next_month.replace(day=1, hour=0, minute=0, second=0)
        return user.free_updates_used < user.free_updates_monthly

    # If reset date passed, reset counter
    if now >= user.free_updates_reset_at:
        user.free_updates_used = 0
        next_month = (user.free_updates_reset_at + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0)
        user.free_updates_reset_at = next_month

    return user.free_updates_used < user.free_updates_monthly


def consume_free_update(user: User) -> bool:
    """Consume one free update, return True if successful."""
    if not has_free_updates_available(user):
        return False
    user.free_updates_used += 1
    return True


def get_update_cost_eur(db: Session) -> float:
    """Get update cost from Settings, default to 1.99."""
    s = db.query(Setting).filter_by(key="UPDATE_COST_EUR").first()
    try:
        return float(s.value) if s else 1.99
    except (ValueError, TypeError):
        return 1.99


def get_free_updates_quota(db: Session) -> int:
    """Get free updates per month from Settings, default to 3."""
    s = db.query(Setting).filter_by(key="FREE_UPDATES_PER_MONTH").first()
    try:
        return int(s.value) if s else 3
    except (ValueError, TypeError):
        return 3
