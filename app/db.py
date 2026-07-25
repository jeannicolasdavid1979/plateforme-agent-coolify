"""Database session management."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .models import Base

_log = logging.getLogger("db")
_FALLBACK_DB = "/tmp/orchestrator.db"  # repli éphémère si le dossier de données est inutilisable


def _resolve_sqlite_path(db_url: str) -> str:
    """Chemin du fichier depuis une URL sqlite (3 slashes = relatif, 4 = absolu)."""
    return db_url.replace("sqlite:///", "")


def _get_engine():
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./data/orchestrator.db")
    if "sqlite:///" in db_url and ":memory:" not in db_url:
        db_path = _resolve_sqlite_path(db_url)
        db_dir = os.path.dirname(db_path)
        if db_dir:
            # Cas fatal historique : /app/data monté comme un FICHIER (mauvaise
            # config de stockage Coolify) fait lever FileExistsError à makedirs
            # et boucle le conteneur. On ne crashe plus : on bascule sur un
            # emplacement de repli en signalant fortement le problème.
            if os.path.exists(db_dir) and not os.path.isdir(db_dir):
                _log.error(
                    "Le dossier de données %r existe mais n'est PAS un dossier "
                    "(montage fichier ?). Persistance DÉSACTIVÉE — repli sur %s. "
                    "Configurez un VOLUME (type dossier) monté sur %r dans Coolify.",
                    db_dir, _FALLBACK_DB, db_dir,
                )
                db_url = "sqlite:///" + _FALLBACK_DB
            else:
                try:
                    os.makedirs(db_dir, exist_ok=True)
                except FileExistsError:
                    # Déjà présent sous une autre forme (montage, lien vers
                    # dossier) : rien à créer, SQLite ouvrira la base.
                    pass
                except OSError as exc:
                    _log.error(
                        "Création de %r impossible (%s) — repli sur %s.",
                        db_dir, exc, _FALLBACK_DB,
                    )
                    db_url = "sqlite:///" + _FALLBACK_DB
    connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}
    return create_engine(db_url, connect_args=connect_args, echo=False)


engine = _get_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    """Create all tables. Call once at startup."""
    Base.metadata.create_all(engine)
    # Migration légère pour les bases SQLite existantes : create_all ne
    # rajoute pas de colonne à une table déjà créée.
    _MIGRATIONS = [
        "ALTER TABLE tenants ADD COLUMN balance_eur FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE tenants ADD COLUMN openrouter_api_key VARCHAR(255)",
        "ALTER TABLE tenants ADD COLUMN openrouter_key_hash VARCHAR(128)",
        "ALTER TABLE users ADD COLUMN consent_at DATETIME",
        "ALTER TABLE users ADD COLUMN consent_version VARCHAR(32)",
        "ALTER TABLE tenants ADD COLUMN hosting_plan VARCHAR(16) NOT NULL DEFAULT 'none'",
        "ALTER TABLE tenants ADD COLUMN hosting_paid_until DATETIME",
        "ALTER TABLE tenants ADD COLUMN suspended_at DATETIME",
        "ALTER TABLE tenants ADD COLUMN stripe_subscription_id VARCHAR(64)",
        "ALTER TABLE checkouts ADD COLUMN plan VARCHAR(16)",
        "ALTER TABLE checkouts ADD COLUMN promo_code VARCHAR(32)",
        "ALTER TABLE checkouts ADD COLUMN discount_eur FLOAT NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN verification_token VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN reset_token VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN reset_expires DATETIME",
        "ALTER TABLE users ADD COLUMN last_seen DATETIME",
        # Mises à jour d'agents
        "ALTER TABLE users ADD COLUMN free_updates_monthly INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE users ADD COLUMN free_updates_used INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN free_updates_reset_at DATETIME",
        "ALTER TABLE tenants ADD COLUMN hermes_webui_version VARCHAR(64)",
        "ALTER TABLE tenants ADD COLUMN hermes_agent_version VARCHAR(64)",
        "ALTER TABLE tenants ADD COLUMN last_update_check_at DATETIME",
        "ALTER TABLE tenants ADD COLUMN last_update_at DATETIME",
    ]
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()  # colonne déjà présente
    _log_persistence_state()


def db_diagnostics() -> dict:
    """État de la persistance : où est réellement écrite la base, si elle
    survivra au prochain redéploiement, et ce qu'elle contient.

    Sert à diagnostiquer le cas le plus coûteux en exploitation : un
    conteneur redéployé SANS volume monté sur le dossier de données — la
    base repart vide à chaque déploiement (comptes et agents « disparus »)."""
    url = str(engine.url)
    info: dict = {"url": url, "is_sqlite": url.startswith("sqlite"), "path": None,
                  "exists": False, "size_bytes": 0, "modified_at": None,
                  "is_fallback": False, "persistent": None, "users": 0, "tenants": 0}
    if info["is_sqlite"] and ":memory:" not in url:
        path = _resolve_sqlite_path(url)
        info["path"] = path
        info["is_fallback"] = os.path.abspath(path) == os.path.abspath(_FALLBACK_DB)
        try:
            st = os.stat(path)
            info["exists"] = True
            info["size_bytes"] = st.st_size
            info["modified_at"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            pass
        # Un dossier de données monté depuis l'hôte apparaît dans /proc/mounts ;
        # sans montage, tout ce qui y est écrit meurt avec le conteneur.
        db_dir = os.path.dirname(os.path.abspath(path)) or "/"
        try:
            with open("/proc/mounts") as f:
                mounts = {line.split()[1] for line in f if len(line.split()) > 1}
            info["persistent"] = (not info["is_fallback"]) and any(
                db_dir == m or db_dir.startswith(m.rstrip("/") + "/") for m in mounts if m != "/"
            )
        except OSError:
            info["persistent"] = None

    try:
        with engine.connect() as conn:
            for table in ("users", "tenants"):
                try:
                    info[table] = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
                except Exception:
                    pass
    except Exception:
        pass
    return info


def _log_persistence_state() -> None:
    """Journalise l'état de la persistance au démarrage — la première chose à
    regarder dans les logs Coolify quand des données « ont disparu »."""
    d = db_diagnostics()
    if d["is_fallback"]:
        _log.error(
            "PERSISTANCE DÉSACTIVÉE — base de repli %s : les données seront PERDUES "
            "au prochain redéploiement. Montez un volume (dossier) sur le dossier de "
            "données dans Coolify.", d["path"],
        )
    elif d["persistent"] is False:
        _log.warning(
            "Base %s dans un dossier NON monté : aucun volume persistant détecté — "
            "les comptes et agents repartiront de zéro au prochain redéploiement. "
            "Coolify → l'app → Persistent Storage → Volume → /app/data.", d["path"],
        )
    else:
        _log.info("Base %s (%s octets) — %s compte(s), %s agent(s).",
                  d["path"], d["size_bytes"], d["users"], d["tenants"])


def get_db():
    """FastAPI dependency — yields a session and closes it after the request."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
