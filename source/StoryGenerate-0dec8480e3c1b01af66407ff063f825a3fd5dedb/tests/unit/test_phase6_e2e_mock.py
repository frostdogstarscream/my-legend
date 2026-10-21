"""Phase 6.1: E2E Mock Pipeline Tests — 4 Combination Matrix.

Hard requirement: 真实运行 Mock Pipeline，不能只用 code audit。
验证所有 4 个组合都能在 Mock Provider 下完成完整路径。
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


def _make_runtime():
    """构建带 MockLLMProvider 的 Runtime。"""
    provider = LatencyControlledMockProvider(delay_sec=0.01)
    return Runtime(llm=provider)


def _make_lib():
    return RuleLibrary.load()


# ============================================================================
# E2E: viral_drama + audio_drama (Legacy Default)
# ============================================================================
class TestViralAudioE2E:
    """Legacy: viral_drama + audio_drama 默认行为。"""

    def test_e2e_viral_audio_has_output(self, tmp_path):
        """基本回归：output 存在。"""
        result = run_pipeline(
            idea="社畜穿越成将军府嫡女，用现代记账法整顿府库",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
            story_mode="viral_drama",
            content_form="audio_drama",
        )
        assert "episodes" in result or "episode" in result

    def test_e2e_viral_audio_default(self, tmp_path):
        """不传 content_form → audio_drama（完全向后兼容）。"""
        result = run_pipeline(
            idea="社畜穿越成将军府嫡女",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
        )
        assert "episodes" in result or "episode" in result


# ============================================================================
# E2E: general + audio_drama
# ============================================================================
class TestGeneralAudioE2E:
    """General mode + audio_drama: 证明 General ≠ Prose。"""

    def test_e2e_general_audio(self, tmp_path):
        result = run_pipeline(
            idea="三个刚毕业的年轻人在合租屋里发现房东准备突然收回房子",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
            story_mode="general",
            content_form="audio_drama",
        )
        assert "episodes" in result or "episode" in result


# ============================================================================
# E2E: general + prose_story
# ============================================================================
class TestGeneralProseE2E:
    """General mode + prose_story: 核心验证。"""

    def test_e2e_general_prose_has_output(self, tmp_path):
        """ProseStory 产出验证。"""
        result = run_pipeline(
            idea="三个刚毕业的年轻人在合租屋里发现房东准备突然收回房子，他们必须在一周内决定是各自离开还是一起留下来。",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
            story_mode="general",
            content_form="prose_story",
        )
        assert "prose" in result, f"Expected prose, got: {list(result.keys())}"
        prose = result["prose"]
        assert prose.title
        assert len(prose.paragraphs) > 0
        assert prose.plain_text

    def test_e2e_general_prose_no_audio_labels(self, tmp_path):
        """Prose 输出不应含 Audio 标签。"""
        result = run_pipeline(
            idea="一个年轻画家在旅途中寻找灵感的故事",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
            story_mode="general",
            content_form="prose_story",
        )
        prose = result.get("prose")
        if prose is None:
            pytest.skip("Prose output not available")
        for label in ("SFX:", "NARRATOR:", "【音效】", "【旁白】"):
            assert label not in prose.plain_text, f"Audio label '{label}' in prose"


# ============================================================================
# E2E: viral_drama + prose_story
# ============================================================================
class TestViralProseE2E:
    """Viral mode + prose_story: 证明 Mode ≠ Content Form。"""

    def test_e2e_viral_prose(self, tmp_path):
        result = run_pipeline(
            idea="社畜穿越成将军府嫡女，用现代记账法整顿府库",
            workspace=str(tmp_path),
            lib=_make_lib(),
            runtime=_make_runtime(),
            target_episodes=1,
            story_mode="viral_drama",
            content_form="prose_story",
        )
        assert "prose" in result, (
            f"viral_drama + prose_story must produce prose. Got: {list(result.keys())}"
        )
        prose = result["prose"]
        assert prose.title
        assert len(prose.paragraphs) > 0


# ============================================================================
# Phase 6.1 关键验收：Prose 不绕过 Planning
# ============================================================================
class TestProseDoesNotBypassPlanning:
    """关键验收 1+2: Prose 前必须存在 Characters / Behavior / Outline / Ledger / Plan。"""

    def test_planning_nodes_exist_in_graph(self):
        """验证 episode graph 包含 gen_beats（共享 Planning）。"""
        from drama_engine.nodes.episode import build_episode_graph
        g = build_episode_graph()
        nodes = g.get_graph().nodes
        assert "gen_beats" in nodes, "gen_beats must exist for shared planning"

    def test_route_start_always_gen_beats(self):
        """route_start 不按 content_form 分流。"""
        from drama_engine.nodes.episode import route_start
        assert route_start({"content_form": {"key": "prose_story"}}) == "gen_beats"
        assert route_start({"content_form": {"key": "audio_drama"}}) == "gen_beats"
        assert route_start({}) == "gen_beats"

    def test_prose_path_after_planning(self):
        """prose_writer 只在 gen_beats 之后才执行。"""
        from drama_engine.nodes.episode import route_content_form
        # gen_beats 后 → content form 分叉
        assert route_content_form({"content_form": {"key": "prose_story"}}) == "prose_writer"
        assert route_content_form({"content_form": {"key": "audio_drama"}}) == "validate_ep"

    def test_prose_output_has_no_episodes_path(self):
        """Prose 路径不产生 audio episodes（结构验证）。"""
        from drama_engine.nodes.episode import build_episode_graph
        g = build_episode_graph()
        edges = g.get_graph().edges
        # prose_writer → prose_validate → END (not to validate_ep)
        prose_edges = [e for e in edges if e[0] == "prose_writer"]
        for e in prose_edges:
            assert e[1] not in ("validate_ep", "repair_audio",
                                "repair_beat", "repair_compliance")
        # prose_validate → END
        guard_edges = [e for e in edges if e[0] == "prose_validate"]
        assert any(e[1] == "__end__" for e in guard_edges)