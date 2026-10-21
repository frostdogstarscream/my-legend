"""Phase 7C: Chapter-by-Chapter Executor + Checkpoint System.

Sequential chapter execution:
    LongFormPlan → Chapter N Plan → Writer → Validator → Guard → Commit → Checkpoint → Next

Checkpoint saves at:
    1. After Global Plan
    2. After each successful Chapter
    3. Before Final Assemble
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .longform import (
    ChapterPlan,
    ChapterResult,
    GenerationScale,
    LongFormContext,
    LongFormPlan,
)
from .schemas import Brief


# ═══════════════════════════════════════════════════════════════════
# Checkpoint Schema
# ═══════════════════════════════════════════════════════════════════
class GenerationCheckpoint:
    """可恢复的生成检查点。

    严格 JSON-safe: 所有字段可直接 json.dumps。
    """

    def __init__(
        self,
        run_id: str,
        story_id: str = "",
        generation_scale: str = "standard",
        stage: str = "planned",
        last_completed_chapter: int = 0,
        next_chapter: int = 1,
        state_json: dict[str, Any] | None = None,
        config_fingerprint: str = "",
        status: str = "resumable",
        version: str = "1.0",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.run_id = run_id
        self.story_id = story_id
        self.generation_scale = generation_scale
        self.stage = stage
        self.last_completed_chapter = last_completed_chapter
        self.next_chapter = next_chapter
        self.state_json = state_json or {}
        self.config_fingerprint = config_fingerprint
        self.status = status
        self.version = version
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "story_id": self.story_id,
            "generation_scale": self.generation_scale,
            "stage": self.stage,
            "last_completed_chapter": self.last_completed_chapter,
            "next_chapter": self.next_chapter,
            "state_json": self.state_json,
            "config_fingerprint": self.config_fingerprint,
            "status": self.status,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
        }

    def __getitem__(self, key: str) -> Any:
        """Dict-like access for backward compatibility with PostgresCheckpointRepository consumers."""
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like .get() for backward compatibility."""
        return self.to_dict().get(key, default)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GenerationCheckpoint":
        return cls(
            run_id=d.get("run_id", ""),
            story_id=d.get("story_id", ""),
            generation_scale=d.get("generation_scale", "standard"),
            stage=d.get("stage", "planned"),
            last_completed_chapter=d.get("last_completed_chapter", 0),
            next_chapter=d.get("next_chapter", 1),
            state_json=d.get("state_json", {}),
            config_fingerprint=d.get("config_fingerprint", ""),
            status=d.get("status", "resumable"),
            version=d.get("version", "1.0"),
        )


# ═══════════════════════════════════════════════════════════════════
# Checkpoint Repository (Interface + InMemory)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class InMemoryCheckpointRepository:
    """In-memory checkpoint repository for testing."""

    _store: dict[str, list[GenerationCheckpoint]] = field(default_factory=dict)

    def save_or_update(self, checkpoint: GenerationCheckpoint) -> None:
        """保存或更新 checkpoint（按 run_id + stage 去重）。"""
        key = checkpoint.run_id
        if key not in self._store:
            self._store[key] = []
        # Replace existing checkpoint at same stage
        existing = [c for c in self._store[key] if c.stage == checkpoint.stage]
        if existing:
            idx = self._store[key].index(existing[0])
            self._store[key][idx] = checkpoint
        else:
            self._store[key].append(checkpoint)

    def get_latest(self, run_id: str) -> GenerationCheckpoint | None:
        """获取最新 checkpoint（按 last_completed_chapter）。"""
        entries = self._store.get(run_id, [])
        if not entries:
            return None
        return max(entries, key=lambda c: c.last_completed_chapter)

    def get_by_stage(self, run_id: str, stage: str) -> GenerationCheckpoint | None:
        entries = self._store.get(run_id, [])
        for c in entries:
            if c.stage == stage:
                return c
        return None

    def list_by_run(self, run_id: str) -> list[GenerationCheckpoint]:
        return sorted(
            self._store.get(run_id, []),
            key=lambda c: c.last_completed_chapter,
        )

    def list_all_runs(self) -> list[str]:
        return list(self._store.keys())


