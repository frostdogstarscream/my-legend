"""Pipeline audit: trace every LLM call, model, tokens, timing.

Generates ONE R4 story with exhaustive call-level logging.
"""
from __future__ import annotations

import json, os, sys, time, uuid
from pathlib import Path

sys.path.insert(0, "4-引擎代码")

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

NEUTRAL_IDEA = (
    "结婚八年的妻子林敏，在打扫时发现了丈夫锁在抽屉里的一个信封。"
    "里面的内容让她感到被欺骗和背叛。她开始思考：这段婚姻是否值得继续，"
    "自己这些年到底是谁，以及未来该怎么走。"
)

print("=" * 60)
print("PIPELINE AUDIT: Full token breakdown")
print(f"Model: {os.environ.get('DRAMA_LLM_MODEL', '?')}")
print("=" * 60)

provider = build_provider_from_env()
provider.call_history = []  # Reset call tracking

plan_gen = LLMPlanGenerator(provider)
templates = MemoryStoryTemplateRepository()
executor = ApprovedPlanLLMExecutor(provider)
service = StoryWorkflowService(
    InMemoryWorkflowRepository(), plan_gen, executor,
    template_resolver=StoryTemplateResolver(templates),
)

request = CreateStoryRequest(
    request_id=uuid.uuid4().hex,
    idempotency_key=uuid.uuid4().hex,
    user_instruction=NEUTRAL_IDEA,
    creation_preferences=CreationPreferences(
        story_mode=StoryModeChoice.GENERAL, genre="G01", recipe_id="R4",
        target_duration_sec=180, target_episodes=1,
    ),
    characters=CharacterSelection(
        selected_character_ids=["protagonist"],
        role_bindings=[{"character_id": "protagonist", "story_role_id": "protagonist", "performer_id": "protagonist"}],
    ),
)
context = OperationContext(character_snapshots={
    "protagonist": {
        "canon": {"name": "林敏", "immutable_facts": ["三十五岁", "结婚八年"]},
        "profile": {"goal": "找到自己的人生方向", "motivation": "不愿继续活在欺骗中", "fear": "无法独立生活"},
        "profile_revision": 1,
    },
})

# ── Stage 1: Plan ──
t0 = time.monotonic()
provider.call_history.clear()  # Track only this stage
plan = service.prepare_story_plan(request, context)
plan_elapsed = time.monotonic() - t0
plan_calls = list(provider.call_history)

print(f"\n── Stage 1: Plan ({plan_elapsed:.1f}s) ──")
for i, c in enumerate(plan_calls):
    print(f"  Call {i+1}: model={c.get('model','?')} "
          f"in={c.get('input_tokens',c.get('prompt_tokens','?'))} "
          f"out={c.get('output_tokens',c.get('completion_tokens','?'))} "
          f"reasoning={c.get('reasoning_tokens',0)} "
          f"cached={c.get('cached_tokens',0)}")

plan_total_in = sum(c.get("input_tokens", c.get("prompt_tokens", 0)) for c in plan_calls)
plan_total_out = sum(c.get("output_tokens", c.get("completion_tokens", 0)) for c in plan_calls)
plan_reasoning = sum(c.get("reasoning_tokens", 0) for c in plan_calls)
print(f"  Plan TOTAL: in={plan_total_in} out={plan_total_out} reasoning={plan_reasoning}")

# ── Stage 2: Approve ──
job = service.approve_story_plan(
    plan_id=plan.plan_id, expected_revision=plan.plan_revision,
    fingerprint=plan.plan_fingerprint or plan.plan_id,
    idempotency_key=uuid.uuid4().hex,
)

# ── Stage 3: Writer ──
t0 = time.monotonic()
provider.call_history.clear()
delivery = service.generate_from_approved_plan(job.job_id)
writer_elapsed = time.monotonic() - t0
writer_calls = list(provider.call_history)

print(f"\n── Stage 2: Writer + Annotation ({writer_elapsed:.1f}s) ──")
for i, c in enumerate(writer_calls):
    print(f"  Call {i+1}: model={c.get('model','?')} "
          f"in={c.get('input_tokens',c.get('prompt_tokens','?'))} "
          f"out={c.get('output_tokens',c.get('completion_tokens','?'))} "
          f"reasoning={c.get('reasoning_tokens',0)} "
          f"cached={c.get('cached_tokens',0)}")

writer_total_in = sum(c.get("input_tokens", c.get("prompt_tokens", 0)) for c in writer_calls)
writer_total_out = sum(c.get("output_tokens", c.get("completion_tokens", 0)) for c in writer_calls)
writer_reasoning = sum(c.get("reasoning_tokens", 0) for c in writer_calls)
print(f"  Writer TOTAL: in={writer_total_in} out={writer_total_out} reasoning={writer_reasoning}")

# ── Summary ──
grand_in = plan_total_in + writer_total_in
grand_out = plan_total_out + writer_total_out
grand_reasoning = plan_reasoning + writer_reasoning
total_calls = len(plan_calls) + len(writer_calls)
total_time = plan_elapsed + writer_elapsed

print(f"\n── PIPELINE SUMMARY ──")
print(f"  Total calls: {total_calls}")
print(f"  Plan: {len(plan_calls)} call(s), {plan_elapsed:.1f}s")
print(f"  Writer+Annotator: {len(writer_calls)} call(s), {writer_elapsed:.1f}s")
print(f"  Grand input:  {grand_in:,} tokens")
print(f"  Grand output: {grand_out:,} tokens")
print(f"  Grand reasoning: {grand_reasoning:,} tokens")
print(f"  Grand total (incl. reasoning): {grand_in + grand_out + grand_reasoning:,} tokens")
print(f"  Grand duration: {total_time:.1f}s")

# ── Story stats ──
story_text = ""
for ep in delivery.episodes:
    for scene in ep.scenes:
        for u in scene.utterances:
            story_text += u.text
char_count = len(story_text)
lines = sum(1 for c in story_text if c in "。！？\n")
est_sec = char_count / 3.5  # rough estimate for Chinese
print(f"\n── STORY STATS ──")
print(f"  Characters: {char_count}")
print(f"  Estimated duration: {est_sec:.0f}s (target: 180s)")
print(f"  Title: {plan.plan.title}")
print(f"  Premise: {plan.plan.premise[:100]}...")
print(f"  Ending: {plan.plan.ending[:80]}...")

# Save detailed trace
out = {
    "plan_calls": [dict(c) for c in plan_calls],
    "writer_calls": [dict(c) for c in writer_calls],
    "plan_tokens": {"in": plan_total_in, "out": plan_total_out, "reasoning": plan_reasoning},
    "writer_tokens": {"in": writer_total_in, "out": writer_total_out, "reasoning": writer_reasoning},
    "grand_total": {"in": grand_in, "out": grand_out, "reasoning": grand_reasoning,
                     "total_with_reasoning": grand_in + grand_out + grand_reasoning},
    "timing": {"plan_s": round(plan_elapsed,1), "writer_s": round(writer_elapsed,1), "total_s": round(total_time,1)},
    "story": {"chars": char_count, "est_sec": round(est_sec, 0), "title": plan.plan.title},
}
Path("6-实测产物/acceptance").mkdir(parents=True, exist_ok=True)
with open("6-实测产物/acceptance/pipeline_audit.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\nSaved: 6-实测产物/acceptance/pipeline_audit.json")