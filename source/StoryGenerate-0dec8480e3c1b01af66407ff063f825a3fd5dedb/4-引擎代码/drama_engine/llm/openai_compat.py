"""OpenAI 兼容适配器 —— Phase 1 重构。

变更:
  - httpx.Timeout 拆分 connect/read/write/pool
  - 按 HTTP 状态码区分可重试/不可重试
  - DeadlineContext 集成：每次调用前检查剩余时间
  - 错误统一映射到 core.errors 标准错误码
  - build_provider_from_env: prod 环境禁止 Mock fallback
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from ..core.deadline import DeadlineContext
from ..core.errors import (
    HTTP_NON_RETRYABLE_STATUSES,
    HTTP_RETRYABLE_STATUSES,
    GenerationTimeoutError,
    ModelInvalidOutputError,
    ModelNotConfiguredError,
    ModelTimeoutError,
    ModelUnavailableError,
    classify_http_error,
)
from ..core.settings import get_settings
from ..config import PROMPT_VERSION
from .base import BaseLLMProvider, LLMResult, LLMSpec, estimate_tokens
from .usage_context import current_usage_context

# 常见供应商的默认端点
KNOWN_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "openai": "https://api.openai.com/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


class OpenAICompatProvider(BaseLLMProvider):
    """任意 OpenAI 兼容端点。Phase 1 增强：分层超时 + 智能重试 + Deadline 感知。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = KNOWN_ENDPOINTS["deepseek"],
        connect_timeout: float = 15.0,
        read_timeout: float = 60.0,
        write_timeout: float = 30.0,
        pool_timeout: float = 5.0,
        max_retries: int = 1,
    ) -> None:
        self.name = "openai_compat"
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.call_history: list[dict[str, Any]] = []

        # 分层超时
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )

    def _attempt_metadata(self, spec: LLMSpec, attempt: int, started_at: datetime) -> dict[str, Any]:
        context = current_usage_context()
        return {
            **context,
            "call_id": uuid.uuid4().hex,
            "provider": self.name,
            "stage": context.get("stage") or "generation",
            "node": context.get("node") or spec.role,
            "role": spec.role,
            "requested_model": spec.model,
            "attempt": attempt + 1,
            "prompt_version": PROMPT_VERSION,
            "started_at": started_at.isoformat(),
        }

    def complete(
        self,
        spec: LLMSpec,
        system: str,
        user: str,
        deadline: DeadlineContext | None = None,
    ) -> LLMResult:
        """执行一次 LLM 调用。

        deadline: 若提供，实际 read timeout = min(read_timeout, deadline.remaining)
        """
        payload: dict = {
            "model": spec.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": spec.temperature,
            "max_tokens": spec.max_tokens,
        }
        payload.update(spec.extra.get("provider_params", {}))
        if spec.extra.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}

        # 按节点设定 read timeout，受 deadline 约束
        node_timeout = spec.extra.get("node_timeout_sec")
        read_to = self._timeout.read
        if node_timeout is not None:
            read_to = min(read_to, node_timeout)
        if deadline is not None:
            read_to = min(read_to, deadline.remaining_seconds())

        effective_timeout = httpx.Timeout(
            connect=self._timeout.connect,
            read=read_to,
            write=self._timeout.write,
            pool=self._timeout.pool,
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            # 每次重试前都检查 deadline
            if deadline is not None and deadline.expired():
                raise GenerationTimeoutError(
                    f"Deadline 已过期（已用 {deadline.elapsed():.1f}s），"
                    f"第 {attempt + 1} 次调用被拒绝"
                )

            t0 = time.perf_counter()
            started_at = datetime.now(timezone.utc)
            attempt_meta = self._attempt_metadata(spec, attempt, started_at)
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=effective_timeout,
                )
                status = resp.status_code

                # 非重试型错误：立即失败
                if status in HTTP_NON_RETRYABLE_STATUSES:
                    self.call_history.append({
                        **attempt_meta, "status": "error", "error": f"HTTP {status}",
                        "error_code": f"HTTP_{status}", "usage_source": "unavailable",
                        "latency_ms": int((time.perf_counter() - t0) * 1000),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    raise classify_http_error(status, f"HTTP {status}")

                # 重试型错误
                if status in HTTP_RETRYABLE_STATUSES:
                    self.call_history.append({
                        **attempt_meta, "status": "error", "error": f"HTTP {status}",
                        "error_code": f"HTTP_{status}", "usage_source": "unavailable",
                        "latency_ms": int((time.perf_counter() - t0) * 1000),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    last_error = classify_http_error(status, f"HTTP {status}")
                    if attempt < self.max_retries:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise last_error

                resp.raise_for_status()
                data = resp.json()
                latency = int((time.perf_counter() - t0) * 1000)
                usage = data.get("usage") or {}
                prompt_details = usage.get("prompt_tokens_details") or {}
                completion_details = usage.get("completion_tokens_details") or {}
                usage_reported = "prompt_tokens" in usage or "completion_tokens" in usage
                result = LLMResult(
                    text=data["choices"][0]["message"]["content"] or "",
                    model=data.get("model", spec.model),
                    input_tokens=usage.get("prompt_tokens") or estimate_tokens(system + user),
                    output_tokens=usage.get("completion_tokens") or 0,
                    latency_ms=latency,
                )
                self.call_history.append({
                    **attempt_meta,
                    "actual_model": result.model, "attempt": attempt + 1,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cache_read_tokens": prompt_details.get("cached_tokens", 0) or 0,
                    "cache_write_tokens": prompt_details.get("cache_creation_tokens", 0) or 0,
                    "reasoning_tokens": completion_details.get("reasoning_tokens", 0) or 0,
                    "reasoning_included_in_output": True,
                    "usage_source": "reported" if usage_reported else "estimated",
                    "provider_request_id": resp.headers.get("x-request-id") or data.get("id"),
                    "status": "success", "error_code": None,
                    "latency_ms": result.latency_ms,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                return result

            except (GenerationTimeoutError, ModelInvalidOutputError,
                    ModelUnavailableError, ModelTimeoutError):
                raise  # 这些已经是我们的错误类型，直接抛出

            except httpx.TimeoutException as exc:
                self.call_history.append({
                    **attempt_meta, "status": "timeout", "error": "timeout",
                    "error_code": "MODEL_TIMEOUT", "usage_source": "unavailable",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                last_error = ModelTimeoutError(str(exc))
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue

            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                last_error = classify_http_error(code, str(exc))
                self.call_history.append({
                    **attempt_meta, "status": "error", "error": f"HTTP {code}",
                    "error_code": f"HTTP_{code}", "usage_source": "unavailable",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                if code in HTTP_RETRYABLE_STATUSES and attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error

            except Exception as exc:
                self.call_history.append({
                    **attempt_meta, "status": "error", "error": type(exc).__name__,
                    "error_code": type(exc).__name__, "usage_source": "unavailable",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                last_error = ModelUnavailableError(str(exc))
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue

        raise (last_error or ModelUnavailableError("LLM 调用失败"))


def build_provider_from_env() -> OpenAICompatProvider:
    """按环境变量装配。prod 环境缺少密钥时抛出异常。"""
    import os

    settings = get_settings()
    key = settings.llm_api_key

    # ---- Prod Mock Guard ----
    if not key:
        if settings.is_prod:
            raise ModelNotConfiguredError(
                "生产环境缺少 API Key。请设置 DRAMA_LLM_API_KEY 或 STORY_LLM_API_KEY。"
            )
        # dev/test 允许 Mock fallback（通过调用方处理）
        raise ModelNotConfiguredError("未配置 API Key（dev/test 环境可回退 Mock）")

    return OpenAICompatProvider(
        api_key=key,
        base_url=settings.llm_base_url,
        connect_timeout=settings.http_connect_timeout_sec,
        read_timeout=settings.http_read_timeout_sec,
        write_timeout=settings.http_write_timeout_sec,
        max_retries=settings.max_retries,
    )


def build_provider_from_env_safe() -> BaseLLMProvider:
    """安全版：dev/test 环境下缺少密钥时回退 Mock。"""
    from .mock import MockLLMProvider

    try:
        return build_provider_from_env()
    except ModelNotConfiguredError:
        settings = get_settings()
        if settings.allow_mock:
            return MockLLMProvider()
        raise
