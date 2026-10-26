"""Classify changed file paths: test, source code, docs, migration, infra, critical.

Matching is done on **path segments**, never raw substrings: the old rule
``"core" in path`` flagged ``ml/score.py`` and ``docs/encore.md`` as critical,
and ``"auth" in path`` flagged ``docs/author.md``.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

DEFAULT_CRITICAL = ("auth", "payment", "payments", "billing", "security", "core")

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".kt", ".rb", ".rs", ".cs", ".php",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".sh", ".vue", ".svelte",
}
DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}

MIGRATION_RE = re.compile(r"(^|/)(migrations?|alembic)(/|$)|\.sql$", re.I)
INFRA_RE = re.compile(
    r"(^|/)(dockerfile|docker-compose[^/]*|\.env[^/]*|[^/]*\.ya?ml|terraform/|helm/|k8s/)",
    re.I,
)
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/"        # test directories
    r"|(^|/)test_[^/]*$"                   # test_foo.py
    r"|_test\.(py|go)$"                    # foo_test.py, foo_test.go
    r"|\.(test|spec)\.[jt]sx?$"            # foo.test.ts, foo.spec.js
    r"|(^|/)conftest\.py$",
    re.I,
)


def _parts(path: str) -> list[str]:
    return [p.lower() for p in PurePosixPath(path.replace("\\", "/")).parts]


def is_test(path: str) -> bool:
    return bool(TEST_RE.search(path.replace("\\", "/")))


def is_source(path: str) -> bool:
    """Application code that should normally come with tests (not docs/config/tests)."""
    return PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS and not is_test(path)


def is_migration(path: str) -> bool:
    return bool(MIGRATION_RE.search(path))


def is_infra(path: str) -> bool:
    return bool(INFRA_RE.search(path))


def is_critical(path: str, critical: tuple[str, ...] = DEFAULT_CRITICAL) -> bool:
    """True if a directory segment or the file's stem names a critical area.

    Entries containing ``/`` are path prefixes (``src/payments/``); others are
    segment names (``auth`` matches ``auth/login.py`` and ``api/auth.py``,
    not ``docs/author.md``). Case-insensitive.
    """
    norm = path.replace("\\", "/").lower()
    parts = _parts(path)
    names = set(parts[:-1]) | {PurePosixPath(norm).stem}
    for entry in critical:
        e = entry.lower().strip()
        if not e:
            continue
        if "/" in e:
            if norm.startswith(e.rstrip("/") + "/"):
                return True
        elif e in names:
            return True
    return False
