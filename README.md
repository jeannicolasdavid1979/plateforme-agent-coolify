# Plateforme Agent Coolify

Plateforme SaaS qui déploie des agents Hermes sur infrastructure Coolify. Chaque agent créé via le dashboard est automatiquement déployé comme un **Service Coolify natif** (template `hermes-agent-with-webui`), avec deux conteneurs (agent + webui) qui communiquent via volumes partagés.

## Architecture

```
Utilisateur → Dashboard Web (https://plateformeagentcoolify.kechlab.com)
                    │
                    ▼
            API FastAPI (port 8000)
                    │
                    ▼
            Coolify API (POST /api/v1/services)
                    │
                    ▼
            Template "hermes-agent-with-webui"
                    │
            ┌───────┴───────┐
            ▼               ▼
    hermes-agent        hermes-webui
(nousresearch/         (ghcr.io/nesquena/
hermes-agent)          hermes-webui)
    port 8642            port 8787
            │               │
            └─── volume ────┘
            (agent code partagé)
```

### Pourquoi cette approche ?

Au lieu de faire `docker run` manuellement (qui crée des conteneurs invisibles dans Coolify), la plateforme utilise l'**API Service de Coolify** pour déployer le même template que celui qu'on utilise manuellement depuis le dashboard Coolify.

**Avantages :**
- ✅ Deux conteneurs par agent (agent + webui) — fini l'erreur "AIAgent not available"
- ✅ Gestion Coolify native (logs, terminal, restart, env vars)
- ✅ Traefik + SSL gérés par Coolify
- ✅ Volume partagé : la webui voit le code de l'agent sans installation
- ✅ Chaque agent est visible dans le projet Coolify "Les projets de JN"

## Infrastructure

### VPS Hetzner (Allemagne)

| Élément | Valeur |
|---------|--------|
| **IP publique** | `46.225.127.18` |
| **IP Tailscale** | `100.101.75.71` |
| **OS** | Ubuntu 24.04.4 LTS |
| **Kernel** | 6.8.0-101-generic |
| **CPU** | 8 cœurs |
| **RAM** | 16 Go |
| **Uptime depuis** | 2026-03-03 |

### Réseau Docker (VPS)

```
Internet → VPS (46.225.127.18)
           ├── Traefik v3.6 (coolify-proxy) — ports 80/443/8080
           ├── Coolify v4.1.2 — port 8080 (dashboard)
           ├── Coolify DB (PostgreSQL 15)
           ├── Coolify Redis 7
           ├── Coolify Realtime (Soketi)
           ├── Coolify Sentinel
           │
           ├── Plateforme Agent Coolify (port 8000)
           │   → https://plateformeagentcoolify.kechlab.com
           │
           ├── Ancien orchestrateur (port 8000)
           │   → https://plateformehermes.kechlab.com
           │
           ├── N8N (port 5679)
           │   → https://n8n.kechlab.com
           │
           ├── Ollama API + Open WebUI
           │   → https://ollama.kechlab.com
           │
           ├── CouchDB (Obsidian LiveSync)
           │   → https://couchdb.kechlab.com
           │
           └── Sites web :
               ├── iptvgiantcoucou.kechlab.com
               ├── atelierbrigittedho.kechlab.com
               ├── barbier.kechlab.com
               ├── kech-lab.com
               ├── mistersmoke.ma
               ├── transport-marrakech.com
               └── studiorevelateur.com
```

### Réseau Tailscale (8 devices)

| Device | IP Tailscale | Localisation |
|--------|-------------|--------------|
| VPS Hetzner | 100.101.75.71 | Allemagne |
| Synology DS918+ | 100.120.250.19 | Castellane |
| Mac Mini | 100.124.122.36 | Castellane |
| iPhone JND | — | Mobile |
| iPhone Emilie | — | Mobile |
| iPad Emilie | — | Mobile |
| PC Charlie | — | Mandelieu |
| MacBook Sofiia | — | Paris |

### Coolify — Configuration

| Élément | UUID / Valeur |
|---------|---------------|
| **Projet** "Les projets de JN" | `tso4ocs4k0g0oso8wg04k4c8` |
| **Environnement** | `production` |
| **Serveur** localhost | `mggo8cg8kokwcgk48sw0o4c4` |
| **Destination** (réseau Docker `coolify`) | `xl2ufkvx4pjnjpj8hgsoq9wd` |
| **Version Coolify** | v4.1.2 (Laravel 12.60.2) |
| **Proxy** | Traefik v3.6.17 |
| **Sentinel** | activé (metrics toutes les 10s) |

