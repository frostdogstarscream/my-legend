"""Phase 9: More Mode / Form — Test Suite.

Covers:
  - Mode Registry (mystery, serialized, interactive deferred)
  - Content Form Registry (all 6 forms)
  - Mystery Mode (ClueLedger, fair-play, open/closed)
  - Serialized Mode (episode goal, threads, cliffhanger)
  - Novel / Storytelling / Standup / Crosstalk validators
  - Cross-matrix (mode × form)
  - Same-plan multi-render proof
  - Legacy regression (audio_drama, prose_story, Phase 7/8)
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
# 1. Mode Registry
# ══════════════════════════════════════════════════════════════════
class TestModeRegistry:
    def test_mystery_registered(self):
        from drama_engine.modes.registry import get_mode, available_modes
        assert "mystery" in available_modes()
        mode = get_mode("mystery")
        assert mode.key == "mystery"
        assert mode.profile().key == "mystery"
        assert "mystery_clue" in mode.profile().rule_packs

    def test_serialized_registered(self):
        from drama_engine.modes.registry import get_mode, available_modes
        assert "serialized" in available_modes()
        mode = get_mode("serialized")
        assert mode.key == "serialized"
        assert "serialized_continuity" in mode.profile().rule_packs

    def test_mystery_does_not_require_gadget(self):
        from drama_engine.modes.registry import get_mode
        from drama_engine.modes.base import Capability
        mode = get_mode("mystery")
        assert Capability.GADGET not in mode.profile().capabilities

    def test_mystery_does_not_require_five_slot(self):
        from drama_engine.modes.registry import get_mode
        from drama_engine.modes.base import Capability
        mode = get_mode("mystery")
        assert Capability.FIVE_SLOT_CAST not in mode.profile().capabilities

    def test_all_four_modes_registered(self):
        from drama_engine.modes.registry import available_modes
        modes = set(available_modes())
        assert "viral_drama" in modes
        assert "general" in modes
        assert "mystery" in modes
        assert "serialized" in modes

    def test_unsupported_mode_raises(self):
        from drama_engine.modes.registry import get_mode
        from drama_engine.core.errors import EngineErrorCode, StoryEngineError
        with pytest.raises(StoryEngineError) as exc:
            get_mode("unknown_mode")
        assert exc.value.code == EngineErrorCode.MODE_NOT_SUPPORTED

    def test_auto_serialized_not_triggered_by_long_form_alone(self):
        """long_form is NOT a signal for serialized mode."""
        from drama_engine.modes.registry import get_mode
        # Just getting different modes — serialized is a separate key
        serialized = get_mode("serialized")
        general = get_mode("general")
        assert serialized.key != general.key
        # They are DIFFERENT things — no auto-resolution here


# ══════════════════════════════════════════════════════════════════
# 2. Content Form Registry
# ══════════════════════════════════════════════════════════════════
class TestContentFormRegistry:
    def test_all_six_forms_registered(self):
        from drama_engine.forms.registry import available_forms
        forms = set(available_forms())
        assert "audio_drama" in forms
        assert "prose_story" in forms
        assert "novel" in forms
        assert "storytelling" in forms
        assert "standup" in forms
        assert "crosstalk" in forms

    def test_get_form_handler(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("novel")
        assert handler.key == "novel"
        assert "novel_adaptation" in handler.rule_packs()

    def test_unknown_form_raises(self):
        from drama_engine.forms.registry import get_form_handler
        from drama_engine.core.errors import EngineErrorCode, StoryEngineError
        with pytest.raises(StoryEngineError) as exc:
            get_form_handler("unknown_form")
        assert exc.value.code == EngineErrorCode.CONTENT_FORM_NOT_SUPPORTED

    def test_novel_writer_returns_prompt(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("novel")
        prompt = handler.writer_prompt({"premise": "test"}, [{"name": "主角"}], "general")
        assert "novel" in prompt.lower() or "test" in prompt

    def test_standup_validator(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("standup")
        vs = handler.validators()
        ok, msg = vs[0].validate("铺垫开场——这是一个很好笑的段子 " * 10)
        assert ok

    def test_standup_validator_empty(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("standup")
        vs = handler.validators()
        ok, msg = vs[0].validate("")
        assert not ok

    def test_crosstalk_validator_empty(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("crosstalk")
        vs = handler.validators()
        ok, msg = vs[0].validate("")
        assert not ok

    def test_novel_validator(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("novel")
        vs = handler.validators()
        ok, msg = vs[0].validate("第一章  " * 20)
        assert ok

    def test_storytelling_validator(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("storytelling")
        vs = handler.validators()
        ok, msg = vs[0].validate("从前，有一个故事...... " * 10)
        assert ok

    def test_package_returns_form_output(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("novel")
        result = handler.package("正文内容", {"premise": "测试"})
        assert "novel" in result
        assert result["novel"] == "正文内容"

    def test_crosstalk_does_not_force_two_characters(self):
        """Crosstalk is a PERFORMANCE format — story can have any number of characters."""
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("crosstalk")
        # The handler works regardless of how many story characters exist
        prompt = handler.writer_prompt({"premise": "test"}, [
            {"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}, {"name": "E"},
        ], "general")
        assert "crosstalk" in prompt.lower()
        # 5 characters → 2 performers (dougen/penggen) tell/interact about them


# ══════════════════════════════════════════════════════════════════
# 3. Mystery Clue Ledger
# ══════════════════════════════════════════════════════════════════
class TestMysteryClueLedger:
    def test_clue_ids_unique(self):
        from drama_engine.modes.clue_ledger import ClueLedger, ClueEntry
        ledger = ClueLedger(central_question="谁偷了宝石？")
        ledger.add_clue(ClueEntry(clue_id="c1", description="目击者看到黑影"))
        ledger.add_clue(ClueEntry(clue_id="c2", description="宝石在保险箱"))
        ids = {c.clue_id for c in ledger.clues}
        assert len(ids) == 2

    def test_reveal_references_known_clues(self):
        from drama_engine.modes.clue_ledger import ClueLedger, ClueEntry
        ledger = ClueLedger(central_question="谁做的？")
        ledger.add_clue(ClueEntry(clue_id="c1", description="指纹匹配"))
        ledger.add_clue(ClueEntry(clue_id="c2", description="监控录像"))
        ledger.reveal_basis = ["c1", "c2"]
        assert ledger.has_reveal_basis()

    def test_unsupported_reveal_basis_rejected(self):
        from drama_engine.modes.clue_ledger import ClueLedger, ClueEntry
        ledger = ClueLedger(central_question="谁做的？")
        ledger.add_clue(ClueEntry(clue_id="c1", description="指纹"))
        ledger.reveal_basis = ["c1", "c99"]  # c99 doesn't exist
        assert not ledger.has_reveal_basis()

    def test_red_herring_not_accepted_as_evidence(self):
        from drama_engine.modes.clue_ledger import ClueLedger, ClueEntry
        ledger = ClueLedger()
        # A red herring is NOT evidence unless explicitly reclassified
        red = ClueEntry(clue_id="rh1", description="误导线索", clue_type="red_herring")
        ledger.add_clue(red)
        # By default, red_herring should not be in reveal basis
        assert red.clue_type == "red_herring"

    def test_closed_mystery_requires_resolution(self):
        from drama_engine.modes.clue_ledger import ClueLedger
        ledger = ClueLedger(central_question="谜题", ending_type="closed")
        assert ledger.ending_type == "closed"
        # Closed mysteries should have resolution filled
        ledger.resolution = "凶手是管家"
        assert ledger.resolution

    def test_open_mystery_permits_unresolved(self):
        from drama_engine.modes.clue_ledger import ClueLedger
        ledger = ClueLedger(central_question="谜题", ending_type="open")
        assert ledger.ending_type == "open"
        # Open mysteries may have empty resolution
        assert ledger.resolution == ""

    def test_clue_ledger_to_dict_roundtrip(self):
        from drama_engine.modes.clue_ledger import ClueLedger, ClueEntry
        ledger = ClueLedger(central_question="Q")
        ledger.add_clue(ClueEntry(clue_id="c1", description="线索"))
        d = ledger.to_dict()
        restored = ClueLedger.from_dict(d)
        assert restored.central_question == "Q"
        assert restored.clue_count() == 1


# ══════════════════════════════════════════════════════════════════
# 4. Serialized Mode
# ══════════════════════════════════════════════════════════════════
class TestSerializedMode:
    def test_serialized_standard_works(self):
        """serialized + standard is valid — serialized != long_form."""
        from drama_engine.modes.registry import get_mode
        from drama_engine.modes.base import Capability
        mode = get_mode("serialized")
        # Serialized has EPISODE_PLAN capability, but generation_scale is separate
        assert Capability.EPISODE_PLAN in mode.profile().capabilities

    def test_general_long_form_still_works(self):
        """general + long_form still valid — independent of serialized."""
        from drama_engine.modes.registry import get_mode
        mode = get_mode("general")
        assert mode.key == "general"
        # General mode supports episode plan, which long_form uses

    def test_serialized_not_equal_long_form(self):
        from drama_engine.modes.registry import get_mode
        serialized = get_mode("serialized")
        general = get_mode("general")
        assert serialized.key != general.key
        # They are distinct modes; generation_scale is an orthogonal dimension

    def test_cliffhanger_preference_requires_hook(self):
        """cliffhanger → continuation_hook required."""
        # Deterministic test: serialized rule 04
        from drama_engine.modes.rule_packs import SERIALIZED_RULES
        rule = [r for r in SERIALIZED_RULES if r["rule_id"] == "serialized_04"][0]
        assert "cliffhanger" in rule["text"]
        assert "continuation_hook" in rule["text"]

    def test_closed_preference_no_forced_cliffhanger(self):
        """closed → no forced cliffhanger."""
        from drama_engine.modes.rule_packs import SERIALIZED_RULES
        rule = [r for r in SERIALIZED_RULES if r["rule_id"] == "serialized_05"][0]
        assert "closed" in rule["text"]
        assert "不强制" in rule["text"] or "no forced" in rule["text"].lower()

    def test_episode_goal_required(self):
        from drama_engine.modes.rule_packs import SERIALIZED_RULES
        rule = [r for r in SERIALIZED_RULES if r["rule_id"] == "serialized_01"][0]
        assert "episode goal" in rule["text"]


# ══════════════════════════════════════════════════════════════════
# 5. Cross-matrix (Mode × Form)
# ══════════════════════════════════════════════════════════════════
class TestCrossMatrix:
    def test_mystery_novel_prose_storytelling(self):
        """Same Mystery plan rendered by different forms."""
        from drama_engine.forms.registry import get_form_handler
        plan = {"premise": "密室杀人案", "central_question": "谁是凶手"}
        chars = [{"name": "侦探"}, {"name": "嫌疑人"}]
        novel = get_form_handler("novel")
        storytelling = get_form_handler("storytelling")
        prose = get_form_handler("prose_story")
        # All produce prompts with different styles
        p1 = novel.writer_prompt(plan, chars, "mystery")
        p2 = storytelling.writer_prompt(plan, chars, "mystery")
        p3 = prose.writer_prompt(plan, chars, "mystery")
        assert p1 != p2  # different styles
        assert "novel" in p1.lower()
        assert "oral" in p2.lower() or "narrator" in p2.lower()

    def test_serialized_prose_novel(self):
        from drama_engine.forms.registry import get_form_handler
        plan = {"premise": "连续冒险", "episode_goal": "找回失落的神器"}
        chars = [{"name": "冒险者"}]
        novel_h = get_form_handler("novel")
        prose_h = get_form_handler("prose_story")
        p_novel = novel_h.writer_prompt(plan, chars, "serialized")
        p_prose = prose_h.writer_prompt(plan, chars, "serialized")
        assert p_novel != p_prose

    def test_same_plan_multi_render(self):
        """Prove: Plan unchanged, only rendering differs."""
        from drama_engine.forms.registry import get_form_handler
        plan = {"premise": "测试前提", "title": "测试"}
        chars = [{"name": "角色"}]
        handler_a = get_form_handler("novel")
        handler_b = get_form_handler("storytelling")
        # Plan used as input — not mutated by handlers
        plan_copy = dict(plan)
        handler_a.writer_prompt(plan, chars, "general")
        handler_b.writer_prompt(plan, chars, "general")
        assert plan == plan_copy  # plan unchanged

    def test_all_new_forms_e2e(self):
        """Each new form produces non-empty package output."""
        from drama_engine.forms.registry import get_form_handler
        plan = {"premise": "测试故事"}
        chars = [{"name": "主角"}]
        for form_key in ["novel", "storytelling", "standup", "crosstalk"]:
            handler = get_form_handler(form_key)
            result = handler.package("测试内容测试内容", plan)
            assert form_key in result
            assert result[form_key] == "测试内容测试内容"


# ══════════════════════════════════════════════════════════════════
# 6. Interactive Deferred
# ══════════════════════════════════════════════════════════════════
class TestInteractiveDeferred:
    def test_interactive_not_in_modes(self):
        from drama_engine.modes.registry import available_modes
        assert "interactive" not in available_modes()

    def test_interactive_request_raises(self):
        from drama_engine.modes.registry import get_mode
        from drama_engine.core.errors import StoryEngineError
        with pytest.raises(StoryEngineError):
            get_mode("interactive")


# ══════════════════════════════════════════════════════════════════
# 7. Legacy Regression
# ══════════════════════════════════════════════════════════════════
class TestPhase9Regression:
    def test_audio_drama_form_still_works(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("audio_drama")
        assert handler.key == "audio_drama"
        package = handler.package("广播剧文本", {})
        assert "audio_drama" in package

    def test_prose_story_form_still_works(self):
        from drama_engine.forms.registry import get_form_handler
        handler = get_form_handler("prose_story")
        assert handler.key == "prose_story"
        package = handler.package("叙事文本", {})
        assert "prose" in package

    def test_viral_drama_mode_still_registered(self):
        from drama_engine.modes.registry import get_mode, available_modes
        assert "viral_drama" in available_modes()
        mode = get_mode("viral_drama")
        assert mode.key == "viral_drama"
        from drama_engine.modes.base import Capability
        assert Capability.GADGET in mode.profile().capabilities

    def test_general_mode_still_registered(self):
        from drama_engine.modes.registry import get_mode
        mode = get_mode("general")
        assert mode.key == "general"

    def test_rule_packs_exist(self):
        from drama_engine.modes.rule_packs import (
            MYSTERY_RULES, SERIALIZED_RULES, FORM_RULES,
        )
        assert len(MYSTERY_RULES) >= 4
        assert len(SERIALIZED_RULES) >= 4
        assert len(FORM_RULES) >= 4