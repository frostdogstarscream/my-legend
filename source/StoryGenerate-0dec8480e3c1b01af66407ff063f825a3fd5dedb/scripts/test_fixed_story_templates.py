"""Fixed-template story generation: Template + Recipe + Plan + Writer + StoryDelivery.

Usage:
    python scripts/test_fixed_story_templates.py              # all cases
    python scripts/test_fixed_story_templates.py --case T01   # single case
    python scripts/test_fixed_story_templates.py --retry      # retry failures only
"""
from __future__ import annotations

import argparse, json, os, sys, time, uuid, traceback
from pathlib import Path

# Force UTF-8 output to avoid emoji encoding errors on Windows GBK
sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None

sys.path.insert(0, "4-引擎代码")

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.workflow.schemas import (
    CreationPreferences, CreateStoryRequest, StoryModeChoice,
    StoryTemplateRef, OperationContext,
)
from drama_engine.workflow.adapters import LLMPlanGenerator, ApprovedPlanLLMExecutor
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.repository import InMemoryWorkflowRepository
from drama_engine.workflow.delivery import (
    export_delivery_bundle, verify_round_trip,
    delivery_to_readable_script, validate_delivery_integrity,
)
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import StoryTemplateResolver
from drama_engine.templates.defaults import BUILTIN_TEMPLATES
from drama_engine.templates.models import StoryTemplateSpec

PROJECT = Path(__file__).resolve().parent.parent
FIXTURE = PROJECT / "tests" / "fixtures" / "fixed_story_cases.json"
OUT_DIR = PROJECT / "6-实测产物" / "fixed_template_acceptance"

# ── Mode mapping ──
MODE_MAP = {
    "general": StoryModeChoice.GENERAL,
    "viral_drama": StoryModeChoice.VIRAL_DRAMA,
    "mystery": StoryModeChoice.MYSTERY,
    "serialized": StoryModeChoice.SERIALIZED,
}


def resolve_mode(mode: str) -> StoryModeChoice:
    """Map mode string to StoryModeChoice enum."""
    return MODE_MAP.get(mode, StoryModeChoice.AUTO)


