"""Targeted Plan-only comparison: same idea x different Recipes.

Generates only the Plan stage (not full stories) to compare how different
strategies affect story outline, character design, and narrative structure.
"""
from __future__ import annotations

import json, os, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "4-引擎代码"))

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.llm.router import ModelTierMap, resolve_spec, TIER_STRONG, TIER_BALANCED, TIER_FAST
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice,
    CharacterSelection, OperationContext, PlanContent,
)
from drama_engine.workflow.adapters import LLMPlanGenerator
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver

MODEL_NAME = os.environ.get("DRAMA_LLM_MODEL", "qwen/qwen3.7-max")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "6-实测产物" / "strategy_compare_plans"
CREATIVE_IDEA = (
    "一个三十五岁的全职主妇林敏，结婚八年，每天在婆婆的挑剔和丈夫的冷漠中度过。"
    "某天她无意中发现丈夫手机里的暧昧信息和转账记录，原来他不仅出轨，还在悄悄转移家庭存款。"
    "林敏没有立刻摊牌，而是开始悄悄收集证据，联系律师，同时重新捡起婚前做的手工皮具生意。"
    "当她终于准备好一切，在家庭聚会上当着所有亲戚的面揭露真相时，她不是在哭诉——"
    "而是在展示自己已经签好的离婚协议和新工作室的钥匙。"
)

def utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_plan_only(label, prefs):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Mode={prefs.story_mode} Genre={prefs.genre} Recipe={prefs.recipe_id}")
    print(f"{'='*60}")

    result = {"label": label, "preferences": prefs.model_dump(mode="json")}

    try:
        provider = build_provider_from_env()
        tier_map = ModelTierMap(mapping={TIER_STRONG: MODEL_NAME, TIER_BALANCED: MODEL_NAME, TIER_FAST: MODEL_NAME})
        import drama_engine.llm.router as router_mod
        _orig = router_mod.resolve_spec
        router_mod.resolve_spec = lambda role, tier_map_override=None, **ov: _orig(role, tier_map=tier_map, **ov)

        plan_gen = LLMPlanGenerator(provider)
        # Dummy executor (never called)
        class NoopExecutor:
            def execute(self, snapshot): raise NotImplementedError
        templates = MemoryStoryTemplateRepository()
        service = StoryWorkflowService(
            InMemoryWorkflowRepository(), plan_gen, NoopExecutor(),
            template_resolver=StoryTemplateResolver(templates),
        )

        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction=CREATIVE_IDEA,
            creation_preferences=prefs,
            characters=CharacterSelection(
                selected_character_ids=["protagonist"],
                role_bindings=[{"character_id": "protagonist", "story_role_id": "protagonist", "performer_id": "protagonist"}],
            ),
        )
        context = OperationContext(character_snapshots={
            "protagonist": {
                "canon": {"name": "林敏", "immutable_facts": ["三十五岁", "全职主妇", "手工皮具手艺"]},
                "profile": {"goal": "重新掌控自己的人生", "motivation": "不再忍让", "fear": "失去孩子的抚养权"},
                "profile_revision": 1,
            },
        })

        print("  Generating Plan...")
        plan = service.prepare_story_plan(request, context)
        strategy = plan.strategy_snapshot or {}
        p = plan.plan

        # Token trace
        trace = plan.model_trace
        total_in = sum(t.get("input_tokens", 0) for t in trace)
        total_out = sum(t.get("output_tokens", 0) for t in trace)

        result["plan"] = p.model_dump(mode="json")
        result["strategy_snapshot"] = strategy
        result["resolved_preferences"] = plan.resolved_preferences.model_dump(mode="json")
        result["auto_filled"] = plan.auto_filled_fields
        result["tokens"] = {"input": total_in, "output": total_out, "calls": len(trace)}

        print(f"  [OK] Title: {p.title}")
        print(f"  [OK] Premise: {p.premise[:80]}...")
        print(f"  [OK] Theme: {p.theme}")
        print(f"  [OK] Strategy: mode={strategy.get('story_mode')} genre={strategy.get('genre_id')} recipe={strategy.get('recipe_id')}")
        print(f"  [OK] Conflict: {strategy.get('conflict_mechanism', 'N/A')[:80]}...")
        print(f"  [OK] Ending: {strategy.get('ending_strategy', 'N/A')[:80]}...")
        print(f"  [OK] Portrayals: {len(p.character_portrayals)}")
        print(f"  [OK] Episodes: {len(p.episode_outlines)}")
        print(f"  [OK] Tokens: {total_in} in / {total_out} out")

        router_mod.resolve_spec = _orig
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  [ERR] {result['error']}")

    return result


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = utcnow_str()
    print(f"Plan-Only Strategy Comparison — {MODEL_NAME}")
    print(f"Idea: {CREATIVE_IDEA[:120]}...")

    results = []

    # Case 1: AUTO
    results.append(run_plan_only("Case1_AUTO", CreationPreferences(
        story_mode=StoryModeChoice.AUTO, genre="auto",
        target_duration_sec=180, target_episodes=1,
    )))

    # Case 2: BASELINE general, no recipe
    results.append(run_plan_only("Case2_BASELINE_no_recipe", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre="auto", recipe_id=None,
        target_duration_sec=180, target_episodes=1,
    )))

    # Case 3: R4 家庭反击
    results.append(run_plan_only("Case3_R4_family_revenge", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre="G01", recipe_id="R4",
        target_duration_sec=180, target_episodes=1,
    )))

    # Case 4: R5 破镜重圆治愈
    results.append(run_plan_only("Case4_R5_healing_romance", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre="G02", recipe_id="R5",
        target_duration_sec=180, target_episodes=1,
    )))

    # Save
    dump_path = OUTPUT_DIR / f"plan_comparison_{ts}.json"
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nFull results: {dump_path}")

    # Individual plan files
    for r in results:
        label = r["label"].replace(" ", "_")
        plan = r.get("plan") or {}
        strategy = r.get("strategy_snapshot") or {}
        if plan:
            plan_path = OUTPUT_DIR / f"{label}_plan.json"
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump({
                    "label": r["label"],
                    "title": plan.get("title"), "premise": plan.get("premise"),
                    "theme": plan.get("theme"),
                    "beginning": plan.get("beginning"),
                    "development": plan.get("development"),
                    "climax": plan.get("climax"),
                    "ending": plan.get("ending"),
                    "episode_outlines": plan.get("episode_outlines"),
                    "character_portrayals": plan.get("character_portrayals"),
                    "strategy": strategy,
                    "auto_filled": r.get("auto_filled"),
                    "tokens": r.get("tokens"),
                    "error": r.get("error"),
                }, f, ensure_ascii=False, indent=2)

    # Comparison summary
    print("\n" + "=" * 60)
    print("  PLAN COMPARISON")
    print("=" * 60)
    for r in results:
        strategy = r.get("strategy_snapshot") or {}
        plan = r.get("plan") or {}
        tokens = r.get("tokens") or {}
        err = r.get("error")
        print(f"\n--- {r['label']} ---")
        if err:
            print(f"  ERROR: {err}")
            continue
        print(f"  Strategy: mode={strategy.get('story_mode')} genre={strategy.get('genre_name')} recipe={strategy.get('recipe_name')}")
        print(f"  Plan: {plan.get('title')}")
        print(f"  Premise: {plan.get('premise')[:120]}")
        print(f"  Theme: {plan.get('theme')}")
        print(f"  Structure: {plan.get('beginning')[:60]}... | {plan.get('climax')[:60]}... | {plan.get('ending')[:60]}...")
        print(f"  Portrayals: {[p.get('story_name') + ':' + p.get('external_goal')[:30] for p in plan.get('character_portrayals', [])]}")
        print(f"  Tokens: {tokens.get('input')}/{tokens.get('output')}")


if __name__ == "__main__":
    main()