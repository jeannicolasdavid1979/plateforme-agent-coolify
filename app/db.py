"""Database session management."""
from __future__ import annotations

import logging
import os

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
    ]
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()  # colonne déjà présente


def get_db():
    """FastAPI dependency — yields a session and closes it after the request."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
