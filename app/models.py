"""Pydantic models for ReleaseGuard's API contract."""
from datetime import datetime

from pydantic import BaseModel, Field


class FileChange(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    additions: int = Field(ge=0, default=0)
    deletions: int = Field(ge=0, default=0)
    previous_path: str | None = Field(default=None, max_length=1000,
                                      description="Old path if the file was renamed; scored as touched too.")


class AnalyzeRequest(BaseModel):
    """One candidate deployment / pull request."""
    files: list[FileChange] = Field(min_length=1, max_length=10_000)
    deploy_time: datetime | None = None
    critical_paths: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Critical areas (override the defaults): a name such as 'auth' matches "
                    "that directory or file stem anywhere; an entry with '/' is a path prefix.",
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
