"""节点公共工具：从 RunnableConfig 取规则库与运行时，以及计数/重试/修复预算的工具函数。

Phase 1 新增：
  - Repair Budget: 全局修复次数上限 + 单阶段修复次数上限
  - Deadline-aware: 修复前检查剩余时间
"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from ..config import RuleLibrary
from ..contracts import Runtime
from ..core.settings import get_settings

MAX_ATTEMPTS = 3

# Phase 1: 修复预算（可通过 settings 覆盖）
_REPAIR_SETTINGS = get_settings()


def deps(config: RunnableConfig | None) -> tuple[RuleLibrary, Runtime]:
    """约定：图以 config={"configurable": {"lib": ..., "runtime": ...}} 调用。"""
    cfg: dict[str, Any] = (config or {}).get("configurable") or {}
    lib = cfg.get("lib")
    runtime = cfg.get("runtime")
    if lib is None or runtime is None:
        raise RuntimeError("图必须以 config={'configurable': {'lib':…, 'runtime':…}} 调用")
    return lib, runtime


# ---------------------------------------------------------------- 计数器（累加）
def bump(key: str, n: int = 1) -> dict[str, int]:
    return {key: n}


# ---------------------------------------------------------------- 重试轮次（覆盖）
def attempt_of(state: dict, key: str) -> int:
    return int((state.get("attempts") or {}).get(key, 0))


def next_attempt(state: dict, key: str) -> tuple[int, dict[str, int]]:
    value = attempt_of(state, key) + 1
    return value, {key: value}


# ---------------------------------------------------------------- Repair Budget (Phase 1)
def repair_count(state: dict) -> int:
    """全局修复总次数。"""
    return int((state.get("repair_counts") or {}).get("total", 0))


def repair_count_for_stage(state: dict, stage: str) -> int:
    """单阶段修复次数。"""
    return int((state.get("repair_counts") or {}).get(stage, 0))


def check_repair_budget(state: dict, stage: str) -> bool:
    """检查修复预算是否充足。返回 True 表示允许执行修复。"""
    total = repair_count(state)
    stage_count = repair_count_for_stage(state, stage)
    if total >= _REPAIR_SETTINGS.max_total_repairs:
        return False
    if stage_count >= _REPAIR_SETTINGS.max_repairs_per_stage:
        return False
    return True


def record_repair(stage: str) -> dict[str, int]:
    """记录一次修复（返回 repair_counts 增量）。"""
    return {"total": 1, stage: 1}


# ---------------------------------------------------------------- 校验结论工具
def findings_payload(findings) -> list[dict]:
    out = []
    for f in findings or []:
        out.append(f.model_dump() if hasattr(f, "model_dump") else dict(f))
    return out


def only(findings, route: str) -> list[dict]:
    return [p for p in findings_payload(findings) if p.get("route") == route]


def has_errors(report) -> bool:
    if report is None:
        return False
    return bool(report.errors)


def error_signature(report) -> str:
    if report is None:
        return ""
    return ",".join(sorted({f.rule_id for f in report.errors}))


def stuck_guard(state: dict, key: str, report) -> dict[str, Any]:
    sig = error_signature(report)
    prev = (state.get("attempts") or {}).get(f"{key}_sig")
    return {
        f"{key}_sig": sig,
        f"{key}_stuck": 1 if (prev and prev == sig) else 0,
    }


def is_stuck(state: dict, key: str) -> bool:
    return bool((state.get("attempts") or {}).get(f"{key}_stuck", 0))


def should_repair(state: dict, key: str, limit_key: str | None = None,
                  stage: str = "") -> bool:
    """统一的修复决策：有 error、未超轮次上限、未卡住、修复预算充足。"""
    if not has_errors(state.get("project_report")):
        return False
    if attempt_of(state, limit_key or key) >= MAX_ATTEMPTS:
        return False
    if is_stuck(state, key):
        return False
    # Phase 1: 修复预算
    if stage and not check_repair_budget(state, stage):
        return False
    return True
