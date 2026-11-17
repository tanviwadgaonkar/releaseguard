"""The release-gate agent: it must stop for a person, and only act on a yes."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.agent import AgentService, AuditLog, SimulatedExecutor
from app.main import app
from app.security import API_KEY_ENV

LOW = {"files": [{"path": "README.md", "additions": 2, "deletions": 0}]}
# A migration plus a critical path plus untested source: scores HIGH.
HIGH = {"files": [
    {"path": "app/auth/login.py", "additions": 300, "deletions": 250},
    {"path": "db/migrations/0042_users.sql", "additions": 40, "deletions": 0},
    {"path": ".github/workflows/deploy.yml", "additions": 5, "deletions": 1},
]}


@pytest.fixture
def service(tmp_path):
    svc = AgentService(audit=AuditLog(str(tmp_path / "audit.jsonl")), executor=SimulatedExecutor())
    previous = app.state.agent_service
    app.state.agent_service = svc
    yield svc
    app.state.agent_service = previous


@pytest.fixture
def client(service, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    return TestClient(app)


def start(client, payload):
    resp = client.post("/agent/runs", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def decide(client, run_id, approve, reviewer="vinit", reason=""):
    return client.post(f"/agent/runs/{run_id}/decision",
                       json={"approve": approve, "reviewer": reviewer, "reason": reason})


def events(run):
    return [e["event"] for e in run["audit"]]


def test_low_risk_change_proceeds_without_a_person(client, service):
    run = start(client, LOW)
    assert run["status"] == "auto_approved_and_executed"
    assert run["report"]["level"] == "LOW"
    assert len(service.executor.executed) == 1
    assert events(run) == ["run_started", "action_proposed", "action_executed"]


def test_high_risk_change_pauses_and_executes_nothing(client, service):
    run = start(client, HIGH)
    assert run["report"]["level"] == "HIGH"
    assert run["status"] == "awaiting_approval"
    assert run["proposal"]["name"] == "block_release_and_request_signoff"
    assert service.executor.executed == []
    assert "action_executed" not in events(run)


def test_approval_executes_exactly_once_and_is_logged(client, service):
    run_id = start(client, HIGH)["run_id"]
    resp = decide(client, run_id, True, reviewer="priya", reason="rollback plan attached")
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "approved_and_executed"
    assert len(service.executor.executed) == 1
    assert events(run) == ["run_started", "action_proposed", "decision_recorded", "action_executed"]
    decision = next(e for e in run["audit"] if e["event"] == "decision_recorded")
    assert decision["reviewer"] == "priya" and decision["approved"] is True
    assert decision["reason"] == "rollback plan attached"


def test_rejection_never_executes(client, service):
    run_id = start(client, HIGH)["run_id"]
    run = decide(client, run_id, False, reason="no rollback plan").json()
    assert run["status"] == "rejected"
    assert service.executor.executed == []
    assert events(run) == ["run_started", "action_proposed", "decision_recorded", "action_skipped"]


def test_a_run_can_only_be_decided_once(client, service):
    run_id = start(client, HIGH)["run_id"]
    assert decide(client, run_id, True).status_code == 200
    again = decide(client, run_id, True)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"
    assert len(service.executor.executed) == 1


def test_a_rejected_run_cannot_be_approved_later(client, service):
    run_id = start(client, HIGH)["run_id"]
    assert decide(client, run_id, False).status_code == 200
    assert decide(client, run_id, True).status_code == 409
    assert service.executor.executed == []


def test_a_low_risk_run_has_nothing_to_decide(client, service):
    run_id = start(client, LOW)["run_id"]
    assert decide(client, run_id, True).status_code == 409
    assert len(service.executor.executed) == 1


def test_simultaneous_approvals_execute_once(client, service):
    run_id = start(client, HIGH)["run_id"]
    codes = []

    def approve():
        codes.append(decide(client, run_id, True).status_code)

    threads = [threading.Thread(target=approve) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409, 409]
    assert len(service.executor.executed) == 1


def test_unknown_run_is_404(client):
    assert client.get("/agent/runs/nope").status_code == 404
    assert decide(client, "nope", True).status_code == 404


@pytest.mark.parametrize("body", [
    {"approve": True},                                  # no reviewer
    {"approve": True, "reviewer": ""},                  # empty reviewer
    {"reviewer": "priya"},                              # no decision
    {"approve": "maybe", "reviewer": "priya"},          # not a boolean
    {"approve": True, "reviewer": "x" * 101},           # too long
    {"approve": True, "reviewer": "a", "reason": "x" * 501},
])
def test_bad_decisions_are_rejected_and_nothing_runs(client, service, body):
    run_id = start(client, HIGH)["run_id"]
    resp = client.post(f"/agent/runs/{run_id}/decision", json=body)
    assert resp.status_code == 422
    assert service.executor.executed == []
    assert client.get(f"/agent/runs/{run_id}").json()["status"] == "awaiting_approval"


def test_invalid_change_is_rejected_before_any_run_starts(client, service):
    assert client.post("/agent/runs", json={"files": []}).status_code == 422
    assert service.audit.events("anything") == []


def test_too_many_waiting_runs_returns_429(tmp_path, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    svc = AgentService(audit=AuditLog(), executor=SimulatedExecutor(), max_pending=2)
    previous = app.state.agent_service
    app.state.agent_service = svc
    try:
        c = TestClient(app)
        first = start(c, HIGH)
        start(c, HIGH)
        full = c.post("/agent/runs", json=HIGH)
        assert full.status_code == 429
        assert full.json()["error"]["code"] == "too_many_requests"
        decide(c, first["run_id"], False)          # deciding frees a slot
        assert c.post("/agent/runs", json=HIGH).status_code == 201
    finally:
        app.state.agent_service = previous


def test_every_agent_route_needs_the_api_key(service, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "s3cret")
    c = TestClient(app)
    assert c.post("/agent/runs", json=HIGH).status_code == 401
    assert c.get("/agent/runs/abc").status_code == 401
    assert c.post("/agent/runs/abc/decision", json={"approve": True, "reviewer": "x"}).status_code == 401
    ok = c.post("/agent/runs", json=HIGH, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 201


def test_audit_log_file_is_append_only_json_lines(client, service, tmp_path):
    first = start(client, HIGH)["run_id"]
    decide(client, first, True)
    second = start(client, LOW)["run_id"]
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in lines]
    assert {p["run_id"] for p in parsed} == {first, second}
    assert [p["event"] for p in parsed if p["run_id"] == first] == [
        "run_started", "action_proposed", "decision_recorded", "action_executed"]
    assert all("time" in p for p in parsed)


def test_get_run_reports_the_same_state_as_start(client):
    started = start(client, HIGH)
    assert client.get(f"/agent/runs/{started['run_id']}").json() == started


def test_decisions_show_up_in_metrics(client):
    run_id = start(client, HIGH)["run_id"]
    decide(client, run_id, False)
    body = client.get("/metrics").text
    assert 'releaseguard_agent_decisions_total{decision="rejected"}' in body
