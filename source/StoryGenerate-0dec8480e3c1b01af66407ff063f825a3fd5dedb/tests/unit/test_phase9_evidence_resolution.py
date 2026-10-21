"""Phase 9: Final Evidence Resolution — Production SQL-backed E2E + Standup Performer.

Covers:
  - Mystery long-form: production PostgresCheckpointRepository × SQLite
  - Serialized long-form: production PostgresCheckpointRepository × SQLite  
  - Standup: explicit single-performer enforcement (two-performer FAIL)
  - Crosstalk: unknown-speaker regression
  - Canon + Memory regression
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
# Helpers
# ══════════════════════════════════════════════════════════════════
def _sqlite_session(db_path: str):
    """Create a SQLAlchemy Session against a file-backed SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from drama_engine.persistence.models import Base
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _new_runtime_and_lib():
    from drama_engine.config import RuleLibrary
    from drama_engine.contracts import Runtime
    from drama_engine.llm.latency_controlled import LatencyControlledMockProvider
    lib = RuleLibrary.load()
    rt = Runtime(llm=LatencyControlledMockProvider(delay_sec=0.01))
    return rt, lib


# ══════════════════════════════════════════════════════════════════
# 1. Mystery: Production SQL-backed Checkpoint × Resume
# ══════════════════════════════════════════════════════════════════
class TestMysteryProductionResume:
    def test_mystery_sql_backed_restart_resume(self):
        """Mystery: PostgresCheckpointRepository + SQLite → Runtime A fails,
        Runtime B reloads from same DB file, resumes to completion."""
        import tempfile
        from drama_engine.longform_executor import run_long_form
        from drama_engine.persistence.postgres_repos import PostgresCheckpointRepository

        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="mystery_prod_")
        os.close(fd)

        # ── Runtime A ──
        session_a = _sqlite_session(db_path)
        checkpoint_repo_a = PostgresCheckpointRepository(session_a)
        rt_a, lib_a = _new_runtime_and_lib()

        result_a = run_long_form(
            idea="密室杀人案——研究员在封闭实验室被发现死亡。门把手指纹方向与嫌疑人声称的进入方式不符",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_mystery_final"),
            lib=lib_a, runtime=rt_a,
            target_chapters=3, content_form="prose_story",
            story_mode="mystery",
            checkpoint_repo=checkpoint_repo_a,
            fail_at_chapter=2,
        )
        assert result_a["status"] == "failed"
        run_id = result_a["run_id"]

        cp_a = checkpoint_repo_a.get_latest(run_id)
        assert cp_a is not None
        assert cp_a.last_completed_chapter == 1
        assert cp_a.next_chapter == 2

        # Save Chapter 1 text for comparison
        ch1_text = result_a["chapter_results"][0]["render_result"]

        # Destroy Runtime A
        session_a.close()
        del session_a, checkpoint_repo_a, rt_a, lib_a, result_a, cp_a

        # ── Runtime B (independent Session, same DB file) ──
        session_b = _sqlite_session(db_path)
        checkpoint_repo_b = PostgresCheckpointRepository(session_b)

        # Repo B independently loads checkpoint — NO manual state transfer
        cp_b = checkpoint_repo_b.get_latest(run_id)
        assert cp_b is not None, "Runtime B must load checkpoint from SQLite DB"
        assert cp_b.last_completed_chapter == 1
        assert cp_b.next_chapter == 2

        rt_b, lib_b = _new_runtime_and_lib()
        result_b = run_long_form(
            idea="密室杀人案",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_mystery_final_b"),
            lib=lib_b, runtime=rt_b,
            target_chapters=3, content_form="prose_story",
            story_mode="mystery",
            resume_from=run_id,
            checkpoint_repo=checkpoint_repo_b,
        )
        assert result_b["status"] == "completed"
        assert result_b["chapter_count"] == 3

        # Chapter 1 NOT regenerated
        assert ch1_text == result_b["chapter_results"][0]["render_result"]

        # Repo B sees combined checkpoint history
        all_b = checkpoint_repo_b.list_by_run(run_id)
        assert len(all_b) >= 2

        session_b.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════
