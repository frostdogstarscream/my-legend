"""Request-scoped metadata attached to every provider attempt."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_USAGE_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("story_llm_usage_context", default={})


def current_usage_context() -> dict[str, Any]:
    return dict(_USAGE_CONTEXT.get())


@contextmanager
def usage_scope(**values: Any) -> Iterator[None]:
    merged = current_usage_context()
    merged.update({key: value for key, value in values.items() if value is not None})
    token = _USAGE_CONTEXT.set(merged)
    try:
        yield
    finally:
        _USAGE_CONTEXT.reset(token)
