"""Provider failures must be classified, not collapsed into 'unknown result'."""
import pytest

from my_legend_production.errors import (
    AUTH_FAILED,
    CONTENT_REJECTED,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAVAILABLE,
    RATE_LIMITED,
    classify_provider_error,
    provider_failure,
    public_detail,
)

PLANNING_TASK = {
    "task_id": "8e94ae074df744fe96f8a908a60a64ea",
    "status": "FAILED",
    "error_code": "WORKER_ERROR",
    "error_message": "HTTP 401",
    "usage": {
        "calls": 1,
        "unknown_usage_calls": 1,
        "call_records": [
            {"node": "outline", "status": "error", "error_code": "HTTP_401", "error": "HTTP 401"}
        ],
    },
}


def test_auth_failure_is_named_even_with_unknown_usage():
    detail = provider_failure(PLANNING_TASK)
    assert detail["code"] == AUTH_FAILED
    assert detail["http_status"] == 401
    assert detail["retryable"] is False
    assert "鉴权" in detail["message"]


def test_status_is_read_from_call_records():
    task = {"status": "FAILED", "error_code": "WORKER_ERROR", "usage": {"call_records": [{"error_code": "HTTP_403"}]}}
    detail = provider_failure(task)
    assert detail["code"] == AUTH_FAILED
    assert detail["http_status"] == 403


@pytest.mark.parametrize(
    "message,expected,retryable",
    [
        ("HTTP 429", RATE_LIMITED, True),
        ("HTTP 503", PROVIDER_UNAVAILABLE, True),
        ("HTTP 504", PROVIDER_TIMEOUT, True),
        ("request timed out", PROVIDER_TIMEOUT, True),
    ],
)
def test_transient_failures_are_retryable(message, expected, retryable):
    detail = provider_failure({"error_message": message})
    assert detail["code"] == expected
    assert detail["retryable"] is retryable


def test_content_policy_is_not_retryable():
    detail = provider_failure({"error_message": "blocked by content_policy"})
    assert detail["code"] == CONTENT_REJECTED
    assert detail["retryable"] is False


def test_unknown_provider_failure_still_classifies():
    detail = classify_provider_error({"error_code": "WORKER_ERROR"})
    assert detail["code"] == "PROVIDER_ERROR"
    assert detail["http_status"] is None


def test_policy_errors_have_no_provider_detail():
    assert provider_failure({"error_code": "STORY_REVISION_UNCHANGED"}) is None
    assert provider_failure({"error_code": "CALL_BUDGET_EXCEEDED"}) is None


def test_public_detail_drops_unknown_shapes():
    assert public_detail({"code": "STORY_REVISION_UNCHANGED"}) == {}
    assert public_detail(None) == {}
    assert public_detail({"code": AUTH_FAILED, "http_status": "401", "provider_message": "x" * 900})["http_status"] == 401
    assert len(public_detail({"code": AUTH_FAILED, "provider_message": "x" * 900})["provider_message"]) == 500