### Accès SSH au VPS

```bash
# Tailscale (prioritaire)
ssh root@100.101.75.71

# IP publique (si Tailscale down)
ssh root@46.225.127.18

# Mot de passe root
# → FirstRoot2026
```

⚠️ Le SSH peut être refusé si le service `sshd` est down ou si Tailscale
est en veille. Dans ce cas, utiliser le terminal Coolify
(`coolify.kechlab.com` → Terminal).

### Domaine et DNS

- **Domaine principal** : `kechlab.com`
- **DNS géré par** Cloudflare
- **Wildcard DNS** : `*.kechlab.com` → `46.225.127.18`
- **Certificats SSL** : Let's Encrypt (via Traefik, automatique)
- **Routing** : Traefik v3.6 (labels Docker automatiques)

## Variables d'environnement

Configurées dans Coolify → Application → Environment :

| Variable | Description | Valeur exemple |
|----------|-------------|----------------|
| `COOLIFY_API_URL` | URL de l'API Coolify | `https://coolify.kechlab.com` |
| `COOLIFY_API_TOKEN` | Token API Coolify (Settings → API) | `4\|iYCS...c76c` |
| `COOLIFY_PROJECT_UUID` | UUID du projet Coolify | `tso4ocs4k0g0oso8wg04k4c8` |
| `COOLIFY_ENVIRONMENT` | Environnement Coolify | `production` |
| `COOLIFY_SERVER_UUID` | UUID du serveur | `mggo8cg8kokwcgk48sw0o4c4` |
| `COOLIFY_DESTINATION_UUID` | UUID du réseau/destination | `xl2ufkvx4pjnjpj8hgsoq9wd` |
| `OPENROUTER_API_KEY` | Clé OpenRouter partagée | `sk-or-v1-...` |
| `ORCH_JWT_SECRET` | Secret pour les JWT | `HermesPlatformSecret2026!Kechlab` |
| `ORCH_ADMIN_EMAILS` | Emails admin (séparés par virgules) | `david.jn@orange.fr` |
| `BASE_DOMAIN` | Domaine de base pour les agents | `kechlab.com` |

## API Endpoints

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/` | Dashboard web |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Documentation Swagger |
| `POST` | `/api/auth/register` | Créer un compte |
| `POST` | `/api/auth/login` | Se connecter |
| `GET` | `/api/auth/me` | Profil utilisateur |
| `GET` | `/api/agents` | Lister ses agents |
| `POST` | `/api/agents` | Créer un agent (déploie via Coolify) |
| `GET` | `/api/agents/{id}` | Détails d'un agent (URL, mot de passe) |
| `GET` | `/api/agents/{id}/jobs` | Jobs de provisioning |
| `GET` | `/api/admin/agents` | Lister tous les agents (admin) |
| `POST` | `/api/admin/agents/{id}/redeploy` | Redéployer un agent (admin) |

## Flow de déploiement d'un agent

```
1. POST /api/agents
   → Crée le tenant en DB (status=pending)
   → Lance le provisioning en background thread

2. ProvisioningEngine._step_deploy_service()
   → CoolifyClient.deploy_agent()
   → POST /api/v1/services {type: "hermes-agent-with-webui"}
   → Coolify crée 2 conteneurs (agent + webui) + volumes + réseau

3. _step_configure_env()
   → Set OPENROUTER_API_KEY, HERMES_INSTANCE_MODEL, HERMES_SYSTEM_PROMPT
   → Via POST/PATCH /api/v1/services/{uuid}/envs

4. _step_set_fqdn()
   → PATCH /api/v1/services/{uuid} {fqdn: "https://agent-xxx.kechlab.com"}

5. _step_start_service()
   → POST /api/v1/services/{uuid}/start

6. _step_health_check()
   → Attendre 15s que les conteneurs démarrent
   → Injecter le modèle dans config.yaml via docker exec
   → Poll /health sur l'URL publique pendant 120s
   → Status: running ✅
