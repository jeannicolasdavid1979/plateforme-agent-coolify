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
    _start_hosting_sweeper()


def _start_hosting_sweeper():
    """Tâche de fond : applique le cycle de vie de l'hébergement (suspension des
    échéances dépassées, suppression après rétention) toutes les heures. Best
    effort — les erreurs sont journalisées sans interrompre la boucle."""
    import asyncio

    async def _loop():
        from .api import enforce_hosting
        from .db import SessionFactory

        while True:
            try:
                db = SessionFactory()
                try:
                    changed = enforce_hosting(db)
                    if changed:
                        logging.getLogger("hosting").info("Cycle de vie : %d agent(s) mis à jour", changed)
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                logging.getLogger("hosting").exception("Balayage hébergement échoué")
            await asyncio.sleep(3600)

    try:
        asyncio.get_event_loop().create_task(_loop())
    except RuntimeError:
        # Pas de boucle asyncio (contexte de test synchrone) — le balayage
        # reste déclenchable via /api/admin/enforce-hosting.
        pass


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
