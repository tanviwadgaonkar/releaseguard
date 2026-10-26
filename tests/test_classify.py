"""Path classification: the rules every factor depends on."""
import pytest

from app.classify import is_critical, is_source, is_test
from app.models import AnalyzeRequest, FileChange
from app.scoring import score_release


@pytest.mark.parametrize("path", [
    "ml/score.py",          # contains "core"
    "docs/author.md",       # contains "auth"
    "docs/repayment-faq.md",  # contains "payment"
    "src/encore/player.py",
])
def test_substrings_are_not_critical(path):
    assert not is_critical(path)


@pytest.mark.parametrize("path", [
    "auth/login.py", "api/auth.py", "src/payments/checkout.py", "billing/invoice.py",
    "Security/keys.py", "core/settings.py",
])
def test_critical_segments_and_stems(path):
    assert is_critical(path)


def test_custom_critical_entries_are_case_insensitive_and_support_prefixes():
    assert is_critical("notebooks/etl.py", ("Notebooks",))
    assert is_critical("src/payments/v2/charge.py", ("src/payments/",))
    assert not is_critical("lib/src/payments/x.py", ("src/payments/",))  # prefix, not substring


@pytest.mark.parametrize("path", [
    "tests/test_api.py", "test_utils.py", "pkg/foo_test.py", "server/handler_test.go",
    "web/cart.spec.ts", "web/cart.test.jsx", "web/__tests__/cart.js", "tests/conftest.py",
])
def test_test_files(path):
    assert is_test(path) and not is_source(path)


@pytest.mark.parametrize("path,source", [
    ("service/api.py", True), ("web/cart.ts", True), ("README.md", False),
    ("docs/setup.md", False), ("docker-compose.yml", False), ("assets/logo.png", False),
    ("contest/solution.py", True),  # "contest" is not a test directory
])
def test_source_detection(path, source):
    assert is_source(path) is source


def test_docs_only_change_does_not_claim_missing_tests():
    report = score_release(AnalyzeRequest(files=[FileChange(path="README.md", additions=12, deletions=2)]))
    assert report.score == 0
    assert not any(f.triggered for f in report.factors)


def test_spec_files_count_as_tests():
    req = AnalyzeRequest(files=[FileChange(path="web/cart.ts", additions=20),
                                FileChange(path="web/cart.spec.ts", additions=30)])
    missing = next(f for f in score_release(req).factors if f.name == "tests_missing")
    assert not missing.triggered
