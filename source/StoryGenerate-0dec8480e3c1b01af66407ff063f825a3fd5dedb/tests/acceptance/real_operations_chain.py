"""Real-provider/PostgreSQL four-operation acceptance. No Mock fallback.

python tests/acceptance/real_operations_chain.py --preflight-only
python tests/acceptance/real_operations_chain.py --rounds 3
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "4-引擎代码"))
from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from drama_engine.core.settings import get_settings
from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.llm.router import resolve_spec
from drama_engine.workflow.adapters import ApprovedPlanLLMExecutor, LLMPlanGenerator
from drama_engine.workflow.delivery import export_delivery_bundle, validate_delivery_integrity
from drama_engine.workflow.operations import operation_errors
from drama_engine.workflow.repository import SqlAlchemyWorkflowRepository
from drama_engine.workflow.schemas import OperationContext, StoryDelivery
from drama_engine.workflow.service import StoryWorkflowService


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def usage(calls):
    unique = {c["call_id"]: c for c in calls}
    reported = [c for c in unique.values() if c.get("usage_source") == "reported"]
    return {
        "calls": len(unique), "reported_input_tokens": sum(c.get("input_tokens", 0) for c in reported),
        "reported_output_tokens": sum(c.get("output_tokens", 0) for c in reported),
        "reported_total_tokens": sum(c.get("input_tokens", 0) + c.get("output_tokens", 0) for c in reported),
        "reasoning_tokens_included_in_output": sum(c.get("reasoning_tokens", 0) for c in reported),
        "cache_read_tokens": sum(c.get("cache_read_tokens", 0) for c in reported),
        "estimated_input_tokens": sum(c.get("input_tokens", 0) for c in unique.values() if c.get("usage_source") == "estimated"),
        "unavailable_usage_calls": sum(c.get("usage_source") == "unavailable" for c in unique.values()),
        "model_request_latency_sec": sum(c.get("latency_ms", 0) for c in unique.values()) / 1000,
    }


class Probe(BaseModel):
    ok: bool


def provider_with_evidence(directory):
    provider = build_provider_from_env()
    original_complete = provider.complete
    def capture(spec, system, user, deadline=None):
        start = len(provider.call_history)
        capture_id = uuid.uuid4().hex
        record = {"model": spec.model, "role": spec.role, "system": system, "user": user,
                  "capture_id": capture_id, "status": "pending", "started_at": datetime.now(timezone.utc).isoformat()}
        target = directory / f"call_{capture_id}.json"
        save(target, record)
        try:
            result = original_complete(spec, system, user, deadline)
            record["response_text"] = result.text
            record["status"] = "completed"
            return result
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            record["status"] = "failed"
            raise
        finally:
            calls = provider.call_history[start:]
            record["calls"] = calls
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            save(target, record)
    provider.complete = capture
    return provider


def engine_for_schema(url, schema):
    return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})


def migrate(url, schema):
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    engine = engine_for_schema(url, schema)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine


CONTEXT = OperationContext(character_snapshots={
    "linlan": {"canon": {"name": "林岚", "immutable_facts": ["林岚是广播站维修员，非记者"]},
               "profile": {"goal": "修复广播站", "speech_style": "简短具体，先确认再行动"}},
    "zhouye": {"canon": {"name": "周野", "immutable_facts": ["周野是社区广播主持人，非维修员"]},
                "profile": {"goal": "恢复街坊广播", "speech_style": "热心、会自嘲"}},
})


def request(intent, instruction, source=None, **extra):
    value = {"intent": intent, "request_id": uuid.uuid4().hex, "idempotency_key": uuid.uuid4().hex,
             "user_instruction": instruction, **extra}
    if source:
        value["source_selector"] = {"type": "by_id", "story_version_id": source.story_version_id}
    return value


def revision(source, scope, target, instruction, downstream="branch_from_here"):
    return request("revise", instruction, source, edit={"scope": scope, "target": target,
        "instruction": instruction, "preserve": {"identity": True, "facts": True}, "downstream_policy": downstream})


def run_operation(engine, out, label, value, context=None, retry_job_id=None):
    directory = out / label
    directory.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    provider = provider_with_evidence(directory / "model_calls")
    record = {"label": label, "intent": value["intent"], "status": "running"}
    save(directory / "request.json", value)
    job_id = None
    def service(session):
        class CaptureExecutor:
            def execute(self, snapshot):
                candidate = ApprovedPlanLLMExecutor(provider).execute(snapshot)
                save(directory / "candidate.json", candidate.model_dump(mode="json"))
                return candidate
        return StoryWorkflowService(SqlAlchemyWorkflowRepository(session), LLMPlanGenerator(provider), CaptureExecutor())
    try:
        if retry_job_id:
            with Session(engine) as session:
                app = service(session)
                job = app.retry_failed_job(retry_job_id)
                plan = job.input_snapshot.preview
                job_id = job.job_id
                record.update(retry_job_id=job_id, approved_snapshot_reused=True)
                save(directory / "plan.json", plan.model_dump(mode="json"))
                save(directory / "approved_job.json", job.model_dump(mode="json"))
        else:
            with Session(engine) as session:
                plan = service(session).prepare_story_plan(value, context)
                save(directory / "plan.json", plan.model_dump(mode="json"))
            with Session(engine) as session:
                app = service(session)
                restored_plan = app.get_story_plan(plan.plan_id)
                assert restored_plan == plan
                job = app.approve_story_plan(plan.plan_id, plan.plan_revision, plan.plan_fingerprint, uuid.uuid4().hex)
                job_id = job.job_id
                save(directory / "approved_job.json", job.model_dump(mode="json"))
        with Session(engine) as session:
            delivery = service(session).generate_from_approved_plan(job_id)
        with Session(engine) as session:
            app = service(session)
            restored = app.get_story_version(delivery.story_version_id)
            assert restored == delivery
            job = app.get_generation_job(job_id)
            assert job.status == "SUCCEEDED" and job.result_story_version_id == delivery.story_version_id
            assert app.generate_from_approved_plan(job_id) == delivery
            assert not operation_errors(job.input_snapshot, restored)
            assert validate_delivery_integrity(restored).valid
            export_delivery_bundle(restored, directory, "delivery")
            reloaded = StoryDelivery.model_validate_json((directory / "delivery.json").read_text(encoding="utf-8"))
            assert reloaded == restored
            save(directory / "persisted_job.json", job.model_dump(mode="json"))
            source = job.input_snapshot.preview.source_reference
            if source:
                old = StoryDelivery.model_validate(source.source_content_snapshot["delivery"])
                hashes = {ep.episode_id: hashlib.sha256(ep.model_dump_json().encode()).hexdigest() for ep in old.episodes}
                save(directory / "source_hashes.json", hashes)
                save(directory / "diff.json", {"parent": old.story_version_id,
                    "child": restored.story_version_id, "generated_episode_ids": restored.generated_episode_ids,
                    "changed_episodes": [ep.episode_id for ep in restored.episodes if
                        hashes.get(ep.episode_id) != hashlib.sha256(ep.model_dump_json().encode()).hexdigest()]})
            record.update(status="passed", story_id=restored.story_id, version_no=restored.version_no,
                          story_version_id=restored.story_version_id, parent=restored.parent_story_version_id,
                          episodes=len(restored.episodes), operation_checks=restored.quality_report.operation_checks)
        return restored
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        if job_id:
            with Session(engine) as session:
                job = service(session).get_generation_job(job_id)
                save(directory / "failed_job.json", job.model_dump(mode="json"))
        return None
    finally:
        record.update(usage(provider.call_history), wall_time_sec=round(time.monotonic() - start, 3))
        save(directory / "calls.json", provider.call_history)
        save(directory / "result.json", record)
        print(json.dumps({k: record[k] for k in ("label", "status", "wall_time_sec", "reported_total_tokens")}, ensure_ascii=False), flush=True)


def blocked(out, label, intent, reason):
    save(out / label / "result.json", {"label": label, "intent": intent, "status": "blocked", "reason": reason})


def run_round(engine, out, number):
    prefix = f"round_{number}"
    created = run_operation(engine, out, prefix + "_create", request("create",
        "林岚（维修员）与周野（主持人）修复老街广播站。两集，每集120秒。第一集确定旧电源接头进水导致故障，"
        "没有人为破坏；两人从互相猜疑到愿意合作。约定广播恢复后敲三声铃给街坊报平安，第二集尚未兑现约定，"
        "保留这个伏笔，给后续留出维修验收任务。fact_delta、relationship_delta记录真实变化，"
        "三声铃伏笔在new_open_threads记录为‘三声铃报平安’，后续回收时必须原样引用。"
        "这是现实日常维修故事，两集只围绕故障排查与维修验收准备；不要增加神秘信号、倒计时、陌生人或新危机。"
        "第二集不得提前敲三声铃，也不能敲暖气管等替代物完成报平安。",
        client_context={"operation_requirements": {
            "identities": "林岚是维修员，周野是主持人，姓名和身份保持一致。",
            "fault": "第一集用实际排查行动确认旧电源接头进水故障，排除人为破坏。",
            "cooperation": "两人从猜疑到愿意合作，正文出现共同维修行动。",
            "deferred_bell": "截至第二集结束，三声铃报平安尚未兑现，也不能以暖气管等替代物提前敲三声报平安；不能把已经完成的报平安因为新危机说成未完成。",
        }},
        creation_preferences={"story_mode": "general", "genre": "温情", "target_episodes": 2,
            "target_duration_sec": 120, "ending_preference": "open"},
        characters={"selected_character_ids": ["linlan", "zhouye"], "role_bindings": [
            {"character_id": "linlan", "story_role_id": "repairer", "performer_id": "voice-linlan"},
            {"character_id": "zhouye", "story_role_id": "host", "performer_id": "voice-zhouye"}]}), CONTEXT)
    if created is None:
        for op in ("revise", "continue", "remix"):
            blocked(out, prefix + "_" + op, op, "Create未通过")
        return None
    scene = next(s for s in created.episodes[0].scenes if any(u.kind == "dialogue" for u in s.utterances))
    revised = run_operation(engine, out, prefix + "_revise", revision(created, "scene",
        {"episode_id": created.episodes[0].episode_id, "scene_id": scene.scene_id},
        "仅调整指定场景中的决策表达：林岚明确先请周野共同核对设备状态，再决定下一步。"
        "保留该场已经发生的事实、行动结果及角色身份，不改变故障原因和其他场景；不得提前兑现三声铃。"))
    if revised is None:
        for op in ("continue", "remix"):
            blocked(out, prefix + "_" + op, op, "Revise未通过")
        return created
    continued = run_operation(engine, out, prefix + "_continue", request("continue",
        "新增两集，继承全部人物、进水故障事实与合作关系。第一新增集完成带负载试音、发现音量仍不稳定并登记待校准；"
        "第二新增集承接该结果完成音量校准，最后真正敲三声铃报平安，回收‘三声铃报平安’伏笔，不能再留在未解列表。",
        revised, creation_preferences={"target_episodes": 2, "ending_preference": "closed"},
        client_context={"operation_requirements": {
            "bell_effect": "结尾必须用sfx、action或narration明确呈现已经发生的三声铃响，例如叮、叮、叮；不能只写角色宣布要敲或说我敲了。",
        }, "evidence_kinds": {"constraint:bell_effect": ["sfx", "action", "narration"]}}))
    if continued is None:
        blocked(out, prefix + "_remix", "remix", "Continue未通过")
        return created
    run_operation(engine, out, prefix + "_remix", request("remix",
        "创作两集平行版本，保留维修员林岚、主持人周野和老街广播站背景。改变冲突：设备没有进水，"
        "周野曾因害怕告别而主动断开电源并隐瞒。结尾由周野承认隐瞒、公开道歉，两人约定合作整改，"
        "不能沿用原版修好设备敲铃报平安的结局。", continued,
        creation_preferences={"story_mode": "general", "genre": "温情", "target_episodes": 2,
                              "target_duration_sec": 120, "ending_preference": "closed"},
        remix={"instruction": "平行版本中设备并未进水，故障由周野主动断开电源并隐瞒造成；结局改为公开认错与合作整改。",
               "retain_elements": ["林岚是维修员，周野是主持人", "故事发生在老街广播站"],
               "transform_elements": ["设备故障改为周野主动断开电源并隐瞒，不能仍是进水故障",
                                      "结局改为周野公开认错并合作整改，不能以三声铃报平安收束"],
               "relation_to_source": "parallel_world"}))
    return created


def specialties(engine, out, created):
    ep = created.episodes[0]
    scene = next(s for s in ep.scenes if any(u.kind == "dialogue" for u in s.utterances))
    line = next(u for u in scene.utterances if u.kind == "dialogue")
    cases = [
        ("line", {"episode_id": ep.episode_id, "scene_id": scene.scene_id, "line_id": line.line_id},
         "仅将指定台词表达改得更口语化，保持原意、身份及事实，长度与原文接近。"),
        ("episode", {"episode_id": ep.episode_id}, "只重写指定集，使两人合作过程更自然。保留进水故障事实、角色身份和结果，不兑现三声铃。"),
        ("arc", {"episode_ids": [e.episode_id for e in created.episodes]}, "重写两集合作弧线，让信任逐步建立；保留人物、进水故障和未兑现三声铃的结果。"),
        ("ending", {}, "仅将最后一场收束表达改为具体的维修验收准备动作，保留原有事实结果，不兑现三声铃。"),
        ("whole_story", {}, "重写完整故事，减少说教，以具体行动建立信任；保留两人身份、进水故障以及三声铃尚未兑现的结果。"),
    ]
    def run_special(case):
        scope, target, instruction = case
        return run_operation(engine, out, "special_" + scope, revision(created, scope, target, instruction))
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run_special, cases))
    continuation_file = out / "round_1_continue" / "delivery.json"
    if continuation_file.exists():
        continued = StoryDelivery.model_validate_json(continuation_file.read_text(encoding="utf-8"))
        run_operation(engine, out, "special_continue_again", request("continue",
            "新增一集，承接广播恢复且三声铃约定已兑现、音量已校准的结果。两人合作组织首次街坊广播，不能再把这些问题当作未完成。",
            continued, creation_preferences={"target_episodes": 1}))
    else:
        blocked(out, "special_continue_again", "continue", "首轮Continue未通过")


def main():
    batch_start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    out = args.output or ROOT / "6-实测产物" / ("operations_" + time.strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    schema = "storyoperations_" + uuid.uuid4().hex[:12]
    url = os.environ.get("TEST_DATABASE_URL") or "postgresql+psycopg://storygen:storygen_dev@localhost:5432/storygenerate"
    manifest = {"git_sha": sha, "tracked_dirty": bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip()),
        "schema": schema, "rounds": args.rounds, "real_provider": True, "mock_fallback": False,
        "target": "engine_and_structured_delivery", "started_at": datetime.now(timezone.utc).isoformat()}
    save(out / "manifest.json", manifest)
    provider = provider_with_evidence(out / "preflight" / "model_calls")
    probes = []
    for role in ("outline", "performance_annotation"):
        spec = resolve_spec(role, max_tokens=2048, temperature=0)
        try:
            result = provider.complete_structured(spec, "只返回JSON", '返回{"ok":true}', Probe)
            probes.append({"role": role, "model": spec.model, "passed": result.ok})
        except Exception as exc:
            probes.append({"role": role, "model": spec.model, "passed": False, "error_type": type(exc).__name__})
    save(out / "preflight" / "probes.json", probes)
    save(out / "preflight" / "calls.json", provider.call_history)
    if not all(p["passed"] for p in probes):
        save(out / "summary.json", {"status": "blocked", "reason": "真实模型探测未通过", "probes": probes,
                                     **usage(provider.call_history)})
        print("MODEL_PREFLIGHT_FAILED: " + str(out), flush=True)
        return 1
    if args.preflight_only:
        print("MODEL_PREFLIGHT_PASSED: " + str(out), flush=True)
        return 0
    engine = migrate(url, schema)
    try:
        with ThreadPoolExecutor(max_workers=min(args.rounds, 3)) as pool:
            roots = list(pool.map(lambda n: run_round(engine, out, n), range(1, args.rounds + 1)))
        if roots and roots[0]:
            specialties(engine, out, roots[0])
        else:
            for scope in ("line", "episode", "arc", "ending", "whole_story", "continue_again"):
                blocked(out, "special_" + scope, "continue" if scope == "continue_again" else "revise", "首轮Create未通过")
    finally:
        engine.dispose()
        manifest.update(completed_at=datetime.now(timezone.utc).isoformat(),
                        wall_time_sec=round(time.monotonic() - batch_start, 3))
        save(out / "manifest.json", manifest)
        results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*/result.json"))]
        all_calls = [c for p in out.glob("*/calls.json") for c in json.loads(p.read_text(encoding="utf-8"))]
        save(out / "summary.json", {"results": results, **usage(all_calls),
            "passed": sum(r["status"] == "passed" for r in results),
            "failed": sum(r["status"] == "failed" for r in results),
            "blocked": sum(r["status"] == "blocked" for r in results)})
    print("ARTIFACTS: " + str(out), flush=True)
    return 0 if all(r["status"] == "passed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
