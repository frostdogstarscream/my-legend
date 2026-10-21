"""Tests for StoryDelivery export, validation, and role mapping."""
from __future__ import annotations

import json
import pytest

from drama_engine.workflow.schemas import (
    StoryDelivery, EpisodeDelivery, SceneDelivery, Utterance,
    EmphasisSpan, PauseCue, Emotion, PerformanceValidationReport,
    PlanContent, EpisodeOutline, StoryVersionStatus, CharacterSnapshot,
)
from drama_engine.workflow.delivery import (
    build_character_role_map,
    delivery_to_readable_script,
    delivery_to_json,
    delivery_from_json,
    verify_round_trip,
    validate_delivery_integrity,
    export_delivery_bundle,
    DeliveryIntegrityReport,
)


# ── Fixtures ──────────────────────────────────────────────────

def make_utterance(line_id: str, kind="dialogue", speaker="protagonist",
                   text="你好。", emotion=None, tone=None, emphasis=None):
    return Utterance(
        line_id=line_id, kind=kind, speaker_role_id=speaker if kind == "dialogue" else None,
        narrator_id="narrator" if kind == "narration" else None,
        performer_id=speaker if kind == "dialogue" else "narrator",
        text=text,
        emotion=emotion or Emotion.NEUTRAL,
        tone_instruction=tone or "平静地说",
        emphasis=emphasis or [EmphasisSpan(span_text="你好", occurrence=1, start_char=0, end_char=2)],
    )


def make_minimal_delivery(**overrides) -> StoryDelivery:
    """Create a minimal valid StoryDelivery for testing."""
    defaults = dict(
        story_id="story-1",
        story_version_id="sv-1",
        version_no=1,
        lineage_type="create",
        approved_plan_id="plan-1",
        approved_plan_revision=1,
        title="测试故事",
        summary="这是一个测试故事。",
        preview_blurb="测试预告文案，十五字以上但不超过一百六十字的预览。",
        story_mode="general",
        content_form="audio_drama",
        character_snapshot=[],
        character_portrayals=[
            {"role_id": "protagonist", "story_name": "林敏", "external_goal": "找到真相",
             "emotional_avoidance": "面对现实", "speech_style": "温柔但坚定", "relationship_stance": ""},
            {"role_id": "husband", "story_name": "陈宇", "external_goal": "维持现状",
             "emotional_avoidance": "失去妻子", "speech_style": "紧张、结巴", "relationship_stance": ""},
        ],
        outline=PlanContent(
            title="测试故事", premise="这是一个测试故事。", theme="测试主题",
            beginning="开端。", development="发展。", climax="高潮。", ending="结局。",
            episode_outlines=[
                EpisodeOutline(index=1, title="第一集", core_goal="测试目标", major_beats=["节拍1", "节拍2"],
                               ending="测试结局", target_duration_sec=180),
            ],
            character_portrayals=[],
        ),
        episodes=[
            EpisodeDelivery(
                episode_id="ep-1",
                index=1,
                title="第一集",
                synopsis="本集概要。",
                duration_target_sec=180,
                duration_estimate_sec=175,
                scenes=[
                    SceneDelivery(
                        scene_id="ep1-scene1",
                        scene_index=1,
                        title="客厅",
                        location="家中客厅",
                        dramatic_goal="揭开秘密",
                        utterances=[
                            make_utterance("ep1-s1-l1", "narration", text="门被推开了。"),
                            make_utterance("ep1-s1-l2", "dialogue", "protagonist", "你终于回来了。"),
                            make_utterance("ep1-s1-l3", "dialogue", "husband", "嗯……我回来了。"),
                            make_utterance("ep1-s1-l4", "sfx", text="关门的声音"),
                        ],
                    ),
                ],
                validation_status="PASSED",
                ready_for_playback=True,
            ),
        ],
        quality_report=PerformanceValidationReport(
            validation_status="PASSED", spoken_line_count=3,
            emotion_coverage=1.0, tone_coverage=1.0,
            emphasis_coverage=1.0, emphasis_span_validity=1.0,
        ),
        ready_for_playback=True,
        status=StoryVersionStatus.CANDIDATE,
    )
    defaults.update(overrides)
    return StoryDelivery(**defaults)


# ── Tests ─────────────────────────────────────────────────────

class TestCharacterRoleMap:
    def test_builds_map_from_portrayals(self):
        delivery = make_minimal_delivery()
        role_map = build_character_role_map(delivery)
        assert "protagonist" in role_map
        assert role_map["protagonist"].get("story_name") == "林敏" or role_map["protagonist"].get("canon_name") == "林敏"
        assert "speech_style" in role_map["protagonist"]
        assert role_map["husband"]["story_name"] == "陈宇"

    def test_includes_narrator(self):
        delivery = make_minimal_delivery()
        role_map = build_character_role_map(delivery)
        assert "narrator" in role_map
        assert role_map["narrator"]["canon_name"] == "旁白"

    def test_enriches_from_snapshots(self):
        delivery = make_minimal_delivery(character_snapshot=[
            CharacterSnapshot(
                character_id="protagonist", role_id="protagonist", performer_id="perf-A",
                canon={"name": "林敏", "immutable_facts": ["三十五岁"]},
                profile={"goal": "找到真相", "motivation": "不甘", "fear": "失去一切", "speech_style": "温柔"},
            ),
        ])
        role_map = build_character_role_map(delivery)
        assert role_map["protagonist"]["performer_id"] == "perf-A"
        assert "三十五岁" in role_map["protagonist"]["immutable_facts"]


