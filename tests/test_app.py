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


def test_register_and_login():
    # Register
    resp = client.post("/api/auth/register", json={"email": "test@example.com", "password": "secret123"})
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
    client.post("/api/auth/register", json={"email": "dup@example.com", "password": "secret123"})
    resp = client.post("/api/auth/register", json={"email": "dup@example.com", "password": "secret123"})
    assert resp.status_code == 409


def test_invalid_login():
    resp = client.post("/api/auth/login", json={"email": "nope@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_create_agent_requires_auth():
    resp = client.post("/api/agents", json={"name": "Test", "subdomain": "test-agent"})
    assert resp.status_code == 401


def test_list_agents():
    # Register and get token
    resp = client.post("/api/auth/register", json={"email": "user@example.com", "password": "secret123"})
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # List agents (empty)
    resp = client.get("/api/agents", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["agents"] == []


def _register(email="buyer@example.com"):
    resp = client.post("/api/auth/register", json={"email": email, "password": "secret123"})
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

    resp = client.post(f"/api/agents/{agent_id}/topup", headers=headers)
    assert resp.status_code == 200
    checkout_id = resp.json()["checkout_url"].split("/pay/")[1]
    resp = client.post(f"/api/pay/{checkout_id}")
    assert resp.status_code == 200
    assert resp.json()["credited_eur"] == 10.0

    resp = client.get(f"/api/agents/{agent_id}", headers=headers)
    assert resp.json()["balance_eur"] == 10.0


def test_pricing_defaults_and_admin_update():
    # Lecture publique des prix (défauts de config)
    resp = client.get("/api/pricing")
    assert resp.status_code == 200
    assert resp.json() == {
        "deploy_price_eur": 29.0,
        "topup_amount_eur": 10.0,
        "initial_credit_eur": 5.0,
    }

    # Un utilisateur normal ne peut pas modifier les prix
    headers = _register("pleb@example.com")
    resp = client.put("/api/admin/pricing", json={"deploy_price_eur": 1.0}, headers=headers)
    assert resp.status_code == 403

    # L'admin (email dans admin_emails, promu au login) peut
    from app.config import get_settings
    get_settings().admin_emails = "boss@example.com"
    client.post("/api/auth/register", json={"email": "boss@example.com", "password": "secret123"})
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
        def create_service(self, name):
            calls["probes"] += 1
            return "probe000000000ab"
        def get_compose_raw(self, u): return TEMPLATE
        def delete_service(self, u): return True
        def create_service_from_compose(self, name, compose_yaml):
            calls["compose_creates"].append((name, compose_yaml))
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
    name, compose = calls["compose_creates"][0]
    assert name == "hermes-test-tpl"
    assert "SERVICE_FQDN_HERMESWEBUI_8787=https://test-tpl.kechlab.com" in compose
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
