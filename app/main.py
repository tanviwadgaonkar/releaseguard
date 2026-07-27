"""ReleaseGuard API - explainable deployment-risk scoring.

Run:  uvicorn app.main:app --reload      (docs at http://localhost:8000/docs)
"""
from fastapi import FastAPI

from app.models import AnalyzeRequest, RiskReport
from app.scoring import score_release

app = FastAPI(
    title="ReleaseGuard",
    version="0.1.0",
    description="Deployment risk & regression analyzer - every score is "
                "explained by auditable factors.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze", response_model=RiskReport)
def analyze(request: AnalyzeRequest) -> RiskReport:
    """Score one candidate release / pull request."""
    return score_release(request)
