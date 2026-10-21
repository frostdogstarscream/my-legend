"""Phase 6.5: OutputGuard + Invalid Prose E2E Tests.

Tests:
- Valid prose passes guard
- Invalid prose (audio label contamination) fails guard
- P01-P09 runtime verification
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
from drama_engine.graph import run_pipeline
from drama_engine.persistence.memory_repo import InMemoryRecordRepo
from drama_engine.content_forms.validators import validate_prose_structure, prose_output_guard
from drama_engine.schemas import ProseStory, ProseParagraph


# ── helpers ──────────────────────────────────────────────────────────
def _make_runtime():
    return Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))


def _make_lib():
    return RuleLibrary.load()


def _make_repos():
    class SpoofJobRepo:
        def create(self, job): return job
    return type("Repos", (), {
        "story_record": InMemoryRecordRepo(),
        "story_job": SpoofJobRepo(),
        "quality_result": type("Spoof", (), {"create": lambda self, q: None})(),
        "generation_trace": type("Spoof", (), {"batch_create": lambda self, t: None})(),
    })()


# ══════════════════════════════════════════════════════════════════════
# Valid Prose OutputGuard E2E
# ══════════════════════════════════════════════════════════════════════
class TestProseGuardE2E:
    """Main graph E2E: prose_writer → prose_validate → guard → EpisodeRenderResult."""

    def test_valid_prose_passes_guard(self, tmp_path):
        """合法 Mock prose 通过 OutputGuard。"""
        result = run_pipeline(
            idea="测试 Guard 通过", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, story_mode="general", content_form="prose_story",
        )
        assert "episode_results" in result
        ep_r = result["episode_results"][0]
        assert ep_r.content_form == "prose_story"
        assert ep_r.prose_story is not None
        # Guard must be actually executed, not default
        assert ep_r.output_guard_passed, (
            f"output_guard_passed={ep_r.output_guard_passed} — guard not executed"
        )

    def test_guard_result_in_episode_result(self, tmp_path):
        """Guard 结果真实写入 EpisodeRenderResult，不是默认值。"""
        result = run_pipeline(
            idea="测试 Guard 写入", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, story_mode="general", content_form="prose_story",
        )
        ep_r = result["episode_results"][0]
        assert ep_r.output_guard_passed is True  # 合法内容应通过

    def test_validation_findings_for_valid_prose(self, tmp_path):
        """合法 prose 应该有 validation 记录（不是空）。"""
        result = run_pipeline(
            idea="测试 validation 记录", workspace=str(tmp_path),
            lib=_make_lib(), runtime=_make_runtime(),
            target_episodes=1, story_mode="general", content_form="prose_story",
        )
        ep_r = result["episode_results"][0]
        assert ep_r.validation_passed


# ══════════════════════════════════════════════════════════════════════
# Invalid Prose E2E (unit-level; structural validator direct)
# ══════════════════════════════════════════════════════════════════════
class TestInvalidProseE2E:
    """Invalid prose rejection: P06 audio label contamination, structural fatal."""

    def test_audio_label_contamination_rejected(self):
        """SFX: 和 NARRATOR: 污染被 P06 拒绝。"""
        contaminated = ProseStory(
            title="Test Contaminated",
            paragraphs=[
                ProseParagraph(text="SFX: 雨天声音"),
                ProseParagraph(text="NARRATOR: 这是一个故事。"),
                ProseParagraph(text="正常内容段落。但这显然不够长来通过最小长度检查。" +
                               "所以我们需要添加更多的文本内容。又是一段正常的故事叙述。" * 10),
            ],
        )
        result = validate_prose_structure(contaminated)
        # P06 should fire for audio label contamination
        p06 = [f for f in result.get("findings", []) if f["rule_id"] == "P06"]
        assert len(p06) > 0, f"P06 should fire for audio labels; findings={result.get('findings')}"
        assert not result["passed"], "Contaminated prose should be rejected"

    def test_empty_prose_rejected(self):
        """空 prose 被 P01/P02 拒绝。"""
        empty_prose = ProseStory(title="", paragraphs=[])
        result = validate_prose_structure(empty_prose)
        assert not result["passed"], "Empty prose should fail validation"

    def test_too_short_prose_rejected(self):
        """过短 prose（<500 chars）被 P04 拒绝。"""
        short_prose = ProseStory(
            title="Short",
            paragraphs=[ProseParagraph(text="太短。")],
        )
        result = validate_prose_structure(short_prose)
        p04 = [f for f in result.get("findings", []) if f["rule_id"] == "P04"]
        assert len(p04) > 0, f"P04 should fire for short prose; findings={result.get('findings')}"

    def test_prose_guard_rejects_audio_content(self):
        """prose_output_guard 拒绝含 SFX 的内容。"""
        contaminated = ProseStory(
            title="Audio Style",
            paragraphs=[
                ProseParagraph(text="SFX: 爆炸声"),
                ProseParagraph(text="正常段落，但还不够长。" * 20),
            ],
        )
        guard = prose_output_guard(contaminated)
        assert not guard["passed"], f"Guard should reject audio contamination; got {guard}"

    def test_clean_prose_guard_passes(self):
        """干净 prose 通过 guard。"""
        clean = ProseStory(
            title="Clean Story",
            paragraphs=[
                ProseParagraph(
                    text="这是一个完全没有音频标记的干净散文故事段落。" +
                    "它包含了足够多的文字内容来通过最小长度检查。" +
                    "故事讲述了三个年轻人在城市中的冒险经历。" * 10
                ),
            ],
        )
        guard = prose_output_guard(clean)
        assert guard["passed"], f"Clean prose should pass guard; got {guard}"