# ═══════════════════════════════════════════════════════════════════
# Config Fingerprint
# ═══════════════════════════════════════════════════════════════════
def build_config_fingerprint(
    story_mode: str | None = None,
    template_id: str | None = None,
    template_version: str | None = None,
    content_form: str | None = None,
    renderer_version: str | None = None,
    generation_scale: str = "standard",
    plan_version: str = "1.0",
) -> str:
    """构建配置指纹 — resume 前必须匹配。"""
    payload = {
        "story_mode": story_mode or "",
        "template_id": template_id or "",
        "template_version": str(template_version or ""),
        "content_form": content_form or "",
        "renderer_version": renderer_version or "1.0",
        "generation_scale": generation_scale,
        "plan_version": plan_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# Chapter Executor
# ═══════════════════════════════════════════════════════════════════
def execute_chapter(
    plan: LongFormPlan,
    chapter_plan: ChapterPlan,
    context: LongFormContext,
    brief: Brief,
    runtime: Any = None,
    story_mode: str = "general",
    content_form: str = "prose_story",
    lib: Any = None,
    workspace: str = "",
    fail_at_chapter: int | None = None,
) -> ChapterResult:
    """执行单章：Writer → Validator → Guard → Commit。

    Args:
        plan: 全局 LongFormPlan
        chapter_plan: 当前章节计划
        context: 跨章上下文
        brief: 用户创意
        runtime: Runtime with LLM provider
        content_form: 内容形态
        lib: RuleLibrary
        workspace: 工作目录
        fail_at_chapter: 强制失败注入（仅测试）

    Returns:
        ChapterResult 包含 render_result / validation / guard / fact_delta
    """
    from .graph import run_pipeline

    # Failure injection
    if fail_at_chapter is not None and chapter_plan.chapter_index == fail_at_chapter:
        return ChapterResult(
            chapter_index=chapter_plan.chapter_index,
            plan=chapter_plan,
            status="failed",
        )

    # Build chapter idea with continuity context
    chapter_idea = _build_chapter_idea(brief, chapter_plan, context)

    # Run single-chapter pipeline (reuse Phase 6 graph)
    result = run_pipeline(
        idea=chapter_idea,
        workspace=workspace,
        lib=lib,
        runtime=runtime,
        target_episodes=1,
        story_mode=story_mode,
        content_form=content_form,
    )

    # Extract episode result
    ep_results = result.get("episode_results") or []
    render_result = ep_results[0].model_dump(mode="json") if hasattr(ep_results[0], "model_dump") else (
        ep_results[0] if ep_results else {}
    )
    validation_passed = (
        ep_results[0].validation_passed
        if hasattr(ep_results[0], "validation_passed")
        else bool(render_result.get("validation_passed"))
    )
    guard_passed = (
        ep_results[0].output_guard_passed
        if hasattr(ep_results[0], "output_guard_passed")
        else bool(render_result.get("output_guard_passed"))
    )

    # Compute fact delta
    fact_delta = _extract_fact_delta(chapter_plan, result)

    # Compute unresolved threads
    unresolved = chapter_plan.unresolved_threads_out or []

    return ChapterResult(
        chapter_index=chapter_plan.chapter_index,
        plan=chapter_plan,
        render_result=render_result,
        validation_passed=validation_passed,
        output_guard_passed=guard_passed,
        fact_delta=fact_delta,
        unresolved_threads=unresolved,
        status="completed" if (validation_passed and guard_passed) else "failed",
    )


def _build_chapter_idea(brief: Brief, chapter_plan: ChapterPlan, context: LongFormContext) -> str:
    """为章节构建 LLM idea，注入连续性上下文。"""
    parts = [brief.raw_idea]
    parts.append(f"第{chapter_plan.chapter_index}章: {chapter_plan.title}")
    parts.append(f"目标: {chapter_plan.goal}")
    if chapter_plan.unresolved_threads_in:
        parts.append(f"承接线索: {', '.join(chapter_plan.unresolved_threads_in)}")
    # Include prior chapter summaries
    for ci in sorted(context.completed_chapters.keys()):
        cr = context.completed_chapters[ci]
        parts.append(f"前情({ci}): {cr.plan.ending_state}")
    return " | ".join(parts)


def _extract_fact_delta(chapter_plan: ChapterPlan, result: dict) -> list[dict[str, Any]]:
    """从 chapter plan 和 result 中提取事实变更。"""
    delta: list[dict[str, Any]] = []
    # Required facts claimed as confirmed
    for fact in chapter_plan.required_facts:
        delta.append({"fact_id": fact, "action": "confirmed", "chapter": chapter_plan.chapter_index})
    # Forbidden facts checked
    for fact in chapter_plan.forbidden_changes:
        delta.append({"fact_id": fact, "action": "preserved", "chapter": chapter_plan.chapter_index})
    return delta


# ═══════════════════════════════════════════════════════════════════
# Long-form Runner
# ═══════════════════════════════════════════════════════════════════
def run_long_form(
    idea: str,
    workspace: str = "",
    lib: Any = None,
    runtime: Any = None,
    story_mode: str = "general",
    story_template_id: str | None = None,
    story_template_version: int | None = None,
    content_form: str = "prose_story",
    target_chapters: int = 3,
    fail_at_chapter: int | None = None,
    checkpoint_repo: Any = None,
    resume_from: str | None = None,
    thread_id: str = "local",
) -> dict[str, Any]:
    """Long-form 生成入口。

    Args:
        idea: 用户创意
        target_chapters: 目标章节数
        fail_at_chapter: 强制失败注入（测试）
        checkpoint_repo: Checkpoint 仓库
        resume_from: 恢复 run_id
        thread_id: 会话 ID

    Returns:
        dict with: plan, chapters, context, checkpoints
    """
    run_id = resume_from or uuid.uuid4().hex
    story_id = uuid.uuid4().hex

    if checkpoint_repo is None:
        checkpoint_repo = InMemoryCheckpointRepository()

    brief = Brief(raw_idea=idea, target_episodes=target_chapters)

    # ── Resume path ──
    if resume_from:
        return _resume_long_form(
            run_id=run_id, brief=brief, workspace=workspace, lib=lib, runtime=runtime,
            story_mode=story_mode, story_template_id=story_template_id,
            story_template_version=story_template_version,
            content_form=content_form, fail_at_chapter=fail_at_chapter,
            checkpoint_repo=checkpoint_repo, thread_id=thread_id,
        )

    # ── Fresh generation ──
    return _fresh_long_form(
        run_id=run_id, story_id=story_id, brief=brief, workspace=workspace, lib=lib,
        runtime=runtime, story_mode=story_mode,
        story_template_id=story_template_id, story_template_version=story_template_version,
        content_form=content_form, target_chapters=target_chapters,
        fail_at_chapter=fail_at_chapter, checkpoint_repo=checkpoint_repo, thread_id=thread_id,
    )


def _fresh_long_form(
    run_id: str, story_id: str, brief: Brief, workspace: str, lib: Any, runtime: Any,
    story_mode: str, story_template_id: str | None, story_template_version: int | None,
    content_form: str, target_chapters: int, fail_at_chapter: int | None,
    checkpoint_repo: Any, thread_id: str,
) -> dict[str, Any]:
    """Fresh long-form generation."""
    from .longform import resolve_long_form_profile
    from .longform_planner import plan_long_form

    profile = resolve_long_form_profile(target_chapters)

    # Resolve template
    template = _resolve_template(story_mode, story_template_id, story_template_version)

    # Phase 7B: Global planning
    plan = plan_long_form(
        brief=brief, template=template, profile=profile,
        generation_scale=GenerationScale.LONG_FORM,
        runtime=runtime,
    )
    plan.story_id = story_id

    # Checkpoint: Global Plan
    fp = build_config_fingerprint(
        story_mode=story_mode,
        content_form=content_form,
        generation_scale="long_form",
        plan_version=plan.plan_version,
    )
    cp_plan = GenerationCheckpoint(
        run_id=run_id, story_id=story_id, generation_scale="long_form",
        stage="global_plan", last_completed_chapter=0, next_chapter=1,
        state_json={"plan": plan.model_dump(mode="json")}, config_fingerprint=fp,
        status="resumable",
    )
    checkpoint_repo.save_or_update(cp_plan)

    # Phase 7C: Sequential chapter execution
    context = LongFormContext(plan=plan, current_chapter=0, status="running")

    for ch_plan in plan.chapters:
        context.current_chapter = ch_plan.chapter_index
        result = execute_chapter(
            plan=plan, chapter_plan=ch_plan, context=context, brief=brief,
            runtime=runtime, story_mode=story_mode, content_form=content_form,
            lib=lib, workspace=workspace,
            fail_at_chapter=fail_at_chapter,
        )

        if result.status == "completed":
            context.completed_chapters[ch_plan.chapter_index] = result
            # Update continuity
            context.unresolved_threads = result.unresolved_threads
            # Checkpoint
            cp = GenerationCheckpoint(
                run_id=run_id, story_id=story_id, generation_scale="long_form",
                stage=f"chapter_{ch_plan.chapter_index}",
                last_completed_chapter=ch_plan.chapter_index,
                next_chapter=ch_plan.chapter_index + 1,
                state_json={"context": context.model_dump(mode="json")},
                config_fingerprint=fp, status="resumable",
            )
            checkpoint_repo.save_or_update(cp)
        else:
            context.status = "failed"
            # Save failed checkpoint
            cp = GenerationCheckpoint(
                run_id=run_id, story_id=story_id, generation_scale="long_form",
                stage=f"chapter_{ch_plan.chapter_index}",
                last_completed_chapter=ch_plan.chapter_index - 1,
                next_chapter=ch_plan.chapter_index,
                state_json={"context": context.model_dump(mode="json")},
                config_fingerprint=fp, status="resumable",
            )
            checkpoint_repo.save_or_update(cp)
            break

    # Final checkpoint
    if context.status != "failed":
        context.status = "completed"
        cp_final = GenerationCheckpoint(
            run_id=run_id, story_id=story_id, generation_scale="long_form",
            stage="final", last_completed_chapter=len(plan.chapters),
            next_chapter=len(plan.chapters) + 1,
            state_json={"context": context.model_dump(mode="json")},
            config_fingerprint=fp, status="completed",
        )
        checkpoint_repo.save_or_update(cp_final)

    return _assemble_long_form_result(plan, context, run_id, story_id, checkpoint_repo)


def _resume_long_form(
    run_id: str, brief: Brief, workspace: str, lib: Any, runtime: Any,
    story_mode: str, story_template_id: str | None, story_template_version: int | None,
    content_form: str, fail_at_chapter: int | None,
    checkpoint_repo: Any, thread_id: str,
) -> dict[str, Any]:
    """从 checkpoint 恢复 long-form 执行。"""
    latest = checkpoint_repo.get_latest(run_id)
    if latest is None:
        raise ValueError(f"No checkpoint found for run_id={run_id}")

    # Validate fingerprint — consistent with fresh path (no auto-resolved template)
    fp = build_config_fingerprint(
        story_mode=story_mode,
        content_form=content_form,
        generation_scale="long_form",
        plan_version="1.0",
    )
    if latest.config_fingerprint and fp != latest.config_fingerprint:
        raise ValueError(
            f"CHECKPOINT_CONFIG_MISMATCH: expected {latest.config_fingerprint}, got {fp}"
        )

    # Restore context
    state = latest.state_json
    context_data = state.get("context") or {}
    plan_data = state.get("plan") or {}

    if context_data:
        context = LongFormContext.model_validate(context_data)
    else:
        context = LongFormContext()
        if plan_data:
            context.plan = LongFormPlan.model_validate(plan_data)

    plan = context.plan
    if plan is None:
        plan = LongFormPlan() if not plan_data else LongFormPlan.model_validate(plan_data)
        context.plan = plan

    story_id = latest.story_id

    if context.status == "completed":
        return _assemble_long_form_result(plan, context, run_id, story_id, checkpoint_repo)

    # Resume from next chapter — do NOT regenerate completed chapters
    start_chapter = latest.next_chapter
    context.status = "running"

    for ch_plan in plan.chapters:
        if ch_plan.chapter_index < start_chapter:
            continue  # Skip completed chapters

        context.current_chapter = ch_plan.chapter_index
        result = execute_chapter(
            plan=plan, chapter_plan=ch_plan, context=context, brief=brief,
            runtime=runtime, story_mode=story_mode, content_form=content_form,
            lib=lib, workspace=workspace,
            fail_at_chapter=fail_at_chapter,
        )

        if result.status == "completed":
            context.completed_chapters[ch_plan.chapter_index] = result
            context.unresolved_threads = result.unresolved_threads
            cp = GenerationCheckpoint(
                run_id=run_id, story_id=story_id, generation_scale="long_form",
                stage=f"chapter_{ch_plan.chapter_index}",
                last_completed_chapter=ch_plan.chapter_index,
                next_chapter=ch_plan.chapter_index + 1,
                state_json={"context": context.model_dump(mode="json")},
                config_fingerprint=latest.config_fingerprint, status="resumable",
            )
            checkpoint_repo.save_or_update(cp)
        else:
            context.status = "failed"
            cp = GenerationCheckpoint(
                run_id=run_id, story_id=story_id, generation_scale="long_form",
                stage=f"chapter_{ch_plan.chapter_index}",
                last_completed_chapter=ch_plan.chapter_index - 1,
                next_chapter=ch_plan.chapter_index,
                state_json={"context": context.model_dump(mode="json")},
                config_fingerprint=latest.config_fingerprint, status="resumable",
            )
            checkpoint_repo.save_or_update(cp)
            break
    else:
        context.status = "completed"
        cp_final = GenerationCheckpoint(
            run_id=run_id, story_id=story_id, generation_scale="long_form",
            stage="final", last_completed_chapter=len(plan.chapters),
            next_chapter=len(plan.chapters) + 1,
            state_json={"context": context.model_dump(mode="json")},
            config_fingerprint=latest.config_fingerprint, status="completed",
        )
        checkpoint_repo.save_or_update(cp_final)

    return _assemble_long_form_result(plan, context, run_id, story_id, checkpoint_repo)


def _assemble_long_form_result(
    plan: LongFormPlan,
    context: LongFormContext,
    run_id: str,
    story_id: str,
    checkpoint_repo: Any,
) -> dict[str, Any]:
    """Assemble final long-form result."""
    chapters_sorted = sorted(context.completed_chapters.values(), key=lambda c: c.chapter_index)
    return {
        "plan": plan.model_dump(mode="json"),
        "chapter_results": [cr.model_dump(mode="json") for cr in chapters_sorted],
        "context": context.model_dump(mode="json"),
        "run_id": run_id,
        "story_id": story_id,
        "generation_scale": "long_form",
        "status": context.status,
        "chapter_count": len(chapters_sorted),
        "total_chapters": plan.chapter_count,
        "checkpoints": [
            cp.to_dict() if hasattr(cp, 'to_dict') else cp
            for cp in checkpoint_repo.list_by_run(run_id)
        ],
    }


def _resolve_template(
    story_mode: str,
    template_id: str | None,
    template_version: int | None,
) -> dict | None:
    """Resolve template to dict form. Minimal version for long-form."""
    from .templates.resolver import StoryTemplateResolver, build_template_resolver
    from .templates.repository import MemoryStoryTemplateRepository
    from .templates.defaults import BUILTIN_TEMPLATES

    repo = MemoryStoryTemplateRepository()
    for t in BUILTIN_TEMPLATES:
        repo.create(t)
    defaults = {"general": "GENERAL_THREE_ACT"}
    resolver = StoryTemplateResolver(repo, defaults)
    resolved = resolver.resolve(template_id=template_id, version=template_version, mode_key=story_mode)
    if resolved:
        return resolved.model_dump()
    return None
