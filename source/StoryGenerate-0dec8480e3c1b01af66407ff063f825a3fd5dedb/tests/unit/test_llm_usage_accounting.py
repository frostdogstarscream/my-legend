from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel

from drama_engine.contracts import LLMSpec
from drama_engine.llm.accounting import VersionedModelPrice, calculate_supplier_cost
from drama_engine.llm.openai_compat import OpenAICompatProvider
from drama_engine.llm.usage_context import usage_scope


def _price(**overrides):
    values = {
        "model_id": "writer", "price_revision": "2026-10", "effective_from": datetime.now(timezone.utc),
        "input_per_million": Decimal("1"), "output_per_million": Decimal("2"),
    }
    values.update(overrides)
    return VersionedModelPrice(**values)


def test_reasoning_tokens_are_not_double_counted_when_included_in_output():
    item = calculate_supplier_cost({
        "usage_source": "reported", "input_tokens": 1000, "output_tokens": 2000,
        "reasoning_tokens": 500, "reasoning_included_in_output": True,
    }, _price())
    assert item.confirmed_amount == Decimal("0.005")


def test_unavailable_usage_is_unknown_not_zero_cost():
    item = calculate_supplier_cost({"usage_source": "unavailable"}, _price())
    assert item.unknown_call_count == 1
    assert item.confirmed_amount == 0
    assert item.warnings


def test_provider_records_reported_usage_and_request_context(monkeypatch):
    class Response:
        status_code = 200
        headers = {"x-request-id": "provider-1"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "completion-1", "model": "writer-actual",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 101, "completion_tokens": 29,
                    "prompt_tokens_details": {"cached_tokens": 7},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            }

    monkeypatch.setattr("drama_engine.llm.openai_compat.httpx.post", lambda *args, **kwargs: Response())
    provider = OpenAICompatProvider("not-a-real-key", base_url="https://example.invalid", max_retries=0)
    with usage_scope(request_id="req-1", job_id="job-1", stage="generation"):
        provider.complete(LLMSpec(role="episode_writer", model="writer"), "system", "user")
    call = provider.call_history[0]
    assert call["usage_source"] == "reported"
    assert call["request_id"] == "req-1"
    assert call["job_id"] == "job-1"
    assert call["cache_read_tokens"] == 7
    assert call["reasoning_tokens"] == 5
    assert call["reasoning_included_in_output"] is True


def test_structured_json_repair_has_distinct_attempt_kind(monkeypatch):
    class Payload(BaseModel):
        value: int

    responses = iter(["not-json", '{"value": 7}'])

    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "writer", "choices": [{"message": {"content": next(responses)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    monkeypatch.setattr("drama_engine.llm.openai_compat.httpx.post", lambda *args, **kwargs: Response())
    provider = OpenAICompatProvider("not-a-real-key", base_url="https://example.invalid", max_retries=0)
    with usage_scope(request_id="req-2", attempt_kind="first_draft"):
        result = provider.complete_structured(
            LLMSpec(role="episode_writer", model="writer"), "system", "user", Payload,
        )
    assert result.value == 7
    assert [call.get("attempt_kind") for call in provider.call_history] == [
        "first_draft", "json_schema_repair",
    ]
