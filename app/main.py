"""ReleaseGuard API - explainable deployment-risk scoring.

Run:  uvicorn app.main:app --reload      (docs at http://localhost:8000/docs)
"""
from fastapi import Depends, FastAPI

from app.errors import install_error_handlers
from app.models import AnalyzeRequest, RiskReport
from app.scoring import score_release
from app.security import require_api_key

app = FastAPI(
    title="ReleaseGuard",
    version="0.1.0",
    description="Deployment risk & regression analyzer - every score is "
                "explained by auditable factors.",
)
install_error_handlers(app)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze", response_model=RiskReport, dependencies=[Depends(require_api_key)])
def analyze(request: AnalyzeRequest) -> RiskReport:
    """Score one candidate release / pull request."""
    return score_release(request)
