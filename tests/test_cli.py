import io
import json
from pathlib import Path

from app import cli
from app.models import AnalyzeRequest
from app.scoring import from_github_files, score_release

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_file_command_and_exit_codes(capsys):
    assert cli.main(["file", str(EXAMPLES / "sample_pr_high_risk.json")]) == 2  # HIGH gates CI
    assert json.loads(capsys.readouterr().out)["level"] == "HIGH"
    assert cli.main(["file", str(EXAMPLES / "sample_pr_low_risk.json")]) == 0


def test_markdown_report(capsys):
    cli.main(["--markdown", "file", str(EXAMPLES / "sample_pr_high_risk.json")])
    out = capsys.readouterr().out
    assert out.startswith("### ReleaseGuard: HIGH risk")
    assert "| ⚠️ | Database migration | 20 |" in out


def test_fetch_pr_files_paginates_and_sends_token():
    pages = {1: [{"filename": f"f{i}.py"} for i in range(100)], 2: [{"filename": "last.py"}]}
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.append(req)
        page = int(req.full_url.rsplit("page=", 1)[1])
        return Resp(json.dumps(pages[page]).encode())

    files = cli.fetch_pr_files("o/r", 7, token="t0k", opener=opener)
    assert len(files) == 101 and len(seen) == 2
    assert seen[0].get_header("Authorization") == "Bearer t0k"
    assert "/repos/o/r/pulls/7/files" in seen[0].full_url


def test_renamed_file_is_scored_under_both_names():
    files = from_github_files([{"filename": "lib/login.py", "previous_filename": "auth/login.py",
                                "status": "renamed", "additions": 0, "deletions": 0}])
    assert [(f.path, f.previous_path) for f in files] == [("lib/login.py", "auth/login.py")]
    report = score_release(AnalyzeRequest(files=files))
    assert next(f for f in report.factors if f.name == "critical_path").triggered


def test_renames_count_once_for_file_count():
    # 5 pure renames are 5 files, below the "many files" threshold of 8 (was counted as 10).
    gh = [{"filename": f"new/m{i}.md", "previous_filename": f"old/m{i}.md", "status": "renamed"} for i in range(5)]
    report = score_release(AnalyzeRequest(files=from_github_files(gh)))
    assert not next(f for f in report.factors if f.name == "file_count").triggered


def test_github_http_error_is_reported_not_raised(monkeypatch, capsys):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(cli, "fetch_pr_files", boom)
    assert cli.main(["github", "o/r", "1"]) == 1
    assert "404 Not Found" in capsys.readouterr().err