```

## Structure du code

```
plateforme-agent-coolify/
├── app/
│   ├── main.py           # FastAPI app + static files + health
│   ├── config.py         # Settings (env vars: COOLIFY_*, OPENROUTER_*, etc.)
│   ├── models.py         # SQLAlchemy: User, Tenant, ProvisioningJob
│   ├── db.py             # Session management (SQLite)
│   ├── security.py       # JWT auth + scrypt password hashing
│   ├── coolify.py        # CoolifyClient: deploy_agent() via Service API
│   ├── provisioning.py   # ProvisioningEngine: deploy steps (async)
│   ├── api.py            # REST routes: auth, agent CRUD, admin
│   └── static/
│       └── index.html    # Dashboard web (login, create agent, list)
├── tests/
│   └── test_app.py       # 6 tests (health, auth, agents)
├── Dockerfile            # python:3.12-slim + deps
├── docker-compose.yml    # Déploiement Coolify
├── pyproject.toml        # Dépendances Python
└── README.md             # Ce fichier
```

## Déploiement initial

### 1. Créer l'application dans Coolify

1. Coolify → Projects → "Les projets de JN" → production → New Resource
2. Type: **Public Repository** (GitHub)
3. Repository: `https://github.com/jeannicolasdavid1979/plateforme-agent-coolify.git`
4. Branch: `main`
5. Build Pack: **Dockerfile**
6. Port: `8000`
7. FQDN: `https://plateformeagentcoolify.kechlab.com`

### 2. Configurer les variables d'environnement

Dans Coolify → Application → Environment, ajouter toutes les variables
du tableau ci-dessus.

### 3. Déployer

Cliquer "Deploy" dans Coolify. L'application démarre sur le port 8000,
Traefik route le trafic HTTPS automatiquement.

### 4. Créer un compte admin

```bash
curl -X POST https://plateformeagentcoolify.kechlab.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"david.jn@orange.fr","password":"FirstRoot2026"}'
```

L'email doit être dans `ORCH_ADMIN_EMAILS` pour avoir les droits admin.

## Modèles supportés (via OpenRouter)

| Modèle | ID OpenRouter | Coût |
|--------|---------------|------|
| **GPT-4o** (défaut) | `openai/gpt-4o` | Payant |
| GPT-4o mini | `openai/gpt-4o-mini` | Payant |
| Claude Sonnet 4.5 | `anthropic/claude-sonnet-4.5` | Payant |
| Claude Opus 4.6 | `anthropic/claude-opus-4.6` | Payant |
| Llama 3.3 70B | `meta-llama/llama-3.3-70b-instruct:free` | Gratuit |
| DeepSeek V3 | `deepseek/deepseek-chat-v3-0324:free` | Gratuit |

## Accès aux agents déployés

Chaque agent est accessible sur :
- **URL** : `https://<subdomain>.kechlab.com`
- **Mot de passe** : généré automatiquement, visible dans le dashboard
- **Coolify** : visible dans "Les projets de JN" → production
- **Logs** : Coolify → Service → Logs
- **Terminal** : Coolify → Service → Terminal

## Maintenance

### Redémarrer la plateforme

```bash
# Via API Coolify
curl -X POST https://coolify.kechlab.com/api/v1/applications/ze97k0w7xh2yliwn5ro58dr2/restart \
  -H "Authorization: Bearer $COOLIFY_API_TOKEN"
```

### Mettre à jour le code

```bash
git push origin main
# Coolify redéploie automatiquement (si webhook configuré)
# Ou déclencher manuellement via l'API
```

### Accéder à la DB SQLite

```bash
ssh root@100.101.75.71
docker exec <container-name> python3 -c "
from app.db import SessionFactory
from app.models import Tenant
s = SessionFactory()
for t in s.query(Tenant).all():
    print(f'{t.name} | {t.status} | {t.instance_url}')
"
```

## FAQ

**Q: L'agent affiche "AIAgent not available" ?**
R: Impossible avec cette plateforme. Le template Coolify déploie deux conteneurs
séparés (agent + webui) avec un volume partagé. L'agent tourne dans son propre
conteneur `nousresearch/hermes-agent`.

**Q: Le health check échoue ?**
R: C'est normal les 2 premières minutes. Coolify doit démarrer les conteneurs
et Traefik doit obtenir le certificat SSL. Le health check a un timeout de 120s.

**Q: Comment changer le modèle d'un agent existant ?**
R: Dans la WebUI de l'agent (Paramètres → Modèle), ou via docker exec :
```bash
docker exec hermes-agent-<uuid> bash -c 'cat > /home/hermes/.hermes/config.yaml << EOF
model:
  default: "openai/gpt-4o"
  provider: "auto"
  base_url: "https://openrouter.ai/api/v1"
EOF'
```

**Q: Comment supprimer un agent ?**
R: Via Coolify (Service → Delete) ou via l'API Coolify :
```bash
curl -X DELETE https://coolify.kechlab.com/api/v1/services/<uuid> \
  -H "Authorization: Bearer $COOLIFY_API_TOKEN"
```

## Licence

Privée — JN David © 2026
