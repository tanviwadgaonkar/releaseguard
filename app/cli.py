"""Score a release from the command line.

    python -m app.cli file examples/sample_pr_high_risk.json
    python -m app.cli github tanviwadgaonkar/releaseguard 3 [--markdown]

``github`` reads the PR's changed files from the GitHub REST API (paginated,
up to GitHub's 3,000-file limit). Public repos need no token; set
``GITHUB_TOKEN`` for private repos or a higher rate limit. Exit code is 0 for
LOW/MEDIUM and 2 for HIGH, so it can gate a CI job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from app.models import AnalyzeRequest, RiskReport
from app.scoring import from_github_files, score_release

API = "https://api.github.com"


def fetch_pr_files(repo: str, number: int, token: str | None = None, opener=urllib.request.urlopen) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "releaseguard"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    files: list[dict] = []
    for page in range(1, 31):  # 30 pages x 100 = GitHub's 3,000-file cap
        req = urllib.request.Request(f"{API}/repos/{repo}/pulls/{number}/files?per_page=100&page={page}",
                                     headers=headers)
        with opener(req, timeout=30) as resp:
            batch = json.loads(resp.read())
        files += batch
        if len(batch) < 100:
            break
    return files


def to_markdown(report: RiskReport, title: str) -> str:
    rows = "\n".join(f"| {'⚠️' if f.triggered else '·'} | {f.label} | {f.points} | {f.detail} |"
                     for f in report.factors)
    return (f"### ReleaseGuard: {report.level} risk ({report.score}/100) — {title}\n\n"
            f"**{report.recommendation}**\n\n| | Factor | Points | Evidence |\n|---|---|---|---|\n{rows}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="releaseguard", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--markdown", action="store_true", help="print a Markdown report instead of JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("file")
    f.add_argument("path")
    g = sub.add_parser("github")
    g.add_argument("repo", help="owner/name")
    g.add_argument("number", type=int)
    a = p.parse_args(argv)

    if a.cmd == "file":
        request = AnalyzeRequest.model_validate_json(Path(a.path).read_text(encoding="utf-8"))
        title = a.path
    else:
        try:
            gh_files = fetch_pr_files(a.repo, a.number, os.environ.get("GITHUB_TOKEN"))
        except urllib.error.HTTPError as exc:
            print(f"GitHub API error for {a.repo}#{a.number}: {exc.code} {exc.reason}", file=sys.stderr)
            return 1
        if not gh_files:
            print("PR has no changed files", file=sys.stderr)
            return 0
        request = AnalyzeRequest(files=from_github_files(gh_files))
        title = f"{a.repo}#{a.number}"

    report = score_release(request)
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252, which has no ⚠️
        sys.stdout.reconfigure(encoding="utf-8")
    print(to_markdown(report, title) if a.markdown else report.model_dump_json(indent=2))
    return 2 if report.level == "HIGH" else 0


if __name__ == "__main__":
    sys.exit(main())
