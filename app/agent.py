"""A release-gate agent that proposes an action and waits for a human decision.

The agent is a small LangGraph graph::

    assess -> propose --(LOW)--------------------------> execute -> END
                 |
                 +--(MEDIUM / HIGH)--> approval_gate  (pauses here)
                                            |
                                      record_decision --(approved)--> execute -> END
                                            |
                                            +--(rejected)--> reject -> END

* ``assess`` scores the change with the existing rule-based scorer.
* ``propose`` turns the risk level into one proposed action on the release
  pipeline (mark ready, add a reviewer, or block and request sign-off). This is
  a fixed policy, not a language model, so the proposal is predictable and testable.
* ``approval_gate`` calls LangGraph's ``interrupt``: the run stops and its state
  is saved until a person approves or rejects it. Nothing is executed before
  that decision, and a rejected run never reaches ``execute``.
* Every step is written to an append-only audit log (see ``AuditLog``).

Side effects (audit lines, the executor call) live only in nodes that run
once. LangGraph re-runs the interrupted node from its start when a run
resumes, so ``approval_gate`` does nothing except ask and return the answer.

Limits, stated plainly: the default executor is *simulated* (it records what
would have happened and calls nothing external), runs are kept in memory and
are lost on restart (the audit log file survives), and the reviewer name is
whatever the caller sends, because the service has one shared API key.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.models import AnalyzeRequest
from app.scoring import score_release

AUDIT_LOG_ENV = "RELEASEGUARD_AUDIT_LOG"
MAX_PENDING_ENV = "RELEASEGUARD_MAX_PENDING_RUNS"
DEFAULT_MAX_PENDING = 1000

# Levels the policy lets through without a person. Everything else must be approved.
AUTO_APPROVED_LEVELS = frozenset({"LOW"})

# What the agent can do to the release pipeline. Only the harmless one runs on its own.
ACTIONS = {
    "LOW": ("mark_release_ready", "Mark the release ready to ship with standard review."),
    "MEDIUM": ("request_extra_reviewer", "Add one extra reviewer to the release and turn on monitoring."),
    "HIGH": ("block_release_and_request_signoff", "Block auto-release and open a senior sign-off request with a rollback plan."),
}


class RunState(TypedDict, total=False):
    run_id: str
    request: dict
    report: dict
    proposal: dict
    needs_approval: bool
    decision: dict
    outcome: Literal["executed", "rejected"]
    result: dict


class Executor(Protocol):
    def execute(self, run_id: str, action: dict) -> dict: ...


class SimulatedExecutor:
    """Records what would have been done. It calls no external system."""

    def __init__(self) -> None:
        self.executed: list[dict] = []

    def execute(self, run_id: str, action: dict) -> dict:
        record = {"run_id": run_id, "action": action["name"], "simulated": True}
        self.executed.append(record)
        return record


class AuditLog:
    """Append-only record of everything the agent does, one JSON object per line.

    Events are kept in memory (so a run's history can be returned by the API)
    and, when a path is given, appended to that file as they happen.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def record(self, run_id: str, event: str, **data: Any) -> dict:
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "run_id": run_id, "event": event, **data}
        with self._lock:
            self._events.append(entry)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry) + "\n")
        return entry

    def events(self, run_id: str) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._events if e["run_id"] == run_id]


