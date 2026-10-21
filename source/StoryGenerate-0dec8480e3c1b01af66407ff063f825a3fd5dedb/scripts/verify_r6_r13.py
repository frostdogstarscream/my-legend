"""Batch R6-R13 real-model verification — 4 comparison pairs + R9 + R10.

Pattern: proven pipeline_audit.py setup (LLMPlanGenerator + ApprovedPlanLLMExecutor).
"""
import json, os, sys, time, uuid
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, "4-引擎代码")
PROJECT = Path(os.environ.get("VERIFY_OUT", __file__)).resolve().parent.parent
OUT_DIR = PROJECT / '6-实测产物' / 'acceptance' / 'r6_r13_verification'
os.makedirs(OUT_DIR, exist_ok=True)

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator, ApprovedPlanLLMExecutor
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.workflow.delivery import export_delivery_bundle, verify_round_trip, delivery_to_readable_script

# ── Cases ──────────────────────────────────────────────────────────────────

COMPARISONS = [
    {
        "id": "C1_R6_vs_R11",
        "idea": "一个退休刑警在整理旧案时发现了一桩二十年前的失踪案与他最信任的搭档有关。",
        "mode": "mystery",
        "genre": "G11",
        "recipe_a": "R6",
        "recipe_b": "R11",
        "note": "R6悬疑反转 vs R11先婚后爱 on 悬疑题材",
    },
    {
        "id": "C2_R7_vs_R13",
        "idea": "被公司以'能力不足'为由辞退的中年程序员，拿着赔偿金和同事的源代码，在共享办公室里开发出颠覆原公司的产品。",
        "mode": "viral_drama",
        "genre": "G14",
        "recipe_a": "R7",
        "recipe_b": "R13",
        "note": "R7逆袭打脸 vs R13职场商战",
    },
    {
        "id": "C3_R8_vs_R6",
        "idea": "高考后，两个被调换身份的女生同时收到了对方的录取通知书——一个该去清华，一个该去职校。",
        "mode": "general",
        "genre": "G11",
        "recipe_a": "R8",
        "recipe_b": "R6",
        "note": "R8身份反转 vs R6悬疑反转 on 身份错位题材",
    },
    {
        "id": "C4_R4_vs_R12",
        "idea": "离婚冷静期的最后一天，妻子在收拾行李时发现了丈夫藏在衣柜夹层里的器官捐献登记卡——器官接收人是她。",
        "mode": "general",
        "genre": "G01",
        "recipe_a": "R4",
        "recipe_b": "R12",
        "note": "R4家庭反击 vs R12替身虐恋 on 高情感创意",
    },
]

STANDALONE = [
    {
        "id": "S_R9",
        "idea": "九十年代的下岗女工凭借一手缝纫手艺，在市场经济浪潮中撑起一个家。",
        "mode": "general",
        "genre": "G13",
        "recipe_id": "R9",
        "note": "R9年代奋斗验证",
    },
    {
        "id": "S_R10",
        "idea": "普通上班族在地铁事故中获得读心能力，却发现每个同事都在隐瞒会改变他人生的秘密。",
        "mode": "viral_drama",
        "genre": "G12",
        "recipe_id": "R10",
        "note": "R10异能觉醒验证",
    },
]


