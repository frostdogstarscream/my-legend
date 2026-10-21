"""LLM 基础设施：规格对象复用 + 结构化输出兜底。

`LLMSpec` / `LLMResult` 定义在 contracts 层（因为它们是 Protocol 签名的一部分），
这里只做转发并提供共享基类，避免同一概念出现两份定义。
"""
from __future__ import annotations

import json
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..contracts import LLMProvider, LLMResult, LLMSpec, TokenUsage  # noqa: F401  (re-export)
from .usage_context import usage_scope

T = TypeVar("T", bound=BaseModel)

# 结构化输出的统一系统指令。所有供应商共用，便于横向对比。
SCHEMA_INSTRUCTION = """你是一个结构化数据生成器。
只输出一个 JSON 对象，不要输出任何解释、前言、Markdown 代码围栏或注释。
JSON 必须严格符合以下 JSON Schema：
{schema}

若某字段在给定信息中无法确定，请依据 Schema 中的 description 做出最合理的专业推断，不要留空、不要填 null（除非 Schema 明确允许）。"""


class StructuredOutputError(RuntimeError):
    def __init__(self, raw: str, errors: list[str]) -> None:
        super().__init__("结构化输出无法解析: " + "; ".join(errors))
        self.raw = raw
        self.errors = errors


class BaseLLMProvider:
    """所有适配器的共享逻辑：结构化输出的解析与一次带错重问。"""

    name = "base"

    # Phase 4: telemetry reference, injected by Runtime
    _telemetry: Any = None

    def _trace_llm_call(self, spec: LLMSpec, result: "LLMResult", latency_ms: int) -> None:
        """记录一次 LLM 调用到 Telemetry（如果已注入）。"""
        if self._telemetry is None:
            return
        self._telemetry.record(TokenUsage(
            node=spec.role, model=spec.model,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            latency_ms=latency_ms, cached=False,
        ))

    def complete(self, spec: LLMSpec, system: str, user: str) -> LLMResult:
        raise NotImplementedError

    def complete_structured(
        self, spec: LLMSpec, system: str, user: str, schema: type[T]
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
        instruction = SCHEMA_INSTRUCTION.format(schema=schema_json)
        full_system = f"{system}\n\n{instruction}" if system else instruction

        # Phase 4: trace every LLM call
        t0 = time.monotonic()
        result = self.complete(spec, full_system, user)
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._trace_llm_call(spec, result, latency_ms)
        parsed, errors = _try_parse(result.text, schema)
        if parsed is not None:
            return parsed

        # 带错重问一次。这是最便宜的修复手段 —— 比让校验节点发现再回退便宜得多。
        retry_user = (
            f"{user}\n\n---\n上一次你的输出无法通过校验，错误如下：\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\n请重新输出完整的 JSON 对象。"
        )
        with usage_scope(attempt_kind="json_schema_repair"):
            result2 = self.complete(spec, full_system, retry_user)
        self._trace_llm_call(spec, result2, result2.latency_ms)
        parsed2, errors2 = _try_parse(result2.text, schema)
        if parsed2 is not None:
            return parsed2
        raise StructuredOutputError(result2.text, errors2)


def _try_parse(text: str, schema: type[T]) -> tuple[T | None, list[str]]:
    cleaned = _strip_fences(text)
    try:
        return schema.model_validate_json(cleaned), []
    except ValidationError as exc:
        return None, [f"{e['loc']}: {e['msg']}" for e in exc.errors()[:10]]
    except (json.JSONDecodeError, ValueError) as exc:
        return None, [f"JSON 解析失败: {exc}"]


def _strip_fences(text: str) -> str:
    """剥掉模型习惯性添加的 ```json 围栏与前后解说。"""
    s = text.strip()
    if "```" in s:
        start = s.find("```")
        body = s[start + 3 :]
        if body.lstrip().lower().startswith("json"):
            body = body.lstrip()[4:]
        end = body.find("```")
        if end != -1:
            body = body[:end]
        s = body.strip()
    # 兜底：截取第一个 { 到最后一个 }
    first, last = s.find("{"), s.rfind("}")
    if first != -1 and last != -1 and last > first:
        s = s[first : last + 1]
    return s


def estimate_tokens(text: str) -> int:
    """粗估 token。中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token。

    仅用于 mock 与预算预估；生产环境应使用供应商返回的真实用量。
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def timed(fn, *args, **kwargs) -> tuple[Any, int]:
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, int((time.perf_counter() - t0) * 1000)