def build_graph(audit: AuditLog, executor: Executor, checkpointer: Any | None = None):
    """Compile the release-gate graph. A checkpointer is required for pausing."""

    def assess(state: RunState) -> dict:
        request = AnalyzeRequest.model_validate(state["request"])
        report = score_release(request)
        audit.record(state["run_id"], "run_started", score=report.score, level=report.level)
        return {"report": report.model_dump(mode="json")}

    def propose(state: RunState) -> dict:
        level = state["report"]["level"]
        name, description = ACTIONS[level]
        needs_approval = level not in AUTO_APPROVED_LEVELS
        proposal = {"name": name, "description": description, "level": level}
        audit.record(state["run_id"], "action_proposed", action=name, level=level,
                     approval_required=needs_approval)
        return {"proposal": proposal, "needs_approval": needs_approval}

    def approval_gate(state: RunState) -> dict:
        # Nothing but the question lives here: this node runs again on resume.
        decision = interrupt({"proposal": state["proposal"], "report": state["report"]})
        return {"decision": decision}

    def record_decision(state: RunState) -> dict:
        decision = state["decision"]
        audit.record(state["run_id"], "decision_recorded", approved=decision["approve"],
                     reviewer=decision["reviewer"], reason=decision.get("reason", ""))
        return {}

    def execute(state: RunState) -> dict:
        result = executor.execute(state["run_id"], state["proposal"])
        audit.record(state["run_id"], "action_executed", action=state["proposal"]["name"],
                     simulated=bool(result.get("simulated")))
        return {"outcome": "executed", "result": result}

    def reject(state: RunState) -> dict:
        audit.record(state["run_id"], "action_skipped", action=state["proposal"]["name"],
                     reason="rejected by reviewer")
        return {"outcome": "rejected"}

    def after_propose(state: RunState) -> str:
        return "approval_gate" if state["needs_approval"] else "execute"

    def after_decision(state: RunState) -> str:
        return "execute" if state["decision"]["approve"] else "reject"

    graph = StateGraph(RunState)
    graph.add_node("assess", assess)
    graph.add_node("propose", propose)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("record_decision", record_decision)
    graph.add_node("execute", execute)
    graph.add_node("reject", reject)
    graph.add_edge(START, "assess")
    graph.add_edge("assess", "propose")
    graph.add_conditional_edges("propose", after_propose, ["approval_gate", "execute"])
    graph.add_edge("approval_gate", "record_decision")
    graph.add_conditional_edges("record_decision", after_decision, ["execute", "reject"])
    graph.add_edge("execute", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


class RunNotFound(Exception):
    pass


class RunNotAwaitingApproval(Exception):
    pass


class TooManyPendingRuns(Exception):
    pass


class AgentService:
    """Starts runs, applies decisions and reports status. One instance per app."""

    def __init__(self, audit: AuditLog | None = None, executor: Executor | None = None,
                 max_pending: int | None = None) -> None:
        self.audit = audit or AuditLog(os.environ.get(AUDIT_LOG_ENV) or None)
        self.executor = executor or SimulatedExecutor()
        self.graph = build_graph(self.audit, self.executor)
        self.max_pending = max_pending if max_pending is not None else int(
            os.environ.get(MAX_PENDING_ENV, DEFAULT_MAX_PENDING))
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def start(self, request: AnalyzeRequest) -> dict:
        with self._lock:
            if len(self._pending) >= self.max_pending:
                raise TooManyPendingRuns
            run_id = uuid.uuid4().hex
        self.graph.invoke({"run_id": run_id, "request": request.model_dump(mode="json")},
                          self._config(run_id))
        status = self.status(run_id)
        if status["status"] == "awaiting_approval":
            with self._lock:
                self._pending.add(run_id)
        return status

    def decide(self, run_id: str, approve: bool, reviewer: str, reason: str = "") -> dict:
        # The lock makes "check it is waiting, then resume" one step, so two
        # simultaneous decisions cannot both execute the action.
        with self._lock:
            current = self.status(run_id)
            if current["status"] != "awaiting_approval":
                raise RunNotAwaitingApproval(current["status"])
            self.graph.invoke(Command(resume={"approve": approve, "reviewer": reviewer, "reason": reason}),
                              self._config(run_id))
            self._pending.discard(run_id)
        return self.status(run_id)

    def status(self, run_id: str) -> dict:
        snapshot = self.graph.get_state(self._config(run_id))
        values = snapshot.values
        if not values:
            raise RunNotFound(run_id)
        if snapshot.next:
            status = "awaiting_approval"
        elif values.get("outcome") == "rejected":
            status = "rejected"
        elif values.get("decision"):
            status = "approved_and_executed"
        else:
            status = "auto_approved_and_executed"
        return {"run_id": run_id, "status": status, "report": values.get("report"),
                "proposal": values.get("proposal"), "decision": values.get("decision"),
                "result": values.get("result"), "audit": self.audit.events(run_id)}
