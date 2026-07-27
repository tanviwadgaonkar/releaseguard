"""API tests via FastAPI TestClient."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_analyze_low_risk():
    payload = {"files": [{"path": "README.md", "additions": 3, "deletions": 1}]}
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["level"] == "LOW"
    assert body["score"] == sum(f["points"] for f in body["factors"])


def test_analyze_high_risk_has_explanations():
    payload = {"files": [
        {"path": "billing/invoice.py", "additions": 400, "deletions": 120},
        {"path": "migrations/0007_alter.sql", "additions": 60, "deletions": 0},
    ]}
    body = client.post("/analyze", json=payload).json()
    assert body["level"] == "HIGH"
    assert any(f["triggered"] and f["detail"] for f in body["factors"])
    assert "sign-off" in body["recommendation"]


def test_validation_error_on_bad_payload():
    resp = client.post("/analyze", json={"files": [{"path": "a.py", "additions": -5}]})
    assert resp.status_code == 422
