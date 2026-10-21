"""增强 Telemetry —— Task 8: 可观测性。

变更:
  - 每个节点记录 model / tier / retry_count / repair_count
  - 最终 summary 包含最慢节点
  - 绝不记录 API Key / Authorization Header
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeTrace:
    """单次节点调用的埋点记录。"""

    node: str = ""
    model: str = ""
    model_tier: str = ""
    attempt: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "unknown"  # ok / error / timeout
    error_code: str = ""
    retry_count: int = 0
    repair_count: int = 0

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "model": self.model,
            "model_tier": self.model_tier,
            "attempt": self.attempt,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "status": self.status,
            "error_code": self.error_code,
            "retry_count": self.retry_count,
            "repair_count": self.repair_count,
        }


@dataclass
class GenerationTelemetry:
    """一次生成请求的完整遥测。"""

    request_id: str = ""
    traces: list[NodeTrace] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0

    def record(self, trace: NodeTrace) -> None:
        self.traces.append(trace)

    def summary(self) -> dict[str, Any]:
        if not self.traces:
            return {"total_calls": 0}

        total_in = sum(t.input_tokens for t in self.traces)
        total_out = sum(t.output_tokens for t in self.traces)
        total_retry = sum(t.retry_count for t in self.traces)
        total_repair = sum(t.repair_count for t in self.traces)
        errors = [t for t in self.traces if t.status not in ("ok", "unknown")]

        slowest = max(self.traces, key=lambda t: t.latency_ms)
        fastest = min(self.traces, key=lambda t: t.latency_ms)

        return {
            "total_latency_ms": int((self.finished_at - self.started_at) * 1000),
            "total_model_calls": len(self.traces),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_retry_count": total_retry,
            "total_repair_count": total_repair,
            "error_count": len(errors),
            "slowest_node": {
                "node": slowest.node,
                "latency_ms": slowest.latency_ms,
                "model": slowest.model,
            },
            "fastest_node": {
                "node": fastest.node,
                "latency_ms": fastest.latency_ms,
            },
            "by_tier": _by_tier(self.traces),
        }

    def stop(self) -> None:
        self.finished_at = time.monotonic()


def _by_tier(traces: list[NodeTrace]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in traces:
        if t.model_tier:
            out[t.model_tier] = out.get(t.model_tier, 0) + 1
    return out


def new_trace(node: str, model: str, tier: str = "",
              attempt: int = 0, retry_count: int = 0,
              repair_count: int = 0) -> NodeTrace:
    return NodeTrace(
        node=node, model=model, model_tier=tier,
        attempt=attempt, retry_count=retry_count,
        repair_count=repair_count, started_at=time.monotonic(),
    )