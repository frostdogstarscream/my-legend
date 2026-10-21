"""One independent structured-model request, without hidden retries or session context."""
import json
import httpx
from urllib.parse import urlsplit

from .repository import ProductionError


class VisualModel:
    def __init__(self, base_url, key, model, *, transport=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ProductionError("VISUAL_MODEL_URL_INVALID")
        self.model = model
        self.client = httpx.Client(base_url=base_url.rstrip("/") + "/", headers={"Authorization": "Bearer " + key},
                                  trust_env=False, follow_redirects=False, transport=transport)

    def complete(self, system, payload, schema, timeout):
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"input": payload, "output_schema": schema}, ensure_ascii=False)}],
                "max_tokens": 12000, "stream": False, "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"}}
        try:
            response = self.client.post("chat/completions", json=body, timeout=min(180, max(1, timeout)))
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError()
            return {"content": content, "requested_model": self.model, "returned_model": result.get("model"),
                    "usage": {k: result.get("usage", {}).get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ProductionError("VISUAL_MODEL_RESPONSE_UNCERTAIN") from None
