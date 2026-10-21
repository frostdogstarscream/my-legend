"""Controlled strategy comparison: same genre, same idea, different recipes.

Key controls:
- Same genre (G01) for R4 vs R5
- Same creative idea (neutral, no pre-determined resolution)
- True no-recipe baseline (strategy=None)
- Model controlled via DRAMA_LLM_MODEL env var
- Full data logging: tokens, timing, errors, call traces
"""
from __future__ import annotations

import json, os, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "4-引擎代码"))

from drama_engine.llm.openai_compat import OpenAICompatProvider, build_provider_from_env
from drama_engine.llm.router import resolve_spec
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice, PlanContent,
    CharacterSelection, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver

# ── Configuration ──────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "6-实测产物" / "acceptance"
SHARED_GENRE = "G01"  # 现实家庭冲突 — same for both R4 and R5
NEUTRAL_IDEA = (
    "结婚八年的妻子林敏，在打扫时发现了丈夫锁在抽屉里的一个信封。"
    "里面的内容让她感到被欺骗和背叛。她开始思考：这段婚姻是否值得继续，"
    "自己这些年到底是谁，以及未来该怎么走。"
)
TARGET_SEC = 180
TARGET_EPISODES = 1


def utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def build_provider():
    """Build provider from environment. Must have DRAMA_LLM_API_KEY set."""
    return build_provider_from_env()


def make_context():
    return OperationContext(character_snapshots={
        "protagonist": {
            "canon": {"name": "林敏", "immutable_facts": ["三十五岁", "结婚八年"]},
            "profile": {
                "goal": "找到自己的人生方向",
                "motivation": "不愿继续活在欺骗中",
                "fear": "无法独立生活",
            },
            "profile_revision": 1,
        },
    })


def make_request(prefs):
    return CreateStoryRequest(
        request_id=uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex,
        user_instruction=NEUTRAL_IDEA,
        creation_preferences=prefs,
        characters=CharacterSelection(
            selected_character_ids=["protagonist"],
            role_bindings=[{
                "character_id": "protagonist",
                "story_role_id": "protagonist",
                "performer_id": "protagonist",
            }],
        ),
    )


