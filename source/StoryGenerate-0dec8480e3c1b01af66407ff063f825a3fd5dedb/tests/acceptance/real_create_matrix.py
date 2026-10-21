"""Twelve fixed live Create cases. Requires a real API key; no Mock fallback.

Run `python tests/acceptance/real_create_matrix.py [index]`. With no index, all
twelve cases run. Each case uses a fresh SQLite database only for acceptance
evidence; PostgreSQL remains the release persistence target.
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.persistence.models import Base
from drama_engine.workflow.adapters import ApprovedPlanLLMExecutor, LLMPlanGenerator
from drama_engine.workflow.repository import SqlAlchemyWorkflowRepository
from drama_engine.workflow.schemas import OperationContext
from drama_engine.workflow.service import StoryWorkflowService


CASES = [
    ("14-17", "teen", 120, "general", 1, "温情", "小镇少年发现旧钟表能记录家人的留言，学会告别与陪伴。"),
    ("14-17", "teen", 180, "mystery", 2, "轻悬疑", "校图书馆每晚有一本书自动归位，两位朋友寻找温暖的真相。"),
    ("14-17", "teen", 300, "serialized", 4, "科幻", "四名学生修复城市气象站，发现一封来自未来的提醒。"),
    ("14-17", "teen", 120, "viral_drama", 1, "喜剧", "社团新成员误把机器人报名参加才艺比赛，引发善意误会。"),
    ("18-35", "teen", 180, "general", 2, "都市", "两位合租者通过交换便签慢慢化解生活误会。"),
    ("18-35", "mature_non_explicit", 300, "mystery", 4, "心理", "四位老友重聚后发现一张匿名照片，面对各自隐瞒的往事。"),
    ("18-35", "mature_non_explicit", 120, "serialized", 1, "职场", "一名新人在裁员前夕决定公开项目数据中的错误。"),
    ("18-35", "mature_non_explicit", 180, "viral_drama", 2, "爱情", "前任在婚礼策划公司意外成为搭档，必须处理真实情绪。"),
    ("36-55", "teen", 300, "general", 4, "冒险", "四位家长带孩子修复老街地图，发现被遗忘的社区故事。"),
    ("36-55", "mature_non_explicit", 120, "mystery", 1, "犯罪", "一名审计员发现社区基金账目异常，选择保护证人与证据。"),
    ("36-55", "mature_non_explicit", 180, "serialized", 2, "职场", "两位创业伙伴面对资金断裂，重新定义彼此的信任。"),
    ("36-55", "mature_non_explicit", 300, "viral_drama", 4, "都市", "四位邻居因老楼改造冲突而重新审视各自的人生选择。"),
]
REVISED = {2, 5, 8, 11}
_ATTEMPT_EVIDENCE: dict[int, list[dict]] = {}


class EvidenceCapturingExecutor:
    """Preserve a failed candidate for diagnosis without marking it READY."""

    def __init__(self, delegate, output_dir: str | None, index: int):
        self.delegate = delegate
        self.output_dir = output_dir
        self.index = index

    def execute(self, snapshot):
        delivery = self.delegate.execute(snapshot)
        if self.output_dir:
            target = Path(self.output_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"case_{self.index:02d}_candidate.json").write_text(
                delivery.model_dump_json(indent=2), encoding="utf-8"
            )
        return delivery


def run_case(index: int) -> dict:
    band, rating, duration, mode, character_count, genre, idea = CASES[index - 1]
    context = OperationContext(character_snapshots={
        f"char-{number}": {
            "canon_revision": 1,
            "profile_revision": 1,
            "canon": {"name": f"角色{number}", "immutable_facts": ["普通人"]},
            "profile": {"goal": "推动故事中的选择"},
        }
        for number in range(1, character_count + 1)
    })
    selected = list(context.character_snapshots)
    request = {
        "intent": "create", "request_id": f"live-create-{index:02d}",
        "idempotency_key": f"live-plan-{index:02d}", "user_instruction": idea,
        "creation_preferences": {
            "audience_band": band, "content_rating": rating,
            "target_duration_sec": duration, "story_mode": mode,
            "genre": genre, "target_episodes": 1, "language": "zh-CN",
        },
        "characters": {
            "selected_character_ids": selected,
            "role_bindings": [{
                "character_id": cid, "story_role_id": f"role-{n}",
                "performer_id": f"voice-{n}",
            } for n, cid in enumerate(selected, 1)],
        },
    }
    provider = build_provider_from_env()
    _ATTEMPT_EVIDENCE[index] = provider.call_history
    with tempfile.TemporaryDirectory(prefix="storygenerate-live-", ignore_cleanup_errors=True) as temp:
        engine = create_engine(f"sqlite:///{Path(temp) / 'workflow.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            service = StoryWorkflowService(
                SqlAlchemyWorkflowRepository(session),
                LLMPlanGenerator(provider), EvidenceCapturingExecutor(
                    ApprovedPlanLLMExecutor(provider),
                    os.environ.get("STORY_ACCEPTANCE_OUTPUT_DIR"), index,
                ),
            )
            start = time.monotonic()
            plan = service.prepare_story_plan(request, context)
            session.commit()
            if index in REVISED:
                plan = service.revise_story_plan(
                    plan.plan_id, plan.plan_revision,
                    "保留核心冲突，让最后一次角色选择更清晰，并在结尾自然呼应开场。",
                )
                session.commit()
            output_dir = os.environ.get("STORY_ACCEPTANCE_OUTPUT_DIR")
            if output_dir:
                target = Path(output_dir)
                target.mkdir(parents=True, exist_ok=True)
                (target / f"case_{index:02d}_plan.json").write_text(
                    plan.model_dump_json(indent=2), encoding="utf-8"
                )
            job = service.approve_story_plan(
                plan.plan_id, plan.plan_revision, plan.plan_fingerprint,
                f"live-approval-{index:02d}",
            )
            session.commit()
            delivery = service.generate_from_approved_plan(job.job_id)
            session.commit()
            attempts = list(provider.call_history)
            successful = [call for call in attempts if call.get("status") == "success"]
            targeted_repairs = [
                call for call in attempts
                if call.get("attempt_kind") == "targeted_duration_repair"
            ]
            evidence = {
                "index": index, "git_sha": subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"], text=True
                ).strip(),
                "working_tree_dirty": bool(subprocess.check_output(
                    ["git", "status", "--porcelain", "--untracked-files=no"], text=True
                ).strip()),
                "audience_band": band, "content_rating": rating,
                "duration": duration, "mode": mode, "characters": character_count,
                "plan_revision": plan.plan_revision, "plan_id": plan.plan_id,
                "job_id": job.job_id, "story_version_id": delivery.story_version_id,
                "ready": delivery.ready_for_playback,
                "spoken_lines": delivery.quality_report.spoken_line_count,
                "emotion_coverage": delivery.quality_report.emotion_coverage,
                "tone_coverage": delivery.quality_report.tone_coverage,
                "emphasis_coverage": delivery.quality_report.emphasis_coverage,
                "latency_sec": round(time.monotonic() - start, 2),
                "first_draft_contract_passed": not targeted_repairs,
                "targeted_duration_repair_calls": len(targeted_repairs),
                "llm_call_count": len(attempts),
                "reported_input_tokens": sum(
                    call.get("input_tokens", 0) for call in successful
                    if call.get("usage_source") == "reported"
                ),
                "reported_output_tokens": sum(
                    call.get("output_tokens", 0) for call in successful
                    if call.get("usage_source") == "reported"
                ),
                "usage_unavailable_calls": sum(
                    call.get("usage_source") == "unavailable" for call in attempts
                ),
                "llm_attempts": attempts,
                "rule_sha256": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (Path(__file__).resolve().parents[2] / "3-规则库").glob("*.json")
                },
            }
            if output_dir:
                target = Path(output_dir)
                (target / f"case_{index:02d}_delivery.json").write_text(
                    delivery.model_dump_json(indent=2), encoding="utf-8"
                )
                (target / f"case_{index:02d}_evidence.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            engine.dispose()
            return evidence


def main() -> int:
    if len(sys.argv) > 1 and "-" in sys.argv[1]:
        first, last = (int(value) for value in sys.argv[1].split("-", 1))
        indices = list(range(first, last + 1))
    else:
        indices = [int(sys.argv[1])] if len(sys.argv) > 1 else list(range(1, 13))
    failed = False
    def one(index):
        try:
            return run_case(index)
        except Exception as exc:
            attempts = list(_ATTEMPT_EVIDENCE.get(index, []))
            result = {
                "index": index, "ready": False,
                "error_type": type(exc).__name__, "error": str(exc)[:500],
                "llm_call_count": len(attempts),
                "reported_input_tokens": sum(
                    call.get("input_tokens", 0) for call in attempts
                    if call.get("usage_source") == "reported"
                ),
                "reported_output_tokens": sum(
                    call.get("output_tokens", 0) for call in attempts
                    if call.get("usage_source") == "reported"
                ),
                "usage_unavailable_calls": sum(
                    call.get("usage_source") == "unavailable" for call in attempts
                ),
                "llm_attempts": attempts,
            }
            output_dir = os.environ.get("STORY_ACCEPTANCE_OUTPUT_DIR")
            if output_dir:
                target = Path(output_dir)
                target.mkdir(parents=True, exist_ok=True)
                (target / f"case_{index:02d}_error.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            return result
    workers = int(os.environ.get("STORY_ACCEPTANCE_MAX_WORKERS", "2"))
    if workers < 1 or workers > 3:
        raise ValueError("STORY_ACCEPTANCE_MAX_WORKERS must be 1-3")
    with ThreadPoolExecutor(max_workers=min(workers, len(indices))) as pool:
        futures = {pool.submit(one, index): index for index in indices}
        for future in as_completed(futures):
            result = future.result()
            failed |= not result.get("ready", False)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
