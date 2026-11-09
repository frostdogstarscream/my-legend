"""One independent structured-model request, without hidden retries or session context."""
import json
import re
import httpx
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .repository import ProductionError


class VisualModel:
    def __init__(self, base_url, key, model, *, transport=None, max_tokens=12000, read_timeout=180, thinking=False):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ProductionError("VISUAL_MODEL_URL_INVALID")
        self.model = model
        self.is_opencode_go = base_url.rstrip('/') == 'https://opencode.ai/inference/go/openai/v1'
        self.max_tokens, self.read_timeout, self.thinking = max_tokens, read_timeout, thinking
        self.client = httpx.Client(base_url=base_url.rstrip("/") + "/", headers={"Authorization": "Bearer " + key},
                                  trust_env=False, follow_redirects=False, transport=transport)
        if self.is_opencode_go:
            self.client.headers['User-Agent'] = 'my-legend-visual-director/1.0'
            self.set_session(str(uuid4()))

    def set_session(self, session_id):
        if self.is_opencode_go:
            self.client.headers['x-opencode-session'] = str(UUID(str(session_id)))

    def complete(self, system, payload, schema, timeout):
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"input": payload, "output_schema": schema}, ensure_ascii=False)}],
                "max_tokens": self.max_tokens, "stream": False, "response_format": {"type": "json_object"},
                "thinking": {"type": "enabled" if self.thinking else "disabled"}}
        try:
            # Complete non-streamed plans took >150s in production. Allow a long
            # read within the caller's remaining task budget, without extending
            # connection setup or introducing retries.
            remaining = max(1, timeout)
            request_timeout = httpx.Timeout(connect=min(15, remaining), read=min(self.read_timeout, remaining),
                                            write=min(30, remaining), pool=min(15, remaining))
            response = self.client.post("chat/completions", json=body, timeout=request_timeout)
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError()
            finish = result['choices'][0].get('finish_reason')
            return {"content": content, "requested_model": self.model, "returned_model": result.get("model"),
                    "finish_reason": finish if finish in ('stop','length','content_filter','tool_calls') else 'unknown',
                    "request_settings": {"max_tokens": self.max_tokens, "thinking": body['thinking']['type'],
                                         "read_timeout_seconds": request_timeout.read},
                    "usage": {k: result.get("usage", {}).get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            error = ProductionError("VISUAL_MODEL_RESPONSE_UNCERTAIN")
            # Keep bounded structured error fields, never the full response or credentials.
            error.diagnostic = {'exception_type': type(exc).__name__,
                                'http_status': exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None}
            if isinstance(exc,httpx.HTTPStatusError):
                def safe(value):
                    if not isinstance(value,(str,int,float)):return None
                    value=str(value)
                    for private in (self.client.headers.get('Authorization','').removeprefix('Bearer '),system,body['messages'][1]['content']):
                        if private:value=value.replace(private,'[REDACTED]')
                    value=re.sub(r'(?i)bearer\s+\S+|(?:oc_)?sk-[A-Za-z0-9_-]+|oc_sk_[A-Za-z0-9_-]+','[REDACTED]',value)
                    value=re.sub(r'https?://\S+','[URL]',value)
                    return value[:600]
                try:
                    detail=exc.response.json()
                    problem=detail.get('error',{}) if isinstance(detail,dict) else {}
                    metadata=detail.get('metadata',{}) if isinstance(detail,dict) else {}
                    if isinstance(problem,dict):
                        error.diagnostic['provider_error']={k:safe(problem[k]) for k in ('type','code','message') if k in problem and safe(problem[k]) is not None}
                    if isinstance(metadata,dict) and 'limitName' in metadata:
                        error.diagnostic['limit_name']=safe(metadata['limitName'])
                except (ValueError,TypeError):pass
                for name in ('retry-after','x-request-id'):
                    if name in exc.response.headers:error.diagnostic[name]=safe(exc.response.headers[name])
            raise error from None
