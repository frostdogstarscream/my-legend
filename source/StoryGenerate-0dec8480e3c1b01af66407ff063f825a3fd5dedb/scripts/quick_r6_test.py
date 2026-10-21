"""R6 real-model verification — uses proven pipeline_audit.py pattern."""
import json, os, sys, time, uuid
from pathlib import Path

sys.path.insert(0, "4-引擎代码")
PROJECT = Path(r'H:\Projects\StoryGenerate')
OUT_DIR = PROJECT / '6-实测产物' / 'acceptance' / 'r6_r13_verification'
os.makedirs(OUT_DIR, exist_ok=True)

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice,
    CharacterSelection, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator, ApprovedPlanLLMExecutor
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.workflow.delivery import export_delivery_bundle, verify_round_trip, delivery_to_readable_script

IDEA = (
    "一个退休刑警在整理旧案时发现了一桩二十年前的失踪案与他最信任的搭档有关。"
)

print("=" * 60)
print("R6 悬疑反转 — 真实模型验证")
print(f"Model: {os.environ.get('DRAMA_LLM_MODEL', '?')}")
print(f"创意: {IDEA}")
print("=" * 60)

provider = build_provider_from_env()
provider.call_history = []

plan_gen = LLMPlanGenerator(provider)
executor = ApprovedPlanLLMExecutor(provider)
service = StoryWorkflowService(
    InMemoryWorkflowRepository(), plan_gen, executor,
)

start = time.time()

request = CreateStoryRequest(
    request_id=uuid.uuid4().hex,
    idempotency_key=uuid.uuid4().hex,
    user_instruction=IDEA,
    creation_preferences=CreationPreferences(
        story_mode=StoryModeChoice.MYSTERY,
        genre="G11",  # 悬疑推理
        recipe_id="R6",  # 悬疑反转
        target_duration_sec=180,
        target_episodes=1,
    ),
)
context = OperationContext()

try:
    plan = service.prepare_story_plan(request, context)
    elapsed_plan = time.time() - start
    print(f'\nPlan 完成 ({elapsed_plan:.0f}s)')
    plan_path = OUT_DIR / 'A_R6_plan.json'
    plan_path.write_text(json.dumps(plan.model_dump(mode='json'), ensure_ascii=False, indent=2), encoding='utf-8')

    job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, 'r6-verify')
    print(f'Approve: job_id={job.job_id}')

    delivery = service.generate_from_approved_plan(job.job_id)
    elapsed_total = time.time() - start
    print(f'Generate 完成 ({elapsed_total:.0f}s)')

    rt_ok = verify_round_trip(delivery)
    print(f'Round-trip: {"OK" if rt_ok else "FAIL"}')

    export_delivery_bundle(delivery, str(OUT_DIR), 'A_R6')

    total_utts = sum(len(scene.utterances) for ep in delivery.episodes for scene in ep.scenes)
    duration = sum(sum(u.duration_estimate_sec or 0 for u in scene.utterances) for ep in delivery.episodes for scene in ep.scenes)
    print(f'{len(delivery.episodes)} 集 / {total_utts} 句 / ~{duration:.0f}s')

    # Token breakdown
    total_in = sum(c.get('usage', {}).get('prompt_tokens', 0) for c in provider.call_history)
    total_out = sum(c.get('usage', {}).get('completion_tokens', 0) for c in provider.call_history)
    total_reason = sum(c.get('usage', {}).get('completion_tokens_details', {}).get('reasoning_tokens', 0) for c in provider.call_history)
    print(f'Tokens: in={total_in} out={total_out} reasoning={total_reason} total={total_in+total_out+total_reason}')
    print(f'LLM calls: {len(provider.call_history)}')

    script_path = OUT_DIR / 'A_R6_story.txt'
    script_path.write_text(delivery_to_readable_script(delivery), encoding='utf-8')
    print(f'\nOK: R6 verification succeeded')
    print(f'Plan: {plan_path}')
    print(f'Script: {script_path}')

except Exception as e:
    elapsed = time.time() - start
    print(f'\nFAIL ({elapsed:.0f}s): {e}')
    import traceback
    traceback.print_exc()