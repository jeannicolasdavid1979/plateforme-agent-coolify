FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn[standard] httpx pydantic pydantic-settings sqlalchemy pyjwt python-multipart

COPY app/ ./app/

# Emplacement de la base par défaut : chemin ABSOLU dans /app/data. Vaut aussi
# en déploiement Dockerfile (où le docker-compose.yml — et son DATABASE_URL —
# n'est pas lu). Pour PERSISTER les données, monter un VOLUME (dossier) sur
# /app/data : Coolify → l'app → Persistent Storage → Volume → /app/data.
ENV DATABASE_URL=sqlite:////app/data/orchestrator.db
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
