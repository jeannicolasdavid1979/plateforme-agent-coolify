"""Database session management."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .models import Base


def _get_engine():
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./data/orchestrator.db")
    # For SQLite, ensure the directory exists
    if "sqlite:///" in db_url and ":memory:" not in db_url:
        db_path = db_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
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
