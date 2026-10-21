from __future__ import annotations

import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from lingban_pipeline import adapters
from lingban_pipeline.adapters import QianwenAudioTTS
from lingban_pipeline.io import file_hash
from lingban_pipeline.registry import CapabilityError, UnknownResult


def test_qwen_audio_uses_singular_instruction_and_own_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIO_KEY", "test-value")
    audio = tmp_path / "source.wav"
    sf.write(audio, np.full(100, .02), 24000)
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == "qwen-audio-3.0-tts-flash"
            assert body["input"]["text"] == "这件事，我会处理。"
            assert body["input"]["instruction"] == "先迟疑，再坚定；重音放在‘我’。"
            assert body["input"]["voice"] == "longanhuan_v3.6"
            assert "instructions" not in body["input"] and "optimize_instructions" not in body["input"]
            return httpx.Response(200, json={"request_id": "mock-only", "output": {"audio": {"url": "http://example.org/result?signature=private"}}, "usage": {"characters": 10}})
        assert str(request.url).startswith("https://example.org/")
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=audio.read_bytes())
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    result = QianwenAudioTTS({"credential_env": "TEST_AUDIO_KEY"}).generate({"text": "这件事，我会处理。", "voice": "longanhuan_v3.6", "instructions": "先迟疑，再坚定；重音放在‘我’。", "required_controls": ["instructions"]}, tmp_path / "out.wav")
    assert len(calls) == 2 and file_hash(audio) == file_hash(tmp_path / "out.wav")
    assert result["actual_parameters"]["instruction"]
    assert not result["capability_gaps"]
    assert "signature" not in json.dumps(result) and "test-value" not in json.dumps(result)


def test_qwen_audio_rejects_word_sync_on_nonstreaming():
    with pytest.raises(CapabilityError, match="unsupported"):
        QianwenAudioTTS({}).request_plan({"text": "台词", "voice": "longanhuan_v3.6", "required_controls": ["word_timestamps"]})


def test_qwen_audio_result_unknown_after_retrieval_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIO_KEY", "test-value")
    posts = []
    def handler(request):
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, json={"output": {"audio": {"url": "https://example.org/result"}}})
        return httpx.Response(503)
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    with pytest.raises(UnknownResult, match="retrieval"):
        QianwenAudioTTS({"credential_env": "TEST_AUDIO_KEY"}).generate({"text": "台词", "voice": "longanhuan_v3.6", "instructions": "自然"}, tmp_path / "none.wav")
    assert len(posts) == 1


@pytest.mark.parametrize("config", [{"rate": 3}, {"seed": -1}, {"sample_rate": 32000}])
def test_qwen_audio_rejects_invalid_parameters(config):
    with pytest.raises(CapabilityError):
        QianwenAudioTTS(config).request_plan({"text": "台词", "voice": "longanhuan_v3.6"})


def test_live_config_does_not_replace_explicit_local_media(make_project):
    from lingban_pipeline.performance import create_tasks, design
    script, cfg, base = make_project(count=1, effects=False)
    cfg["tts"] = {"adapter": "qianwen_audio_tts", "model": "qwen-audio-3.0-tts-flash"}
    tasks = create_tasks(script, design(script, cfg), cfg, base)
    assert tasks[0]["adapter"] == "local"
