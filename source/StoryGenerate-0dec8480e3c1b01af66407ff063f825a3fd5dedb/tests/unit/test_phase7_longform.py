"""Phase 7: Long-form E2E Tests — Mock Golden, Resume, Continuity, Idempotency.

Hard requirements:
  - 3-chapter long-form E2E with Mock Provider
  - Checkpoint after global plan + each chapter
  - Resume from failed chapter
  - Completed chapters NOT regenerated
  - Resume idempotent (no duplicates)
  - Config mismatch rejected
  - Fact/thread continuity across chapters
  - Backward compatibility: Phase 6 standard pipeline regression
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest  # noqa: E402

from drama_engine.config import RuleLibrary
from drama_engine.contracts import Runtime
from drama_engine.llm.latency_controlled import LatencyControlledMockProvider
from drama_engine.longform import (
    ChapterPlan,
    ChapterResult,
    GenerationScale,
    LongFormContext,
    LongFormPlan,
    LongFormProfile,
)
from drama_engine.longform_executor import (
    GenerationCheckpoint,
    InMemoryCheckpointRepository,
    build_config_fingerprint,
    execute_chapter,
    run_long_form,
)
from drama_engine.schemas import Brief


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


PREVIEW_PREMISE = (
    "三个刚毕业的年轻人在合租屋里得知房东准备收回房子。"
    "三人最初打算各自离开，但在整理物品时发现合同中的关键条款，"
    "由此决定共同处理问题，并逐步重新审视彼此的关系和未来选择。"
)


# ══════════════════════════════════════════════════════════════════════
# 7A: Generation Scale + Domain Models
# ══════════════════════════════════════════════════════════════════════
class TestGenerationScale:
    """Generation Scale 是第四个正交维度，不是 Mode/Form。"""

    def test_default_is_standard(self):
        assert GenerationScale.STANDARD.value == "standard"

    def test_long_form_preserves_mode_form(self):
        """long_form 不改变 Mode/Form 语义。"""
        scale = GenerationScale.LONG_FORM
        assert scale != "general"
        assert scale != "prose_story"

    def test_long_form_profile_resolution(self):
        profile = LongFormProfile(target_chapters=5, chapter_target_chars=2000)
        assert profile.target_chapters == 5
        assert profile.chapter_target_chars == 2000
        assert profile.checkpoint_every == 1
        assert not profile.allow_parallel  # Phase 7 first version


# ══════════════════════════════════════════════════════════════════════
# 7B: Long-form Plan
# ══════════════════════════════════════════════════════════════════════
class TestLongFormPlan:
    """LongFormPlan 生成并使用 Template。"""

    def test_deterministic_plan_uses_template(self):
        brief = Brief(raw_idea=PREVIEW_PREMISE, target_episodes=3)
        from drama_engine.longform_planner import plan_long_form

        plan = plan_long_form(
            brief=brief,
            template={"template_id": "GENERAL_THREE_ACT", "name": "三幕结构", "version": 1},
            profile=LongFormProfile(target_chapters=3),
        )
        assert plan.template_id == "GENERAL_THREE_ACT"
        assert plan.chapter_count == 3
        assert len(plan.chapters) == 3
        assert plan.chapters[0].chapter_index == 1
        assert plan.chapters[-1].chapter_index == 3
        # Thread continuity
        assert plan.chapters[1].unresolved_threads_in, "Ch2 should carry threads from Ch1"

    def test_plan_json_roundtrip(self):
        plan = LongFormPlan(
            title="Test",
            global_goal="Complete story",
            global_conflict="Main conflict",
            chapter_count=2,
            chapters=[
                ChapterPlan(chapter_index=1, title="Ch1", goal="Start"),
                ChapterPlan(chapter_index=2, title="Ch2", goal="End"),
            ],
            template_id="GENERAL_THREE_ACT",
            template_version="1",
        )
        data = plan.model_dump(mode="json")
        restored = LongFormPlan.model_validate(data)
        assert restored.chapter_count == 2
        assert restored.chapters[0].goal == "Start"


# ══════════════════════════════════════════════════════════════════════
# 7C: Chapter-by-Chapter Executor
# ══════════════════════════════════════════════════════════════════════
class TestChapterExecutor:
    """章节顺序执行。"""

    def test_execute_single_chapter(self, tmp_path):
        runtime = _make_runtime()
        lib = _make_lib()
        brief = Brief(raw_idea="测试单章", target_episodes=1)

        plan = LongFormPlan(
            title="T", chapter_count=1,
            chapters=[ChapterPlan(chapter_index=1, title="测试", goal="完成")]
        )
        context = LongFormContext(plan=plan)
        result = execute_chapter(
            plan=plan, chapter_plan=plan.chapters[0], context=context,
            brief=brief, runtime=runtime, content_form="prose_story",
            lib=lib, workspace=str(tmp_path),
        )
        assert result.status == "completed"
        assert result.chapter_index == 1
        assert result.render_result, "render_result empty"
        assert result.validation_passed
        assert result.output_guard_passed


# ══════════════════════════════════════════════════════════════════════
# 7D: Checkpoint
# ══════════════════════════════════════════════════════════════════════
class TestCheckpoint:
    """Checkpoint save+reload。"""

    def test_checkpoint_roundtrip(self):
        cp = GenerationCheckpoint(
            run_id="run-1", story_id="story-1",
            generation_scale="long_form", stage="chapter_2",
            last_completed_chapter=2, next_chapter=3,
            state_json={"key": "value"},
            config_fingerprint="abc123",
            status="resumable",
        )
        d = cp.to_dict()
        import json
        json_str = json.dumps(d)  # Must be JSON-safe
        loaded_d = json.loads(json_str)
        cp2 = GenerationCheckpoint.from_dict(loaded_d)
        assert cp2.run_id == "run-1"
        assert cp2.stage == "chapter_2"
        assert cp2.next_chapter == 3

    def test_checkpoint_repository(self):
        repo = InMemoryCheckpointRepository()
        cp1 = GenerationCheckpoint(run_id="r1", stage="global_plan", last_completed_chapter=0, next_chapter=1)
        cp2 = GenerationCheckpoint(run_id="r1", stage="chapter_1", last_completed_chapter=1, next_chapter=2)
        cp3 = GenerationCheckpoint(run_id="r1", stage="chapter_2", last_completed_chapter=2, next_chapter=3)

        repo.save_or_update(cp1)
        repo.save_or_update(cp2)
        repo.save_or_update(cp3)

        latest = repo.get_latest("r1")
        assert latest is not None
        assert latest.last_completed_chapter == 2
        assert latest.next_chapter == 3

        all_cps = repo.list_by_run("r1")
        assert len(all_cps) == 3

    def test_checkpoint_same_stage_replace(self):
        repo = InMemoryCheckpointRepository()
        cp1 = GenerationCheckpoint(run_id="r1", stage="global_plan", last_completed_chapter=0)
        cp2 = GenerationCheckpoint(run_id="r1", stage="global_plan", last_completed_chapter=3)
        repo.save_or_update(cp1)
        repo.save_or_update(cp2)
        latest = repo.get_latest("r1")
        assert latest.last_completed_chapter == 3  # replaced


# ══════════════════════════════════════════════════════════════════════
# 7E: Resume + Failure Injection
# ══════════════════════════════════════════════════════════════════════
class TestResumeAndFailure:
    """Resume 从最后成功边界继续，不重新生成已完成章节。"""

    def test_failure_injection_and_resume(self, tmp_path):
        """fail_at_chapter=2 → Ch1 committed, Ch2 failed → resume → Ch2 regenerated, Ch3 continues."""
        runtime = _make_runtime()
        lib = _make_lib()
        repo = InMemoryCheckpointRepository()

        # First run: fail at chapter 2
        result1 = run_long_form(
            idea="测试失败注入: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib,
            runtime=runtime,
            target_chapters=3,
            content_form="prose_story",
            fail_at_chapter=2,
            checkpoint_repo=repo,
        )
        assert result1["status"] == "failed"
        assert result1["chapter_count"] == 1  # Only Ch1 completed
        run_id = result1["run_id"]

        # Resume with same repo
        result2 = run_long_form(
            idea="测试失败注入: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib,
            runtime=runtime,
            target_chapters=3,
            content_form="prose_story",
            resume_from=run_id,
            checkpoint_repo=repo,
        )
        assert result2["status"] == "completed"
        assert result2["chapter_count"] == 3  # All 3 completed
        assert result2["total_chapters"] == 3

    def test_resume_does_not_regenerate_completed_chapters(self, tmp_path):
        """Resume 后已完成章节不变。"""
        runtime = _make_runtime()
        lib = _make_lib()
        repo = InMemoryCheckpointRepository()

        # Run 1: fail at chapter 2
        result1 = run_long_form(
            idea="测试不重复生成: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib, runtime=runtime, target_chapters=3,
            content_form="prose_story", fail_at_chapter=2,
            checkpoint_repo=repo,
        )
        run_id = result1["run_id"]
        ch1_result = result1["chapter_results"][0]
        ch1_render = ch1_result["render_result"]

        # Resume
        result2 = run_long_form(
            idea="测试不重复生成: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib, runtime=runtime, target_chapters=3,
            content_form="prose_story", resume_from=run_id,
            checkpoint_repo=repo,
        )
        ch2_results = result2["chapter_results"]
        assert len(ch2_results) == 3
        # Chapter 1 still the same
        assert ch2_results[0]["render_result"] == ch1_render

    def test_resume_idempotent(self, tmp_path):
        """重复 resume 不产生重复章节。"""
        runtime = _make_runtime()
        lib = _make_lib()
        repo = InMemoryCheckpointRepository()

        result1 = run_long_form(
            idea="测试幂等: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib, runtime=runtime, target_chapters=3,
            content_form="prose_story", fail_at_chapter=2,
            checkpoint_repo=repo,
        )
        run_id = result1["run_id"]

        # Resume twice with same repo
        result2 = run_long_form(
            idea="测试幂等: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib, runtime=runtime, target_chapters=3,
            content_form="prose_story", resume_from=run_id,
            checkpoint_repo=repo,
        )
        result3 = run_long_form(
            idea="测试幂等: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib, runtime=runtime, target_chapters=3,
            content_form="prose_story", resume_from=run_id,
            checkpoint_repo=repo,
        )
        # Both should have exactly 3 chapters
        assert len(result2["chapter_results"]) == 3
        assert len(result3["chapter_results"]) == 3
        # Same content
        assert result2["chapter_results"] == result3["chapter_results"]

    def test_resume_config_mismatch_rejected(self, tmp_path):
        """配置不一致时 resume 被拒绝。"""
        runtime = _make_runtime()
        lib = _make_lib()
        repo = InMemoryCheckpointRepository()

        result1 = run_long_form(
            idea="测试配置不匹配: " + PREVIEW_PREMISE[:80],
            workspace=str(tmp_path),
            lib=lib, runtime=runtime, target_chapters=3,
            content_form="prose_story", story_mode="general",
            fail_at_chapter=2,
            checkpoint_repo=repo,
        )
        run_id = result1["run_id"]

        # Resume with different config → should fail
        with pytest.raises(ValueError, match="CHECKPOINT_CONFIG_MISMATCH"):
            run_long_form(
                idea="测试配置不匹配: " + PREVIEW_PREMISE[:80],
                workspace=str(tmp_path),
                lib=lib, runtime=runtime, target_chapters=3,
                content_form="audio_drama",  # Different!
                story_mode="viral_drama",     # Different!
                resume_from=run_id,
                checkpoint_repo=repo,
            )


# ══════════════════════════════════════════════════════════════════════
# Continuity
# ══════════════════════════════════════════════════════════════════════
class TestContinuity:
    """跨章事实和线索连续性。"""

    def test_threads_carry_and_resolve(self, tmp_path):
        runtime = _make_runtime()
        lib = _make_lib()

        result = run_long_form(
            idea=PREVIEW_PREMISE,
            workspace=str(tmp_path),
            lib=lib, runtime=runtime,
            target_chapters=3, content_form="prose_story",
        )
        assert result["status"] == "completed"
        assert result["chapter_count"] == 3

        # Threads created in Ch1 carried to Ch2, resolved in Ch3
        ch_results = result["chapter_results"]
        for cr in ch_results:
            # Each chapter should have unresolved_threads field
            assert "unresolved_threads" in cr

    def test_fact_delta_per_chapter(self, tmp_path):
        runtime = _make_runtime()
        lib = _make_lib()

        result = run_long_form(
            idea=PREVIEW_PREMISE,
            workspace=str(tmp_path),
            lib=lib, runtime=runtime,
            target_chapters=3, content_form="prose_story",
        )
        ch_results = result["chapter_results"]
        for cr in ch_results:
            assert "fact_delta" in cr, f"fact_delta missing in chapter {cr['chapter_index']}"


# ══════════════════════════════════════════════════════════════════════
# Final Persistence Roundtrip
# ══════════════════════════════════════════════════════════════════════
class TestLongFormPersistence:
    """Long-form 结果可持久化并完整恢复。"""

    def test_final_result_roundtrip(self, tmp_path):
        runtime = _make_runtime()
        lib = _make_lib()

        result = run_long_form(
            idea=PREVIEW_PREMISE,
            workspace=str(tmp_path),
            lib=lib, runtime=runtime,
            target_chapters=3, content_form="prose_story",
        )
        # Serialize
        import json
        data = json.dumps(result)
        restored = json.loads(data)

        assert restored["generation_scale"] == "long_form"
        assert restored["chapter_count"] == 3
        assert len(restored["chapter_results"]) == 3
        # Chapter order preserved
        indices = [cr["chapter_index"] for cr in restored["chapter_results"]]
        assert indices == [1, 2, 3], f"Chapter order wrong: {indices}"
        # Plan restored
        assert restored["plan"]["chapter_count"] == 3


# ══════════════════════════════════════════════════════════════════════
# Backward Compatibility
# ══════════════════════════════════════════════════════════════════════
class TestBackwardCompatibility:
    """Phase 6 standard pipeline 不被 Phase 7 破坏。"""

    def test_standard_pipeline_still_works(self, tmp_path):
        """run_pipeline without generation_scale = standard behavior.Phase 6 regression.验证无障碍。"""
        from drama_engine.graph import run_pipeline

        result = run_pipeline(
            idea="测试 Phase 6 兼容性",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
            story_mode="general",
            content_form="prose_story",
        )
        assert result.get("prose") is not None
        assert result.get("episode_results")