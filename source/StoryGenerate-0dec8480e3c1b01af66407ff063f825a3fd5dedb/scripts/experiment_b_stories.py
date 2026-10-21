"""Experiment B: Full story delivery for R4 and R5.

Generates complete 180s audio drama scripts for:
- R4 家庭反击 (same G01 genre, same neutral idea)
- R5 破镜重圆治愈 (same G01 genre, same neutral idea)

The key question: do different recipes produce materially different stories?
"""
from __future__ import annotations

import json, os, sys, time, uuid, traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "4-引擎代码"))

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice,
    CharacterSelection, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator, ApprovedPlanLLMExecutor
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver

# ── Configuration ──────────────────────────────────────────────
OUTPUT_DIR = Path("H:/Projects/StoryGenerate/6-实测产物/acceptance")
SHARED_GENRE = "G01"
NEUTRAL_IDEA = (
    "结婚八年的妻子林敏，在打扫时发现了丈夫锁在抽屉里的一个信封。"
    "里面的内容让她感到被欺骗和背叛。她开始思考：这段婚姻是否值得继续，"
    "自己这些年到底是谁，以及未来该怎么走。"
)
TARGET_SEC = 180
TARGET_EPISODES = 1

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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


def generate_full_story(label, recipe_id, provider):
    """Generate Plan + approve + write full story delivery."""
    print(f"\n{'='*60}")
    print(f"  {label}: recipe={recipe_id}")
    print(f"{'='*60}")

    result = {
        "label": label,
        "recipe_id": recipe_id,
        "timestamp": utcnow_str(),
        "idea": NEUTRAL_IDEA,
        "genre": SHARED_GENRE,
    }
    t0_total = time.monotonic()
    total_tokens = {"input": 0, "output": 0, "calls": 0}

    try:
        prefs = CreationPreferences(
            story_mode=StoryModeChoice.GENERAL,
            genre=SHARED_GENRE,
            recipe_id=recipe_id,
            target_duration_sec=TARGET_SEC,
            target_episodes=TARGET_EPISODES,
        )

        plan_gen = LLMPlanGenerator(provider)
        templates = MemoryStoryTemplateRepository()
        service = StoryWorkflowService(
            InMemoryWorkflowRepository(), plan_gen,
            DummyExecutor(),
            template_resolver=StoryTemplateResolver(templates),
        )

        request = make_request(prefs)
        context = make_context()

        # Step 1: Plan
        t0 = time.monotonic()
        plan_preview = service.prepare_story_plan(request, context)
        plan_elapsed = time.monotonic() - t0
        plan_trace = list(getattr(provider, "call_history", []))
        plan_tokens = {"input": 0, "output": 0}
        for t in plan_trace:
            plan_tokens["input"] += t.get("input_tokens", 0)
            plan_tokens["output"] += t.get("output_tokens", 0)

        result["plan"] = plan_preview.plan.model_dump(mode="json")
        result["plan_strategy"] = plan_preview.strategy_snapshot
        result["plan_elapsed"] = round(plan_elapsed, 1)
        result["plan_tokens"] = plan_tokens
        total_tokens["input"] += plan_tokens["input"]
        total_tokens["output"] += plan_tokens["output"]
        total_tokens["calls"] += 1
        print(f"  [Plan] title='{plan_preview.plan.title}' ({plan_elapsed:.1f}s)")
        print(f"  [Plan] premise: {plan_preview.plan.premise[:120]}...")
        print(f"  [Plan] theme: {plan_preview.plan.theme}")

        # Step 2: Approve with correct parameters
        approve_key = uuid.uuid4().hex
        job = service.approve_story_plan(
            plan_id=plan_preview.plan_id,
            expected_revision=plan_preview.plan_revision,
            fingerprint=plan_preview.plan_fingerprint or plan_preview.plan_id,
            idempotency_key=approve_key,
        )
        result["approved_at"] = job.approved_plan_revision if hasattr(job, "approved_plan_revision") else "ok"
        result["job_id"] = job.job_id
        print(f"  [Approve] plan={plan_preview.plan_id} job={job.job_id}")

        # Step 3: Generate full story
        executor = ApprovedPlanLLMExecutor(provider)
        service.story_executor = executor
        delivery = service.generate_from_approved_plan(job.job_id)
        writer_elapsed = time.monotonic() - t0_total - plan_elapsed
        writer_trace = list(getattr(provider, "call_history", []))[len(plan_trace):]
        writer_tokens = {"input": 0, "output": 0}
        for t in writer_trace:
            writer_tokens["input"] += t.get("input_tokens", 0)
            writer_tokens["output"] += t.get("output_tokens", 0)

        result["writer_elapsed"] = round(writer_elapsed, 1)
        result["writer_tokens"] = writer_tokens
        total_tokens["input"] += writer_tokens["input"]
        total_tokens["output"] += writer_tokens["output"]
        total_tokens["calls"] += 1

        story_text = ""
        for ep in delivery.episodes:
            ep_text = ""
            for scene in ep.scenes:
                for u in scene.utterances:
                    ep_text += f"[{u.speaker_role_id or 'narration'}] {u.text}\n"
            story_text += ep_text
            result.setdefault("episodes", []).append({
                "episode_id": ep.episode_id,
                "title": ep.title,
                "text": ep_text,
            })

        result["story_text"] = story_text
        total_elapsed = time.monotonic() - t0_total
        result["total_elapsed"] = round(total_elapsed, 1)
        result["total_tokens"] = total_tokens

        print(f"  [Writer] {len(delivery.episodes)} episodes, {len(story_text)} chars ({writer_elapsed:.1f}s)")
        print(f"  [Total] {total_elapsed:.1f}s, tokens: {total_tokens}")

        # Save readable story
        path = OUTPUT_DIR / f"{label}_{utcnow_str()}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"=== {label} ===\n")
            f.write(f"Recipe: {recipe_id}  Genre: {SHARED_GENRE}\n")
            f.write(f"Idea: {NEUTRAL_IDEA}\n\n")
            f.write(f"--- Plan ---\n")
            f.write(f"Title: {plan_preview.plan.title}\n")
            f.write(f"Premise: {plan_preview.plan.premise}\n")
            f.write(f"Theme: {plan_preview.plan.theme}\n")
            f.write(f"Beginning: {plan_preview.plan.beginning}\n")
            f.write(f"Development: {plan_preview.plan.development}\n")
            f.write(f"Climax: {plan_preview.plan.climax}\n")
            f.write(f"Ending: {plan_preview.plan.ending}\n\n")
            f.write(f"--- Story ---\n")
            f.write(story_text)
        print(f"  [Save] {path}")

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        print(f"  [ERR] {result['error']}")

    return result


