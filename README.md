# Plateforme Agent Coolify

Déploie des agents Hermes sur Coolify en utilisant l'API Service de Coolify
(template `hermes-agent-with-webui`), avec gestion des clés OpenRouter,
authentification, et dashboard.

## Principe

Quand un utilisateur crée un agent via le dashboard :
1. L'orchestrateur appelle `POST /api/v1/services` sur l'API Coolify
2. Coolify déploie le template `hermes-agent-with-webui` (2 conteneurs : agent + webui)
3. L'orchestrateur configure le FQDN, la clé OpenRouter, le modèle et le system prompt
4. L'agent est visible et gérable depuis Coolify (logs, terminal, restart)

## Variables d'environnement

```
COOLIFY_API_URL=https://coolify.kechlab.com
COOLIFY_API_TOKEN=<token>
COOLIFY_PROJECT_UUID=tso4ocs4k0g0oso8wg04k4c8
COOLIFY_ENVIRONMENT=production
COOLIFY_SERVER_UUID=mggo8cg8kokwcgk48sw0o4c4
COOLIFY_DESTINATION_UUID=xl2ufkvx4pjnjpj8hgsoq9wd
OPENROUTER_API_KEY=<clé partagée>
ORCH_JWT_SECRET=<secret>
ORCH_ADMIN_EMAILS=david.jn@orange.fr
BASE_DOMAIN=kechlab.com
```

## Déploiement

```bash
docker compose build
docker compose up -d
```

Le dashboard répond sur le FQDN configuré dans Coolify (port 8000).
