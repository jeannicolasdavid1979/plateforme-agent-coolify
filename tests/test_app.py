"""Basic tests for the plateforme-agent-coolify."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import init_db, engine
from app.models import Base


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
