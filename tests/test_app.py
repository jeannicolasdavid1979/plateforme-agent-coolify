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
