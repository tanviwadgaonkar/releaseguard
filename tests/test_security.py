"""API-key authentication: open by default, enforced when a key is configured."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import API_KEY_ENV

client = TestClient(app)
PAYLOAD = {"files": [{"path": "README.md", "additions": 1, "deletions": 0}]}


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "s3cret-key")
    return "s3cret-key"


def test_open_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert client.post("/analyze", json=PAYLOAD).status_code == 200


def test_missing_key_is_rejected(api_key):
    resp = client.post("/analyze", json=PAYLOAD)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "ApiKey"


def test_wrong_key_is_rejected(api_key):
    resp = client.post("/analyze", json=PAYLOAD, headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


def test_correct_key_is_accepted(api_key):
    resp = client.post("/analyze", json=PAYLOAD, headers={"X-API-Key": api_key})
    assert resp.status_code == 200


def test_auth_is_checked_before_the_body(api_key):
    """An unauthenticated caller must not learn the request schema from 422 messages."""
    resp = client.post("/analyze", json={"files": []})
    assert resp.status_code == 401


def test_health_stays_open_when_a_key_is_set(api_key):
    assert client.get("/health").status_code == 200
