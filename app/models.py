"""Pydantic models for ReleaseGuard's API contract."""
from datetime import datetime

from pydantic import BaseModel, Field


class FileChange(BaseModel):
    path: str
    additions: int = Field(ge=0, default=0)
    deletions: int = Field(ge=0, default=0)


class AnalyzeRequest(BaseModel):
    """One candidate deployment / pull request."""
    files: list[FileChange]
    deploy_time: datetime | None = None
    critical_paths: list[str] | None = Field(
        default=None,
        description="Path prefixes considered critical (overrides defaults).",
    )


class Factor(BaseModel):
    name: str
    label: str
    triggered: bool
    points: int
    detail: str


class RiskReport(BaseModel):
    score: int = Field(ge=0, le=100)
    level: str            # LOW | MEDIUM | HIGH
    recommendation: str
    factors: list[Factor]
    summary: str
