import json

import httpx
import pytest

from lingban_pipeline.adapters import checked_response
from lingban_pipeline.registry import CapabilityError, Registry


def test_rejection_metadata_never_keeps_provider_message_or_headers(tmp_path):
    response = httpx.Response(401, json={"error": {"code": "invalid_api_key", "message": "Secret-bearing provider message"}}, headers={"Authorization": "Secret-bearing header"})
    registry = Registry(tmp_path)
    with pytest.raises(CapabilityError) as caught:
        registry.run("probe", True, lambda: checked_response(response), lambda _: None)
    assert caught.value.diagnostics == {"http_status": 401, "provider_code": "invalid_api_key", "category": "authentication"}
    stored = registry.get("probe")
    assert json.loads(stored["error"]) == caught.value.diagnostics
    assert "Secret-bearing" not in stored["error"]


def test_credential_shaped_error_code_is_not_recorded():
    response = httpx.Response(400, json={"code": "sk-" + "fake-sensitive-code", "message": "Not retained"})
    with pytest.raises(CapabilityError) as caught:
        checked_response(response)
    assert "provider_code" not in caught.value.diagnostics
