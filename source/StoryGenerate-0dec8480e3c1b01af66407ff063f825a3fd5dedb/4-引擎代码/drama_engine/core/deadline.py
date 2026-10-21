"""DeadlineContext —— Task 3: 全局超时控制。

原则：
  - 一个 Generation Request 只有一个全局 absolute deadline
  - 所有节点共享同一个 DeadlineContext
  - 节点实际可用时间 = min(node_timeout, deadline.remaining)
"""
from __future__ import annotations

import time

from .errors import GenerationTimeoutError


class DeadlineContext:
    """请求级超时上下文。

    用法:
        ctx = DeadlineContext(timeout_sec=120)
        ctx.ensure_time("gadget_design", minimum_required=10)
        # ... do work ...
        timeout = ctx.effective_timeout(node_timeout=70)
    """

    def __init__(self, timeout_sec: float) -> None:
        self.started_at: float = time.monotonic()
        self.deadline_at: float = self.started_at + timeout_sec

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0.0

    def ensure_time(self, stage: str, minimum_required: float = 0.0) -> None:
        """若剩余时间不足，直接抛出 GenerationTimeoutError。"""
        remaining = self.remaining_seconds()
        if remaining < minimum_required:
            raise GenerationTimeoutError(
                f"阶段 '{stage}' 需要至少 {minimum_required:.0f}s，"
                f"剩余 {remaining:.1f}s，已拒绝执行"
            )

    def effective_timeout(self, node_timeout: float) -> float:
        """返回节点实际可用的 HTTP timeout。

        = min(node_timeout, remaining_seconds())，但不低于 1 秒。
        """
        return max(1.0, min(node_timeout, self.remaining_seconds()))

    def child(self, stage: str, stage_timeout: float) -> "DeadlineContext":
        """创建一个子 Deadline（以 stage_timeout 和父 deadline 中较早者为准）。

        用于 Repair 等子阶段：Repair 不能无限消耗全局 Deadline。
        """
        effective = min(stage_timeout, self.remaining_seconds())
        return DeadlineContext(effective)

    def __repr__(self) -> str:
        return (
            f"DeadlineContext(elapsed={self.elapsed():.1f}s, "
            f"remaining={self.remaining_seconds():.1f}s)"
        )


# 全局工厂
def make_deadline(timeout_sec: float | None = None,
                  settings_timeout: float = 120.0) -> DeadlineContext:
    """创建一个 DeadlineContext。"""
    return DeadlineContext(timeout_sec if timeout_sec is not None else settings_timeout)