"""Real-model comparison: same idea x different Recipes.

Runs 4 strategy configurations against a real LLM and saves all artifacts
for human comparison of Plan quality and story differences.

Usage:
  $env:DRAMA_LLM_API_KEY = "your-key"
  $env:DRAMA_LLM_MODEL = "deepseek-chat"
  python scripts/compare_strategies.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "4-引擎代码"))

from drama_engine.llm.openai_compat import OpenAICompatProvider, build_provider_from_env
from drama_engine.llm.router import ModelTierMap, resolve_spec, TIER_FAST, TIER_BALANCED, TIER_STRONG
from drama_engine.core.settings import get_settings
from drama_engine.workflow.schemas import (
    CreationPreferences,
    CreateStoryRequest,
    PlanContent,
    StoryModeChoice,
    StoryTemplateRef,
    CharacterSelection,
    OperationContext,
    CharacterSnapshot,
)
from drama_engine.workflow.adapters import LLMPlanGenerator, LLMPerformanceAnnotator, ApprovedPlanLLMExecutor
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver

# ---- Configuration ----
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "6-实测产物" / "strategy_compare"
MODEL_NAME = os.environ.get("DRAMA_LLM_MODEL", "deepseek-chat")

# A creative idea that can plausibly work with different recipes
CREATIVE_IDEA = (
    "一个三十五岁的全职主妇林敏，结婚八年，每天在婆婆的挑剔和丈夫的冷漠中度过。"
    "某天她无意中发现丈夫手机里的暧昧信息和转账记录，原来他不仅出轨，还在悄悄转移家庭存款。"
    "林敏没有立刻摊牌，而是开始悄悄收集证据，联系律师，同时重新捡起婚前做的手工皮具生意。"
    "当她终于准备好一切，在家庭聚会上当着所有亲戚的面揭露真相时，她不是在哭诉——"
    "而是在展示自己已经签好的离婚协议和新工作室的钥匙。"
)


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def build_provider():
    """Build the real LLM provider, preferring env vars."""
    try:
        return build_provider_from_env()
    except Exception:
        # Fallback: explicit key
        api_key = os.environ.get("DRAMA_LLM_API_KEY", "")
        base_url = os.environ.get("DRAMA_LLM_BASE_URL", "https://api.deepseek.com/v1")
        if not api_key:
            raise RuntimeError("Set DRAMA_LLM_API_KEY env var")
        return OpenAICompatProvider(api_key=api_key, base_url=base_url)


def run_case(label: str, prefs: CreationPreferences) -> dict:
    """Run one strategy configuration end-to-end."""
    print(f"\n{'='*60}")
    print(f"  CASE: {label}")
    print(f"  Mode: {prefs.story_mode} | Genre: {prefs.genre} | Recipe: {prefs.recipe_id or 'auto'}")
    print(f"{'='*60}")

    result = {
        "label": label,
        "timestamp": utcnow_str(),
        "preferences": prefs.model_dump(mode="json"),
        "idea": CREATIVE_IDEA,
    }

    try:
        provider = build_provider()
        tier_mapping = {TIER_STRONG: MODEL_NAME, TIER_BALANCED: MODEL_NAME, TIER_FAST: MODEL_NAME}
        tier_map = ModelTierMap(mapping=tier_mapping)

        # Patch resolve_spec for this run
        import drama_engine.llm.router as router_mod
        _orig_resolve = router_mod.resolve_spec
        def _patched(role, tier_map_override=None, **overrides):
            return _orig_resolve(role, tier_map=tier_map, **overrides)
        router_mod.resolve_spec = _patched

        plan_gen = LLMPlanGenerator(provider)
        annotator = LLMPerformanceAnnotator(provider)
        story_exec = ApprovedPlanLLMExecutor(provider, annotator)
        templates = MemoryStoryTemplateRepository()
        service = StoryWorkflowService(
            InMemoryWorkflowRepository(), plan_gen, story_exec,
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
                "profile": {
                    "goal": "重新掌控自己的人生",
                    "motivation": "不再忍让",
                    "fear": "失去孩子的抚养权",
                    "personality": ["隐忍", "细致", "果断"],
                },
                "profile_revision": 1,
            },
        })

        # Stage 1: Plan
        print("  [1/3] Generating Plan...")
        plan = service.prepare_story_plan(request, context)
        result["plan_id"] = plan.plan_id
        result["plan_fingerprint"] = plan.plan_fingerprint
        result["plan"] = plan.plan.model_dump(mode="json")
        result["strategy_snapshot"] = plan.strategy_snapshot
        result["resolved_preferences"] = plan.resolved_preferences.model_dump(mode="json")
        result["auto_filled_fields"] = plan.auto_filled_fields
        result["explicit_fields"] = plan.explicit_fields

        # Token from model trace
        trace = plan.model_trace
        total_in = sum(t.get("input_tokens", 0) for t in trace)
        total_out = sum(t.get("output_tokens", 0) for t in trace)
        result["plan_tokens"] = {"input": total_in, "output": total_out, "calls": len(trace)}

        # Stage 2: Approve
        print("  [2/3] Approving Plan...")
        job = service.approve_story_plan(
            plan.plan_id, 1, plan.plan_fingerprint,
            f"compare-{label.replace(' ', '-')}-{utcnow_str()}",
        )

        # Stage 3: Generate
        print("  [3/3] Generating Story...")
        delivery = service.generate_from_approved_plan(job.job_id)
        result["story_version_id"] = delivery.story_version_id
        result["delivery"] = delivery.model_dump(mode="json")
        result["ready_for_playback"] = delivery.ready_for_playback
        result["status"] = delivery.status
        result["episode_count"] = len(delivery.episodes)
        result["error"] = None

        # Total tokens (plan + delivery trace)
        delivery_trace = delivery.model_trace
        all_traces = trace + delivery_trace
        total_all_in = sum(t.get("input_tokens", 0) for t in all_traces)
        total_all_out = sum(t.get("output_tokens", 0) for t in all_traces)
        result["total_tokens"] = {"input": total_all_in, "output": total_all_out, "calls": len(all_traces)}

        strategy = plan.strategy_snapshot or {}
        print(f"  [OK] Plan title: {plan.plan.title}")
        print(f"  [OK] Plan premise: {plan.plan.premise[:80]}...")
        print(f"  [OK] Strategy: mode={strategy.get('story_mode')}, "
              f"genre={strategy.get('genre_id')}, recipe={strategy.get('recipe_id')}")
        print(f"  [OK] Delivery ready: {delivery.ready_for_playback}")
        print(f"  [OK] Plan tokens: {total_in} in / {total_out} out")
        print(f"  [OK] Total tokens: {total_all_in} in / {total_all_out} out")

        # Restore
        router_mod.resolve_spec = _orig_resolve

    except Exception as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        print(f"  [ERR] {type(exc).__name__}: {exc}")

    return result


def save_results(all_results: list[dict]):
    """Save all results to the output directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = utcnow_str()

    # Full dump
    dump_path = OUTPUT_DIR / f"comparison_{ts}.json"
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nFull results: {dump_path}")

    # Individual plan files
    for r in all_results:
        label = r["label"].replace(" ", "_").replace(":", "").replace("(", "").replace(")", "")
        strategy = r.get("strategy_snapshot") or {}
        plan = r.get("plan") or {}
        delivery = r.get("delivery") or {}

        if plan:
            plan_path = OUTPUT_DIR / f"{label}_plan.json"
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump({
                    "label": r["label"],
                    "title": plan.get("title", ""),
                    "premise": plan.get("premise", ""),
                    "theme": plan.get("theme", ""),
                    "beginning": plan.get("beginning", ""),
                    "development": plan.get("development", ""),
                    "climax": plan.get("climax", ""),
                    "ending": plan.get("ending", ""),
                    "episode_outlines": plan.get("episode_outlines", []),
                    "character_portrayals": plan.get("character_portrayals", []),
                    "continuity_constraints": plan.get("continuity_constraints", []),
                    "strategy_snapshot": strategy,
                    "resolved_preferences": r.get("resolved_preferences", {}),
                    "auto_filled_fields": r.get("auto_filled_fields", []),
                    "plan_tokens": r.get("plan_tokens", {}),
                    "error": r.get("error"),
                }, f, ensure_ascii=False, indent=2)

        if delivery:
            delivery_path = OUTPUT_DIR / f"{label}_delivery.json"
            with open(delivery_path, "w", encoding="utf-8") as f:
                json.dump({
                    "label": r["label"],
                    "title": delivery.get("title", ""),
                    "summary": delivery.get("summary", ""),
                    "preview_blurb": delivery.get("preview_blurb", ""),
                    "episodes": delivery.get("episodes", []),
                    "quality_report": delivery.get("quality_report", {}),
                    "ready_for_playback": delivery.get("ready_for_playback", False),
                    "total_tokens": r.get("total_tokens", {}),
                    "error": r.get("error"),
                }, f, ensure_ascii=False, indent=2)

    # Summary Markdown table
    summary_path = OUTPUT_DIR / f"summary_{ts}.md"
    lines = [
        "# Strategy Comparison Summary",
        f"Generated: {ts}",
        f"Model: {MODEL_NAME}",
        f"",
        f"Idea: {CREATIVE_IDEA[:120]}...",
        f"",
        "| # | Label | Mode | Genre | Recipe | Plan Title | Premise | Ready | Tokens In/Out | Error |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(all_results, 1):
        strategy = r.get("strategy_snapshot") or {}
        plan = r.get("plan") or {}
        tokens = r.get("total_tokens") or {}
        lines.append(
            f"| {i} | {r['label']} | {strategy.get('story_mode', '?')} | "
            f"{strategy.get('genre_id', '?')} | {strategy.get('recipe_id', '?')} | "
            f"{plan.get('title', 'ERROR')[:30]} | "
            f"{plan.get('premise', 'ERROR')[:40]}... | "
            f"{r.get('ready_for_playback', '?')} | "
            f"{tokens.get('input', '?')}/{tokens.get('output', '?')} | "
            f"{r.get('error', 'ok')} |"
        )
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Summary: {summary_path}")

    return dump_path


def main():
    print(f"Strategy Comparison -- {MODEL_NAME}")
    print(f"Idea: {CREATIVE_IDEA[:120]}...")
    print(f"Output: {OUTPUT_DIR}")

    all_results = []

    # Case 1: AUTO -- fully automatic
    r1 = run_case("Case1_AUTO_full_auto", CreationPreferences(
        story_mode=StoryModeChoice.AUTO,
        genre="auto",
        target_duration_sec=180,
        target_episodes=1,
    ))
    all_results.append(r1)
    if r1.get("error"):
        print("WARNING: AUTO case failed, continuing with remaining cases...")

    # Case 2: BASELINE -- general mode, no explicit recipe
    r2 = run_case("Case2_BASELINE_general_no_recipe", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL,
        genre="auto",
        recipe_id=None,
        target_duration_sec=180,
        target_episodes=1,
    ))
    all_results.append(r2)

    # Case 3: R4 -- family revenge explicit
    r3 = run_case("Case3_R4_family_revenge", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL,
        genre="G01",
        recipe_id="R4",
        target_duration_sec=180,
        target_episodes=1,
    ))
    all_results.append(r3)

    # Case 4: R5 -- healing romance explicit
    r4 = run_case("Case4_R5_healing_romance", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL,
        genre="G02",
        recipe_id="R5",
        target_duration_sec=180,
        target_episodes=1,
    ))
    all_results.append(r4)

    # Save everything
    save_results(all_results)

    # Print comparison
    print("\n" + "=" * 60)
    print("  COMPARISON SUMMARY")
    print("=" * 60)
    for r in all_results:
        strategy = r.get("strategy_snapshot") or {}
        plan = r.get("plan") or {}
        print(f"\n--- {r['label']} ---")
        print(f"  Strategy: mode={strategy.get('story_mode')}, "
              f"genre={strategy.get('genre_name')}, recipe={strategy.get('recipe_name')}")
        cm = strategy.get('conflict_mechanism', 'N/A')
        print(f"  Conflict: {cm[:100]}...")
        print(f"  Causal:   {strategy.get('causal_driver', 'N/A')[:100]}...")
        print(f"  Ending:   {strategy.get('ending_strategy', 'N/A')[:100]}...")
        print(f"  Plan:     {plan.get('title', 'ERROR')}")
        print(f"  Premise:  {plan.get('premise', 'ERROR')[:120]}...")
        err = r.get('error')
        if err:
            print(f"  Error:    {err}")
        else:
            tokens = r.get('total_tokens', {})
            print(f"  Tokens:   {tokens.get('input', '?')} in / {tokens.get('output', '?')} out")


if __name__ == "__main__":
    main()