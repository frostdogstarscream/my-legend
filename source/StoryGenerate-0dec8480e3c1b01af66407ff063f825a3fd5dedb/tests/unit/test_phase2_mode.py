"""Phase 2 单元测试 —— Mode System (Protocol / Registry / ViralDrama)。

验证 Mode 抽象层：注册、发现、默认行为、错误处理。
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest
from drama_engine.modes.base import Capability, ModeContext, ModeProfile, StoryMode
from drama_engine.modes.registry import (
    ModeRegistry,
    ModeNotFoundError,
    get_mode,
    register_mode,
    available_modes,
    default_mode_key,
)
from drama_engine.modes.viral_drama import ViralDramaMode, VIRAL_DRAMA_PROFILE


class TestCapability:
    def test_all_capabilities_unique(self):
        vals = [c.value for c in Capability]
        assert len(vals) == len(set(vals))

    def test_gadget_present(self):
        assert Capability.GADGET
        assert Capability.GADGET.value == "gadget"

    def test_str_conversion(self):
        assert Capability("gadget") == Capability.GADGET
        assert Capability("episode_plan") == Capability.EPISODE_PLAN

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Capability("not_a_capability")


class TestModeProfile:
    def test_viral_drama_profile(self):
        p = ModeProfile(**VIRAL_DRAMA_PROFILE)
        assert p.key == "viral_drama"
        assert p.version == "1.0"
        assert Capability.GADGET in p.capabilities
        assert Capability.BEHAVIOR in p.capabilities
        assert Capability.FACT_LEDGER in p.capabilities
        assert Capability.EPISODE_PLAN in p.capabilities
        assert Capability.EPISODE_WRITER in p.capabilities
        assert len(p.capabilities) == 10
        assert "drama_formula" in p.rule_packs
        assert p.default_content_form == "audio_drama"

    def test_profile_serializable(self):
        p = ModeProfile(**VIRAL_DRAMA_PROFILE)
        j = p.model_dump()
        assert j["key"] == "viral_drama"
        assert "gadget" in j["capabilities"]


class TestViralDramaMode:
    def test_key(self):
        m = ViralDramaMode()
        assert m.key == "viral_drama"

    def test_supports_gadget(self):
        m = ViralDramaMode()
        assert m.supports("gadget") is True
        assert m.supports(Capability.GADGET) is True

    def test_supports_unknown(self):
        m = ViralDramaMode()
        assert m.supports("fiction_novel") is False
        assert m.supports("") is False

    def test_validate_request_noop(self):
        m = ViralDramaMode()
        # 当前 Viral Drama 接受所有 brief
        m.validate_request({"raw_idea": "test"})  # 不应抛异常

    def test_conforms_to_protocol(self):
        assert isinstance(ViralDramaMode(), StoryMode)


class TestModeRegistry:
    def test_same_instance_on_repeated_get(self):
        """Registry 返回同一实例（单例）。"""
        m1 = get_mode()
        m2 = get_mode("viral_drama")
        # same key → same mode object (因为 key 不存在时才进 bootstrap)
        assert m1 is m2

    def test_get_default(self):
        m = get_mode()
        assert m.key == "viral_drama"

    def test_get_none(self):
        m = get_mode(None)
        assert m.key == "viral_drama"

    def test_get_explicit(self):
        m = get_mode("viral_drama")
        assert m.key == "viral_drama"

    def test_get_unknown(self):
        with pytest.raises(ModeNotFoundError) as exc:
            get_mode("ghost_mode")
        assert "ghost_mode" in str(exc.value)

    def test_available_modes(self):
        modes = available_modes()
        # Phase 5: general added alongside viral_drama
        assert "viral_drama" in modes
        assert "general" in modes

    def test_default_mode_key(self):
        assert default_mode_key() == "viral_drama"

    def test_register_new_mode(self):
        class FakeMode:
            key = "fake_test_mode"

            def profile(self):
                return ModeProfile(key="fake_test_mode")

            def validate_request(self, brief):
                pass

        register_mode(FakeMode())
        assert "fake_test_mode" in available_modes()

        m = get_mode("fake_test_mode")
        assert m.key == "fake_test_mode"
        assert isinstance(m, StoryMode)


class TestModeContext:
    def test_from_mode(self):
        m = ViralDramaMode()
        ctx = ModeContext.from_mode(m)
        assert ctx.key == "viral_drama"
        assert ctx.version == "1.0"
        assert ctx.supports("gadget") is True
        assert ctx.supports("episode_plan") is True

    def test_supports_string(self):
        ctx = ModeContext(key="test", capabilities={Capability.GADGET})
        assert ctx.supports("gadget") is True
        assert ctx.supports("behavior") is False

    def test_supports_invalid_string(self):
        ctx = ModeContext(key="test")
        assert ctx.supports("nonexistent") is False

    def test_serializable(self):
        ctx = ModeContext(key="test", capabilities={Capability.GADGET, Capability.BEHAVIOR})
        d = ctx.model_dump()
        assert d["key"] == "test"
        assert "gadget" in d["capabilities"]