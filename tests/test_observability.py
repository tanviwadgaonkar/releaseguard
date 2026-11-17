"""Request IDs, structured logs, body-size limit and Prometheus metrics."""
import json
import logging

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

import app.main as main
from app.main import app
from app.observability import JsonFormatter

client = TestClient(app)
safe_client = TestClient(app, raise_server_exceptions=False)
PAYLOAD = {"files": [{"path": "README.md", "additions": 1, "deletions": 0}]}


def _sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- request IDs -----------------------------------------------------------

def test_every_response_has_a_request_id():
    assert client.get("/health").headers["x-request-id"]


def test_a_safe_incoming_request_id_is_kept():
    resp = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert resp.headers["x-request-id"] == "trace-123"


def test_an_unsafe_incoming_request_id_is_replaced():
    resp = client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})
    assert resp.headers["x-request-id"] != "bad id\twith spaces"


def test_error_body_carries_the_same_request_id_as_the_header():
    resp = client.post("/analyze", json={"files": []}, headers={"X-Request-ID": "abc-1"})
    assert resp.json()["error"]["request_id"] == "abc-1"
    assert resp.headers["x-request-id"] == "abc-1"


def test_unexpected_error_still_returns_a_request_id(monkeypatch):
    def boom(_request):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "score_release", boom)
    resp = safe_client.post("/analyze", json=PAYLOAD)
    assert resp.status_code == 500
    assert resp.json()["error"]["request_id"] == resp.headers["x-request-id"]


# --- structured logs -------------------------------------------------------

def test_each_request_writes_one_log_line_with_the_fields(caplog):
    with caplog.at_level(logging.INFO, logger="releaseguard"):
        client.get("/health", headers={"X-Request-ID": "log-1"})
    record = next(r for r in caplog.records if getattr(r, "request_id", None) == "log-1")
    assert (record.method, record.path, record.status) == ("GET", "/health", 200)
    assert record.duration_ms >= 0


def test_json_formatter_outputs_valid_json(caplog):
    with caplog.at_level(logging.INFO, logger="releaseguard"):
        client.get("/health", headers={"X-Request-ID": "log-2"})
    record = next(r for r in caplog.records if getattr(r, "request_id", None) == "log-2")
    line = json.loads(JsonFormatter().format(record))
    assert line["request_id"] == "log-2" and line["status"] == 200


# --- body-size limit -------------------------------------------------------

def test_oversized_body_is_rejected_with_413(monkeypatch):
    monkeypatch.setenv("RELEASEGUARD_MAX_BODY_BYTES", "50")
    resp = client.post("/analyze", json=PAYLOAD)  # the JSON body is longer than 50 bytes
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_body_under_the_limit_is_accepted(monkeypatch):
    monkeypatch.setenv("RELEASEGUARD_MAX_BODY_BYTES", "5000")
    assert client.post("/analyze", json=PAYLOAD).status_code == 200


# --- metrics ---------------------------------------------------------------

def test_metrics_endpoint_uses_the_prometheus_format():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "releaseguard_requests_total" in resp.text


def test_analyses_counter_increments_for_the_returned_level():
    before = _sample("releaseguard_analyses_total", {"level": "LOW"})
    client.post("/analyze", json=PAYLOAD)
    assert _sample("releaseguard_analyses_total", {"level": "LOW"}) == before + 1


def test_request_counter_uses_the_route_template_and_status():
    labels = {"method": "POST", "path": "/analyze", "status": "200"}
    before = _sample("releaseguard_requests_total", labels)
    client.post("/analyze", json=PAYLOAD)
    assert _sample("releaseguard_requests_total", labels) == before + 1


def test_unknown_urls_do_not_create_new_label_values():
    client.get("/random-url-12345")
    text = client.get("/metrics").text
    assert "random-url-12345" not in text
    assert 'path="unmatched"' in text
