"""Explainable deployment-risk scoring.

Each factor is a small, auditable rule that returns whether it triggered,
how many points it adds, and human-readable evidence. The final score is the
capped sum - so every point in the score can be traced to a reason.
"""
from app.classify import DEFAULT_CRITICAL, is_critical, is_infra, is_migration, is_source, is_test
from app.models import AnalyzeRequest, Factor, FileChange, RiskReport

LEVELS = ((60, "HIGH"), (30, "MEDIUM"), (0, "LOW"))
RECOMMENDATIONS = {
    "LOW": "Safe to release with standard review.",
    "MEDIUM": "Request one extra reviewer and deploy with monitoring.",
    "HIGH": "Block auto-release: require senior sign-off, staged rollout and a rollback plan.",
}


def _fmt(paths: list[str], limit: int = 3) -> str:
    shown = ", ".join(paths[:limit])
    extra = len(paths) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _factor(name, label, triggered, points, detail) -> Factor:
    return Factor(name=name, label=label, triggered=triggered,
                  points=points if triggered else 0, detail=detail)


def score_release(req: AnalyzeRequest) -> RiskReport:
    files: list[FileChange] = req.files
    # A rename touches both locations (moving auth/x.py out of auth/ is still
    # a critical-area change), but it is one file for the size factors.
    paths = [f.path for f in files] + [f.previous_path for f in files if f.previous_path]
    additions = sum(f.additions for f in files)
    deletions = sum(f.deletions for f in files)
    total = additions + deletions
    critical_prefixes = tuple(req.critical_paths) if req.critical_paths else DEFAULT_CRITICAL

    src_files = [f for f in files if is_source(f.path)]
    test_files = [f for f in files if is_test(f.path)]
    migration_hits = [p for p in paths if is_migration(p)]
    infra_hits = [p for p in paths if is_infra(p)]
    critical_hits = [p for p in paths if is_critical(p, critical_prefixes)]

    factors = [
        _factor("diff_size", "Large diff", total > 150,
                20 if total > 500 else 10,
                f"{total} changed lines ({additions}+ / {deletions}-)"),
        _factor("file_count", "Many files touched", len(files) > 8,
                15 if len(files) > 20 else 8,
                f"{len(files)} files in this change"),
        _factor("migrations", "Database migration", bool(migration_hits), 20,
                _fmt(migration_hits) or "no migration files"),
        _factor("critical_path", "Critical path modified", bool(critical_hits), 20,
                _fmt(critical_hits) or "no critical paths touched"),
        _factor("infra_config", "Infra / config change", bool(infra_hits), 10,
                _fmt(infra_hits) or "no infra files touched"),
        _factor("tests_missing", "Source changed without tests",
                bool(src_files) and not test_files, 15,
                f"{len(src_files)} source code files changed, {len(test_files)} test files updated"),
        _factor("deletion_heavy", "Deletion-heavy change",
                deletions > 100 and deletions > 2 * max(additions, 1), 10,
                f"{deletions} deletions vs {additions} additions"),
        _factor("risky_window", "Friday deploy window",
                req.deploy_time is not None and req.deploy_time.weekday() == 4, 5,
                req.deploy_time.strftime("%A %H:%M") if req.deploy_time else "no deploy time given"),
    ]

    score = min(100, sum(f.points for f in factors))
    level = next(lvl for threshold, lvl in LEVELS if score >= threshold)
    triggered = [f.label for f in factors if f.triggered]
    summary = (f"{level} risk ({score}/100): " + "; ".join(triggered)
               if triggered else "LOW risk (0/100): no risk factors triggered")

    return RiskReport(score=score, level=level,
                      recommendation=RECOMMENDATIONS[level],
                      factors=factors, summary=summary)


def from_github_files(gh_files: list[dict]) -> list[FileChange]:
    """Convert the GitHub API `GET /pulls/{n}/files` payload to FileChange list.

    A renamed file keeps its old name in ``previous_path``, so it is scored
    under both names but counted once.
    """
    return [FileChange(path=f["filename"], additions=f.get("additions", 0), deletions=f.get("deletions", 0),
                       previous_path=f.get("previous_filename") if f.get("status") == "renamed" else None)
            for f in gh_files]
