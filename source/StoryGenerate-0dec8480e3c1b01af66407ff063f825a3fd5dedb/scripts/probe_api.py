"""Quick connectivity probe for the real model."""
import os, sys
sys.path.insert(0, "4-引擎代码")

if not os.environ.get("DRAMA_LLM_API_KEY"):
    raise SystemExit("Set DRAMA_LLM_API_KEY in the process environment before running this script.")
os.environ.setdefault("DRAMA_LLM_MODEL", "qwen/qwen3.7-max")

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.llm.router import resolve_spec
from pydantic import BaseModel

class Probe(BaseModel):
    ok: bool
    text: str

provider = build_provider_from_env()
spec = resolve_spec("outline", max_tokens=64, temperature=0)
print(f"Endpoint: {provider._base_url if hasattr(provider, '_base_url') else 'default'}")
print(f"Model: {spec.model}  Tier: {spec.tier}")
try:
    result = provider.complete_structured(
        spec, "只返回符合Schema的JSON。", '{"ok": true, "text": "hello"}', Probe
    )
    print(f"OK={result.ok}  text={result.text}")
    print("SUCCESS: API connectivity confirmed")
except Exception as exc:
    print(f"FAILED: {type(exc).__name__}: {exc}")