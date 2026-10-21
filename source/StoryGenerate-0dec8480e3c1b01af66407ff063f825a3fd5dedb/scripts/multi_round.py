"""Multi-round R4/R5 comparison: rounds 2 and 3.

Each round: same idea, same G01, same model, only recipe_id changes.
Saves readable story text + full call trace + plan details.
"""
from __future__ import annotations

import json, os, sys, time, uuid, traceback
from pathlib import Path

sys.path.insert(0, "4-引擎代码")

from drama_engine.llm.openai_compat import build_provider_from_env, OpenAICompatProvider
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice,
    CharacterSelection, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator, ApprovedPlanLLMExecutor
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver

OUT = Path("6-实测产物/acceptance")
OUT.mkdir(parents=True, exist_ok=True)

NEUTRAL_IDEA = (
    "结婚八年的妻子林敏，在打扫时发现了丈夫锁在抽屉里的一个信封。"
    "里面的内容让她感到被欺骗和背叛。她开始思考：这段婚姻是否值得继续，"
    "自己这些年到底是谁，以及未来该怎么走。"
)


def make_request():
    return CreateStoryRequest(
        request_id=uuid.uuid4().hex, idempotency_key=uuid.uuid4().hex,
        user_instruction=NEUTRAL_IDEA,
        creation_preferences=CreationPreferences(
            story_mode=StoryModeChoice.GENERAL, genre="G01",
            target_duration_sec=180, target_episodes=1,
        ),
        characters=CharacterSelection(
            selected_character_ids=["protagonist"],
            role_bindings=[{"character_id": "protagonist", "story_role_id": "protagonist", "performer_id": "protagonist"}],
        ),
    )


def make_context():
    return OperationContext(character_snapshots={
        "protagonist": {
            "canon": {"name": "林敏", "immutable_facts": ["三十五岁", "结婚八年"]},
            "profile": {"goal": "找到自己的人生方向", "motivation": "不愿继续活在欺骗中", "fear": "无法独立生活"},
            "profile_revision": 1,
        },
    })


def trace_tokens(call_history: list) -> dict:
    """Extract token totals from provider call history."""
    in_tok = out_tok = reason = 0
    for c in call_history:
        in_tok += c.get("input_tokens", c.get("prompt_tokens", 0))
        out_tok += c.get("output_tokens", c.get("completion_tokens", 0))
        reason += c.get("reasoning_tokens", 0)
    return {"input": in_tok, "output": out_tok, "reasoning": reason, "calls": len(call_history)}


