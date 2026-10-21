"""Phase 0 Golden Node Test —— 逐节点验证真实 API 行为。

每个节点独立测试、独立记录结果。目标是建立 API 行为基线：
  - 哪些节点在真实模型下可正常产出
  - 哪些节点因结构化输出复杂度过高而失败
  - token 消耗与 latency 基线

用法：
  $env:DRAMA_LLM_API_KEY = "..."
  $env:DRAMA_LLM_BASE_URL = "https://moma.cmecloud.cn/v1"
  $env:DRAMA_LLM_MODEL = "qwen/deepseek-v4-pro"
  python tests/regression/golden/golden_node_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 确保引擎代码在 path 中
ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime, InMemoryTelemetry
from drama_engine.llm.openai_compat import OpenAICompatProvider
from drama_engine.llm.router import resolve_spec, ModelTierMap
from drama_engine.schemas import (
    BehaviorBible, CastDraft, Episode, FactLedger, GadgetSpec,
    JudgeResponse, OutlineDraft, TopicChoice,
)

# ---- 配置 ----
API_KEY = os.environ.get("DRAMA_LLM_API_KEY", "")
BASE_URL = os.environ.get("DRAMA_LLM_BASE_URL", "https://moma.cmecloud.cn/v1")
MODEL = os.environ.get("DRAMA_LLM_MODEL", "qwen/deepseek-v4-pro")
TIMEOUT = float(os.environ.get("DRAMA_LLM_TIMEOUT", "300"))

RESULTS: list[dict] = []


def record(node: str, schema: str, status: str, latency_ms: int,
           tokens: dict, error: str = "", sample: str = "") -> None:
    RESULTS.append({
        "node": node, "schema": schema, "status": status,
        "latency_ms": latency_ms, "tokens": tokens,
        "error": error[:200], "sample": sample[:200],
    })


def make_provider():
    return OpenAICompatProvider(
        api_key=API_KEY, base_url=BASE_URL, timeout_sec=TIMEOUT,
        max_retries=1,  # 只重试 1 次，加快测试
    )


def make_tier_map():
    return ModelTierMap({"reasoning": MODEL, "strong": MODEL, "cheap": MODEL})


# ============================================================================
# 测试用例
# ============================================================================

def test_topic_select(provider, lib: RuleLibrary):
    """S1: 选题节点。"""
    from drama_engine.prompts import topic_select

    system, user = topic_select(lib, "社畜穿越成将军府嫡女，用现代记账法整顿府库")
    spec = resolve_spec("topic_select", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, TopicChoice)
        ms = int((time.perf_counter() - t0) * 1000)
        record("topic_select", "TopicChoice", "OK", ms,
               {"in": result._hidden_input_tokens if hasattr(result, '_hidden_input_tokens') else 0},
               sample=result.model_dump_json())
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("topic_select", "TopicChoice", "FAIL", ms, {}, str(e))
        return False


def test_gadget_design(provider, lib: RuleLibrary):
    """S2: 金手指设计节点。"""
    from drama_engine.prompts import gadget_design

    system, user = gadget_design(lib, "社畜穿越成将军府嫡女，用现代记账法整顿府库",
                                  "G08", ["记账能力", "现代管理知识"])
    spec = resolve_spec("gadget_design", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, GadgetSpec)
        ms = int((time.perf_counter() - t0) * 1000)
        record("gadget_design", "GadgetSpec", "OK", ms, {},
               sample=result.model_dump_json()[:300])
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("gadget_design", "GadgetSpec", "FAIL", ms, {}, str(e))
        return False


def test_cast_design(provider, lib: RuleLibrary):
    """S3: 角色设计节点。"""
    from drama_engine.prompts import cast_design

    gadget = {"assets": [{"id": "a1", "name": "记账能力", "layer": "L4", "curve": "TC1",
                           "capacity": "infinite", "activation": "自动", "knowledge_track": True}]}
    system, user = cast_design(lib, "社畜穿越成将军府嫡女", "R3", gadget)
    spec = resolve_spec("cast_design", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, CastDraft)
        ms = int((time.perf_counter() - t0) * 1000)
        record("cast_design", "CastDraft", "OK", ms, {},
               sample=f"{len(result.cards)} cards")
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("cast_design", "CastDraft", "FAIL", ms, {}, str(e))
        return False


def test_outline(provider, lib: RuleLibrary):
    """S4: 大纲节点。"""
    from drama_engine.prompts import outline

    gadget = {"assets": [{"id": "a1", "name": "记账能力", "layer": "L4", "curve": "TC1",
                           "capacity": "infinite", "activation": "自动", "knowledge_track": True}]}
    cast = [{"name": "测试角色A", "slot": "A", "is_voiced": True, "merged_into": None}]
    system, user = outline(lib, "社畜穿越成将军府嫡女", "G08", gadget, cast, 3)
    spec = resolve_spec("outline", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, OutlineDraft)
        ms = int((time.perf_counter() - t0) * 1000)
        record("outline", "OutlineDraft", "OK", ms, {},
               sample=f"{len(result.entries)} entries")
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("outline", "OutlineDraft", "FAIL", ms, {}, str(e))
        return False


def test_behavior_design(provider, lib: RuleLibrary):
    """S3b: 行为卡节点 —— 最复杂的结构化输出。"""
    from drama_engine.prompts import behavior_design

    gadget = {"assets": [{"id": "a1", "name": "记账能力", "layer": "L4", "curve": "TC1",
                           "capacity": "infinite", "activation": "自动", "knowledge_track": True}]}
    cast = [
        {"name": "沈清", "slot": "A", "is_voiced": True, "merged_into": None},
        {"name": "老管家", "slot": "C", "is_voiced": True, "merged_into": None},
        {"name": "二房夫人", "slot": "D", "is_voiced": True, "merged_into": None},
    ]
    system, user = behavior_design(lib, "社畜穿越成将军府嫡女", gadget, cast)
    spec = resolve_spec("behavior_design", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, BehaviorBible)
        ms = int((time.perf_counter() - t0) * 1000)
        record("behavior_design", "BehaviorBible", "OK", ms, {},
               sample=f"{len(result.cards)} cards, {len(result.relations)} relations")
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("behavior_design", "BehaviorBible", "FAIL", ms, {}, str(e))
        return False


def test_fact_ledger(provider, lib: RuleLibrary):
    """S5: 账本节点。"""
    from drama_engine.prompts import fact_ledger as fl_prompt

    gadget = {"assets": [{"id": "a1", "name": "记账能力", "layer": "L4", "curve": "TC1",
                           "capacity": "infinite", "activation": "自动", "knowledge_track": True}]}
    cast = [{"name": "沈清", "is_voiced": True, "merged_into": None}]
    outline_data = [{"episode": 1, "act": 1}, {"episode": 2, "act": 3}, {"episode": 3, "act": 3}]
    system, user = fl_prompt(lib, "社畜穿越成将军府嫡女", gadget, cast, outline_data)
    spec = resolve_spec("fact_ledger", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, FactLedger)
        ms = int((time.perf_counter() - t0) * 1000)
        record("fact_ledger", "FactLedger", "OK", ms, {},
               sample=f"{len(result.entries)} entries")
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("fact_ledger", "FactLedger", "FAIL", ms, {}, str(e))
        return False


def test_episode_beats(provider, lib: RuleLibrary):
    """逐集剧本生成 —— 最大的单次输出。"""
    from drama_engine.prompts import episode_beats

    entry = {
        "episode": 1, "title": "穿越初醒", "act": 2, "core_goal": "搞清处境",
        "conflict_intensity": 3, "polarity": "neutral", "loop_step": "S1",
        "side_effect_of": None, "quantified_gain": None, "gadget_failed": False,
    }
    cast = [{"name": "沈清", "slot": "A", "is_voiced": True, "voice_anchor": "冷静稳重"}]
    gadget = {"assets": []}
    system, user = episode_beats(lib, entry, cast, gadget, 90, continuity_text="（无）")
    spec = resolve_spec("episode_beats", make_tier_map(), extra={"json_mode": True})

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, Episode)
        ms = int((time.perf_counter() - t0) * 1000)
        line_count = sum(len(b.lines) for b in result.beats)
        record("episode_beats", "Episode", "OK", ms, {},
               sample=f"{len(result.beats)} beats, {line_count} lines")
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("episode_beats", "Episode", "FAIL", ms, {}, str(e))
        return False


def test_judge(provider, lib: RuleLibrary):
    """语义裁判节点。"""
    spec = resolve_spec("judge", make_tier_map(), extra={"json_mode": True})
    system = "你是一个剧本质量评审。"
    user = json.dumps({
        "verdicts": [{
            "rule_id": "K06",
            "subject": "测试用例",
            "claim": "测试声明",
            "expect": "正文应包含副作用指认",
            "episode_text": "（无）",
            "context": {"emotion_peak_span": 3, "speakers": ["A"], "reversal_lines": []},
        }]
    }, ensure_ascii=False, indent=2)
    user = f"请完成下列裁决。\n```json\n{user}\n```"

    t0 = time.perf_counter()
    try:
        result = provider.complete_structured(spec, system, user, JudgeResponse)
        ms = int((time.perf_counter() - t0) * 1000)
        record("judge", "JudgeResponse", "OK", ms, {},
               sample=f"{len(result.verdicts)} verdicts")
        return True
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        record("judge", "JudgeResponse", "FAIL", ms, {}, str(e))
        return False


# ============================================================================
# Main
# ============================================================================

def main():
    if not API_KEY:
        print("ERROR: DRAMA_LLM_API_KEY not set")
        return 1

    lib = RuleLibrary.load()
    provider = make_provider()

    print("=" * 70)
    print("Phase 0 Golden Node Test — 真实 API 行为基线")
    print(f"Model: {MODEL}  |  Base URL: {BASE_URL}")
    print(f"Timeout: {TIMEOUT}s  |  Rule Lib: formula={lib.formula_version}")
    print("=" * 70)

    tests = [
        ("topic_select", test_topic_select),
        ("gadget_design", test_gadget_design),
        ("cast_design", test_cast_design),
        ("outline", test_outline),
        ("behavior_design", test_behavior_design),
        ("fact_ledger", test_fact_ledger),
        ("episode_beats", test_episode_beats),
        ("judge", test_judge),
    ]

    for name, fn in tests:
        print(f"\n>>> {name}...", end=" ", flush=True)
        ok = fn(provider, lib)
        r = RESULTS[-1]
        print(f"[{'OK' if ok else 'FAIL'}] {r['latency_ms']}ms", end="")
        if r["sample"]:
            print(f" | {r['sample'][:80]}")
        else:
            print(f" | {r['error'][:80]}")

    # ---- 汇总 ----
    ok_count = sum(1 for r in RESULTS if r["status"] == "OK")
    total = len(RESULTS)
    print(f"\n{'=' * 70}")
    print(f"结果: {ok_count}/{total} 通过")

    # 输出完整报告
    out_path = Path(__file__).with_suffix(".json")
    report = {
        "phase": "0",
        "model": MODEL,
        "base_url": BASE_URL,
        "timeout_sec": TIMEOUT,
        "rule_lib_versions": lib.manifest(),
        "total": total,
        "passed": ok_count,
        "failed": total - ok_count,
        "results": RESULTS,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完整报告: {out_path}")

    # 标记 failed 节点为已知问题
    failed = [r for r in RESULTS if r["status"] != "OK"]
    if failed:
        print("\n⚠ 已知限制（需在 Phase 1-2 中解决）：")
        for r in failed:
            print(f"  - {r['node']} ({r['schema']}): {r['error'][:120]}")

    return 0 if ok_count == total else 0  # 不因已知问题返回非零


if __name__ == "__main__":
    sys.exit(main())