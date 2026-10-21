"""Phase 1.5 Benchmark Harness。

对关键节点进行多模型 A/B 对比测试，收集 latency / token / JSON validity 数据。

用法:
    $env:DRAMA_LLM_API_KEY = "..."
    python scripts/benchmark_generation.py --node behavior_design --runs 2
    python scripts/benchmark_generation.py --node outline --runs 2
    python scripts/benchmark_generation.py --node all --runs 1
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

from drama_engine.config import RuleLibrary
from drama_engine.llm.openai_compat import OpenAICompatProvider
from drama_engine.llm.router import ModelTierMap, resolve_spec
from drama_engine.core.settings import get_settings
from drama_engine.core.errors import StoryEngineError

# ---- Settings ----
SETTINGS = get_settings()
API_KEY = SETTINGS.llm_api_key
BASE_URL = SETTINGS.llm_base_url

BENCHMARK_MODELS = {
    "FAST": ["deepseek-v4-flash"],
    "BALANCED": ["qwen/qwen3.7-plus"],
    "STRONG": ["qwen/qwen3.7-max", "qwen/deepseek-v4-pro"],
    "LONG": ["zhipu/glm-5.3"],
}

NODE_CONFIGS = {
    "behavior_design": {"tier": "STRONG", "role": "behavior_design"},
    "outline": {"tier": "STRONG", "role": "outline"},
    "episode_plan": {"tier": "STRONG", "role": "episode_beats"},
    "episode_writer": {"tier": "BALANCED", "role": "episode_beats"},
}

RESULTS: list[dict] = []


def record(node: str, model: str, run_idx: int, **fields):
    RESULTS.append({"node": node, "model": model, "run": run_idx, **fields})


def bench_behavior_design(provider: OpenAICompatProvider, lib: RuleLibrary,
                          model: str, run_idx: int):
    from drama_engine.prompts import behavior_design
    from drama_engine.schemas import BehaviorBible

    gadget = {"assets": [{"id": "a1", "name": "记账能力", "layer": "L4", "curve": "TC1",
                           "capacity": "infinite", "activation": "自动", "knowledge_track": True}]}
    cast = [
        {"name": "沈清", "slot": "A", "is_voiced": True, "merged_into": None},
        {"name": "老管家", "slot": "C", "is_voiced": True, "merged_into": None},
        {"name": "二房夫人", "slot": "D", "is_voiced": True, "merged_into": None},
    ]
    tier_map = ModelTierMap({"STRONG": model, "FAST": model, "BALANCED": model, "LONG": model})
    spec = resolve_spec("behavior_design", tier_map, extra={"json_mode": True})
    system, user = behavior_design(lib, "社畜穿越成将军府嫡女，用现代记账法整顿府库", gadget, cast)

    _run_structured("behavior_design", model, run_idx, provider, spec, system, user,
                    BehaviorBible)


def bench_outline(provider: OpenAICompatProvider, lib: RuleLibrary,
                  model: str, run_idx: int):
    from drama_engine.prompts import outline
    from drama_engine.schemas import OutlineDraft

    gadget = {"assets": [{"id": "a1", "name": "记账能力", "layer": "L4", "curve": "TC1",
                           "capacity": "infinite", "activation": "自动", "knowledge_track": True}]}
    cast = [
        {"name": "沈清", "slot": "A", "is_voiced": True, "merged_into": None},
        {"name": "老管家", "slot": "C", "is_voiced": True, "merged_into": None},
    ]
    tier_map = ModelTierMap({"STRONG": model, "FAST": model, "BALANCED": model, "LONG": model})
    spec = resolve_spec("outline", tier_map, extra={"json_mode": True})
    system, user = outline(lib, "社畜穿越成将军府嫡女", "G08", gadget, cast, 3)

    _run_structured("outline", model, run_idx, provider, spec, system, user, OutlineDraft)


def bench_episode_plan(provider: OpenAICompatProvider, lib: RuleLibrary,
                       model: str, run_idx: int):
    from drama_engine.prompts import episode_beats
    from drama_engine.schemas import Episode

    entry = {
        "episode": 1, "title": "穿越初醒", "act": 2, "core_goal": "搞清处境",
        "conflict_intensity": 3, "polarity": "neutral", "loop_step": "S1",
    }
    cast = [{"name": "沈清", "slot": "A", "is_voiced": True, "voice_anchor": "冷静"}]
    gadget = {"assets": []}
    tier_map = ModelTierMap({"STRONG": model, "FAST": model, "BALANCED": model, "LONG": model})
    spec = resolve_spec("episode_beats", tier_map, extra={"json_mode": True})
    system, user = episode_beats(lib, entry, cast, gadget, 90, continuity_text="（无）")

    _run_structured("episode_plan", model, run_idx, provider, spec, system, user, Episode)


def _run_structured(node: str, model: str, run_idx: int,
                    provider: OpenAICompatProvider, spec, system: str, user: str,
                    schema):
    t0 = time.perf_counter()
    repair = 0
    try:
        result = provider.complete_structured(spec, system, user, schema)
        ms = int((time.perf_counter() - t0) * 1000)
        record(node, model, run_idx, success=True, latency_ms=ms,
               json_valid=True, repair_count=repair,
               sample=result.model_dump_json()[:200])
    except StoryEngineError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record(node, model, run_idx, success=False, latency_ms=ms,
               json_valid=False, repair_count=repair, error=str(e)[:200])
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record(node, model, run_idx, success=False, latency_ms=ms,
               json_valid=False, repair_count=repair, error=str(e)[:200])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Phase 1.5 Benchmark")
    parser.add_argument("--node", default="all",
                        choices=["all", "behavior_design", "outline", "episode_plan",
                                 "episode_writer"])
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: DRAMA_LLM_API_KEY not set")
        return 1

    lib = RuleLibrary.load()
    configs = NODE_CONFIGS

    nodes_to_bench = list(configs.keys()) if args.node == "all" else [args.node]
    runs = args.runs

    for node in nodes_to_bench:
        cfg = configs[node]
        models = BENCHMARK_MODELS.get(cfg["tier"], [SETTINGS.llm_strong_model])
        for model in models:
            provider = OpenAICompatProvider(
                api_key=API_KEY, base_url=BASE_URL,
                connect_timeout=15, read_timeout=180,
                max_retries=1,
            )
            for r in range(1, runs + 1):
                print(f"  [{node}] {model} run {r}/{runs}...", end=" ", flush=True)
                t0 = time.perf_counter()
                if node == "behavior_design":
                    bench_behavior_design(provider, lib, model, r)
                elif node == "outline":
                    bench_outline(provider, lib, model, r)
                elif node == "episode_plan":
                    bench_episode_plan(provider, lib, model, r)
                r_ = RESULTS[-1]
                total_s = (time.perf_counter() - t0)
                status = "OK" if r_["success"] else "FAIL"
                print(f"[{status}] {r_['latency_ms']}ms ({total_s:.0f}s wall)")

    # 汇总
    print(f"\n{'='*70}")
    for node in nodes_to_bench:
        for model in set(r["model"] for r in RESULTS if r["node"] == node):
            node_results = [r for r in RESULTS if r["node"] == node and r["model"] == model]
            ok = sum(1 for r in node_results if r["success"])
            lats = [r["latency_ms"] for r in node_results]
            avg = sum(lats) // len(lats) if lats else 0
            print(f"  {node:20s} {model:30s} {ok}/{len(node_results)} OK  avg={avg}ms")

    out_dir = Path(__file__).resolve().parent.parent / "tests" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"benchmark_{args.node}_{int(time.time())}.json"
    out_path.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())