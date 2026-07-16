"""Basic tests for the plateforme-agent-coolify."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import init_db, engine
from app.models import Base, Checkout


@pytest.fixture(autouse=True)
def setup_db():
    """Fresh in-memory DB for each test."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_engine_survives_non_directory_data_path(tmp_path, monkeypatch):
    """Si le dossier de données est en fait un FICHIER (mauvais montage Coolify),
    le démarrage ne doit PAS boucler : repli sur une base éphémère."""
    from app import db as dbmod

    # data_path/ « data » est un fichier — makedirs lèverait FileExistsError
    fake_dir = tmp_path / "data"
    fake_dir.write_text("not a directory")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{fake_dir}/orchestrator.db")
    # Ne lève pas, et l'engine pointe sur le repli éphémère
    engine = dbmod._get_engine()
    assert engine.url.database == dbmod._FALLBACK_DB


def test_engine_creates_missing_data_directory(tmp_path, monkeypatch):
    """Cas nominal : le dossier de données est créé s'il manque."""
    from app import db as dbmod

    target = tmp_path / "sub" / "data"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}/orchestrator.db")
    dbmod._get_engine()
    assert target.is_dir()


def test_register_and_login():
    # Register
    resp = client.post("/api/auth/register", json={"email": "test@example.com", "password": "secret123", "accept_terms": True})
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert token

    # Login
    resp = client.post("/api/auth/login", json={"email": "test@example.com", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["token"]

    # Me
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


def test_duplicate_register():
    client.post("/api/auth/register", json={"email": "dup@example.com", "password": "secret123", "accept_terms": True})
    resp = client.post("/api/auth/register", json={"email": "dup@example.com", "password": "secret123", "accept_terms": True})
    assert resp.status_code == 409


def test_invalid_login():
    resp = client.post("/api/auth/login", json={"email": "nope@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_create_agent_requires_auth():
    resp = client.post("/api/agents", json={"name": "Test", "subdomain": "test-agent"})
    assert resp.status_code == 401


def test_list_agents():
    # Register and get token
    resp = client.post("/api/auth/register", json={"email": "user@example.com", "password": "secret123", "accept_terms": True})
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # List agents (empty)
    resp = client.get("/api/agents", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["agents"] == []


def _register(email="buyer@example.com"):
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "accept_terms": True},
    )
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_create_agent_payment_flow():
    headers = _register()

    # La création ne déploie pas : elle ouvre une session de paiement
    resp = client.post("/api/agents", json={"name": "Test", "subdomain": "test-pay"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"]["status"] == "awaiting_payment"
    checkout_url = data["checkout_url"]
    assert checkout_url.startswith("/pay/")

    # La page de paiement simulée s'affiche
    resp = client.get(checkout_url)
    assert resp.status_code == 200
    assert "simulation Stripe" in resp.text

    # Le paiement crédite le crédit initial et lance le déploiement
    checkout_id = checkout_url.split("/pay/")[1]
    resp = client.post(f"/api/pay/{checkout_id}")
    assert resp.status_code == 200
    assert resp.json()["credited_eur"] == 5.0

    resp = client.get("/api/agents", headers=headers)
    agent = resp.json()["agents"][0]
    assert agent["status"] != "awaiting_payment"
    assert agent["balance_eur"] == 5.0

    # Une session ne se paye qu'une fois
    resp = client.post(f"/api/pay/{checkout_id}")
    assert resp.status_code == 409


def test_topup_credits_balance():
    headers = _register("topup@example.com")
    resp = client.post("/api/agents", json={"name": "Topup", "subdomain": "test-topup"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]

    # Sans montant → recharge par défaut (10 €)
    resp = client.post(f"/api/agents/{agent_id}/topup", headers=headers)
    assert resp.status_code == 200
    checkout_id = resp.json()["checkout_url"].split("/pay/")[1]
    resp = client.post(f"/api/pay/{checkout_id}")
    assert resp.status_code == 200
    assert resp.json()["credited_eur"] == 10.0

    resp = client.get(f"/api/agents/{agent_id}", headers=headers)
    assert resp.json()["balance_eur"] == 10.0


def test_topup_with_chosen_amount():
    """Le client choisit un montant parmi ceux proposés (5/10/20/50/100)."""
    headers = _register("choose@example.com")
    resp = client.post("/api/agents", json={"name": "Choix", "subdomain": "test-choose"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]

    # Recharge de 50 € de crédit : frais de service 10 % par défaut → 55 € à
    # payer, mais 50 € de crédit IA reçus.
    resp = client.post(f"/api/agents/{agent_id}/topup", json={"amount_eur": 50}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["amount_eur"] == 55.0   # payé, frais inclus
    assert data["credit_eur"] == 50.0   # crédit IA reçu
    assert data["fee_eur"] == 5.0
    checkout_id = data["checkout_url"].split("/pay/")[1]
    # Le crédit versé reste le montant choisi (pas le montant payé)
    assert client.post(f"/api/pay/{checkout_id}").json()["credited_eur"] == 50.0
    assert client.get(f"/api/agents/{agent_id}", headers=headers).json()["balance_eur"] == 50.0

    # Un montant hors liste est refusé (on ne crédite pas n'importe quoi)
    resp = client.post(f"/api/agents/{agent_id}/topup", json={"amount_eur": 7}, headers=headers)
    assert resp.status_code == 400
    assert "invalide" in resp.json()["detail"]


def test_admin_edits_topup_amounts():
    """L'admin peut redéfinir la liste des montants de recharge proposés."""
    from app.config import get_settings
    get_settings().admin_emails = "amounts-admin@example.com"
    client.post("/api/auth/register", json={"email": "amounts-admin@example.com", "password": "secret123", "accept_terms": True})
    resp = client.post("/api/auth/login", json={"email": "amounts-admin@example.com", "password": "secret123"})
    admin = {"Authorization": f"Bearer {resp.json()['token']}"}

    resp = client.put("/api/admin/pricing", json={"topup_amounts_eur": "5, 15, 25"}, headers=admin)
    assert resp.status_code == 200
    assert resp.json()["topup_amounts_eur"] == [5.0, 15.0, 25.0]

    # La nouvelle liste s'applique à la validation des recharges
    resp = client.get("/api/pricing")
    assert resp.json()["topup_amounts_eur"] == [5.0, 15.0, 25.0]

    headers = _register("amounts-user@example.com")
    resp = client.post("/api/agents", json={"name": "A", "subdomain": "test-amt"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]
    # 25 est désormais autorisé, 20 ne l'est plus
    assert client.post(f"/api/agents/{agent_id}/topup", json={"amount_eur": 25}, headers=headers).status_code == 200
    assert client.post(f"/api/agents/{agent_id}/topup", json={"amount_eur": 20}, headers=headers).status_code == 400

    # Une liste vide ou invalide est refusée
    assert client.put("/api/admin/pricing", json={"topup_amounts_eur": "abc"}, headers=admin).status_code == 400


def test_service_fee_default_and_admin_configurable():
    """Les recharges portent des frais de service (10 % par défaut), que l'admin
    peut ajuster. Le crédit IA reçu reste le montant choisi."""
    from app.config import get_settings

    headers = _register("fee-user@example.com")
    resp = client.post("/api/agents", json={"name": "Fee", "subdomain": "test-fee"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]

    # Par défaut : 20 € de crédit → 22 € à payer (frais 2 €)
    resp = client.post(f"/api/agents/{agent_id}/topup", json={"amount_eur": 20}, headers=headers)
    body = resp.json()
    assert body["credit_eur"] == 20.0
    assert body["amount_eur"] == 22.0
    assert body["fee_eur"] == 2.0
    assert body["fee_rate"] == 0.10
    # La page de paiement affiche le total à payer, frais inclus
    assert "22,00" in client.get(body["checkout_url"]).text

    # L'admin porte les frais à 25 %
    get_settings().admin_emails = "fee-admin@example.com"
    client.post("/api/auth/register", json={"email": "fee-admin@example.com", "password": "secret123", "accept_terms": True})
    tok = client.post("/api/auth/login", json={"email": "fee-admin@example.com", "password": "secret123"}).json()["token"]
    admin = {"Authorization": f"Bearer {tok}"}
    resp = client.put("/api/admin/pricing", json={"service_fee_rate": 0.25}, headers=admin)
    assert resp.status_code == 200
    assert resp.json()["service_fee_rate"] == 0.25

    # 20 € de crédit → 25 € à payer désormais
    resp = client.post(f"/api/agents/{agent_id}/topup", json={"amount_eur": 20}, headers=headers)
    assert resp.json()["amount_eur"] == 25.0

    # Un taux hors bornes (> 1) est refusé ; on rétablit le défaut ensuite
    assert client.put("/api/admin/pricing", json={"service_fee_rate": 1.5}, headers=admin).status_code == 400
    client.put("/api/admin/pricing", json={"service_fee_rate": 0.10}, headers=admin)


def test_admin_promoted_from_email_list():
    """Un email présent dans ADMIN_EMAILS est promu admin dès l'inscription,
    sans étape de connexion séparée (auto-cicatrisation après reset de base)."""
    from app.config import get_settings

    get_settings().admin_emails = "self-heal-admin@example.com"
    tok = client.post(
        "/api/auth/register",
        json={"email": "self-heal-admin@example.com", "password": "secret123", "accept_terms": True},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    # Admin immédiatement visible via /me, et l'accès admin est accordé
    assert client.get("/api/auth/me", headers=headers).json()["is_admin"] is True
    assert client.put("/api/admin/pricing", json={"deploy_price_eur": 29.0}, headers=headers).status_code == 200


def test_pricing_defaults_and_admin_update():
    # Lecture publique des prix (défauts de config)
    resp = client.get("/api/pricing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deploy_price_eur"] == 29.0
    assert data["topup_amount_eur"] == 10.0
    assert data["initial_credit_eur"] == 5.0
    assert data["service_fee_rate"] == 0.10
    assert data["topup_amounts_eur"] == [5.0, 10.0, 20.0, 50.0, 100.0]

    # Un utilisateur normal ne peut pas modifier les prix
    headers = _register("pleb@example.com")
    resp = client.put("/api/admin/pricing", json={"deploy_price_eur": 1.0}, headers=headers)
    assert resp.status_code == 403

    # L'admin (email dans admin_emails, promu au login) peut
    from app.config import get_settings
    get_settings().admin_emails = "boss@example.com"
    client.post("/api/auth/register", json={"email": "boss@example.com", "password": "secret123", "accept_terms": True})
    resp = client.post("/api/auth/login", json={"email": "boss@example.com", "password": "secret123"})
    admin_headers = {"Authorization": f"Bearer {resp.json()['token']}"}

    resp = client.put(
        "/api/admin/pricing",
        json={"deploy_price_eur": 49.0, "topup_amount_eur": 20.0, "initial_credit_eur": 2.5},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["deploy_price_eur"] == 49.0

    # Les nouveaux prix s'appliquent aux checkouts suivants
    resp = client.get("/api/pricing")
    assert resp.json()["topup_amount_eur"] == 20.0

    headers = _register("later@example.com")
    resp = client.post("/api/agents", json={"name": "Cher", "subdomain": "test-cher"}, headers=headers)
    checkout_url = resp.json()["checkout_url"]
    resp = client.get(checkout_url)
    assert "49,00" in resp.text


def test_sanitize_env_value():
    from app.coolify import CoolifyClient
    # Les apostrophes cassent le quoting simple du .env généré par Coolify
    assert CoolifyClient._sanitize_env_value(
        "Tu es l'artiste, l'agent IA personnel de ton propriétaire."
    ) == "Tu es l’artiste, l’agent IA personnel de ton propriétaire."
    # Les retours à la ligne aussi
    assert CoolifyClient._sanitize_env_value("ligne 1\nligne 2") == "ligne 1 ligne 2"
    assert CoolifyClient._sanitize_env_value("sk-or-v1-abc123") == "sk-or-v1-abc123"


def test_customize_compose():
    from app.provisioning import customize_compose

    compose = """
services:
  hermes-agent:
    image: nousresearch/hermes-agent
    environment:
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
  hermes-webui:
    image: ghcr.io/nesquena/hermes-webui:latest
    environment:
      - SERVICE_FQDN_HERMESWEBUI_8787
      - SERVICE_URL_HERMESWEBUI=${SERVICE_URL_HERMESWEBUI}
"""
    patched, changes = customize_compose(compose, "https://artiste.kechlab.com")
    assert patched is not None
    assert "SERVICE_FQDN_HERMESWEBUI_8787=https://artiste.kechlab.com" in patched
    assert "SERVICE_URL_HERMESWEBUI=https://artiste.kechlab.com" in patched
    # L'entrypoint qui écrit config.yaml est posé sur le conteneur agent
    assert "config.yaml" in patched and "exec /init" in patched
    assert len(changes) == 3

    # Un compose sans rien de reconnaissable ne casse pas
    patched, changes = customize_compose("services:\n  autre:\n    image: nginx\n", "https://x.y")
    assert patched is None

    # Un compose illisible non plus
    patched, changes = customize_compose(":::pas du yaml", "https://x.y")
    assert patched is None


def test_restart_requires_deployed_agent():
    headers = _register("restart@example.com")
    resp = client.post("/api/agents", json={"name": "R", "subdomain": "test-restart"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]
    # Pas encore de service Coolify → 409
    resp = client.post(f"/api/agents/{agent_id}/restart", headers=headers)
    assert resp.status_code == 409


def test_dedicated_api_key_step(monkeypatch):
    """La clé OpenRouter dédiée est créée, nommée et plafonnée au crédit."""
    from app import provisioning as prov
    from app.db import SessionFactory
    from app.models import Tenant, User

    created = {}

    class FakeKeys:
        def create(self, name, limit_usd):
            created.update(name=name, limit=limit_usd)
            return "sk-or-v1-tenant-key", "hash123"

    monkeypatch.setattr(prov, "get_keys_client", lambda: FakeKeys())

    db = SessionFactory()
    user = User(email="k@y.z", password_hash="h")
    db.add(user); db.commit()
    tenant = Tenant(user_id=user.id, name="Clé", subdomain="test-key",
                    status="pending", balance_eur=5.0)
    db.add(tenant); db.commit()

    engine = prov.ProvisioningEngine(db)
    detail = engine._step_create_api_key(tenant, None)
    assert created == {"name": "hermes-test-key", "limit": 5.0}
    assert tenant.openrouter_api_key == "sk-or-v1-tenant-key"
    assert tenant.openrouter_key_hash == "hash123"
    assert "hermes-test-key" in detail

    # Redéploiement : la clé existante est réutilisée, pas recréée
    created.clear()
    detail = engine._step_create_api_key(tenant, None)
    assert created == {} and "réutilisée" in detail
    db.close()

    # Sans clé maître : repli sur la clé partagée
    monkeypatch.setattr(prov, "get_keys_client", lambda: None)
    db = SessionFactory()
    t2 = Tenant(user_id=user.id, name="P", subdomain="test-key2", status="pending")
    db.add(t2); db.commit()
    detail = prov.ProvisioningEngine(db)._step_create_api_key(t2, None)
    assert "partagée" in detail and t2.openrouter_api_key is None
    db.close()


def test_template_probe_cached_and_compose_creation(monkeypatch):
    """Le template est sondé une seule fois, puis chaque service est créé
    avec le compose personnalisé (domaine intégré dès le premier parse)."""
    from app import provisioning as prov
    from app.db import SessionFactory
    from app.models import Setting, Tenant, User

    TEMPLATE = (
        "services:\n"
        "  hermes-agent:\n    image: nousresearch/hermes-agent\n"
        "  hermes-webui:\n    image: ghcr.io/nesquena/hermes-webui\n"
        "    environment:\n      - SERVICE_URL_HERMESWEBUI_8787\n"
    )
    calls = {"probes": 0, "compose_creates": []}

    class FakeClient:
        def create_service(self, name, urls=None):
            calls["probes"] += 1
            return "probe000000000ab"
        def get_compose_raw(self, u): return TEMPLATE
        def delete_service(self, u): return True
        def create_service_from_compose(self, name, compose_yaml, urls=None):
            calls["compose_creates"].append((name, compose_yaml, urls))
            return "real000000000abc"
        def get_password(self, u): return "pwd"

    db = SessionFactory()
    user = User(email="tpl@y.z", password_hash="h")
    db.add(user); db.commit()
    tenant = Tenant(user_id=user.id, name="T", subdomain="test-tpl", status="pending")
    db.add(tenant); db.commit()

    monkeypatch.setattr(prov, "get_client", lambda: FakeClient())
    engine = prov.ProvisioningEngine(db)
    detail = engine._step_deploy_service(tenant, None)

    assert "compose personnalisé" in detail
    name, compose, urls = calls["compose_creates"][0]
    assert name == "hermes-test-tpl"
    assert "SERVICE_FQDN_HERMESWEBUI_8787=https://test-tpl.kechlab.com" in compose
    # Le domaine passe par le champ officiel `urls` de l'API, ciblé sur le
    # service webui du compose — c'est lui que Coolify lit vraiment.
    assert urls == [{"name": "hermes-webui", "url": "https://test-tpl.kechlab.com"}]
    assert tenant.coolify_service_uuid == "real000000000abc"
    # La sonde a tourné une fois et le template est en cache
    assert calls["probes"] == 1
    assert db.get(Setting, engine.TEMPLATE_CACHE_KEY) is not None

    # Deuxième déploiement : plus de sonde (cache)
    t2 = Tenant(user_id=user.id, name="T2", subdomain="test-tpl2", status="pending")
    db.add(t2); db.commit()
    engine._step_deploy_service(t2, None)
    assert calls["probes"] == 1
    db.close()


def test_set_fqdn_uses_official_urls_field(monkeypatch):
    """L'étape set_fqdn attribue le domaine via PATCH `urls` (mécanisme
    officiel de l'API Coolify), même quand le compose contient déjà le
    domaine — le parseur ignore la valeur des variables SERVICE_FQDN_*."""
    from app import provisioning as prov
    from app.db import SessionFactory
    from app.models import Tenant, User

    COMPOSE = (
        "services:\n"
        "  hermes-webui:\n"
        "    image: ghcr.io/nesquena/hermes-webui\n"
        "    environment:\n"
        "      - SERVICE_FQDN_HERMESWEBUI_8787=https://marocompta.kechlab.com\n"
    )
    patched_urls = {}

    class FakeClient:
        def get_compose_raw(self, u): return COMPOSE
        def set_service_urls(self, svc_uuid, urls):
            patched_urls.update(uuid=svc_uuid, urls=urls)
            return ["marocompta.kechlab.com"]
        def update_compose_raw(self, u, y):
            raise AssertionError("compose déjà à jour : pas de re-patch attendu")

    monkeypatch.setattr(prov, "get_client", lambda: FakeClient())

    db = SessionFactory()
    user = User(email="fqdn@y.z", password_hash="h")
    db.add(user); db.commit()
    tenant = Tenant(
        user_id=user.id, name="M", subdomain="marocompta", status="deploying",
        coolify_service_uuid="svc00000000000ab",
        instance_url="https://marocompta.kechlab.com",
    )
    db.add(tenant); db.commit()

    detail = prov.ProvisioningEngine(db)._step_set_fqdn(tenant, None)
    assert patched_urls["uuid"] == "svc00000000000ab"
    assert patched_urls["urls"] == [
        {"name": "hermes-webui", "url": "https://marocompta.kechlab.com"}
    ]
    assert "marocompta.kechlab.com" in detail
    db.close()

    # find_web_services : seuls les services HTTP (variables magiques/webui)
    from app.provisioning import find_web_services
    assert find_web_services(COMPOSE) == ["hermes-webui"]
    assert find_web_services("services:\n  db:\n    image: postgres\n") == []
    assert find_web_services(None) == []


def test_subdomain_uppercase_is_normalized():
    """Une majuscule ne doit pas provoquer un rejet obscur : DNS est
    insensible à la casse, on normalise en minuscules côté serveur."""
    headers = _register("caps@example.com")
    resp = client.post("/api/agents", json={"name": "Caps", "subdomain": "Mon-Agent"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["agent"]["subdomain"] == "mon-agent"

    # Les caractères vraiment invalides restent refusés, avec un message clair
    resp = client.post("/api/agents", json={"name": "Bad", "subdomain": "mon agent!"}, headers=headers)
    assert resp.status_code == 400
    assert "minuscules" in resp.json()["detail"]


def test_delete_agent():
    headers = _register("deleter@example.com")
    resp = client.post("/api/agents", json={"name": "Éphémère", "subdomain": "test-del"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]

    # Un autre utilisateur ne peut pas le supprimer
    other = _register("other@example.com")
    resp = client.delete(f"/api/agents/{agent_id}", headers=other)
    assert resp.status_code == 404

    resp = client.delete(f"/api/agents/{agent_id}", headers=headers)
    assert resp.status_code == 200
    resp = client.get("/api/agents", headers=headers)
    assert resp.json()["agents"] == []


def test_topup_increases_openrouter_key_cap(monkeypatch):
    """La recharge crédit relève le plafond de la clé OpenRouter dédiée."""
    from app import api
    from app.db import SessionFactory
    from app.models import Tenant, User

    add_credit_calls = []

    class FakeKeys:
        def create(self, name, limit_usd):
            return "sk-or-v1-tenant-key", "hash123"

        def info(self, key_hash):
            return {"limit": 5.0, "usage": 0.5}

        def add_credit(self, key_hash, amount_usd):
            add_credit_calls.append({"key_hash": key_hash, "amount_usd": amount_usd})
            return round(5.0 + amount_usd, 2)

    monkeypatch.setattr(api, "get_keys_client", lambda: FakeKeys())

    # Créer un agent avec crédit initial et clé dédiée
    db = SessionFactory()
    user = User(email="topup-key@y.z", password_hash="h")
    db.add(user); db.commit()
    tenant = Tenant(
        user_id=user.id, name="TopupKey", subdomain="test-topup-key",
        status="running", balance_eur=5.0,
        openrouter_key_hash="hash123"
    )
    db.add(tenant); db.commit()

    # Première recharge : 10 EUR = 10 USD (eur_usd_rate=1.0 par défaut)
    checkout1 = Checkout(
        user_id=user.id, tenant_id=tenant.id, kind="topup",
        amount_eur=10.0, credit_eur=10.0, status="pending"
    )
    db.add(checkout1); db.commit()

    resp = client.post(f"/api/pay/{checkout1.id}")
    assert resp.status_code == 200
    assert len(add_credit_calls) == 1
    assert add_credit_calls[0] == {"key_hash": "hash123", "amount_usd": 10.0}
    # Refresh tenant from DB (API call uses separate session)
    db.refresh(tenant)
    assert tenant.balance_eur == 15.0

    # Deuxième recharge : 5 EUR = 5 USD
    add_credit_calls.clear()
    checkout2 = Checkout(
        user_id=user.id, tenant_id=tenant.id, kind="topup",
        amount_eur=5.0, credit_eur=5.0, status="pending"
    )
    db.add(checkout2); db.commit()

    resp = client.post(f"/api/pay/{checkout2.id}")
    assert resp.status_code == 200
    assert len(add_credit_calls) == 1
    assert add_credit_calls[0] == {"key_hash": "hash123", "amount_usd": 5.0}
    db.refresh(tenant)
    assert tenant.balance_eur == 20.0

    db.close()


def test_topup_handles_openrouter_failure_gracefully(monkeypatch, caplog):
    """Si OpenRouter API échoue, le crédit local est crédité quand même."""
    from app import api
    from app.db import SessionFactory
    from app.models import Tenant, User

    class FailingKeys:
        def add_credit(self, key_hash, amount_usd):
            raise RuntimeError("OpenRouter API temporairement indisponible")

    monkeypatch.setattr(api, "get_keys_client", lambda: FailingKeys())

    db = SessionFactory()
    user = User(email="topup-fail@y.z", password_hash="h")
    db.add(user); db.commit()
    tenant = Tenant(
        user_id=user.id, name="TopupFail", subdomain="test-topup-fail",
        status="running", balance_eur=5.0,
        openrouter_key_hash="hash123"
    )
    db.add(tenant); db.commit()

    checkout = Checkout(
        user_id=user.id, tenant_id=tenant.id, kind="topup",
        amount_eur=10.0, credit_eur=10.0, status="pending"
    )
    db.add(checkout); db.commit()

    resp = client.post(f"/api/pay/{checkout.id}")
    # Le paiement réussit même si OpenRouter échoue
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"

    # Le crédit est crédité localement
    db.refresh(tenant)
    assert tenant.balance_eur == 15.0

    # Un avertissement est enregistré
    assert any("Recharge OpenRouter échouée" in record.message for record in caplog.records)

    db.close()


# ── RGPD & pages légales ─────────────────────────────────────────────


def test_register_requires_consent():
    """Sans acceptation des CGV, l'inscription est refusée (RGPD)."""
    resp = client.post("/api/auth/register", json={"email": "noconsent@example.com", "password": "secret123"})
    assert resp.status_code == 400
    assert "confidentialité" in resp.json()["detail"]

    # Un mot de passe trop court est aussi refusé
    resp = client.post("/api/auth/register", json={"email": "short@example.com", "password": "123", "accept_terms": True})
    assert resp.status_code == 400


def test_register_records_consent():
    """L'acceptation est horodatée et versionnée comme preuve."""
    from app.db import SessionFactory
    from app.models import User
    from sqlalchemy import select
    from app.config import get_settings

    resp = client.post("/api/auth/register", json={"email": "consented@example.com", "password": "secret123", "accept_terms": True})
    assert resp.status_code == 200

    db = SessionFactory()
    user = db.scalar(select(User).where(User.email == "consented@example.com"))
    assert user.consent_at is not None
    assert user.consent_version == get_settings().terms_version
    db.close()


def test_terms_version_endpoint():
    from app.config import get_settings
    resp = client.get("/api/legal/terms-version")
    assert resp.status_code == 200
    assert resp.json()["terms_version"] == get_settings().terms_version


def test_export_account_data():
    """L'export contient le compte, le consentement, les agents et paiements,
    mais jamais le mot de passe haché (secret, non portable)."""
    headers = _register("exporter@example.com")
    client.post("/api/agents", json={"name": "Exp", "subdomain": "test-exp"}, headers=headers)

    resp = client.get("/api/account/export", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["account"]["email"] == "exporter@example.com"
    assert data["account"]["consent"]["version"] is not None
    assert len(data["agents"]) == 1
    assert data["agents"][0]["subdomain"] == "test-exp"
    # Aucune fuite du hash du mot de passe, où qu'il soit
    assert "password_hash" not in str(data)
    assert "scrypt" not in str(data)

    # L'export exige une authentification
    assert client.get("/api/account/export").status_code == 401


def test_delete_account_purges_everything(monkeypatch):
    """L'effacement du compte détruit les agents (Coolify+OpenRouter) et le compte."""
    from app import api
    from app.models import Tenant, User
    from app.db import SessionFactory
    from sqlalchemy import select

    deleted_keys, deleted_services = [], []

    class FakeKeys:
        def delete(self, h): deleted_keys.append(h); return True

    class FakeClient:
        def delete_service(self, u): deleted_services.append(u); return True

    monkeypatch.setattr(api, "get_keys_client", lambda: FakeKeys())
    monkeypatch.setattr(api, "get_client", lambda: FakeClient())

    headers = _register("eraseme@example.com")
    resp = client.post("/api/agents", json={"name": "Gone", "subdomain": "test-gone"}, headers=headers)
    agent_id = resp.json()["agent"]["id"]

    # Simuler un agent réellement déployé (service + clé)
    db = SessionFactory()
    tenant = db.get(Tenant, agent_id)
    tenant.coolify_service_uuid = "svc-xyz"
    tenant.openrouter_key_hash = "hash-xyz"
    db.commit(); db.close()

    resp = client.delete("/api/account", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["agents_removed"] == 1
    assert deleted_keys == ["hash-xyz"]
    assert deleted_services == ["svc-xyz"]

    # Le compte et ses agents ont disparu
    db = SessionFactory()
    assert db.scalar(select(User).where(User.email == "eraseme@example.com")) is None
    assert db.get(Tenant, agent_id) is None
    db.close()

    # Le jeton ne fonctionne plus
    assert client.get("/api/account/export", headers=headers).status_code == 401


def test_legal_pages_render():
    for path in ("/legal/mentions", "/legal/confidentialite", "/legal/cgv", "/legal/cookies"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "text/html" in resp.headers["content-type"]
    # La politique de confidentialité cite les droits RGPD et la CNIL
    body = client.get("/legal/confidentialite").text
    assert "portabilité" in body and "CNIL" in body
    # Les mentions légales exposent l'hébergeur
    assert "Hetzner" in client.get("/legal/mentions").text


# ══════════════════════════════════════════════════════════════════════
#  Hébergement récurrent, cycle de vie FOMO, et paiements Stripe
# ══════════════════════════════════════════════════════════════════════

def _admin(email="ops@example.com"):
    from app.config import get_settings
    get_settings().admin_emails = email
    tok = client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "accept_terms": True},
    ).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _running_agent(email, subdomain, **kw):
    """Crée directement un agent 'running' avec service Coolify (bypass paiement)."""
    from sqlalchemy import select
    from app.db import SessionFactory
    from app.models import Tenant, User

    db = SessionFactory()
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(email=email, password_hash="h"); db.add(user); db.commit()
    tenant = Tenant(
        user_id=user.id, name=subdomain, subdomain=subdomain,
        status="running", coolify_service_uuid="svc-" + subdomain, **kw,
    )
    db.add(tenant); db.commit()
    tid = tenant.id
    db.close()
    return tid


def test_hosting_status_transitions():
    from datetime import datetime, timedelta, timezone
    from app.hosting import hosting_status

    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    # Actif : échéance dans le futur
    st = hosting_status(now + timedelta(days=5), None, grace_days=0, retention_days=30, now=now)
    assert st.state == "active" and st.seconds_left == 5 * 86400
    # Grâce : échéance dépassée mais dans la fenêtre de grâce
    st = hosting_status(now - timedelta(days=1), None, grace_days=3, retention_days=30, now=now)
    assert st.state == "grace"
    # Échéance + grâce dépassées, pas encore marqué suspendu → suspended
    st = hosting_status(now - timedelta(days=5), None, grace_days=3, retention_days=30, now=now)
    assert st.state == "suspended"
    # Suspendu depuis 40 j, rétention 30 → deletable
    st = hosting_status(now - timedelta(days=45), now - timedelta(days=40),
                        grace_days=0, retention_days=30, now=now)
    assert st.state == "deletable"


def test_deploy_includes_first_hosting_month():
    from app.db import SessionFactory
    from app.models import Tenant

    headers = _register("deployhost@example.com")
    agent_id = client.post("/api/agents", json={"name": "H", "subdomain": "test-host"},
                           headers=headers).json()["agent"]["id"]
    checkout_url = client.post(f"/api/agents/{agent_id}/checkout", headers=headers).json()["checkout_url"]
    cid = checkout_url.split("/pay/")[1]
    assert client.post(f"/api/pay/{cid}").status_code == 200

    db = SessionFactory()
    agent = db.get(Tenant, agent_id)
    assert agent.hosting_plan == "manual"  # 1er mois inclus, sans engagement
    assert agent.hosting_paid_until is not None
    db.close()
    # L'API expose l'état d'hébergement
    h = client.get(f"/api/agents/{agent_id}", headers=headers).json()["hosting"]
    assert h["state"] == "active" and h["plan"] == "manual"


def test_hosting_renewal_extends_period():
    from datetime import datetime, timezone
    from app.db import SessionFactory
    from app.models import Tenant

    headers = _register("renew@example.com")  # crée le compte…
    agent_id = _running_agent("renew@example.com", "test-renew",
                              hosting_plan="manual")  # …puis un agent pour ce compte
    # Prolongation sans engagement : 29 €, +1 mois
    resp = client.post(f"/api/agents/{agent_id}/hosting", json={"plan": "manual"}, headers=headers)
    assert resp.json()["amount_eur"] == 29.0
    cid = resp.json()["checkout_url"].split("/pay/")[1]
    assert client.post(f"/api/pay/{cid}").status_code == 200

    db = SessionFactory()
    agent = db.get(Tenant, agent_id)
    delta = (agent.hosting_paid_until.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
    assert 28 <= delta <= 31  # ~1 mois
    db.close()

    # Abonnement mensuel auto : 19 €
    resp = client.post(f"/api/agents/{agent_id}/hosting", json={"plan": "sub_monthly"}, headers=headers)
    assert resp.json()["amount_eur"] == 19.0

    # Annuel : 209 €, plan sub_annual, ~12 mois
    resp = client.post(f"/api/agents/{agent_id}/hosting", json={"plan": "sub_annual"}, headers=headers)
    assert resp.json()["amount_eur"] == 209.0
    cid = resp.json()["checkout_url"].split("/pay/")[1]
    client.post(f"/api/pay/{cid}")
    db = SessionFactory()
    agent = db.get(Tenant, agent_id)
    assert agent.hosting_plan == "sub_annual"
    db.close()


def test_enforce_suspends_then_deletes(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import api
    from app.db import SessionFactory
    from app.models import Tenant

    stops = []

    class FakeClient:
        def stop_service(self, uuid): stops.append(uuid); return True
        def start_service(self, uuid): return True
        def delete_service(self, uuid): return True

    monkeypatch.setattr(api, "get_client", lambda: FakeClient())
    monkeypatch.setattr(api, "get_keys_client", lambda: None)

    # Agent échu depuis 2 jours (grâce 0) → doit être suspendu
    aid = _running_agent("late@example.com", "test-late",
                         hosting_plan="monthly")
    db = SessionFactory()
    db.get(Tenant, aid).hosting_paid_until = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit(); db.close()

    db = SessionFactory()
    changed = api.enforce_hosting(db)
    db.close()
    assert changed >= 1
    assert "svc-test-late" in stops

    db = SessionFactory()
    agent = db.get(Tenant, aid)
    assert agent.suspended_at is not None
    # Force le retard au-delà de la rétention → suppression au prochain balayage
    agent.suspended_at = datetime.now(timezone.utc) - timedelta(days=40)
    db.commit(); db.close()

    db = SessionFactory()
    api.enforce_hosting(db)
    gone = db.get(Tenant, aid)
    db.close()
    assert gone is None


def test_admin_restore_suspended_agent(monkeypatch):
    from datetime import datetime, timezone
    from app import api
    from app.db import SessionFactory
    from app.models import Tenant

    monkeypatch.setattr(api, "get_client", lambda: None)
    admin = _admin("restore-admin@example.com")
    aid = _running_agent("client@example.com", "test-restore", hosting_plan="monthly")
    db = SessionFactory()
    db.get(Tenant, aid).suspended_at = datetime.now(timezone.utc)
    db.commit(); db.close()

    resp = client.post(f"/api/admin/agents/{aid}/restore", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["hosting"]["state"] == "active"
    db = SessionFactory()
    assert db.get(Tenant, aid).suspended_at is None
    db.close()


def test_stripe_link_redirect_with_reference():
    admin = _admin("stripe-admin@example.com")
    # Configure un lien de recharge pour 10 €
    resp = client.put("/api/admin/stripe", json={
        "topup": {"10": "https://buy.stripe.com/test_10"},
    }, headers=admin)
    assert resp.status_code == 200

    headers = _register("stripe-user@example.com")
    aid = client.post("/api/agents", json={"name": "S", "subdomain": "test-stripe"},
                      headers=headers).json()["agent"]["id"]
    data = client.post(f"/api/agents/{aid}/topup", json={"amount_eur": 10}, headers=headers).json()
    # Redirigé vers Stripe, avec client_reference_id = id du checkout
    assert data["checkout_url"].startswith("https://buy.stripe.com/test_10")
    assert "client_reference_id=" in data["checkout_url"]


def test_stripe_webhook_credits_topup(monkeypatch):
    from app import api
    from app.db import SessionFactory
    from app.models import Checkout

    from app.models import Tenant

    monkeypatch.setattr(api, "get_keys_client", lambda: None)
    aid = _running_agent("wh@example.com", "test-wh")
    db = SessionFactory()
    co = Checkout(user_id=db.get(Tenant, aid).user_id, tenant_id=aid, kind="topup",
                  amount_eur=11.0, credit_eur=10.0, status="pending")
    db.add(co); db.commit(); cid = co.id; db.close()

    event = {"type": "checkout.session.completed",
             "data": {"object": {"client_reference_id": cid}}}
    resp = client.post("/api/stripe/webhook", json=event)
    assert resp.status_code == 200 and resp.json()["received"] is True

    db = SessionFactory()
    assert db.get(Checkout, cid).status == "paid"
    assert db.get(Tenant, aid).balance_eur == 10.0
    db.close()

    # Rejeu idempotent : pas de double crédit
    client.post("/api/stripe/webhook", json=event)
    db = SessionFactory()
    assert db.get(Tenant, aid).balance_eur == 10.0
    db.close()


def test_stripe_subscription_webhook_flow(monkeypatch):
    """Abonnement auto : checkout.session.completed mémorise l'abonnement Stripe,
    puis invoice.paid prolonge automatiquement (plan sub_monthly)."""
    from datetime import datetime, timezone
    from app import api
    from app.db import SessionFactory
    from app.models import Checkout, Tenant

    monkeypatch.setattr(api, "get_client", lambda: None)
    headers = _register("subwh@example.com")
    aid = _running_agent("subwh@example.com", "test-subwh", hosting_plan="manual")

    # Le client souscrit l'abonnement mensuel auto → checkout hosting sub_monthly
    data = client.post(f"/api/agents/{aid}/hosting", json={"plan": "sub_monthly"}, headers=headers).json()
    assert data["amount_eur"] == 19.0
    cid = data["checkout_url"].split("/pay/")[1]

    # Stripe confirme avec l'id d'abonnement
    client.post("/api/stripe/webhook", json={
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": cid, "subscription": "sub_ABC"}},
    })
    db = SessionFactory()
    agent = db.get(Tenant, aid)
    assert agent.hosting_plan == "sub_monthly"
    assert agent.stripe_subscription_id == "sub_ABC"
    first_until = agent.hosting_paid_until.replace(tzinfo=timezone.utc)
    db.close()

    # Un mois plus tard, Stripe prélève → invoice.paid prolonge
    resp = client.post("/api/stripe/webhook", json={
        "type": "invoice.paid", "data": {"object": {"subscription": "sub_ABC"}},
    })
    assert resp.status_code == 200
    db = SessionFactory()
    agent = db.get(Tenant, aid)
    assert agent.hosting_paid_until.replace(tzinfo=timezone.utc) > first_until
    assert agent.hosting_plan == "sub_monthly"
    db.close()


def test_stripe_webhook_bad_signature_rejected(monkeypatch):
    from app.config import get_settings
    get_settings().stripe_webhook_secret = "whsec_test"
    try:
        resp = client.post("/api/stripe/webhook",
                           json={"type": "checkout.session.completed", "data": {"object": {}}},
                           headers={"stripe-signature": "t=1,v1=deadbeef"})
        assert resp.status_code == 400
    finally:
        get_settings().stripe_webhook_secret = ""