def generate(recipe_id: str, round_label: str) -> dict:
    """Generate one complete story. Returns dict with all data."""
    label = f"{round_label}_{recipe_id}"
    print(f"\n{'─'*50}\n  {label}\n{'─'*50}")

    result = {"label": label, "recipe_id": recipe_id, "round": round_label, "genre": "G01"}
    t_start = time.monotonic()

    try:
        provider = build_provider_from_env()
        provider.call_history = []
        plan_gen = LLMPlanGenerator(provider)
        templates = MemoryStoryTemplateRepository()
        executor = ApprovedPlanLLMExecutor(provider)
        service = StoryWorkflowService(
            InMemoryWorkflowRepository(), plan_gen, executor,
            template_resolver=StoryTemplateResolver(templates),
        )

        request = make_request()
        request.creation_preferences.recipe_id = recipe_id
        context = make_context()

        # Plan
        t0 = time.monotonic()
        ch_before = len(getattr(provider, "call_history", []))
        plan = service.prepare_story_plan(request, context)
        ch_after = len(getattr(provider, "call_history", []))
        plan_calls = list(provider.call_history)[ch_before:ch_after]
        plan_tokens = trace_tokens(plan_calls)
        plan_elapsed = time.monotonic() - t0

        result["plan"] = {
            "title": plan.plan.title, "premise": plan.plan.premise, "theme": plan.plan.theme,
            "beginning": plan.plan.beginning, "development": plan.plan.development,
            "climax": plan.plan.climax, "ending": plan.plan.ending,
            "elapsed_s": round(plan_elapsed, 1), "tokens": plan_tokens,
            "portrayals": [{"role_id": p.role_id, "story_name": p.story_name, "goal": p.external_goal}
                          for p in plan.plan.character_portrayals],
        }
        print(f"  Plan: '{plan.plan.title}' ({plan_elapsed:.0f}s) "
              f"in={plan_tokens['input']} out={plan_tokens['output']}")

        if plan_tokens["input"] == 0:
            result["error"] = "Plan produced no tokens — API failure"
            return result

        # Approve
        job = service.approve_story_plan(
            plan_id=plan.plan_id, expected_revision=plan.plan_revision,
            fingerprint=plan.plan_fingerprint or plan.plan_id,
            idempotency_key=uuid.uuid4().hex,
        )

        # Writer + Annotation
        t0 = time.monotonic()
        ch_before = len(getattr(provider, "call_history", []))
        delivery = service.generate_from_approved_plan(job.job_id)
        ch_after = len(getattr(provider, "call_history", []))
        writer_calls = list(provider.call_history)[ch_before:ch_after]
        writer_tokens = trace_tokens(writer_calls)
        writer_elapsed = time.monotonic() - t0

        # Extract story text
        story_text = ""
        for ep in delivery.episodes:
            for scene in ep.scenes:
                for u in scene.utterances:
                    speaker = u.speaker_role_id or u.narrator_id or "narration"
                    story_text += f"[{speaker}] {u.text}\n"
        char_count = len(story_text)

        result["writer"] = {
            "elapsed_s": round(writer_elapsed, 1), "tokens": writer_tokens,
            "episodes": len(delivery.episodes), "chars": char_count,
            "est_duration_sec": round(char_count / 3.5, 0),
        }
        result["total"] = {
            "elapsed_s": round(time.monotonic() - t_start, 1),
            "tokens": {
                "input": plan_tokens["input"] + writer_tokens["input"],
                "output": plan_tokens["output"] + writer_tokens["output"],
                "reasoning": plan_tokens["reasoning"] + writer_tokens["reasoning"],
                "calls": plan_tokens["calls"] + writer_tokens["calls"],
            },
        }
        result["story_text"] = story_text

        print(f"  Writer: {char_count} chars, ~{round(char_count/3.5)}s "
              f"({writer_elapsed:.0f}s) calls={writer_tokens['calls']}")

        # Save readable story
        text_path = OUT / f"{label}_story.txt"
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(f"=== {label} ===\n")
            f.write(f"Recipe: {recipe_id}  Genre: G01  Model: {os.environ.get('DRAMA_LLM_MODEL','?')}\n\n")
            f.write(f"--- Plan ---\n")
            f.write(f"Title: {plan.plan.title}\nPremise: {plan.plan.premise}\nTheme: {plan.plan.theme}\n")
            f.write(f"Beginning: {plan.plan.beginning}\nDevelopment: {plan.plan.development}\n")
            f.write(f"Climax: {plan.plan.climax}\nEnding: {plan.plan.ending}\n\n")
            f.write(f"--- Story ---\n{story_text}")
        print(f"  Saved: {text_path}")

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        result["traceback"] = traceback.format_exc()[-500:]
        print(f"  [ERR] {result['error']}")

    return result


def main():
    print("Multi-round R4/R5 Comparison")
    print(f"Model: {os.environ.get('DRAMA_LLM_MODEL', '?')}")
    print(f"Idea: {NEUTRAL_IDEA[:80]}...")

    results = []

    # Round 2
    results.append(generate("R4", "R2"))
    results.append(generate("R5", "R2"))

    # Round 3
    results.append(generate("R4", "R3"))
    results.append(generate("R5", "R3"))

    # Save full dump
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    dump = OUT / f"multi_round_{ts}.json"
    with open(dump, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nFull dump: {dump}")

    # Summary table
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Label':<15} {'Title':<20} {'Plan':>6} {'Writer':>7} {'Total':>7} {'Tokens':>12} {'Dur':>6}")
    for r in results:
        plan = r.get("plan") or {}
        writer = r.get("writer") or {}
        total = r.get("total") or {}
        err = r.get("error", "")
        if err:
            print(f"{r['label']:<15} {'ERR:'+err[:30]:<20}")
        else:
            tok = total.get("tokens", {})
            print(f"{r['label']:<15} {plan.get('title','?')[:18]:<20} "
                  f"{plan.get('elapsed_s','?'):>5}s {writer.get('elapsed_s','?'):>6}s "
                  f"{total.get('elapsed_s','?'):>6}s "
                  f"{tok.get('input',0)+tok.get('output',0):>7}/{tok.get('calls',0)}c "
                  f"{writer.get('est_duration_sec','?'):>5}s")


if __name__ == "__main__":
    main()