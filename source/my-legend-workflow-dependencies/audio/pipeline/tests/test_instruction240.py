import json

import httpx
import pytest

from lingban_pipeline import adapters
from lingban_pipeline.adapters import QianwenAudioTTS, StructuredTextModel
from lingban_pipeline.budget import BudgetExceeded, CallBudget
from lingban_pipeline.instructions import policy, validate_instruction, weighted_length
from lingban_pipeline.performance import design, FIELDS
from lingban_pipeline.registry import CapabilityError


def test_default_240_is_configurable_and_han_range_is_advice():
    assert policy({}) == {"max_weight": 240, "max_corrections": 2, "recommended_han_range": [50, 100]}
    assert policy({}, {"instruction_weight_override": 300})["max_weight"] == 300
    assert policy({"instruction_policy": {"max_weight": 120}})["max_weight"] == 120
    adapter = QianwenAudioTTS({"model": "qwen-audio-3.1-tts-flash"})
    payload = {"text": "台词。", "voice": "test"}
    # Both under 50 Han and over 100 Han are allowed, if within the active weight budget.
    assert adapter.request_plan(payload | {"instructions": "自然交流。"})["input"]["instruction"]
    assert adapter.request_plan(payload | {"instructions": "平" * 110})["input"]["instruction"] == "平" * 110
    assert adapter.request_plan(payload | {"instructions": "平" * 120})["input"]["instruction"]
    with pytest.raises(CapabilityError) as caught:
        adapter.request_plan(payload | {"instructions": "平" * 120 + "A"})
    assert caught.value.diagnostics["weight"] == 241
    assert caught.value.diagnostics["limit"] == 240


def test_prompt_says_50_to_100_but_accepts_complete_short_direction(make_project):
    script, config, _ = make_project(count=1, effects=False)
    config["text_model"] = {"enabled": True}
    class Model:
        def generate(self, prompt, validate):
            source = json.loads(prompt)
            assert "50到100个汉字" in source["instruction_length_guidance"]
            assert "只作建议" in source["instruction_length_guidance"]
            assert source["instruction_policy"]["max_weight"] == 240
            block = next(iter(source["lines"]))
            return validate({"lines": {block: {**{k: "自然" for k in FIELDS}, "emphasis": [], "pauses": [], "instructions": "自然交流，清楚收尾。"}}})
    result = design(script, config, Model())
    assert result["lines"]["line0"]["performance"]["instructions"] == "自然交流，清楚收尾。"


def test_over_240_is_rewritten_not_sliced(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_TEXT_KEY", "not-real")
    sent = []
    def handler(request):
        body = json.loads(request.content);sent.append(body)
        text = "压" * 121 if len(sent) == 1 else "先试探，转为明确请求，最后轻收尾音。"
        return httpx.Response(200, json={"id": "test", "choices": [{"message": {"content": json.dumps({"instruction": text})}}]})
    client = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler)))
    budget = CallBudget(tmp_path)
    model = StructuredTextModel({"model": "test", "url": "https://example.org/text", "credential_env": "TEST_TEXT_KEY"},
                                budget=lambda: budget.reserve("r1", "text", {"batch_max_by_kind": {"text": 8}}))
    result = model.generate("Preserve original spoken text.", lambda value: {"instruction": validate_instruction(value["instruction"], 240)})
    assert len(sent) == 2
    repair = sent[1]["messages"][-1]["content"]
    assert "242" in repair and "240" in repair and "remove at least 2" in repair and "压" * 121 in repair
    assert result["instruction"] == "先试探，转为明确请求，最后轻收尾音。"
    assert budget.stats("r1")["batch_by_kind"]["text"] == 2


def test_eight_text_submissions_per_revision_blocks_ninth_before_http(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_TEXT_KEY", "not-real")
    sent = []
    def handler(request):
        sent.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    client = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler)))
    budget = CallBudget(tmp_path)
    batch = ["r1"]
    model = StructuredTextModel({"model": "test", "url": "https://example.org/text", "credential_env": "TEST_TEXT_KEY"},
                                budget=lambda: budget.reserve(batch[0], "text", {"batch_max_by_kind": {"text": 8}}))
    for _ in range(8): model.generate("test", lambda value: value)
    with pytest.raises(BudgetExceeded) as caught: model.generate("test", lambda value: value)
    assert len(sent) == 8 and caught.value.diagnostics["scope"] == "text_revision"
    restarted = CallBudget(tmp_path)
    assert restarted.stats("r1")["batch_by_kind"]["text"] == 8
    batch[0] = "r2"
    model.generate("test", lambda value: value)
    assert len(sent) == 9 and restarted.stats("r2")["batch_by_kind"]["text"] == 1


def test_two_correction_limit_reports_weight_failure_without_truncation(monkeypatch):
    monkeypatch.setenv("TEST_TEXT_KEY", "not-real")
    sent = []
    def handler(request):
        sent.append(1)
        return httpx.Response(200, json={"id": "test", "choices": [{"message": {"content": json.dumps({"instruction": "压" * 121})}}]})
    client = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler)))
    model = StructuredTextModel({"model": "test", "url": "https://example.org/text", "credential_env": "TEST_TEXT_KEY"})
    with pytest.raises(CapabilityError) as caught:
        model.generate("test", lambda value: validate_instruction(value["instruction"], 240))
    assert len(sent) == 3 and caught.value.diagnostics["correction_rounds"] == 2
    assert all(r["validation"] == "instruction_weight_exceeded" and r["instruction_weight"] == 242 for r in caught.value.diagnostics["request_history"])
