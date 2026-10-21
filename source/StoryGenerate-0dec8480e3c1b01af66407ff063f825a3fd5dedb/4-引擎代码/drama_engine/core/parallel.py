"""Phase 4.5C: Safe Parallel Execution Framework。

轻量并行基础结构，用于在同一 Story Engine 内进行受控并发。

原则：
    - 只并行"输入已冻结、彼此不互相决定结果"的任务
    - Fan-out 前做 FrozenInputSnapshot
    - Fan-in 后做 consistency validator
    - 支持回退顺序执行
    - Deadline-aware & Telemetry-aware

使用方式：
    from core.parallel import run_parallel_group
    results = run_parallel_group("ledger_plan", [task_a, task_b], deadline, tele)
"""
from __future__ import annotations

import concurrent.futures
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ParallelConfig:
    """并行执行配置。由环境变量驱动。"""

    enabled: bool = True
    max_concurrency: int = 2
    min_remaining_deadline_sec: float = 30.0
    fallback_to_sequential: bool = True

    @classmethod
    def from_env(cls) -> ParallelConfig:
        return cls(
            enabled=os.environ.get("STORY_PARALLEL_ENABLED", "true").lower() == "true",
            max_concurrency=int(os.environ.get("STORY_LLM_MAX_CONCURRENCY", "2")),
            min_remaining_deadline_sec=float(
                os.environ.get("STORY_PARALLEL_MIN_DEADLINE", "30")
            ),
            fallback_to_sequential=os.environ.get(
                "STORY_PARALLEL_FALLBACK", "true"
            ).lower() == "true",
        )


@dataclass
class ParallelTask:
    """单个并行任务描述。"""

    name: str
    fn: Callable[[], Any]
    branch_id: str = ""
    timeout_sec: float = 300.0


@dataclass
class ParallelResult:
    """并行任务结果。"""

    branch_id: str
    name: str
    success: bool
    result: Any = None
    error: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ParallelGroupResult:
    """并行组执行结果。"""

    group_name: str
    wall_clock_ms: int
    critical_path_ms: int
    total_llm_work_ms: int
    parallel_saved_ms: int
    results: list[ParallelResult] = field(default_factory=list)
    max_concurrency: int = 0
    sequential: bool = False
    error: str = ""


def run_parallel_group(
    group_name: str,
    tasks: list[ParallelTask],
    deadline_remaining_sec: float = 300.0,
    telemetry: Any = None,
    config: ParallelConfig | None = None,
) -> ParallelGroupResult:
    """执行一组并行任务。

    如果 parallel.enabled=False 或剩余 deadline 不足，回退顺序执行。
    """
    if config is None:
        config = ParallelConfig.from_env()

    t0 = time.monotonic()

    # Guard 1: 并行总开关
    if not config.enabled:
        return _run_sequential(group_name, tasks, t0, telemetry, config)

    # Guard 2: 剩余 deadline 不足
    if deadline_remaining_sec < config.min_remaining_deadline_sec:
        return _run_sequential(group_name, tasks, t0, telemetry, config)

    # Guard 3: 单任务不需要并行
    if len(tasks) <= 1:
        return _run_sequential(group_name, tasks, t0, telemetry, config)

    # Guard 4: 尝试并行
    try:
        return _run_parallel(group_name, tasks, t0, telemetry, config)
    except Exception as exc:
        if config.fallback_to_sequential:
            return _run_sequential(group_name, tasks, t0, telemetry, config, str(exc))
        raise


def _run_sequential(
    group_name: str,
    tasks: list[ParallelTask],
    t0: float,
    telemetry: Any,
    config: ParallelConfig,
    fallback_reason: str = "",
) -> ParallelGroupResult:
    """顺序执行（回退路径）。"""
    results: list[ParallelResult] = []
    total_work = 0
    for task in tasks:
        t_start = time.monotonic()
        try:
            r = task.fn()
            latency = int((time.monotonic() - t_start) * 1000)
            results.append(ParallelResult(
                branch_id=task.branch_id or task.name,
                name=task.name, success=True, result=r,
                latency_ms=latency,
            ))
            total_work += latency
        except Exception as exc:
            latency = int((time.monotonic() - t_start) * 1000)
            results.append(ParallelResult(
                branch_id=task.branch_id or task.name,
                name=task.name, success=False, error=str(exc),
                latency_ms=latency,
            ))
            total_work += latency

    wall = int((time.monotonic() - t0) * 1000)
    if telemetry:
        telemetry.event("parallel_group", {
            "group": group_name, "mode": "sequential",
            "wall_ms": wall, "tasks": len(tasks),
            "fallback_reason": fallback_reason,
        })

    return ParallelGroupResult(
        group_name=group_name,
        wall_clock_ms=wall,
        critical_path_ms=wall,
        total_llm_work_ms=total_work,
        parallel_saved_ms=0,
        results=results,
        max_concurrency=1,
        sequential=True,
        error=fallback_reason,
    )


def _run_parallel(
    group_name: str,
    tasks: list[ParallelTask],
    t0: float,
    telemetry: Any,
    config: ParallelConfig,
) -> ParallelGroupResult:
    """真实并行执行。"""
    results: list[ParallelResult] = []
    max_workers = min(config.max_concurrency, len(tasks))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for t in tasks:
            future = executor.submit(_safe_call, t)
            future_map[future] = t

        for future in concurrent.futures.as_completed(future_map):
            task = future_map[future]
            try:
                r, latency_ms, exc = future.result(timeout=task.timeout_sec)
                if exc:
                    results.append(ParallelResult(
                        branch_id=task.branch_id or task.name,
                        name=task.name, success=False, error=str(exc),
                        latency_ms=latency_ms,
                    ))
                else:
                    results.append(ParallelResult(
                        branch_id=task.branch_id or task.name,
                        name=task.name, success=True, result=r,
                        latency_ms=latency_ms,
                    ))
            except concurrent.futures.TimeoutError:
                results.append(ParallelResult(
                    branch_id=task.branch_id or task.name,
                    name=task.name, success=False,
                    error=f"timeout after {task.timeout_sec}s",
                ))

    wall = int((time.monotonic() - t0) * 1000)
    critical_path = max((r.latency_ms for r in results), default=wall)
    total_work = sum(r.latency_ms for r in results)
    saved = max(0, total_work - wall)

    if telemetry:
        telemetry.event("parallel_group", {
            "group": group_name, "mode": "parallel",
            "wall_ms": wall, "critical_path_ms": critical_path,
            "total_work_ms": total_work, "saved_ms": saved,
            "max_concurrency": max_workers, "tasks": len(tasks),
        })

    return ParallelGroupResult(
        group_name=group_name,
        wall_clock_ms=wall,
        critical_path_ms=critical_path,
        total_llm_work_ms=total_work,
        parallel_saved_ms=saved,
        results=results,
        max_concurrency=max_workers,
        sequential=False,
    )


def _safe_call(task: ParallelTask) -> tuple[Any, int, str | None]:
    """安全的函数调用包装，返回 (result, latency_ms, error_str)。"""
    t0 = time.monotonic()
    try:
        r = task.fn()
        latency = int((time.monotonic() - t0) * 1000)
        return r, latency, None
    except Exception as exc:
        latency = int((time.monotonic() - t0) * 1000)
        return None, latency, str(exc)