class TestReadableScript:
    def test_produces_non_empty_script(self):
        delivery = make_minimal_delivery()
        script = delivery_to_readable_script(delivery)
        assert "测试故事" in script
        assert "林敏" in script
        assert "陈宇" in script
        assert "你终于回来了" in script

    def test_includes_character_legend(self):
        delivery = make_minimal_delivery()
        script = delivery_to_readable_script(delivery)
        assert "登场角色" in script

    def test_includes_scene_headers(self):
        delivery = make_minimal_delivery()
        script = delivery_to_readable_script(delivery)
        assert "场景1" in script
        assert "客厅" in script

    def test_labels_sfx_actions(self):
        delivery = make_minimal_delivery()
        script = delivery_to_readable_script(delivery)
        assert "音效" in script
        assert "关门" in script

    def test_marks_model_generated_characters(self):
        delivery = make_minimal_delivery()
        script = delivery_to_readable_script(delivery)
        assert "模型生成" in script


class TestIntegrityValidation:
    def test_valid_delivery_passes(self):
        delivery = make_minimal_delivery()
        report = validate_delivery_integrity(delivery)
        assert report.valid
        assert report.stats["episodes"] == 1
        assert report.stats["scenes"] == 1

    def test_duplicate_line_id_fails(self):
        delivery = make_minimal_delivery()
        # Clone first utterance
        delivery.episodes[0].scenes[0].utterances.append(
            make_utterance("ep1-s1-l1", "narration", text="重复的ID。"),
        )
        report = validate_delivery_integrity(delivery)
        assert not report.valid

    def test_missing_speaker_on_dialogue_fails(self):
        delivery = make_minimal_delivery()
        delivery.episodes[0].scenes[0].utterances.append(
            Utterance(line_id="ep1-s1-l99", kind="dialogue", text="没角色。", performer_id="?",
                       emotion=Emotion.NEUTRAL, tone_instruction="?",
                       emphasis=[EmphasisSpan(span_text="没", occurrence=1, start_char=0, end_char=1)]),
        )
        report = validate_delivery_integrity(delivery)
        assert not report.valid

    def test_unknown_role_fails(self):
        delivery = make_minimal_delivery()
        delivery.episodes[0].scenes[0].utterances.append(
            make_utterance("ep1-s1-l99", "dialogue", "nonexistent", "你好。"),
        )
        report = validate_delivery_integrity(delivery)
        assert not report.valid

    def test_parens_in_dialogue_warns(self):
        delivery = make_minimal_delivery()
        delivery.episodes[0].scenes[0].utterances.append(
            make_utterance("ep1-s1-l99", "dialogue", "protagonist", "(冷笑)你走吧。"),
        )
        report = validate_delivery_integrity(delivery)
        assert any("(" in w for w in report.warnings)

    def test_empty_title_fails(self):
        delivery = make_minimal_delivery(title="")
        report = validate_delivery_integrity(delivery)
        assert not report.valid

    def test_no_episodes_fails(self):
        delivery = make_minimal_delivery(episodes=[])
        report = validate_delivery_integrity(delivery)
        assert not report.valid


class TestJSONRoundTrip:
    def test_round_trip_preserves_content(self):
        delivery = make_minimal_delivery()
        ok, msg = verify_round_trip(delivery)
        assert ok, msg

    def test_to_json_produces_valid_json(self):
        delivery = make_minimal_delivery()
        json_str = delivery_to_json(delivery)
        parsed = json.loads(json_str)
        assert parsed["title"] == "测试故事"

    def test_from_json_reconstructs_delivery(self):
        delivery = make_minimal_delivery()
        json_str = delivery_to_json(delivery)
        reloaded = delivery_from_json(json_str)
        assert reloaded.title == delivery.title
        assert reloaded.story_id == delivery.story_id

    def test_delivery_format_version_present(self):
        delivery = make_minimal_delivery()
        json_str = delivery_to_json(delivery)
        parsed = json.loads(json_str)
        assert parsed["delivery_format_version"] == "1.0"

    def test_character_portrayals_in_json(self):
        delivery = make_minimal_delivery()
        json_str = delivery_to_json(delivery)
        parsed = json.loads(json_str)
        assert len(parsed["character_portrayals"]) == 2
        assert parsed["character_portrayals"][0]["story_name"] == "林敏"


class TestExportBundle:
    def test_exports_all_files(self, tmp_path):
        delivery = make_minimal_delivery()
        files = export_delivery_bundle(delivery, tmp_path, "test_story")
        assert files["json"].exists()
        assert files["script"].exists()
        assert files["characters"].exists()

    def test_script_is_readable(self, tmp_path):
        delivery = make_minimal_delivery()
        files = export_delivery_bundle(delivery, tmp_path, "test_story")
        content = files["script"].read_text(encoding="utf-8")
        assert "测试故事" in content
        assert "林敏" in content

    def test_characters_json_has_roles(self, tmp_path):
        delivery = make_minimal_delivery()
        files = export_delivery_bundle(delivery, tmp_path, "test_story")
        chars = json.loads(files["characters"].read_text(encoding="utf-8"))
        assert "protagonist" in chars
        assert "narrator" in chars


class TestNewSchemaFields:
    def test_scene_delivery_has_scene_index(self):
        scene = SceneDelivery(scene_id="ep1-s1", scene_index=3, title="测试")
        assert scene.scene_index == 3

    def test_scene_index_defaults_to_1(self):
        scene = SceneDelivery(scene_id="ep1-s1", title="测试")
        assert scene.scene_index == 1

    def test_story_delivery_has_format_version(self):
        delivery = make_minimal_delivery()
        assert delivery.delivery_format_version == "1.0"

    def test_story_delivery_has_character_portrayals(self):
        delivery = make_minimal_delivery()
        assert len(delivery.character_portrayals) == 2
        assert delivery.character_portrayals[0]["role_id"] == "protagonist"