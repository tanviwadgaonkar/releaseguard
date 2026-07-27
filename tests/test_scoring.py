"""Unit tests for the explainable scoring engine."""
from datetime import datetime

from app.models import AnalyzeRequest, FileChange
from app.scoring import from_github_files, score_release


def make(paths_and_lines, **kwargs):
    files = [FileChange(path=p, additions=a, deletions=d)
             for p, a, d in paths_and_lines]
    return AnalyzeRequest(files=files, **kwargs)


def test_docs_only_change_is_low():
    report = score_release(make([("README.md", 12, 2)]))
    assert report.level == "LOW"
    assert report.score < 30


def test_migration_plus_payments_without_tests_is_high():
    req = make([
        ("payments/checkout.py", 220, 40),
        ("migrations/0042_add_invoice_table.sql", 80, 0),
        ("core/settings.yml", 5, 1),
    ])
    report = score_release(req)
    assert report.level == "HIGH"
    triggered = {f.name for f in report.factors if f.triggered}
    assert {"migrations", "critical_path", "tests_missing"} <= triggered


def test_tests_included_removes_missing_tests_factor():
    req = make([
        ("service/api.py", 60, 10),
        ("tests/test_api.py", 40, 0),
    ])
    report = score_release(req)
    missing = next(f for f in report.factors if f.name == "tests_missing")
    assert not missing.triggered


def test_friday_deploy_adds_points():
    friday = datetime(2026, 7, 24, 17, 30)   # a Friday
    monday = datetime(2026, 7, 27, 10, 0)
    base = [("service/api.py", 10, 2), ("tests/test_api.py", 5, 0)]
    assert (score_release(make(base, deploy_time=friday)).score
            > score_release(make(base, deploy_time=monday)).score)


def test_custom_critical_paths_override():
    req = make([("notebooks/etl.py", 30, 5)], critical_paths=["notebooks"])
    report = score_release(req)
    assert next(f for f in report.factors if f.name == "critical_path").triggered


def test_score_is_capped_and_every_point_is_explained():
    req = make([(f"payments/f{i}.py", 60, 60) for i in range(25)]
               + [("migrations/0001.sql", 30, 0), ("docker-compose.yml", 5, 0)],
               deploy_time=datetime(2026, 7, 24, 18, 0))
    report = score_release(req)
    assert report.score <= 100
    assert sum(f.points for f in report.factors) >= report.score


def test_github_files_adapter():
    gh = [{"filename": "auth/login.py", "additions": 9, "deletions": 1},
          {"filename": "README.md", "additions": 2, "deletions": 0}]
    files = from_github_files(gh)
    assert files[0].path == "auth/login.py" and files[0].additions == 9
