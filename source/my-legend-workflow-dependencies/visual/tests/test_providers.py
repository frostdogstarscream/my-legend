import http.client
import io
import json
import unittest
from unittest.mock import patch

from app.config import Settings
from app.providers import ProviderError, chat


def _response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    return io.BytesIO(body)


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(demo_mode=False, provider="deepseek", model="deepseek-chat", api_key="test-key", base_url="https://example.com/v1")
        self.payload = {"choices": [{"message": {"content": "ok"}}]}

    def test_transient_connection_drop_is_retried(self):
        attempts = {"count": 0}

        def flaky(request, timeout):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise http.client.IncompleteRead(b"")
            return _FakeResponse(json.dumps(self.payload).encode("utf-8"))

        with patch("app.providers.urllib.request.urlopen", flaky), patch("app.providers.time.sleep"):
            self.assertEqual(chat("s", "u", settings=self.settings), "ok")
        self.assertEqual(attempts["count"], 3)

    def test_connection_failure_raises_after_max_attempts(self):
        with patch("app.providers.urllib.request.urlopen", side_effect=TimeoutError("timed out")), patch("app.providers.time.sleep"):
            with self.assertRaises(ProviderError):
                chat("s", "u", settings=self.settings)

    def test_bad_request_is_not_retried(self):
        attempts = {"count": 0}

        def bad_request(request, timeout):
            attempts["count"] += 1
            raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError("https://example.com", 400, "Bad Request", {}, io.BytesIO(b'{"error":"bad"}'))

        with patch("app.providers.urllib.request.urlopen", bad_request), patch("app.providers.time.sleep"):
            with self.assertRaises(ProviderError):
                chat("s", "u", settings=self.settings)
        self.assertEqual(attempts["count"], 1)


if __name__ == "__main__":
    unittest.main()