def run_plan(label, prefs, provider, use_strategy=True):
    """Generate a Plan. Returns dict with all results and metadata."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Mode={prefs.story_mode}  Genre={prefs.genre}  Recipe={prefs.recipe_id}")
    print(f"{'='*60}")

    result = {
        "label": label,
        "timestamp": utcnow_str(),
        "preferences": prefs.model_dump(mode="json"),
        "idea": NEUTRAL_IDEA,
        "shared_genre": SHARED_GENRE,
    }

    try:
        plan_gen = LLMPlanGenerator(provider)
        templates = MemoryStoryTemplateRepository()
        service = StoryWorkflowService(
            InMemoryWorkflowRepository(), plan_gen, DummyExecutor(),
            template_resolver=StoryTemplateResolver(templates),
        )

        request = make_request(prefs)
        context = make_context()

        t0 = time.monotonic()

        if use_strategy:
            plan = service.prepare_story_plan(request, context)
        else:
            # Baseline: no strategy, call generator directly
            plan_gen.lib = service._lib
            content = plan_gen.generate(request, None, [], strategy=None)
            plan = service.prepare_story_plan(request, context)
            # Override plan with baseline content
            # (we still go through prepare for validation but this is
            #  the cleanest way to get a no-strategy baseline)
            plan = plan.model_copy(update={
                "plan": content,
                "strategy_snapshot": None,
            })

        elapsed = time.monotonic() - t0

        p = plan.plan
        strategy = plan.strategy_snapshot or {}

        trace = plan.model_trace
        total_in = sum(t.get("input_tokens", 0) for t in trace)
        total_out = sum(t.get("output_tokens", 0) for t in trace)

        result["plan"] = p.model_dump(mode="json")
        result["strategy_snapshot"] = strategy
        result["elapsed_sec"] = round(elapsed, 1)
        result["tokens"] = {"input": total_in, "output": total_out, "calls": len(trace)}
        result["plan_id"] = plan.plan_id
        result["auto_filled"] = plan.auto_filled_fields

        print(f"  [OK] {p.title}")
        print(f"  [OK] Premise: {p.premise[:100]}...")
        print(f"  [OK] Theme: {p.theme}")
        if strategy:
            print(f"  [OK] Strategy: mode={strategy.get('story_mode')} "
                  f"genre={strategy.get('genre_id')} recipe={strategy.get('recipe_id')}")
        print(f"  [OK] Portrayals: {len(p.character_portrayals)}")
        print(f"  [OK] Elapsed: {elapsed:.1f}s  Tokens: {total_in}/{total_out}")

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["error_type"] = type(exc).__name__
        print(f"  [ERR] {result['error']}")

    return result


class DummyExecutor:
    def execute(self, snapshot):
        raise NotImplementedError


def save_results(results):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = utcnow_str()

    # Full dump
    path = OUTPUT_DIR / f"plan_comparison_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nFull dump: {path}")

    # Individual readable plans
    for r in results:
        label = r["label"].replace(" ", "_")
        plan = r.get("plan") or {}
        if plan and not r.get("error"):
            plan_path = OUTPUT_DIR / f"{label}_plan.txt"
            with open(plan_path, "w", encoding="utf-8") as f:
                f.write(f"=== {r['label']} ===\n")
                f.write(f"Title: {plan.get('title')}\n")
                f.write(f"Premise: {plan.get('premise')}\n")
                f.write(f"Theme: {plan.get('theme')}\n\n")
                f.write(f"Beginning: {plan.get('beginning')}\n")
                f.write(f"Development: {plan.get('development')}\n")
                f.write(f"Climax: {plan.get('climax')}\n")
                f.write(f"Ending: {plan.get('ending')}\n\n")
                for ep in plan.get("episode_outlines", []):
                    f.write(f"--- Episode {ep.get('index')} ---\n")
                    f.write(f"Goal: {ep.get('core_goal')}\n")
                    f.write(f"Beats: {json.dumps(ep.get('major_beats'), ensure_ascii=False)}\n")
                    f.write(f"Ending: {ep.get('ending')}\n\n")
                for cp in plan.get("character_portrayals", []):
                    f.write(f"--- {cp.get('story_name')} ---\n")
                    f.write(f"Goal: {cp.get('external_goal', '')}\n")
                    f.write(f"Avoidance: {cp.get('emotional_avoidance', '')}\n")
                    f.write(f"Speech: {cp.get('speech_style', '')}\n\n")
            print(f"Readable plan: {plan_path}")

    # Summary
    summary_path = OUTPUT_DIR / f"summary_{ts}.md"
    lines = [
        "# Plan Comparison Summary",
        f"Timestamp: {ts}",
        f"Model: {os.environ.get('DRAMA_LLM_MODEL', '?')}",
        f"Genre: {SHARED_GENRE}",
        f"Idea: {NEUTRAL_IDEA[:100]}...",
        "",
        "| Label | Mode | Recipe | Plan Title | Theme | Portrayals | Tokens | Error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        strategy = r.get("strategy_snapshot") or {}
        plan = r.get("plan") or {}
        tokens = r.get("tokens") or {}
        err = r.get("error", "")
        lines.append(
            f"| {r['label']} | {strategy.get('story_mode', 'NONE')} | "
            f"{strategy.get('recipe_id', 'none')} | "
            f"{plan.get('title', 'ERR')[:25]} | "
            f"{plan.get('theme', 'N/A')[:25]} | "
            f"{len(plan.get('character_portrayals', []))} | "
            f"{tokens.get('input','?')}/{tokens.get('output','?')} | "
            f"{err[:30] if err else 'ok'} |"
        )
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Summary: {summary_path}")


def main():
    print(f"Controlled Strategy Comparison")
    print(f"Model: {os.environ.get('DRAMA_LLM_MODEL', 'NOT SET')}")
    print(f"Genre: {SHARED_GENRE} (shared across all cases)")
    print(f"Idea: {NEUTRAL_IDEA[:100]}...")

    provider = build_provider()

    results = []

    # Case 1: AUTO — system auto-detects
    results.append(run_plan("A1_AUTO", CreationPreferences(
        story_mode=StoryModeChoice.AUTO, genre="auto",
        target_duration_sec=TARGET_SEC, target_episodes=TARGET_EPISODES,
    ), provider, use_strategy=True))

    # Case 2: BASELINE — no recipe mechanisms at all
    # We generate with strategy=None to produce a true no-recipe baseline
    results.append(run_plan("A2_BASELINE_no_strategy", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre=SHARED_GENRE,
        target_duration_sec=TARGET_SEC, target_episodes=TARGET_EPISODES,
    ), provider, use_strategy=False))

    # Case 3: R4 家庭反击 — same genre G01
    results.append(run_plan("A3_R4_family_revenge", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre=SHARED_GENRE, recipe_id="R4",
        target_duration_sec=TARGET_SEC, target_episodes=TARGET_EPISODES,
    ), provider, use_strategy=True))

    # Case 4: R5 破镜重圆治愈 — same genre G01
    results.append(run_plan("A4_R5_healing", CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre=SHARED_GENRE, recipe_id="R5",
        target_duration_sec=TARGET_SEC, target_episodes=TARGET_EPISODES,
    ), provider, use_strategy=True))

    save_results(results)

    # Print comparison
    print("\n" + "=" * 60)
    print("  PLAN COMPARISON")
    print("=" * 60)
    for r in results:
        plan = r.get("plan") or {}
        err = r.get("error")
        strategy = r.get("strategy_snapshot") or {}
        print(f"\n--- {r['label']} ---")
        if err:
            print(f"  ERROR: {err}")
            continue
        print(f"  Strategy: recipe={strategy.get('recipe_name', 'none')}")
        print(f"  Conflict: {strategy.get('conflict_mechanism', 'N/A')[:100]}")
        print(f"  Causal:   {strategy.get('causal_driver', 'N/A')[:100]}")
        print(f"  Ending:   {strategy.get('ending_strategy', 'N/A')[:100]}")
        print(f"  Title:    {plan.get('title')}")
        print(f"  Theme:    {plan.get('theme')}")
        print(f"  Premise:  {plan.get('premise')[:120]}")
        print(f"  Beginning:{plan.get('beginning')[:80]}...")
        print(f"  Climax:   {plan.get('climax')[:80]}...")
        print(f"  Ending:   {plan.get('ending')[:80]}...")


if __name__ == "__main__":
    main()