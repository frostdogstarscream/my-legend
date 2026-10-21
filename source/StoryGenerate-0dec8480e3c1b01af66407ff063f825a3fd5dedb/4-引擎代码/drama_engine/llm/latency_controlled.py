"""Phase 4.5E: Latency-Controlled Mock Provider.

OKR from real API variance: 每次LLM调用统一 `sleep(delay_sec)` 后再返回合法Mock结果。
  => Engine 性能（调用数／深度／conditional skip／parallel overlap／critical path）可以
     与 Provider SLO 完全分离。

不用于评测故事质量（返回 Mock 结构不反映真实故事水平）。
用于验证：
  - Graph dependency
  - Conditional skip
  - Parallel overlap (time window overlap evidence)
  - Critical Path
  - Repair path
  - Judge path
  - Call count
"""
from __future__ import annotations

import os
import time
from typing import Any

from .base import BaseLLMProvider, LLMResult, LLMSpec
from .mock import MockLLMProvider


class LatencyControlledMockProvider(BaseLLMProvider):
    """每次 complete_structured 统一 sleep(delay_sec) 的测试替身。

    继承 BaseLLMProvider 以复用 telemetry tracing（_trace_llm_call），
    内部委托给 MockLLMProvider(inject_all=False) 产出合法结构。

    delay_sec 可配置:
      环境变量: MOCK_LLM_LATENCY_SEC=2
      构造参数: LatencyControlledMockProvider(delay_sec=5.0)
    """

    name = "latency-controlled-mock"

    def __init__(self, delay_sec: float | None = None) -> None:
        super().__init__()
        self._inner = MockLLMProvider(
            inject_violations=False, inject_tier2=False, inject_fakes=False
        )
        if delay_sec is None:
            delay_sec = float(os.environ.get("MOCK_LLM_LATENCY_SEC", "2.0"))
        self.delay_sec = delay_sec
        # call-level timestamps for parallel overlap verification
        self._calls: list[dict[str, Any]] = []

    @property
    def call_timestamps(self) -> list[dict[str, Any]]:
        """按时间排序的调用记录 (started_at, finished_at, node, model)."""
        return sorted(self._calls, key=lambda c: c["started_at"])

    def complete(self, spec: LLMSpec, system: str, user: str) -> LLMResult:
        t0 = time.monotonic()
        time.sleep(self.delay_sec)
        result = self._inner.complete(spec, system, user)
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._calls.append({
            "started_at": t0,
            "finished_at": time.monotonic(),
            "node": spec.role,
            "model": spec.model,
            "latency_ms": latency_ms,
            "structured": False,
        })
        self._trace_llm_call(spec, result, latency_ms)
        return result

    def complete_structured(self, spec: LLMSpec, system: str, user: str, schema: type):
        t0 = time.monotonic()
        time.sleep(self.delay_sec)
        result = self._inner.complete_structured(spec, system, user, schema)
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._calls.append({
            "started_at": t0,
            "finished_at": time.monotonic(),
            "node": spec.role,
            "model": spec.model,
            "latency_ms": latency_ms,
            "structured": True,
            "schema": schema.__name__,
        })
        # Replicate MockLLMProvider's trace but with our own latency
        from .base import estimate_tokens
        self._trace_llm_call(spec, LLMResult(
            text="[latency-mock]", model=spec.model,
            input_tokens=estimate_tokens(system + user),
            output_tokens=estimate_tokens(user),
        ), latency_ms)
        return result


def verify_parallel_overlap(calls: list[dict[str, Any]], branch_a: str,
                            branch_b: str) -> tuple[bool, float]:
    """验证两个 branch 的时间窗口是否真正重叠。

    返回 (has_overlap, overlap_seconds)。
    """
    a_calls = [c for c in calls if branch_a in c.get("node", "")]
    b_calls = [c for c in calls if branch_b in c.get("node", "")]
    if not a_calls or not b_calls:
        return False, 0.0

    # 取每个 branch 的时间窗口；取最宽重叠区间
    a_start = min(c["started_at"] for c in a_calls)
    a_end = max(c["finished_at"] for c in a_calls)
    b_start = min(c["started_at"] for c in b_calls)
    b_end = max(c["finished_at"] for c in b_calls)

    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    if overlap_end > overlap_start:
        return True, round(overlap_end - overlap_start, 3)
    return False, 0.0