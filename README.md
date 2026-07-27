# ReleaseGuard — Deployment Risk & Regression Analyzer

![CI](https://github.com/tanviwadgaonkar/releaseguard/actions/workflows/ci.yml/badge.svg)

**Explainable deployment-risk scoring.** Give ReleaseGuard the files changed
in a release (or a GitHub PR), and it returns a 0–100 risk score where **every
point is traced to an auditable factor** — changed-code size, database
migrations, critical-path modifications, missing test updates, infra/config
changes, deletion-heavy diffs and risky deploy windows.

```
PR / release metadata ──► factor rules (8) ──► score + level + recommendation
                              │
                    each factor: triggered? points, evidence
```

## Why explainable?

A single opaque number can't gate a release. Every ReleaseGuard factor
returns *what triggered, how many points it added, and the evidence*
(which files), so a reviewer can agree or override with full context.

## API

```bash
uvicorn app.main:app --reload          # interactive docs at /docs

curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d @examples/sample_pr_high_risk.json
```

Response (trimmed):

```json
{
  "score": 80,
  "level": "HIGH",
  "recommendation": "Block auto-release: require senior sign-off, staged rollout and a rollback plan.",
  "factors": [
    {"name": "migrations", "label": "Database migration", "triggered": true,
     "points": 20, "detail": "migrations/0042_add_invoice_table.sql"},
    {"name": "critical_path", "label": "Critical path modified", "triggered": true,
     "points": 20, "detail": "payments/checkout.py"}
  ],
  "summary": "HIGH risk (80/100): Large diff; Database migration; ..."
}
```

`app/scoring.py` also ships a `from_github_files()` adapter that converts the
GitHub `GET /pulls/{n}/files` API payload straight into a scoring request.

## Risk factors

| Factor | Points | Triggered when |
|---|---|---|
| Large diff | 10–20 | > 150 / > 500 changed lines |
| Many files touched | 8–15 | > 8 / > 20 files |
| Database migration | 20 | `migrations/`, `alembic/`, `*.sql` |
| Critical path modified | 20 | `auth/`, `payments/`, `billing/`, `security/`, `core/` (configurable) |
| Infra / config change | 10 | Dockerfile, compose, `*.yml`, `.env`, terraform, k8s, helm |
| Source changed without tests | 15 | source files changed, zero test files updated |
| Deletion-heavy change | 10 | deletions > 100 and > 2× additions |
| Friday deploy window | 5 | deploy time falls on a Friday |

Levels: **LOW** < 30 ≤ **MEDIUM** < 60 ≤ **HIGH** — each with a concrete
release recommendation.

## Run the tests / Docker

```bash
pip install -r requirements.txt
python -m pytest -q          # 11 tests

docker build -t releaseguard .
docker run -p 8000:8000 releaseguard
```

## Roadmap

- GitHub webhook mode: score every PR automatically and comment the report
- Historical calibration: learn factor weights from past incident data
- Regression-test selection: map changed files to the test subset to re-run

## Stack

Python · FastAPI · Pydantic v2 · pytest · Docker · GitHub Actions
