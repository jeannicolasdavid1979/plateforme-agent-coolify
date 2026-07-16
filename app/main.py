"""FastAPI application — main entry point."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .db import init_db
from .legal import router as legal_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(title="Plateforme Agent Coolify", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


# Serve the web UI
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
def index():
    index_file = _static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"status": "ok", "message": "Plateforme Agent Coolify API", "docs": "/docs"}


app.include_router(router)
app.include_router(legal_router)
