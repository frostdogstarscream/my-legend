import json

import httpx
import pytest

from lingban_pipeline import adapters
from lingban_pipeline.adapters import QianwenSFX
from lingban_pipeline.registry import Registry, UnknownResult


def mock_client(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))


def test_sfx_http_media_link_is_retrieved_over_tls_without_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SFX_KEY", "test-secret")
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["input"]["text_prompt"] == "关门一次，pia仅描述物体声音"
            return httpx.Response(200, json={"request_id": "mock-sfx-id", "output": {"audio": {"url": "http://example.org/audio.wav?Signature=private"}}})
        assert request.url.scheme == "https"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"generated-media")
    mock_client(monkeypatch, handler)
    result = QianwenSFX({"credential_env": "TEST_SFX_KEY"}).generate({"prompt": "关门一次，pia仅描述物体声音"}, tmp_path / "result.wav")
    assert len(calls) == 2
    assert (tmp_path / "result.wav").read_bytes() == b"generated-media"
    assert result["retrieval"] == {"returned_url_scheme": "http", "download_scheme": "https", "http_status": 200, "attempts": 1}
    assert "private" not in json.dumps(result) and "test-secret" not in json.dumps(result)


def test_download_failure_keeps_request_id_and_blocks_resubmission(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SFX_KEY", "test-secret")
    submitted = []
    def handler(request):
        if request.method == "POST":
            submitted.append(1)
            return httpx.Response(200, json={"request_id": "mock-sfx-id", "output": {"audio": {"url": "https://example.org/audio.wav?Signature=private"}}})
        return httpx.Response(503)
    mock_client(monkeypatch, handler)
    registry = Registry(tmp_path)
    adapter = QianwenSFX({"credential_env": "TEST_SFX_KEY"})
    action = lambda: adapter.generate({"prompt": "关门"}, tmp_path / "result.wav")
    with pytest.raises(UnknownResult):
        registry.run("raw_test", True, action, lambda _: None)
    stored = registry.get("raw_test")
    assert stored["status"] == "unknown"
    details = json.loads(stored["error"])
    assert details["request_id"] == "mock-sfx-id" and details["media_http_status"] == 503
    assert "private" not in stored["error"] and "test-secret" not in stored["error"]
    with pytest.raises(UnknownResult, match="resubmission disabled"):
        registry.run("raw_test", True, action, lambda _: None)
    assert len(submitted) == 1
