"""Minimal live structured-output probe for the configured model tiers.

Run with DRAMA_LLM_API_KEY in the process environment. Never uses a Mock provider.
"""
from __future__ import annotations

import json
import time

from pydantic import BaseModel

from drama_engine.core.settings import get_settings
from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.llm.router import resolve_spec


class ProbeResult(BaseModel):
    ok: bool
    code: str


def main() -> int:
    provider = build_provider_from_env()
    settings = get_settings()
    roles = [
        ("FAST", "topic_select"),
        ("BALANCED", "episode_writer"),
        ("STRONG", "outline"),
        ("LONG", "repair_compliance"),
    ]
    failed = False
    for tier, role in roles:
        spec = resolve_spec(role, max_tokens=128, temperature=0)
        start = time.monotonic()
        try:
            result = provider.complete_structured(
                spec, "只返回符合 Schema 的 JSON。", "返回 ok=true, code='probe'。", ProbeResult
            )
            success = result.ok and result.code == "probe"
            failed |= not success
            print(json.dumps({
                "tier": tier, "model": spec.model, "success": success,
                "latency_ms": round((time.monotonic() - start) * 1000),
            }, ensure_ascii=False))
        except Exception as exc:
            failed = True
            print(json.dumps({
                "tier": tier, "model": spec.model, "success": False,
                "error_type": type(exc).__name__, "error": str(exc)[:300],
            }, ensure_ascii=False))
    alt_spec = resolve_spec("outline", max_tokens=128, temperature=0)
    alt_spec.model = settings.llm_strong_alt_model
    start = time.monotonic()
    try:
        result = provider.complete_structured(
            alt_spec, "只返回符合 Schema 的 JSON。", "返回 ok=true, code='probe'。", ProbeResult
        )
        success = result.ok and result.code == "probe"
        failed |= not success
        print(json.dumps({
            "tier": "STRONG_ALT", "model": alt_spec.model, "success": success,
            "latency_ms": round((time.monotonic() - start) * 1000),
        }, ensure_ascii=False))
    except Exception as exc:
        failed = True
        print(json.dumps({
            "tier": "STRONG_ALT", "model": alt_spec.model, "success": False,
            "error_type": type(exc).__name__, "error": str(exc)[:300],
        }, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
