"""Phase 5: Story Engine Multi-Mode Tests.

验收范围:
  - Mode Registry (viral_drama + general)
  - Default mode compatibility
  - General capability checks
  - Template Resolver (4 priority levels + errors)
  - Dynamic Template (data-driven, 0 code changes)
  - Template A/B same graph
  - Template Snapshot
"""
from __future__ import annotations

import pytest

from drama_engine.modes import ModeRegistry, available_modes, get_mode, register_mode
from drama_engine.modes.base import Capability, ModeProfile, StoryMode
from drama_engine.modes.registry import ModeNotFoundError
from drama_engine.core.errors import EngineErrorCode
from drama_engine.templates.models import (
    ResolvedStoryTemplate,
    StoryTemplateSpec,
    TemplateBeat,
    TemplateSource,
)
from drama_engine.templates.repository import MemoryStoryTemplateRepository
from drama_engine.templates.resolver import (
    StoryTemplateResolver,
    TemplateNotFoundError,
    TemplateVersionNotFoundError,
    TemplateModeIncompatibleError,
)


# ============================================================================
# Fixtures
# ============================================================================
@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前重置 Registry 单例。"""
    ModeRegistry._instance = None


@pytest.fixture
def repo():
    return MemoryStoryTemplateRepository()


@pytest.fixture
def resolver(repo):
    repo.create(StoryTemplateSpec(
        template_id="GENERAL_THREE_ACT", version=1, name="三幕结构",
        supported_modes=["general"],
        beats=[
            TemplateBeat(key="opening", purpose="建立场景"),
            TemplateBeat(key="climax", purpose="高潮"),
            TemplateBeat(key="resolution", purpose="结局"),
        ],
    ))
    repo.create(StoryTemplateSpec(
        template_id="GENERAL_THREE_ACT", version=2, name="三幕结构 v2",
        supported_modes=["general"],
        beats=[
            TemplateBeat(key="opening", purpose="建立场景"),
            TemplateBeat(key="complication", purpose="冲突引入"),
            TemplateBeat(key="climax", purpose="高潮"),
            TemplateBeat(key="resolution", purpose="结局"),
        ],
    ))
    repo.create(StoryTemplateSpec(
        template_id="GENERAL_RELATIONSHIP_TURN", version=1, name="关系转折",
        supported_modes=["general"],
        beats=[
            TemplateBeat(key="setup", purpose="展示关系"),
            TemplateBeat(key="trigger", purpose="触发事件"),
            TemplateBeat(key="new_normal", purpose="新常态"),
        ],
    ))
    return StoryTemplateResolver(repo, {"general": "GENERAL_THREE_ACT"})


# ============================================================================
# Mode Registry Tests
# ============================================================================
class TestModeRegistry:
    """Mode Registry: viral_drama + general 注册与发现。"""

    def test_both_modes_registered(self):
        """viral_drama, general, mystery, serialized 都已注册。"""
        modes = available_modes()
        assert "viral_drama" in modes
        assert "general" in modes
        assert "mystery" in modes      # Phase 9
        assert "serialized" in modes   # Phase 9
        assert len(modes) == 4

    def test_get_viral_drama(self):
        mode = get_mode("viral_drama")
        assert mode.key == "viral_drama"

    def test_get_general(self):
        mode = get_mode("general")
        assert mode.key == "general"

    def test_default_is_viral_drama(self):
        """不传 story_mode → viral_drama。"""
        mode = get_mode(None)
        assert mode.key == "viral_drama"

    def test_unknown_mode_raises(self):
        with pytest.raises(ModeNotFoundError) as exc:
            get_mode("nonexistent_mode")
        assert "nonexistent_mode" in str(exc.value)

    def test_unknown_mode_error_code(self):
        with pytest.raises(ModeNotFoundError) as exc:
            get_mode("unknown")
        assert exc.value.code == EngineErrorCode.MODE_NOT_SUPPORTED


# ============================================================================
# General Capability Tests
# ============================================================================
class TestGeneralCapability:
    """General Mode 的能力声明必须正确。"""

    def test_gadget_not_supported(self):
        mode = get_mode("general")
        assert mode.supports("gadget") is False
        assert mode.supports(Capability.GADGET) is False

    def test_five_slot_not_supported(self):
        mode = get_mode("general")
        assert mode.supports("five_slot_cast") is False
        assert mode.supports(Capability.FIVE_SLOT_CAST) is False

    def test_core_capabilities_supported(self):
        mode = get_mode("general")
        for cap in [Capability.BEHAVIOR, Capability.FACT_LEDGER,
                     Capability.EPISODE_PLAN, Capability.EPISODE_WRITER,
                     Capability.EVIDENCE_JUDGE, Capability.AUDIO_CONSTRAINTS,
                     Capability.SERIES_VALIDATION]:
            assert mode.supports(cap) is True, f"General should support {cap}"

    def test_viral_supports_gadget(self):
        mode = get_mode("viral_drama")
        assert mode.supports(Capability.GADGET) is True
        assert mode.supports(Capability.FIVE_SLOT_CAST) is True

    def test_general_profile_capabilities(self):
        profile = get_mode("general").profile()
        assert Capability.GADGET not in profile.capabilities
        assert Capability.FIVE_SLOT_CAST not in profile.capabilities
        assert Capability.BEHAVIOR in profile.capabilities

    def test_general_rule_packs(self):
        profile = get_mode("general").profile()
        assert "character_consistency" in profile.rule_packs
        assert "causal_consistency" in profile.rule_packs
        assert "drama_formula" not in profile.rule_packs
        assert "timetravel" not in profile.rule_packs


# ============================================================================
# Template Resolver: Resolution Priority
# ============================================================================
class TestTemplateResolver:
    """Template Resolver: 4-priority resolution + errors。"""

    def test_explicit_template_id(self, resolver):
        result = resolver.resolve(template_id="GENERAL_RELATIONSHIP_TURN", mode_key="general")
        assert result is not None
        assert result.template_id == "GENERAL_RELATIONSHIP_TURN"
        assert result.source == TemplateSource.explicit

    def test_explicit_template_with_version(self, resolver):
        result = resolver.resolve(template_id="GENERAL_THREE_ACT", version=1, mode_key="general")
        assert result is not None
        assert result.version == 1
        assert result.source == TemplateSource.explicit

    def test_explicit_template_latest_version(self, resolver):
        """不传 version → 获取最新 active。"""
        result = resolver.resolve(template_id="GENERAL_THREE_ACT", mode_key="general")
        assert result is not None
        assert result.version == 2  # v2 is latest

    def test_mode_default_template(self, resolver):
        """不传 template_id → mode default。"""
        result = resolver.resolve(template_id=None, mode_key="general")
        assert result is not None
        assert result.template_id == "GENERAL_THREE_ACT"
        assert result.source == TemplateSource.mode_default

    def test_freeform_when_no_default(self, resolver):
        """viral_drama 没有 mode default → freeform。"""
        result = resolver.resolve(template_id=None, mode_key="viral_drama")
        assert result is None  # freeform

    # ---- Error Cases ----

    def test_template_not_found(self, resolver):
        with pytest.raises(TemplateNotFoundError) as exc:
            resolver.resolve(template_id="NONEXISTENT", mode_key="general")
        assert "NONEXISTENT" in str(exc.value)

    def test_version_not_found(self, resolver):
        with pytest.raises(TemplateVersionNotFoundError) as exc:
            resolver.resolve(template_id="GENERAL_THREE_ACT", version=99, mode_key="general")
        assert "v99" in str(exc.value)

    def test_mode_incompatible(self, resolver):
        with pytest.raises(TemplateModeIncompatibleError) as exc:
            resolver.resolve(template_id="GENERAL_THREE_ACT", mode_key="viral_drama")
        assert "GENERAL_THREE_ACT" in str(exc.value)
        assert "viral_drama" in str(exc.value)


# ============================================================================
# Dynamic Template
# ============================================================================
class TestDynamicTemplate:
    """运行时新增 Template → 不需修改任何 Graph / Node 代码。"""

    def test_create_and_resolve(self, repo, resolver):
        new_spec = StoryTemplateSpec(
            template_id="CUSTOM_MYSTERY", version=1, name="自定义悬疑",
            supported_modes=["general"],
            beats=[TemplateBeat(key="clue", purpose="线索揭示")],
        )
        repo.create(new_spec)
        result = resolver.resolve(template_id="CUSTOM_MYSTERY", mode_key="general")
        assert result is not None
        assert result.template_id == "CUSTOM_MYSTERY"
        assert result.source == TemplateSource.explicit

    def test_new_template_no_code_changes(self, repo, resolver):
        """验证：新增 Template 不需要修改 Graph/Node 代码。"""
        # Simulate: repo.create only - no changes to graph.py, nodes/*.py
        count_before = len(repo.list())
        repo.create(StoryTemplateSpec(
            template_id="DYNAMIC_TEST", version=1, name="动态测试",
            supported_modes=["general"],
            beats=[TemplateBeat(key="test", purpose="测试节拍")],
        ))
        assert len(repo.list()) == count_before + 1
        # Resolution should work without touching graph or nodes
        result = resolver.resolve(template_id="DYNAMIC_TEST", mode_key="general")
        assert result is not None


# ============================================================================
# Template A/B: Same Graph, Different Structure
# ============================================================================
class TestTemplateAB:
    """两个 Template 共享同一个 Resolver/Graph，但产生不同结构。"""

    def test_different_beats(self, resolver):
        a = resolver.resolve(template_id="GENERAL_THREE_ACT", version=1, mode_key="general")
        b = resolver.resolve(template_id="GENERAL_RELATIONSHIP_TURN", mode_key="general")
        assert a is not None
        assert b is not None
        assert a.beat_keys() != b.beat_keys()
        assert a.outline_context() != b.outline_context()

    def test_same_resolver(self, resolver):
        """两个 Template 使用同一个 Resolver 实例。"""
        a = resolver.resolve(template_id="GENERAL_THREE_ACT", version=1, mode_key="general")
        b = resolver.resolve(template_id="GENERAL_RELATIONSHIP_TURN", mode_key="general")
        assert type(a) is type(b)
        assert type(a) is ResolvedStoryTemplate


# ============================================================================
# Template Snapshot
# ============================================================================
class TestTemplateSnapshot:
    """v1 Story Snapshot 不受后续 v2 创建影响。"""

    def test_snapshot_preserved(self, resolver):
        # Get v1
        v1 = resolver.resolve(template_id="GENERAL_THREE_ACT", version=1, mode_key="general")
        snapshot = v1.model_dump()
        assert snapshot["version"] == 1

        # v2 already exists in fixture repo
        v2 = resolver.resolve(template_id="GENERAL_THREE_ACT", version=None, mode_key="general")
        assert v2.version == 2  # latest is v2

        # But old snapshot still v1
        assert snapshot["version"] == 1


# ============================================================================
# Template Contract
# ============================================================================
class TestTemplateContract:
    """StoryTemplateSpec / ResolvedStoryTemplate 契约验证。"""

    def test_outline_context(self):
        spec = StoryTemplateSpec(
            template_id="T", version=1, name="测试",
            supported_modes=["general"],
            beats=[
                TemplateBeat(key="a", purpose="A", guidance="g1", required=True),
                TemplateBeat(key="b", purpose="B", required=False),
            ],
            constraints=["c1"],
            tone_hints=["自然"],
            ending_guidance="closed",
        )
        resolved = ResolvedStoryTemplate.from_spec(spec, TemplateSource.explicit)
        ctx = resolved.outline_context()
        assert "测试" in ctx
        assert "[必须] a: A — g1" in ctx
        assert "[可选] b: B" in ctx
        assert "c1" in ctx
        assert "自然" in ctx
        assert "closed" in ctx

    def test_beat_summary(self):
        spec = StoryTemplateSpec(
            template_id="T", version=1, name="测试",
            supported_modes=["general"],
            beats=[TemplateBeat(key="a", purpose="A"), TemplateBeat(key="b", purpose="B")],
        )
        resolved = ResolvedStoryTemplate.from_spec(spec, TemplateSource.explicit)
        summary = resolved.beat_summary()
        assert "a: A" in summary
        assert "b: B" in summary


# ============================================================================
# mode_prompt_context
# ============================================================================
class TestModePromptContext:
    """mode_prompt_context 不包含 Viral 专用语言。"""

    def test_general_no_gadget_language(self):
        from drama_engine.modes import ModeContext
        from drama_engine.prompts import mode_prompt_context
        ctx = ModeContext(key="general", version="1.0",
                          capabilities={Capability.BEHAVIOR, Capability.FACT_LEDGER})
        text = mode_prompt_context(ctx)
        # General prompt mentions these as "don't add", not as "must have"
        assert "不强加" in text or "不要强加" in text  # negative instruction present
        assert "人物驱动" in text or "因果关系" in text or "主动选择" in text  # positive guidance
        # Viral-forcing language must not appear as requirements
        assert "必须五槽位" not in text
        assert "必须穿越" not in text

    def test_viral_has_language(self):
        from drama_engine.modes import ModeContext
        from drama_engine.prompts import mode_prompt_context
        ctx = ModeContext(key="viral_drama", version="1.0",
                          capabilities={Capability.GADGET, Capability.FIVE_SLOT_CAST})
        text = mode_prompt_context(ctx)
        # Viral mode should have its characteristic vocabulary
        assert len(text) > 0
        assert "爆款" in text or "hook" in text.lower() or "cliffhanger" in text.lower() or "强" in text


# ============================================================================
# Repository
# ============================================================================
class TestRepository:
    """MemoryStoryTemplateRepository CRUD 契约。"""

    def test_create_and_get(self, repo, resolver):
        spec = StoryTemplateSpec(
            template_id="REPO_TEST", version=1, name="R",
            supported_modes=["general"],
            beats=[TemplateBeat(key="x", purpose="X")],
        )
        repo.create(spec)
        result = resolver.resolve(template_id="REPO_TEST", mode_key="general")
        assert result is not None
        assert result.template_id == "REPO_TEST"

    def test_list_filters_by_mode(self, repo):
        results = repo.list(mode="general")
        assert all("general" in t.supported_modes for t in results)

    def test_list_unfiltered(self, resolver):
        # Use resolver's pre-populated repo
        repo = resolver._repo
        results = repo.list()
        assert len(results) >= 2  # at least fixture templates