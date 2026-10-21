from __future__ import annotations

import copy
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from lingban_pipeline import adapters
from lingban_pipeline.adapters import QianwenTTS, StructuredTextModel
from lingban_pipeline.graph import Pipeline
from lingban_pipeline.io import load
from lingban_pipeline.registry import CapabilityError


def test_flash_does_not_submit_instruct_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIO_KEY", "test-value")
    sent, endpoints = [], []
    events = iter([{"type": "session.created", "session": {"id": "test-only"}}, {"type": "session.updated"}, {"type": "response.audio.delta", "delta": "AAABAAIAAwA="}, {"type": "session.finished"}])
    class Socket:
        def send(self, value): sent.append(json.loads(value))
        def recv(self): return json.dumps(next(events))
        def close(self): pass
    def connect(url, **kwargs):
        endpoints.append(url)
        return Socket()
    monkeypatch.setattr(adapters.websocket, "create_connection", connect)
    payload = {"voice": "Cherry", "text": "请听我说。", "instructions": "情绪由克制到急切。"}
    result = QianwenTTS({"credential_env": "TEST_AUDIO_KEY"}).generate(payload, tmp_path / "out.wav")
    assert endpoints[0].endswith("?model=qwen3-tts-flash-realtime")
    assert "instructions" not in sent[0]["session"] and "optimize_instructions" not in sent[0]["session"]
    assert next(e for e in sent if e["type"] == "input_text_buffer.append")["text"] == payload["text"]
    assert result["capability_gaps"][0]["code"] == "performance_directions_not_submitted"
    assert sf.info(tmp_path / "out.wav").frames == 4


@pytest.mark.parametrize("config,payload", [
    ({"controls": ["instructions"]}, {"required_controls": []}),
    ({}, {"required_controls": ["instructions"]}),
])
def test_flash_cannot_be_forced_to_accept_instructions(config, payload):
    with pytest.raises(CapabilityError, match="unsupported|not supported"):
        QianwenTTS(config).request_plan({"voice": "Cherry", "text": "台词", "instructions": "表演要求", **payload})


def test_design_only_change_reuses_flash_raw_and_records_gap(tmp_path, make_project):
    script, cfg, base = make_project(count=1, effects=False)
    script["events"][0]["media"] = {}
    cfg["tts"] = {"adapter": "qianwen_tts", "model": "qwen3-tts-flash-realtime", "controls": []}
    cfg["role_voices"] = {"role0": "Cherry"}
    class Flash:
        external = True
        calls = 0
        def generate(self, payload, destination):
            self.calls += 1
            plan = QianwenTTS(cfg["tts"]).request_plan(payload)
            destination.parent.mkdir(parents=True, exist_ok=True)
            sf.write(destination, np.full(4800, .02), 48000)
            return {"model": plan["model"], "actual_parameters": plan["session"], "capability_gaps": plan["capability_gaps"]}
    fake = Flash()
    p = Pipeline(tmp_path / "production", audio_adapters={"qianwen_tts": fake})
    try:
        p.run("job", script, cfg, base)
        first = p.status("job")["preview"]
        changed = copy.deepcopy(script["events"][0]["performance"])
        changed["release"] = "更加急切，短促地结束。"
        p.resume("job", {"decision": "changes", "bundle_sha256": first["bundle"]["bundle_sha256"], "changes": {"events": [{"block_id": "line0", "performance": changed}]}})
        state = p.graph.get_state(p.invocation("job")).values
        assert fake.calls == 1
        assert state["preview"]["checks"]["capability_gaps"][0]["block_id"] == "line0"
        assert "更加急切" in state["tasks"][0]["payload"]["instructions"]
        prompts = load(p.root / "drafts/r2/prompts.json")
        assert "instructions" not in prompts[0]["actual_generation"]["actual_parameters"]
        assert prompts[0]["actual_generation"]["capability_gaps"]
    finally:
        p.close()


@pytest.mark.parametrize("thinking", ["enabled", "disabled"])
def test_deepseek_request_parameters_and_safe_record(monkeypatch, thinking):
    monkeypatch.setenv("TEST_TEXT_KEY", "test-value")
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        return httpx.Response(200, json={"id": "mock-only", "usage": {"total_tokens": 20}, "choices": [{"message": {"content": '{"result": "ok"}', "reasoning_content": "test reasoning must not be exported"}}]})
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    model = StructuredTextModel({"model": "deepseek-flash", "url": "https://api.deepseek.com/chat/completions", "credential_env": "TEST_TEXT_KEY", "thinking": thinking, "max_tokens": 1024, "temperature": .4})
    assert model.generate("Return JSON", lambda x: x)["result"] == "ok"
    assert sent[0]["thinking"] == {"type": thinking}
    assert ("temperature" in sent[0]) == (thinking == "disabled")
    assert sent[0]["max_tokens"] == 1024
    assert model.last_request_record["usage"]["total_tokens"] == 20
    assert "reasoning_content" not in json.dumps(model.last_request_record)
    assert "test-value" not in json.dumps(model.last_request_record)
