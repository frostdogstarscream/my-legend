"""Phase 4.5D: Parallel Ledger + EpisodePlan Draft 节点。

替换 graph 中的 s5_ledger → gen_episode 串行路径：
    gate_outline → parallel_ledger_plan → gate_bible → gen_episode

parallel_ledger_plan 内部：
    FrozenSnapshot
        ├── Branch A: s5_ledger (FactLedger)
        └── Branch B: _episode_plan_draft (EpisodePlan)
    Fan-in: reconcile plan fact references
"""
from __future__ import annotations

import os
import time
from typing import Any

from ..contracts import Runtime
from ..core.parallel import ParallelTask, run_parallel_group
from .project import s5_ledger as _s5_ledger_node


def frozen_snapshot(state: dict) -> dict:
    """从 state 中提取并行所需的只读快照。"""
    return {
        "brief": state.get("brief"),
        "genre_id": state.get("genre_id", ""),
        "recipe_id": state.get("recipe_id", ""),
        "gadget": state.get("gadget"),
        "cast": state.get("cast", []),
        "behavior": state.get("behavior"),
        "outline": state.get("outline", []),
        "resolved_characters": state.get("resolved_characters", []),
        "character_inputs": state.get("character_inputs", []),
        "workspace": state.get("workspace", "."),
        "target_episodes": getattr(state.get("brief", {}), "target_episodes", 1) if hasattr(state.get("brief", {}), "target_episodes") else 1,
        "mode_context": state.get("mode_context"),
    }


def parallel_ledger_plan(state: dict, config) -> dict:
    """Phase 4.5D: 并行 Fact Ledger + Episode Plan Draft。

    使用 run_parallel_group() 的 ThreadPoolExecutor(max_workers=2)。
    两个 branch 共享同一个 only-get snapshot，互不可见。
    """
    lib, runtime = _deps(config)
    tele = getattr(runtime, "telemetry", None)

    snapshot = frozen_snapshot(state)
    target_eps = snapshot.get("target_episodes", 1)

    # Branch A: FactLedger
    def _run_ledger() -> dict:
        ledger_state = dict(state)
        ledger_state.update(snapshot)
        result = _s5_ledger_node(ledger_state, config)
        return {
            "ledger": result.get("ledger"),
            "budgets": result.get("budgets", {}),
            "trace": result.get("trace", []),
        }

    # Branch B: EpisodePlan Draft (uses frozen snapshot, does NOT see ledger)
    def _run_plan_draft() -> dict:
        from ..llm.router import resolve_spec
        from ..prompts import episode_plan as _plan_prompt
        from ..schemas import EpisodePlan

        outline_list = snapshot.get("outline") or []
        if outline_list:
            first = outline_list[0]
            outline_entry = first.model_dump() if hasattr(first, "model_dump") else (dict(first) if isinstance(first, dict) else {})
        else:
            outline_entry = {}
        cast_dicts = []
        for c in (snapshot.get("cast") or []):
            if hasattr(c, "model_dump"):
                cast_dicts.append(c.model_dump())
            elif isinstance(c, dict):
                cast_dicts.append(c)
        gadget_dict = {}
        g = snapshot.get("gadget")
        if g:
            gadget_dict = g.model_dump() if hasattr(g, "model_dump") else (dict(g) if isinstance(g, dict) else {})
        dur = getattr(snapshot.get("brief"), "target_duration_sec", 90) if hasattr(snapshot.get("brief"), "target_duration_sec") else 90

        system, user = _plan_prompt(
            lib, outline_entry, cast_dicts, gadget_dict, dur, continuity_text=""
        )
        spec = resolve_spec("episode_plan")
        plan: EpisodePlan = runtime.llm.complete_structured(spec, system, user, EpisodePlan)
        return {
            "episode_plan_draft": plan.model_dump(),
            "plan_budget": {"llm_calls": 1},
        }

    # Guard: 仅 1 episode 时 plan_draft 无意义
    if target_eps <= 1:
        ledger_result = _run_ledger()
        return {
            **ledger_result,
            "budgets": ledger_result.get("budgets", {}),
            "trace": ledger_result.get("trace", []),
        }

    # 并行执行
    if tele:
        tele.event("parallel_ledger_plan", {"target_episodes": target_eps})

    result = run_parallel_group(
        "ledger_plan",
        [
            ParallelTask(name="ledger", fn=_run_ledger, branch_id="ledger"),
            ParallelTask(name="plan_draft", fn=_run_plan_draft, branch_id="plan_draft"),
        ],
        telemetry=tele,
    )

    # Fan-in: 提取结果
    ledger_data = {}
    plan_data = {}
    for r in result.results:
        if r.branch_id == "ledger" and r.success:
            ledger_data = r.result
        elif r.branch_id == "plan_draft" and r.success:
            plan_data = r.result
        elif not r.success:
            pass  # branch failure handled by RepairBudget, result propagated as None

    # Reconcile: 代码映射 plan draft 中的 fact references 到真实 ledger fact_ids
    plan_draft = plan_data.get("episode_plan_draft", {})
    if plan_draft and ledger_data.get("ledger"):
        plan_draft = _reconcile_fact_refs(plan_draft, ledger_data["ledger"])

    return {
        "ledger": ledger_data.get("ledger"),
        "episode_plan_draft": plan_draft,
        "budgets": _merge_budgets(
            ledger_data.get("budgets", {}),
            plan_data.get("plan_budget", {}),
        ),
        "trace": (ledger_data.get("trace", []) + [f"parallel:saved={result.parallel_saved_ms}ms"]),
    }


def _reconcile_fact_refs(plan_draft: dict, ledger: Any) -> dict:
    """纯代码：将 EpisodePlanDraft 中的语义 fact references 映射到真实 fact_id。

    不调用 LLM。
    """
    entries = getattr(ledger, "entries", []) if hasattr(ledger, "entries") else []
    # 建立 fact_text → fact_id 索引
    fact_map: dict[str, str] = {}
    for e in entries:
        fact_map[getattr(e, "fact_text", "")] = getattr(e, "fact_id", "")
        fact_map[getattr(e, "fact_key", "")] = getattr(e, "fact_id", "")

    # 替换 plan 中的 references
    if isinstance(plan_draft, dict):
        facts_used = plan_draft.get("facts_used", [])
        if facts_used:
            reconciled = []
            for f in facts_used:
                if isinstance(f, dict) and not f.get("fact_id"):
                    text = f.get("fact_text", "") or f.get("fact_key", "")
                    if text and text in fact_map:
                        f["fact_id"] = fact_map[text]
                reconciled.append(f)
            plan_draft["facts_used"] = reconciled

    return plan_draft


def _merge_budgets(a: dict, b: dict) -> dict:
    merged = dict(a)
    for k, v in b.items():
        merged[k] = merged.get(k, 0) + v
    return merged


def _deps(config) -> tuple:
    """从 config 中提取 lib 和 runtime。"""
    conf = config.get("configurable", config) if isinstance(config, dict) else {}
    lib = conf.get("lib")
    runtime = conf.get("runtime")
    if lib is None or runtime is None:
        raise RuntimeError("parallel_ledger_plan 需要 lib 和 runtime 在 config.configurable 中")
    return lib, runtime