def _load_json_templates() -> list[StoryTemplateSpec]:
    """Load JSON template files from templates/ directory."""
    templates_dir = PROJECT / "templates"
    if not templates_dir.is_dir():
        return []
    specs = []
    for f in sorted(templates_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            spec = StoryTemplateSpec(**data)
            specs.append(spec)
        except Exception as e:
            print(f"  [WARN] Skipping {f.name}: {e}")
    return specs


def _token_stats(call_history: list) -> dict:
    """Extract token stats from call history (flat structure).
    
    NOTE: completion_tokens already includes reasoning_tokens per OpenAI API spec.
    We report them separately for visibility but do NOT add them to total_tokens.
    """
    total_in = 0
    total_out = 0
    total_reason = 0
    total_cache = 0
    for c in call_history:
        total_in += c.get("input_tokens", 0) or 0
        total_out += c.get("output_tokens", 0) or 0
        total_reason += c.get("reasoning_tokens", 0) or 0
        total_cache += c.get("cache_read_tokens", 0) or 0
    return {
        "prompt_tokens": total_in,
        "completion_tokens": total_out,
        "reasoning_tokens": total_reason,  # already in completion_tokens
        "cache_read_tokens": total_cache,
        "total_tokens": total_in + total_out,  # NOT +reasoning (already included)
        "llm_calls": len(call_history),
        "reported_input_tokens": sum(c.get("input_tokens", 0) or 0 for c in call_history if c.get("usage_source") == "reported"),
        "reported_output_tokens": sum(c.get("output_tokens", 0) or 0 for c in call_history if c.get("usage_source") == "reported"),
        "estimated_input_tokens": sum(c.get("input_tokens", 0) or 0 for c in call_history if c.get("usage_source") == "estimated"),
        "unavailable_usage_calls": sum(c.get("usage_source") == "unavailable" for c in call_history),
    }


def run_case(case: dict, provider, service, template_resolver) -> dict:
    """Run a single test case through the full pipeline. Returns result dict."""
    case_id = case["id"]
    template_id = case["template_id"]
    template_rev = case["template_revision"]
    mode = case["story_mode"]
    genre = case["genre"]
    recipe_id = case.get("recipe_id")
    target_dur = case["target_duration_sec"]
    idea = case["idea"]

    print(f"\n{'='*60}")
    print(f"  [{case_id}] {template_id} v{template_rev} | {mode} | {genre} | {recipe_id or 'auto'}")
    print(f"  创意: {idea[:80]}...")
    print(f"{'='*60}")

    # Reset call history for this case
    provider.call_history = []
    t0 = time.time()

    result = {"case_id": case_id, "template_id": template_id, "status": "unknown"}

    try:
        # ── Step 1: Create request with explicit template ref ──
        request = CreateStoryRequest(
            request_id=uuid.uuid4().hex,
            idempotency_key=uuid.uuid4().hex,
            user_instruction=idea,
            creation_preferences=CreationPreferences(
                story_mode=resolve_mode(mode),
                genre=genre,
                recipe_id=recipe_id,
                target_duration_sec=target_dur,
                target_episodes=1,
            ),
            template_ref=StoryTemplateRef(
                template_id=template_id,
                template_revision=template_rev,
            ),
        )

        # ── Step 2: Prepare plan ──
        plan = service.prepare_story_plan(request, OperationContext())
        result["plan_sec"] = round(time.time() - t0)

        # Verify template snapshot
        tmpl_snapshot = plan.template_snapshot
        result["template_resolved"] = tmpl_snapshot.get("template_id") if tmpl_snapshot else None
        result["template_version"] = tmpl_snapshot.get("version") if tmpl_snapshot else None

        # Verify strategy snapshot
        strategy_snapshot = plan.strategy_snapshot
        result["strategy_recipe"] = strategy_snapshot.get("recipe_id") if strategy_snapshot else None
        result["strategy_mode"] = strategy_snapshot.get("story_mode") if strategy_snapshot else None
        result["strategy_has_mechanisms"] = bool(
            strategy_snapshot.get("conflict_mechanism") if strategy_snapshot else False
        )

        # Save plan
        case_dir = OUT_DIR / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        plan_path = case_dir / f"{case_id}_plan.json"
        plan_path.write_text(
            json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["plan_file"] = str(plan_path)

        # ── Step 3: Approve ──
        job = service.approve_story_plan(
            plan.plan_id, 1, plan.plan_fingerprint, f"{case_id}-accept"
        )

        # ── Step 4: Generate delivery ──
        delivery = service.generate_from_approved_plan(job.job_id)
        result["total_sec"] = round(time.time() - t0)

        # ── Step 5: Validate ──
        round_trip_ok, round_trip_message = verify_round_trip(delivery)
        result["round_trip"] = "OK" if round_trip_ok else "FAIL"
        result["round_trip_message"] = round_trip_message
        integrity = validate_delivery_integrity(delivery)
        result["integrity_valid"] = integrity.valid
        result["integrity_errors"] = len(integrity.errors)
        result["integrity_warnings"] = len(integrity.warnings)

        # integrity_valid=false means the case FAILS acceptance
        if not integrity.valid:
            result["status"] = "integrity_fail"
            result["error"] = f"Integrity check failed: {integrity.errors[:3]}"
            print(f"  ❌ INTEGRITY FAIL — {len(integrity.errors)} errors")
            # Continue to save artifacts for debugging, but mark as failed

        # ── Step 6: Export ──
        export_delivery_bundle(delivery, str(case_dir), case_id)

        script = delivery_to_readable_script(delivery)
        script_path = case_dir / f"{case_id}_story.txt"
        script_path.write_text(script, encoding="utf-8")
        result["script_file"] = str(script_path)

        # Stats
        total_utts = sum(len(scene.utterances) for ep in delivery.episodes for scene in ep.scenes)
        result["utterances"] = total_utts
        result["episodes"] = len(delivery.episodes)

        # Token stats
        tokens = _token_stats(provider.call_history)
        result.update(tokens)

        result["status"] = "ok" if integrity.valid and round_trip_ok else "integrity_fail"
        print(f"  {result['status']} ({result['total_sec']}s) — {total_utts} utterances, "
              f"{tokens['total_tokens']} tokens, {tokens['llm_calls']} calls")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["total_sec"] = round(time.time() - t0)
        print(f"  ❌ FAIL ({result['total_sec']}s): {e}")
        # Save error log
        case_dir = OUT_DIR / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "_error.txt").write_text(
            f"Error: {e}\n\n{traceback.format_exc()}", encoding="utf-8"
        )

    result.update(_token_stats(provider.call_history))
    result["calls"] = provider.call_history

    return result


def main():
    parser = argparse.ArgumentParser(description="Fixed-template story generation tests")
    parser.add_argument("--case", help="Run a single case by ID (e.g. T01)")
    parser.add_argument("--retry", action="store_true", help="Only retry failed cases")
    args = parser.parse_args()

    print("=" * 60)
    print("FIXED-TEMPLATE STORY GENERATION")
    print(f"Model: {os.environ.get('DRAMA_LLM_MODEL', '?')}")
    print("=" * 60)

    # Load cases
    cases_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    all_cases = cases_data["cases"]

    if args.case:
        all_cases = [c for c in all_cases if c["id"] == args.case]
        if not all_cases:
            print(f"❌ Case '{args.case}' not found in fixture")
            sys.exit(1)

    if args.retry:
        # Only retry cases that don't have a story file yet
        retry_cases = []
        for c in all_cases:
            story_file = OUT_DIR / c["id"] / f"{c['id']}_story.txt"
            if not story_file.exists():
                retry_cases.append(c)
        all_cases = retry_cases
        print(f"Retry mode: {len(all_cases)} cases to rerun")

    print(f"Cases: {len(all_cases)}")

    # Setup provider
    provider = build_provider_from_env()
    plan_gen = LLMPlanGenerator(provider)
    executor = ApprovedPlanLLMExecutor(provider)

    # Setup template repository with builtin + JSON templates
    repo = MemoryStoryTemplateRepository()
    for spec in BUILTIN_TEMPLATES:
        repo.create(spec)
    for spec in _load_json_templates():
        repo.create(spec)
    all_ids = [t.template_id for t in repo.list()]
    print(f"Templates: {all_ids}")

    template_resolver = StoryTemplateResolver(repo)

    service = StoryWorkflowService(
        InMemoryWorkflowRepository(), plan_gen, executor,
        template_resolver=template_resolver,
    )

    # Run all cases
    all_results = []
    for case in all_cases:
        result = run_case(case, provider, service, template_resolver)
        all_results.append(result)

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    ok = [r for r in all_results if r["status"] == "ok"]
    err = [r for r in all_results if r["status"] != "ok"]
    print(f"✅ OK: {len(ok)}")
    print(f"❌ FAIL: {len(err)}")

    for r in ok:
        print(f"  [{r['case_id']}] {r['template_id']} | {r['total_sec']}s | "
              f"{r.get('utterances',0)} utt | {r.get('total_tokens',0)} tokens")
    for r in err:
        print(f"  [{r['case_id']}] {r.get('error', '?')[:80]}")

    total_tokens = sum(r.get("total_tokens", 0) for r in all_results)
    total_time = sum(r.get("total_sec", 0) for r in all_results)
    print(f"\nTotal: {total_time}s, {total_tokens} tokens, {len(ok)} stories")

    # Save summary
    summary_path = OUT_DIR / "_summary.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Summary: {summary_path}")
    print(f"Output: {OUT_DIR}")


if __name__ == '__main__':
    main()
