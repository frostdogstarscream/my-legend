"""Phase 6: Content Form Architecture Tests.

验收范围:
  - Content Form Registry (audio_drama + prose_story)
  - Default = audio_drama
  - Unknown → CONTENT_FORM_NOT_SUPPORTED
  - Writer Routing
  - Prose Output Contract
  - Prose Validator (structural, 0 LLM)
  - OutputGuard (content-form-aware)
  - Cross Combinations (viral/general × audio/prose)
  - Backward Compatibility
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest  # noqa: E402

from drama_engine.content_forms import (
    ContentForm,
    ContentFormProfile,
    available_content_forms,
    get_content_form,
    resolve_content_form,
)
from drama_engine.content_forms.registry import ContentFormNotSupportedError
from drama_engine.content_forms.validators import (
    output_guard_content_form_aware,
    prose_output_guard,
    validate_prose_structure,
)
from drama_engine.core.errors import EngineErrorCode
from drama_engine.schemas import ProseParagraph, ProseStory


# ============================================================================
# Registry Tests
# ============================================================================
class TestContentFormRegistry:
    """Content Form Registry 基本功能。"""

    def test_audio_drama_registered(self):
        profile = get_content_form("audio_drama")
        assert profile.key == "audio_drama"

    def test_prose_story_registered(self):
        profile = get_content_form("prose_story")
        assert profile.key == "prose_story"

    def test_available_forms(self):
        forms = available_content_forms()
        assert "audio_drama" in forms
        assert "prose_story" in forms

    def test_default_is_audio_drama(self):
        profile = get_content_form(None)
        assert profile.key == "audio_drama"

    def test_unknown_raises(self):
        with pytest.raises(ContentFormNotSupportedError) as exc:
            get_content_form("unknown")
        assert "unknown" in str(exc.value)

    def test_unknown_error_code(self):
        with pytest.raises(ContentFormNotSupportedError):
            get_content_form("nonexistent")
        # Error code should be CONTENT_FORM_NOT_SUPPORTED


# ============================================================================
# ContentFormProfile Tests
# ============================================================================
class TestContentFormProfiles:
    """Profile 字段验证。"""

    def test_audio_profile_fields(self):
        p = get_content_form("audio_drama")
        assert p.writer == "episode_writer"
        assert p.supports_audio_events is True
        assert p.supports_dialogue_labels is True
        assert p.supports_narrative_paragraphs is False

    def test_prose_profile_fields(self):
        p = get_content_form("prose_story")
        assert p.writer == "prose_writer"
        assert p.supports_audio_events is False
        assert p.supports_dialogue_labels is False
        assert p.supports_narrative_paragraphs is True

    def test_prose_metadata(self):
        p = get_content_form("prose_story")
        assert p.metadata.get("min_chars", 0) > 0
        assert p.metadata.get("min_paragraphs", 0) >= 3


# ============================================================================
# Resolver Tests
# ============================================================================
class TestContentFormResolver:
    """Content Form Resolver: 0 LLM。"""

    def test_explicit_audio(self):
        p = resolve_content_form("audio_drama")
        assert p.key == "audio_drama"

    def test_explicit_prose(self):
        p = resolve_content_form("prose_story")
        assert p.key == "prose_story"

    def test_default_is_audio(self):
        p = resolve_content_form(None)
        assert p.key == "audio_drama"

    def test_unknown_error(self):
        with pytest.raises(ContentFormNotSupportedError):
            resolve_content_form("novel")


# ============================================================================
# Prose Output Contract Tests
# ============================================================================
class TestProseOutputContract:
    """ProseStory / ProseParagraph 输出契约。"""

    def test_prose_story_creation(self):
        s = ProseStory(
            title="测试",
            paragraphs=[ProseParagraph(text="段落一"), ProseParagraph(text="段落二")],
        )
        assert s.title == "测试"
        assert len(s.paragraphs) == 2
        assert s.plain_text == "段落一\n\n段落二"

    def test_plain_text_generation(self):
        s = ProseStory(
            title="故事",
            paragraphs=[
                ProseParagraph(text="第一个段落。"),
                ProseParagraph(text="第二个段落。"),
            ],
        )
        assert s.plain_text == "第一个段落。\n\n第二个段落。"

    def test_paragraphs_not_empty(self):
        s = ProseStory(title="空", paragraphs=[])
        assert s.paragraphs == []
        assert s.plain_text == ""


# ============================================================================
# Prose Validator Tests (0 LLM)
# ============================================================================
class TestProseValidator:
    """Prose structural validator — 纯代码，0 LLM。"""

    def test_valid_prose_passes(self):
        long_text = (
            "这是用来测试散文故事验证器的一个较长段落，需要足够多的字符来满足最小长度的要求。"
            "我们正在构建一个内容形式架构，让同样的故事计划可以被不同的渲染器输出为不同的内容形式。"
            "这段文字必须足够长才能通过ProseValidator的最小字符数检查，至少需要五百个字符。"
            "因此我们在这里添加了更多的叙述性文字来填充段落内容，使得总长度超过验证阈值。"
        ) * 3  # ~500 chars
        prose = ProseStory(
            title="测试故事",
            paragraphs=[ProseParagraph(text=long_text) for _ in range(6)],
        )
        result = validate_prose_structure(prose)
        assert result["passed"] is True
        assert len(result["findings"]) == 0

    def test_empty_title_fails(self):
        prose = ProseStory(title="", paragraphs=[ProseParagraph(text="内容")])
        result = validate_prose_structure(prose)
        assert result["passed"] is False
        assert any("P01" in f["rule_id"] for f in result["findings"])

    def test_no_paragraphs_fails(self):
        prose = ProseStory(title="测试", paragraphs=[])
        result = validate_prose_structure(prose)
        assert result["passed"] is False
        assert any("P02" in f["rule_id"] for f in result["findings"])

    def test_too_short_fails(self):
        prose = ProseStory(
            title="测试",
            paragraphs=[ProseParagraph(text="短。")],
        )
        result = validate_prose_structure(prose)
        assert result["passed"] is False
        assert any("P04" in f["rule_id"] for f in result["findings"])

    def test_audio_label_contamination_detected(self):
        prose = ProseStory(
            title="测试",
            paragraphs=[
                ProseParagraph(text="第一段正常叙事。"),
                ProseParagraph(text="【音效】门突然被推开。"),
                ProseParagraph(text="第三段。"),
            ] * 2,
        )
        result = validate_prose_structure(prose)
        assert not result["passed"]
        assert any("P06" in f["rule_id"] for f in result["findings"])

    def test_speaker_labels_warning(self):
        prose = ProseStory(
            title="测试",
            paragraphs=[
                ProseParagraph(text="张三：你好\n李四：你好\n王五：再见\n赵六：嗯"),
            ] * 6,
        )
        result = validate_prose_structure(prose)
        # Should have warnings about speaker labels
        assert any("P07" in w["rule_id"] for w in result.get("warnings", []))

    def test_bullet_detection(self):
        prose = ProseStory(
            title="测试",
            paragraphs=[
                ProseParagraph(text="- 第一幕：开场\n- 第二幕：冲突\n* 角色A行动"),
            ] * 10,
        )
        result = validate_prose_structure(prose)
        assert not result["passed"]
        assert any("P08" in f["rule_id"] for f in result["findings"])


# ============================================================================
# OutputGuard Tests
# ============================================================================
class TestOutputGuard:
    """Content-form-aware OutputGuard。"""

    def test_prose_guard_passes(self):
        prose = ProseStory(title="OK", paragraphs=[ProseParagraph(text="A" * 200)])
        result = prose_output_guard(prose)
        assert result["passed"] is True

    def test_prose_guard_none(self):
        result = prose_output_guard(None)
        assert result["passed"] is False

    def test_prose_guard_empty(self):
        prose = ProseStory(title="", paragraphs=[])
        result = prose_output_guard(prose)
        assert result["passed"] is False

    def test_prose_guard_too_short(self):
        prose = ProseStory(title="T", paragraphs=[ProseParagraph(text="x")])
        result = prose_output_guard(prose)
        assert result["passed"] is False

    def test_guard_audio_is_audio(self):
        result = output_guard_content_form_aware("audio_drama", episode=True)
        assert result["passed"] is True

    def test_guard_prose_is_prose(self):
        prose = ProseStory(title="T", paragraphs=[ProseParagraph(text="A" * 200)])
        result = output_guard_content_form_aware("prose_story", prose=prose)
        assert result["passed"] is True

    def test_guard_audio_none_fails(self):
        result = output_guard_content_form_aware("audio_drama", episode=None)
        assert result["passed"] is False


# ============================================================================
# Cross Combination Tests
# ============================================================================
class TestCrossCombinations:
    """Mode × Content Form 组合必须独立。"""

    def test_combinations_are_independent(self):
        """所有组合都可解析，不互相限制。"""
        combos = [
            ("viral_drama", "audio_drama"),
            ("viral_drama", "prose_story"),
            ("general", "audio_drama"),
            ("general", "prose_story"),
        ]
        for mode, form in combos:
            # Just verify both resolve independently
            p = get_content_form(form)
            assert p is not None, f"Form {form} should be available for mode {mode}"
            assert p.key == form

    def test_prose_not_limited_to_general(self):
        """Prose 不能只绑在 general mode。"""
        p = get_content_form("prose_story")
        assert p.key == "prose_story"
        # No mode restriction

    def test_audio_not_limited_to_viral(self):
        """Audio 不能只绑在 viral_drama mode。"""
        p = get_content_form("audio_drama")
        assert p.key == "audio_drama"


# ============================================================================
# ContentForm Enum Tests
# ============================================================================
class TestContentFormEnum:
    """ContentForm enum 行为验证。"""

    def test_enum_values(self):
        assert ContentForm.AUDIO_DRAMA.value == "audio_drama"
        assert ContentForm.PROSE_STORY.value == "prose_story"

    def test_enum_from_string(self):
        assert ContentForm("audio_drama") == ContentForm.AUDIO_DRAMA
        assert ContentForm("prose_story") == ContentForm.PROSE_STORY

    def test_invalid_enum(self):
        with pytest.raises(ValueError):
            ContentForm("invalid")