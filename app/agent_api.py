"""HTTP endpoints for the release-gate agent. Every route needs the API key (when one is set)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent import AgentService, RunNotAwaitingApproval, RunNotFound, TooManyPendingRuns
from app.models import AnalyzeRequest
from app.observability import AGENT_DECISIONS
from app.security import require_api_key

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_api_key)])


class Decision(BaseModel):
    approve: bool
    reviewer: str = Field(min_length=1, max_length=100, description="Who is making the call (recorded in the audit log).")
    reason: str = Field(default="", max_length=500)


def get_service(request: Request) -> AgentService:
    return request.app.state.agent_service


@router.post("/runs", status_code=201)
def start_run(body: AnalyzeRequest, service: AgentService = Depends(get_service)) -> dict:
    """Score a change and propose an action. MEDIUM and HIGH runs wait for a decision."""
    try:
        run = service.start(body)
    except TooManyPendingRuns:
        raise HTTPException(status_code=429, detail="Too many runs are waiting for a decision. Decide some first.")
    if run["status"] != "awaiting_approval":
        AGENT_DECISIONS.labels(decision="auto_approved").inc()
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: str, service: AgentService = Depends(get_service)) -> dict:
    try:
        return service.status(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="No such run.")


@router.post("/runs/{run_id}/decision")
def decide(run_id: str, body: Decision, service: AgentService = Depends(get_service)) -> dict:
    """Approve or reject the proposed action. A run can be decided once."""
    try:
        run = service.decide(run_id, body.approve, body.reviewer, body.reason)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="No such run.")
    except RunNotAwaitingApproval:
        raise HTTPException(status_code=409, detail="This run is not waiting for a decision.")
    AGENT_DECISIONS.labels(decision="approved" if body.approve else "rejected").inc()
    return run