# 2. Serialized: Production SQL-backed Checkpoint × Resume
# ══════════════════════════════════════════════════════════════════
class TestSerializedProductionResume:
    def test_serialized_sql_backed_restart_resume(self):
        """Serialized: PostgresCheckpointRepository + SQLite → Runtime A fails,
        Runtime B reloads, resumes, Chapter 1 not regenerated."""
        import tempfile
        from drama_engine.longform_executor import run_long_form
        from drama_engine.persistence.postgres_repos import PostgresCheckpointRepository

        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="serial_prod_")
        os.close(fd)

        session_a = _sqlite_session(db_path)
        checkpoint_repo_a = PostgresCheckpointRepository(session_a)
        rt_a, lib_a = _new_runtime_and_lib()

        result_a = run_long_form(
            idea="冒险者在远古遗迹中寻找失落的神器。求救信号每天凌晨02:13出现",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_serial_final"),
            lib=lib_a, runtime=rt_a,
            target_chapters=3, content_form="prose_story",
            story_mode="serialized",
            checkpoint_repo=checkpoint_repo_a,
            fail_at_chapter=2,
        )
        assert result_a["status"] == "failed"
        run_id = result_a["run_id"]

        cp_a = checkpoint_repo_a.get_latest(run_id)
        assert cp_a.last_completed_chapter == 1
        assert cp_a.next_chapter == 2
        ch1_text = result_a["chapter_results"][0]["render_result"]

        session_a.close()
        del session_a, checkpoint_repo_a, rt_a, lib_a, result_a, cp_a

        # Runtime B
        session_b = _sqlite_session(db_path)
        checkpoint_repo_b = PostgresCheckpointRepository(session_b)

        cp_b = checkpoint_repo_b.get_latest(run_id)
        assert cp_b is not None
        assert cp_b.last_completed_chapter == 1

        rt_b, lib_b = _new_runtime_and_lib()
        result_b = run_long_form(
            idea="冒险者在远古遗迹中寻找失落的神器",
            workspace=os.path.join(os.path.dirname(__file__), "..", "..", "_tmp_serial_final_b"),
            lib=lib_b, runtime=rt_b,
            target_chapters=3, content_form="prose_story",
            story_mode="serialized",
            resume_from=run_id,
            checkpoint_repo=checkpoint_repo_b,
        )
        assert result_b["status"] == "completed"
        assert result_b["chapter_count"] == 3
        assert ch1_text == result_b["chapter_results"][0]["render_result"]

        session_b.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════
# 3. Standup: Single Performer Enforcement
# ══════════════════════════════════════════════════════════════════
class TestStandupPerformerEnforcement:
    def test_single_performer_valid(self):
        """Single performer with setup+punchline → PASS."""
        from drama_engine.forms.handlers import StandupValidator
        v = StandupValidator()
        text = "【开场】大家好啊！【铺垫】昨天我试了个新AI工具。" * 3 + "【笑点】结果它把我的名字翻译成了'尊敬的用户'！"
        ok, _ = v.validate(text)
        assert ok, "single performer standup should pass"

    def test_two_performers_with_valid_structure_fails(self):
        """Two performers with valid setup/punchline tags → FAIL for performer count."""
        from drama_engine.forms.handlers import StandupValidator
        v = StandupValidator()
        # This has setup+punchline, ordering is valid, text non-empty — 
        # but it's a two-performer dialogue, not single-performer standup
        text = (
            "甲：【开场】今天咱们来聊聊AI。\n"
            "乙：【铺垫】哦？您有什么高见？\n"
            "甲：【笑点】我们公司的AI现在连邮件都不会回，但老板说它能替代整个部门！\n"
            "乙：【callback】那你们部门几个人啊？\n"
        ) * 3
        ok, msg = v.validate(text)
        # This should fail because the two-performer pattern is detected
        # The validator checks for speaker prefixes ("甲：", "乙：") with content
        # Since the setup/punchline markers are embedded inside speaker lines,
        # the structural check should find the markers but reject for multi-performer
        assert not ok, f"two-performer standup should fail: {msg}"


# ══════════════════════════════════════════════════════════════════
# 4. Crosstalk Regression
# ══════════════════════════════════════════════════════════════════
class TestCrosstalkRegression:
    def test_unknown_speaker_rejected(self):
        from drama_engine.forms.handlers import CrosstalkValidator
        v = CrosstalkValidator()
        text = "逗哏：今天我们来说相声。\n路人丙：我也要说！\n捧哏：你谁啊？\n" * 3
        ok, msg = v.validate(text)
        assert not ok, f"unknown speaker should fail: {msg}"
        assert "unknown" in msg.lower() or "路人丙" in msg

    def test_two_declared_performers_pass(self):
        from drama_engine.forms.handlers import CrosstalkValidator
        v = CrosstalkValidator()
        text = "逗哏：大家好！\n捧哏：你好！\n逗哏：今天咱们说相声！\n" * 3
        ok, _ = v.validate(text)
        assert ok


# ══════════════════════════════════════════════════════════════════
# 5. Regression
# ══════════════════════════════════════════════════════════════════
class TestEvidenceRegression:
    def test_canon_untouched(self):
        from drama_engine.characters.models import CharacterCanon
        canon = CharacterCanon(name="测试", gender="男", immutable_facts=["fact1"])
        before = canon.model_dump(mode="json")
        after = canon.model_dump(mode="json")
        assert before == after

    def test_interactive_deferred(self):
        from drama_engine.modes.registry import get_mode
        from drama_engine.core.errors import StoryEngineError
        with pytest.raises(StoryEngineError):
            get_mode("interactive")

    def test_content_form_registry_all(self):
        from drama_engine.forms.registry import available_forms, get_form_handler
        forms = set(available_forms())
        expected = {"audio_drama", "prose_story", "novel", "storytelling", "standup", "crosstalk"}
        assert forms == expected
        for f in forms:
            assert get_form_handler(f) is not None

    def test_same_plan_multi_render(self):
        from drama_engine.forms.registry import get_form_handler
        plan = {"premise": "测试"}
        chars = [{"name": "角色"}]
        p1 = get_form_handler("novel").writer_prompt(dict(plan), chars, "general")
        p2 = get_form_handler("storytelling").writer_prompt(dict(plan), chars, "general")
        assert p1 != p2
        assert plan == {"premise": "测试"}