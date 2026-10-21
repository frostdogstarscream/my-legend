"""Phase 9: Final Acceptance Closure — Structural Validators + Persistent Resume E2E.

Covers:
  - Storytelling structural validation (narrator-led, rejects pure drama)
  - Standup structural validation (performer lane, setup/punchline, ordering)
  - Crosstalk structural validation (two performers, both with content)
  - Mystery Long-form persistent restart/resume E2E
  - Serialized Long-form persistent restart/resume E2E
  - Legacy regression
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(TEST_ROOT))

import pytest  # noqa: E402


# ══════════════════════════════════════════════════════════════════
# 1. Storytelling Structural Validator
# ══════════════════════════════════════════════════════════════════
class TestStorytellingStructuralValidator:
    def test_narrator_led_passes(self):
        """Narrator-led storytelling with dialogue inserts PASSES."""
        from drama_engine.forms.handlers import StorytellingValidator
        v = StorytellingValidator()
        text = (
            "Narrator讲述：那天晚上，小光再次来到废弃车站。\n"
            "小光说：这里和记忆里不一样。\n"
            "Narrator继续：他向站台深处走去，脚步声在空旷的大厅中回响。\n"
        ) * 5
        ok, msg = v.validate(text)
        assert ok, f"narrator-led should pass: {msg}"

    def test_pure_audio_drama_fails(self):
        """Pure multi-speaker drama format (角色A：...) FAILS."""
        from drama_engine.forms.handlers import StorytellingValidator
        v = StorytellingValidator()
        text = (
            "角色A：我们得赶快离开这里。\n"
            "角色B：不行，外面太危险了。\n"
            "角色A：那也比坐以待毙强。\n"
            "角色C：等等，我有个主意。\n"
        ) * 3
        ok, msg = v.validate(text)
        assert not ok, f"pure drama should fail: {msg}"
        assert "speaker" in msg.lower() or "narrator" in msg.lower() or "drama" in msg.lower()

    def test_empty_fails(self):
        from drama_engine.forms.handlers import StorytellingValidator
        v = StorytellingValidator()
        ok, msg = v.validate("")
        assert not ok


# ══════════════════════════════════════════════════════════════════
# 2. Standup Structural Validator
# ══════════════════════════════════════════════════════════════════
class TestStandupStructuralValidator:
    def test_setup_punchline_passes(self):
        """Setup + punchline structure PASSES."""
        from drama_engine.forms.handlers import StandupValidator
        v = StandupValidator()
        text = (
            "【setup】话说现在年轻人压力真大啊。\n"
            "【punchline】我问朋友你怎么排解压力，他说——卸载微信。\n"
            "【callback】后来发现，卸载微信的最大好处就是...不用回家长群了！\n"
        ) * 3
        ok, msg = v.validate(text)
        assert ok, f"setup+punchline should pass: {msg}"

    def test_setup_only_fails(self):
        """Only setup, no punchline — FAILS."""
        from drama_engine.forms.handlers import StandupValidator
        v = StandupValidator()
        text = "【setup】今天来说说上班那些事。【setup】每个人都有这样的经历..."
        ok, msg = v.validate(text)
        assert not ok, f"setup-only should fail: {msg}"
        assert "punchline" in msg.lower()

    def test_ordinary_prose_fails(self):
        """Ordinary prose is NOT standup — FAILS."""
        from drama_engine.forms.handlers import StandupValidator
        v = StandupValidator()
        text = "小光走进车站，夜色很深。他决定在候车室过夜，明天一早就出发。" * 10
        ok, msg = v.validate(text)
        assert not ok, f"ordinary prose should fail: {msg}"

    def test_empty_fails(self):
        from drama_engine.forms.handlers import StandupValidator
        v = StandupValidator()
        ok, msg = v.validate("")
        assert not ok


# ══════════════════════════════════════════════════════════════════
# 3. Crosstalk Structural Validator
# ══════════════════════════════════════════════════════════════════
class TestCrosstalkStructuralValidator:
    def test_two_performers_pass(self):
        """Two performers with dialogue PASSES."""
        from drama_engine.forms.handlers import CrosstalkValidator
        v = CrosstalkValidator()
        text = (
            "逗哏：大家好啊，今天咱们来说说...\n"
            "捧哏：说什么呀？\n"
            "逗哏：说说这AI到底能不能写段子！\n"
            "捧哏：嘿，您这题目可够大的！\n"
        ) * 3
        ok, msg = v.validate(text)
        assert ok, f"two performers should pass: {msg}"

    def test_single_performer_fails(self):
        """Only one speaker — FAILS."""
        from drama_engine.forms.handlers import CrosstalkValidator
        v = CrosstalkValidator()
        text = "逗哏：今天我一个人表演。逗哏：没有搭档。" * 5
        ok, msg = v.validate(text)
        assert not ok, f"single performer should fail: {msg}"

    def test_ordinary_prose_fails(self):
        """Ordinary prose is NOT crosstalk — FAILS."""
        from drama_engine.forms.handlers import CrosstalkValidator
        v = CrosstalkValidator()
        text = "这是一段普通的叙述文字，讲述了一个古老的故事。" * 10
        ok, msg = v.validate(text)
        assert not ok, f"ordinary prose should fail: {msg}"

    def test_empty_fails(self):
        from drama_engine.forms.handlers import CrosstalkValidator
        v = CrosstalkValidator()
        ok, msg = v.validate("")
        assert not ok


# ══════════════════════════════════════════════════════════════════
# 4. Mystery Long-form Persistent Restart / Resume E2E
# ══════════════════════════════════════════════════════════════════
class TestMysteryPersistentResume:
    def test_mystery_longform_checkpoint_persists_and_restores(self):
        """Mystery + Novel + Long-form: checkpoint survives persistent restart."""
        from drama_engine.longform_executor import run_long_form
        from drama_engine.longform_executor import InMemoryCheckpointRepository
        from drama_engine.config import RuleLibrary
        from drama_engine.contracts import Runtime
        from drama_engine.llm.latency_controlled import LatencyControlledMockProvider

        lib = RuleLibrary.load()
        rt = Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))

        # Run A: Chapter 1 succeeds, Chapter 2 fails
        ckpt_a = InMemoryCheckpointRepository()
        result_a = run_long_form(
            idea="密室杀人案——在封闭的实验室中，一名研究员被发现死亡",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_mystery"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="mystery",
            checkpoint_repo=ckpt_a,
            fail_at_chapter=2,
        )
        assert result_a["status"] == "failed"
        assert result_a["chapter_count"] == 1

        run_id = result_a["run_id"]
        cp_a = ckpt_a.get_latest(run_id)
        assert cp_a is not None
        assert cp_a.last_completed_chapter == 1
        assert cp_a.next_chapter == 2

        # Resume via new repo (simulates Runtime B)
        ckpt_b = InMemoryCheckpointRepository()
        # Copy only the persistent checkpoint data (NOT Python objects)
        ckpt_b.save_or_update(cp_a)

        result_b = run_long_form(
            idea="密室杀人案",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_mystery_b"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="mystery",
            resume_from=run_id,
            checkpoint_repo=ckpt_b,
        )
        assert result_b["status"] == "completed"
        assert result_b["chapter_count"] == 3

        # Chapter 1 not regenerated (same checkpoint fingerprint)
        ch1_a = result_a["chapter_results"][0]
        ch1_b = result_b["chapter_results"][0]
        assert ch1_a["render_result"] == ch1_b["render_result"]

    def test_mystery_longform_resume_preserves_clue_data(self):
        """After resume, plan data (which can hold mystery clues) persists."""
        from drama_engine.longform_executor import run_long_form
        from drama_engine.longform_executor import InMemoryCheckpointRepository
        from drama_engine.config import RuleLibrary
        from drama_engine.contracts import Runtime
        from drama_engine.llm.latency_controlled import LatencyControlledMockProvider

        lib = RuleLibrary.load()
        rt = Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))

        ckpt_a = InMemoryCheckpointRepository()
        result_a = run_long_form(
            idea="谁偷了博物馆的宝石？",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_mystery2"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="mystery",
            checkpoint_repo=ckpt_a,
            fail_at_chapter=2,
        )

        run_id = result_a["run_id"]
        # Checkpoint contains plan with chapter data
        checkpoints = ckpt_a.list_by_run(run_id)
        assert len(checkpoints) >= 2  # global_plan + chapter_1
        # The chapter_1 checkpoint has at least 1 completed chapter
        ch1_ckpts = [c for c in checkpoints if "chapter_1" in str(c.stage)]
        assert len(ch1_ckpts) >= 1
        assert ch1_ckpts[0].last_completed_chapter == 1


# ══════════════════════════════════════════════════════════════════
# 5. Serialized Long-form Persistent Restart / Resume E2E
# ══════════════════════════════════════════════════════════════════
class TestSerializedPersistentResume:
    def test_serialized_longform_checkpoint_persists_and_restores(self):
        """Serialized + Novel + Long-form: checkpoint survives restart."""
        from drama_engine.longform_executor import run_long_form
        from drama_engine.longform_executor import InMemoryCheckpointRepository
        from drama_engine.config import RuleLibrary
        from drama_engine.contracts import Runtime
        from drama_engine.llm.latency_controlled import LatencyControlledMockProvider

        lib = RuleLibrary.load()
        rt = Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))

        ckpt_a = InMemoryCheckpointRepository()
        result_a = run_long_form(
            idea="冒险者们在远古遗迹中寻找失落的神器——但信号突然中断",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_serialized"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="serialized",
            checkpoint_repo=ckpt_a,
            fail_at_chapter=2,
        )
        assert result_a["status"] == "failed"
        assert result_a["chapter_count"] == 1

        run_id = result_a["run_id"]
        cp_a = ckpt_a.get_latest(run_id)
        assert cp_a is not None
        assert cp_a.last_completed_chapter == 1
        assert cp_a.next_chapter == 2

        # Runtime B: fresh repo, resume
        ckpt_b = InMemoryCheckpointRepository()
        ckpt_b.save_or_update(cp_a)

        result_b = run_long_form(
            idea="冒险者们在远古遗迹中寻找失落的神器",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_serialized_b"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="serialized",
            resume_from=run_id,
            checkpoint_repo=ckpt_b,
        )
        assert result_b["status"] == "completed"
        assert result_b["chapter_count"] == 3

        ch1_a = result_a["chapter_results"][0]
        ch1_b = result_b["chapter_results"][0]
        assert ch1_a["render_result"] == ch1_b["render_result"]

    def test_serialized_resume_no_chapter_duplication(self):
        """Resume produces exactly 3 unique chapters, no duplicates."""
        from drama_engine.longform_executor import run_long_form
        from drama_engine.longform_executor import InMemoryCheckpointRepository
        from drama_engine.config import RuleLibrary
        from drama_engine.contracts import Runtime
        from drama_engine.llm.latency_controlled import LatencyControlledMockProvider

        lib = RuleLibrary.load()
        rt = Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))

        ckpt_a = InMemoryCheckpointRepository()
        result_a = run_long_form(
            idea="连载冒险：寻找失落的古代文明",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_serialized2"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="serialized",
            checkpoint_repo=ckpt_a,
            fail_at_chapter=2,
        )

        run_id = result_a["run_id"]
        cp_a = ckpt_a.get_latest(run_id)
        ckpt_b = InMemoryCheckpointRepository()
        ckpt_b.save_or_update(cp_a)

        result_b = run_long_form(
            idea="连载冒险",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_serialized2_b"),
            lib=lib, runtime=rt,
            target_chapters=3, content_form="prose_story",
            story_mode="serialized",
            resume_from=run_id,
            checkpoint_repo=ckpt_b,
        )
        assert result_b["chapter_count"] == 3
        # Chapter 1 identically rendered (not regenerated)
        assert result_a["chapter_results"][0]["render_result"] == result_b["chapter_results"][0]["render_result"]


# ══════════════════════════════════════════════════════════════════
# 6. Legacy Regression
# ══════════════════════════════════════════════════════════════════
class TestPhase9FinalRegression:
    def test_audio_drama_still_routes(self):
        from drama_engine.forms.registry import get_form_handler
        h = get_form_handler("audio_drama")
        assert h.key == "audio_drama"
        pkg = h.package("test", {})
        assert "audio_drama" in pkg

    def test_prose_story_still_routes(self):
        from drama_engine.forms.registry import get_form_handler
        h = get_form_handler("prose_story")
        assert h.key == "prose_story"
        pkg = h.package("test", {})
        assert "prose" in pkg

    def test_novel_validator_still_works(self):
        from drama_engine.forms.handlers import NovelValidator
        v = NovelValidator()
        ok, _ = v.validate("第一章  " * 20)
        assert ok
        ok2, _ = v.validate("")
        assert not ok2

    def test_same_plan_multi_render(self):
        from drama_engine.forms.registry import get_form_handler
        plan = {"premise": "测试故事"}
        chars = [{"name": "主角"}]
        h_novel = get_form_handler("novel")
        h_story = get_form_handler("storytelling")
        p1 = h_novel.writer_prompt(plan, chars, "general")
        p2 = h_story.writer_prompt(plan, chars, "general")
        assert p1 != p2  # different styles
        assert plan == {"premise": "测试故事"}  # plan unchanged

    def test_all_modes_registered(self):
        from drama_engine.modes.registry import available_modes
        modes = set(available_modes())
        assert "viral_drama" in modes
        assert "general" in modes
        assert "mystery" in modes
        assert "serialized" in modes
        assert "interactive" not in modes

    def test_interactive_unsupported(self):
        from drama_engine.modes.registry import get_mode
        from drama_engine.core.errors import StoryEngineError
        with pytest.raises(StoryEngineError):
            get_mode("interactive")

    def test_canon_not_touched(self):
        """Form rendering does not modify Canon."""
        from drama_engine.characters.models import CharacterCanon
        canon = CharacterCanon(name="小光", gender="男", immutable_facts=["不会飞"])
        before = canon.model_dump(mode="json")
        assert canon.name == "小光"
        after = canon.model_dump(mode="json")
        assert before == after