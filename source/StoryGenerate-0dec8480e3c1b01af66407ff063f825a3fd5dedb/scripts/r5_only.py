"""Quick R5-only story generation to complete the acceptance pair."""
from __future__ import annotations

import json, os, sys, time, uuid, traceback
from datetime import datetime, timezone
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

OUTPUT_DIR = Path("6-实测产物/acceptance")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NEUTRAL_IDEA = (
    "结婚八年的妻子林敏，在打扫时发现了丈夫锁在抽屉里的一个信封。"
    "里面的内容让她感到被欺骗和背叛。她开始思考：这段婚姻是否值得继续，"
    "自己这些年到底是谁，以及未来该怎么走。"
)

t0_total = time.monotonic()
provider = build_provider_from_env()
plan_gen = LLMPlanGenerator(provider)
templates = MemoryStoryTemplateRepository()
service = StoryWorkflowService(
    InMemoryWorkflowRepository(), plan_gen,
    ApprovedPlanLLMExecutor(provider),  # Real executor from start
    template_resolver=StoryTemplateResolver(templates),
)

request = CreateStoryRequest(
    request_id=uuid.uuid4().hex,
    idempotency_key=uuid.uuid4().hex,
    user_instruction=NEUTRAL_IDEA,
    creation_preferences=CreationPreferences(
        story_mode=StoryModeChoice.GENERAL,
        genre="G01",
        recipe_id="R5",
        target_duration_sec=180,
        target_episodes=1,
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

print("R5 Plan...")
plan = service.prepare_story_plan(request, context)
print(f"  Title: {plan.plan.title}")
print(f"  Theme: {plan.plan.theme}")

print("R5 Approve...")
job = service.approve_story_plan(
    plan_id=plan.plan_id,
    expected_revision=plan.plan_revision,
    fingerprint=plan.plan_fingerprint or plan.plan_id,
    idempotency_key=uuid.uuid4().hex,
)
print(f"  Job: {job.job_id}")

print("R5 Writing...")
t1 = time.monotonic()
delivery = service.generate_from_approved_plan(job.job_id)
elapsed = time.monotonic() - t1
total_elapsed = time.monotonic() - t0_total

story_text = ""
for ep in delivery.episodes:
    for scene in ep.scenes:
        for u in scene.utterances:
            speaker = u.speaker_role_id or u.narrator_id or "narration"
            story_text += f"[{speaker}] {u.text}\n"

path = OUTPUT_DIR / f"R5_healing_story.txt"
with open(path, "w", encoding="utf-8") as f:
    f.write(f"=== R5 破镜重圆治愈 ===\n")
    f.write(f"Recipe: R5  Genre: G01\n\n")
    f.write(f"--- Plan ---\n")
    f.write(f"Title: {plan.plan.title}\n")
    f.write(f"Premise: {plan.plan.premise}\n")
    f.write(f"Theme: {plan.plan.theme}\n")
    f.write(f"Beginning: {plan.plan.beginning}\n")
    f.write(f"Development: {plan.plan.development}\n")
    f.write(f"Climax: {plan.plan.climax}\n")
    f.write(f"Ending: {plan.plan.ending}\n\n")
    f.write(f"--- Story ---\n")
    f.write(story_text)

print(f"  Story: {len(story_text)} chars, {len(delivery.episodes)} episodes")
print(f"  Writer: {elapsed:.0f}s  Total: {total_elapsed:.0f}s")
print(f"  Saved: {path}")