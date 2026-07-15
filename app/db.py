"""Database session management."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
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


def get_db():
    """FastAPI dependency — yields a session and closes it after the request."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
