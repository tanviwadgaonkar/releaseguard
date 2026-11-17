"""Error responses: one shape, no echoed input, no leaked internals."""
from fastapi.testclient import TestClient

import app.main as main
from app.main import app

client = TestClient(app)
# Server errors become a 500 response instead of being re-raised into the test.
safe_client = TestClient(app, raise_server_exceptions=False)


def test_validation_error_has_the_standard_shape():
    resp = client.post("/analyze", json={"files": [{"path": "a.py", "additions": -5}]})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert {"field": "files.0.additions", "message": "Input should be greater than or equal to 0"} in error["details"]


def test_validation_error_does_not_echo_the_submitted_value():
    resp = client.post("/analyze", json={"files": [{"path": "a.py", "additions": -987654}]})
    assert "-987654" not in resp.text


def test_unknown_route_uses_the_same_shape():
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_wrong_method_uses_the_same_shape():
    resp = client.get("/analyze")
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "method_not_allowed"


def test_unexpected_error_is_generic_and_leaks_nothing(monkeypatch):
    def boom(_request):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(main, "score_release", boom)
    resp = safe_client.post("/analyze", json={"files": [{"path": "a.py"}]})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in resp.text


def test_oversized_numbers_and_long_critical_paths_are_rejected():
    big = client.post("/analyze", json={"files": [{"path": "a.py", "additions": 10**12}]})
    assert big.status_code == 422
    long_name = client.post("/analyze", json={"files": [{"path": "a.py"}], "critical_paths": ["x" * 201]})
    assert long_name.status_code == 422
