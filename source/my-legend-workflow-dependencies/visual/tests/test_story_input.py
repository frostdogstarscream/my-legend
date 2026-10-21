import unittest

from app.generator import demo_story_script
from app.story_input import StoryInputError, parse_story_document
from app.tpm import compile_tpm, validate_tpm


def sample_document():
    return {
        "status": "success_with_warning", "request_id": "req-1", "story_id": "story-1",
        "characters": [
            {"role_id": "role_01", "name": "观察者", "identity": "谨慎的观察者", "dialogue_style": {"speaking_rhythm": "steady"}},
            {"role_id": "role_02", "name": "探索者", "identity": "行动派", "dialogue_style": {"speaking_rhythm": "fast"}},
            {"role_id": "role_03", "name": "创造者", "identity": "想象派", "dialogue_style": {"speaking_rhythm": "variable"}},
        ],
        "relationships": [{"role_a": "role_01", "role_b": "role_02", "relationship": "互补"}],
        "story": {"title": "测试故事", "genre": "科幻", "tone": "自然", "theme": "信任", "logline": "测试", "chapters": [
            {"chapter_number": 1, "title": "开始", "summary": "危机", "participating_role_ids": ["role_01", "role_02", "role_03"], "content": "警报响起。观察者发现异常，探索者准备行动，创造者提出镜像假说。"},
            {"chapter_number": 2, "title": "结尾", "summary": "解决", "participating_role_ids": ["role_01", "role_02"], "content": "三人合作解决危机，飞船返回航线。"},
        ]},
        "warnings": ["上游使用了降级模型"], "meta": {"schema_version": "story.v3", "degraded_level": 2},
    }


class StoryInputTests(unittest.TestCase):
    def test_selected_chapters_and_warnings_are_preserved(self):
        context = parse_story_document(sample_document(), [2])
        self.assertEqual([chapter["chapter_number"] for chapter in context.chapters], [2])
        self.assertEqual(context.schema_version, "story.v3")
        self.assertTrue(context.warnings)

    def test_flat_document_is_normalized_without_story_wrapper(self):
        document = sample_document()
        story = document.pop("story")
        document.update({"title": story["title"], "chapters": story["chapters"]})
        context = parse_story_document(document)
        self.assertEqual(context.title, "测试故事")
        self.assertEqual(len(context.chapters), 2)
        self.assertTrue(any("自动归一化" in warning for warning in context.warnings))

    def test_tpm_document_is_normalized_to_a_story_chapter(self):
        tpm = {"schema": "tpm", "version": "1.0.0", "show_id": "show-1", "title": "TPM 测试", "actors": [{"actor_id": "role_01", "character_name": "甲"}], "events": [{"type": "sfx", "start_ms": 0, "name": "钟声"}, {"type": "speech", "actor_id": "role_01", "start_ms": 1000, "text": "我们开始吧。", "performance": {"emotion": {"primary": "serious"}}}]}
        context = parse_story_document(tpm)
        self.assertEqual(context.title, "TPM 测试")
        self.assertIn("甲（serious）：我们开始吧。", context.chapters[0]["content"])

    def test_relationship_and_duplicate_role_validation(self):
        broken = sample_document()
        broken["relationships"][0]["role_b"] = "missing"
        with self.assertRaises(StoryInputError):
            parse_story_document(broken)
        broken = sample_document()
        broken["characters"].append(broken["characters"][0].copy())
        with self.assertRaises(StoryInputError):
            parse_story_document(broken)

    def test_story_script_preserves_role_ids_into_tpm(self):
        context = parse_story_document(sample_document())
        script = demo_story_script(context, {})
        dialogue_ids = {block["role_id"] for block in script["blocks"] if block.get("type") == "dialogue"}
        self.assertTrue(dialogue_ids.issubset({"role_01", "role_02", "role_03"}))
        tpm = compile_tpm(context.title, script)
        # 叙述性舞台提示会编译为旁白演员的台词事件。
        self.assertEqual({actor["actor_id"] for actor in tpm["actors"]}, dialogue_ids | {"narrator"})
        self.assertEqual(validate_tpm(tpm), [])

    def test_story_v31_template_quality_errors_and_voice_profile(self):
        document = sample_document()
        document["meta"]["schema_version"] = "story.v3.1"
        document["characters"][0]["voice_profile"] = {"speaking_rate": "medium", "pitch_tendency": "mid_low", "vocal_strength": "medium", "warmth": 58}
        document["quality_result"] = {"available": True, "passed": True, "overall_score": 95, "issues": [{"severity": "minor", "category": "plot_coherence", "description": "齿轮纤维来源未解释"}]}
        document["errors"] = ["第 2 章曾生成失败后修复"]
        context = parse_story_document(document)
        self.assertTrue(any("齿轮纤维来源未解释" in warning for warning in context.warnings))
        self.assertTrue(any("上游错误" in warning for warning in context.warnings))
        self.assertEqual(context.characters[0]["voice_profile"]["pitch_tendency"], "mid_low")
        script = demo_story_script(context, {})
        tpm = compile_tpm(context.title, script)
        voice = next(actor.get("voice") for actor in tpm["actors"] if actor["actor_id"] == "role_01")
        self.assertEqual(voice["pitch_tendency"], "mid_low")
        self.assertEqual(validate_tpm(tpm), [])

    def test_story_v31_failed_quality_adds_warning(self):
        document = sample_document()
        document["quality_result"] = {"available": True, "passed": False, "overall_score": 52, "issues": []}
        context = parse_story_document(document)
        self.assertTrue(any("质量校验未通过" in warning for warning in context.warnings))

    def test_dify_workflow_response_is_unwrapped(self):
        # Dify 工作流 API 响应：{task_id, workflow_run_id, data:{status, outputs:{result:<story.v3.1>}}}
        wrapped = {"task_id": "t-1", "workflow_run_id": "w-1", "data": {"status": "succeeded", "error": None, "outputs": {"result": sample_document()}}}
        context = parse_story_document(wrapped)
        self.assertEqual(context.title, "测试故事")
        self.assertEqual(len(context.chapters), 2)

    def test_dify_workflow_failure_raises_clear_error(self):
        failed = {"task_id": "t-1", "data": {"status": "failed", "error": "模型超时", "outputs": None}}
        with self.assertRaises(StoryInputError) as raised:
            parse_story_document(failed)
        self.assertIn("模型超时", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