def generate_one(provider, case_id, idea, mode, genre, recipe_id):
    """Generate one story and return dict with results."""
    provider.call_history = []
    plan_gen = LLMPlanGenerator(provider)
    executor = ApprovedPlanLLMExecutor(provider)
    service = StoryWorkflowService(InMemoryWorkflowRepository(), plan_gen, executor)

    t0 = time.time()
    result = {"case": case_id, "recipe": recipe_id, "status": "unknown"}

    try:
        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction=idea,
            creation_preferences=CreationPreferences(
                story_mode=mode, genre=genre, recipe_id=recipe_id,
                target_duration_sec=180, target_episodes=1,
            ),
        )

        plan = service.prepare_story_plan(request, OperationContext())
        result["plan_sec"] = round(time.time() - t0)

        plan_path = OUT_DIR / f"{case_id}_{recipe_id}_plan.json"
        plan_path.write_text(json.dumps(plan.model_dump(mode='json'), ensure_ascii=False, indent=2), encoding='utf-8')
        result["plan_file"] = str(plan_path)

        job = service.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, f'{case_id}-{recipe_id}')
        delivery = service.generate_from_approved_plan(job.job_id)

        result["total_sec"] = round(time.time() - t0)
        result["round_trip"] = "OK" if verify_round_trip(delivery) else "FAIL"

        export_delivery_bundle(delivery, str(OUT_DIR), f"{case_id}_{recipe_id}")

        script = delivery_to_readable_script(delivery)
        script_path = OUT_DIR / f"{case_id}_{recipe_id}_story.txt"
        script_path.write_text(script, encoding='utf-8')
        result["script_file"] = str(script_path)

        total_utts = sum(len(scene.utterances) for ep in delivery.episodes for scene in ep.scenes)
        result["utterances"] = total_utts
        result["episodes"] = len(delivery.episodes)

        # Token breakdown
        total_in = sum(c.get('usage', {}).get('prompt_tokens', 0) for c in provider.call_history)
        total_out = sum(c.get('usage', {}).get('completion_tokens', 0) for c in provider.call_history)
        result["token_in"] = total_in
        result["token_out"] = total_out
        result["llm_calls"] = len(provider.call_history)

        result["status"] = "ok"
        print(f"  [{recipe_id}] OK ({result['total_sec']}s) — {total_utts} utterances, {total_in}/{total_out} tokens")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["total_sec"] = round(time.time() - t0)
        print(f"  [{recipe_id}] FAIL ({result['total_sec']}s): {e}")

    return result


def main():
    provider = build_provider_from_env()
    model = os.environ.get('DRAMA_LLM_MODEL', '?')
    print(f"=== R6-R13 Batch Verification ===\nModel: {model}\nCases: {len(COMPARISONS)} comparisons + {len(STANDALONE)} standalone\n")

    all_results = []

    # ── Comparisons ──
    for comp in COMPARISONS:
        case_id = comp["id"]
        print(f"\n── {case_id}: {comp['note']} ──")
        print(f"   创意: {comp['idea'][:80]}...")

        ra = generate_one(provider, case_id, comp["idea"], comp["mode"], comp["genre"], comp["recipe_a"])
        all_results.append(ra)

        rb = generate_one(provider, case_id, comp["idea"], comp["mode"], comp["genre"], comp["recipe_b"])
        all_results.append(rb)

    # ── Standalone ──
    for s in STANDALONE:
        case_id = s["id"]
        print(f"\n── {case_id}: {s['note']} ──")
        print(f"   创意: {s['idea'][:80]}...")

        r = generate_one(provider, case_id, s["idea"], s["mode"], s["genre"], s["recipe_id"])
        all_results.append(r)

    # ── Summary ──
    print("\n\n========== RESULTS ==========\n")
    ok = [r for r in all_results if r["status"] == "ok"]
    err = [r for r in all_results if r["status"] != "ok"]
    print(f"OK: {len(ok)}")
    print(f"FAIL: {len(err)}")

    if err:
        for e in err:
            print(f"  {e['case']}/{e['recipe']}: {e.get('error', '?')}")

    total_tokens_in = sum(r.get("token_in", 0) for r in ok)
    total_tokens_out = sum(r.get("token_out", 0) for r in ok)
    total_time = sum(r.get("total_sec", 0) for r in all_results)
    print(f"\nTotal: {total_time}s, {total_tokens_in}/{total_tokens_out} tokens, {len(ok)} stories")

    summary_path = OUT_DIR / "_batch_summary.json"
    summary_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Summary: {summary_path}")
    return all_results


if __name__ == '__main__':
    main()