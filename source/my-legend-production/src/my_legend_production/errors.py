"""Provider failure classification shared by the story adapter and the API.

The story engine reports a task status plus a provider message such as
"HTTP 401". Every layer used to collapse that into "unknown result", which hid
the real cause. This module keeps one small public vocabulary so the API and the
UI can say what actually happened while still preserving unknown usage counts.
"""
from __future__ import annotations

import re

AUTH_FAILED = "AUTH_FAILED"
RATE_LIMITED = "RATE_LIMITED"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
CONTENT_REJECTED = "CONTENT_REJECTED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_ERROR = "PROVIDER_ERROR"

PUBLIC_MESSAGES = {
    AUTH_FAILED: "模型服务鉴权失败。请检查模型密钥或服务账号，修复后重新制作。",
    RATE_LIMITED: "模型服务限流，稍后会自动重试。",
    PROVIDER_TIMEOUT: "模型服务响应超时，可以稍后重试。",
    CONTENT_REJECTED: "内容未通过模型安全检查，请调整剧情或表述后重试。",
    PROVIDER_UNAVAILABLE: "模型服务暂时不可用，可以稍后重试。",
    PROVIDER_ERROR: "模型调用失败，请查看制作记录后重试。",
}

_RETRYABLE = {RATE_LIMITED, PROVIDER_TIMEOUT, PROVIDER_UNAVAILABLE}
_SAFETY_TOKENS = ("content_policy", "content policy", "safety", "moderation", "内容安全", "违规")
_TIMEOUT_TOKENS = ("timeout", "timed out", "time out", "超时")
_STATUS = re.compile(r"(?<!\d)([1-5]\d{2})(?!\d)")


def _status(value):
    """Read a three digit HTTP status from an int or a message like 'HTTP 401'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 100 <= value <= 599 else None
    if isinstance(value, str):
        match = _STATUS.search(value)
        if match:
            return int(match.group(1))
    return None


def _record_status(result):
    usage = (result or {}).get("usage") or {}
    for record in usage.get("call_records") or []:
        if not isinstance(record, dict):
            continue
        for key in ("http_status", "status_code"):
            status = _status(record.get(key))
            if status:
                return status
        status = _status(record.get("error_code")) or _status(record.get("error"))
        if status and status >= 400:
            return status
    return None


def _provider_text(result):
    payload = result or {}
    if payload.get("error_message"):
        return str(payload["error_message"])
    candidate = payload.get("error_code") or ""
    return str(candidate)


def classify_provider_error(result=None, *, status_code=None, message=None):
    """Map an engine task or a raw HTTP failure onto the public vocabulary."""
    payload = result or {}
    status = status_code or _status(payload.get("error_message")) or _record_status(payload)
    text = (message or payload.get("error_message") or _provider_text(payload) or "").strip()
    lowered = text.lower()

    if status in (401, 403):
        code = AUTH_FAILED
    elif status == 429:
        code = RATE_LIMITED
    elif status in (408, 504) or any(token in lowered for token in _TIMEOUT_TOKENS):
        code = PROVIDER_TIMEOUT
    elif status and 500 <= status < 600:
        code = PROVIDER_UNAVAILABLE
    elif any(token in lowered for token in _SAFETY_TOKENS):
        code = CONTENT_REJECTED
    else:
        code = PROVIDER_ERROR

    return {
        "code": code,
        "message": PUBLIC_MESSAGES[code],
        "retryable": code in _RETRYABLE,
        "http_status": status,
        # Truncated upstream text only; callers must not persist prompts or headers.
        "provider_message": text[:500] or None,
    }


def provider_failure(result=None, *, status_code=None, message=None):
    """Return a detail only when the input actually carries a provider signal.

    Policy failures such as STORY_REVISION_UNCHANGED are not provider errors and
    must stay without a detail, otherwise the UI would blame the model.
    """
    detail = classify_provider_error(result, status_code=status_code, message=message)
    signalled = detail["http_status"] is not None or detail["code"] != PROVIDER_ERROR
    return detail if signalled else None


def public_detail(value):
    """Keep only the public shape when a detail crosses an API boundary."""
    if not isinstance(value, dict) or not value:
        return {}
    code = value.get("code")
    if code not in PUBLIC_MESSAGES:
        return {}
    status = _status(value.get("http_status"))
    return {
        "code": code,
        "message": PUBLIC_MESSAGES[code],
        "retryable": bool(value.get("retryable", code in _RETRYABLE)),
        "http_status": status,
        "provider_message": (str(value.get("provider_message"))[:500] if value.get("provider_message") else None),
    }