class DummyExecutor:
    def execute(self, snapshot):
        raise NotImplementedError


def main():
    print("Experiment B: Full Story Delivery (R4 vs R5)")
    print(f"Model: {os.environ.get('DRAMA_LLM_MODEL', '?')}")

    provider = build_provider_from_env()
    results = []

    results.append(generate_full_story("B1_R4_family", "R4", provider))
    results.append(generate_full_story("B2_R5_healing", "R5", provider))

    # Save full dump
    ts = utcnow_str()
    dump_path = OUTPUT_DIR / f"story_comparison_{ts}.json"
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nFull dump: {dump_path}")

    # Print comparison
    print("\n" + "=" * 60)
    print("  STORY COMPARISON: R4 vs R5")
    print("=" * 60)
    for r in results:
        plan = r.get("plan") or {}
        err = r.get("error")
        strategy = r.get("plan_strategy") or {}
        tokens = r.get("total_tokens") or {}
        print(f"\n--- {r['label']} ---")
        if err:
            print(f"  ERROR: {err}")
            continue
        print(f"  Recipe: {strategy.get('recipe_name')}")
        print(f"  Conflict: {strategy.get('conflict_mechanism', '')[:120]}")
        print(f"  Causal: {strategy.get('causal_driver', '')[:120]}")
        print(f"  Plan Title: {plan.get('title')}")
        print(f"  Plan Theme: {plan.get('theme')}")
        print(f"  Plan Ending: {plan.get('ending')[:100]}")
        print(f"  Story chars: {len(r.get('story_text', ''))}")
        print(f"  Total time: {r.get('total_elapsed')}s")
        print(f"  Tokens: in={tokens.get('input')} out={tokens.get('output')}")
        # Show first 200 chars of story
        story = r.get("story_text", "")
        if story:
            print(f"  Story preview: {story[:200]}...")


if __name__ == "__main__":
    main()