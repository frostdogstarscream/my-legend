import json
import unittest
from unittest.mock import patch

from app.config import Settings
from app.generator import _generate_story_by_scenes, _meets_contract, _performance_contract, demo_script, format_script
from app.story_input import parse_story_document


class GeneratorTests(unittest.TestCase):
    def test_demo_script_can_be_formatted(self):
        script = demo_script("测试作品", "第一章", "甲：你为什么来？\n乙：因为事情还没有结束。" * 5, {})
        text = format_script(script)
        self.assertIn("测试作品", text)
        self.assertIn("人物表", text)
        self.assertIn("甲", text)

    def test_performance_contract_rejects_outline_length(self):
        contract = _performance_contract({"duration_minutes": 10}, chapter_count=2)
        outline = {"blocks": [{"type": "scene_heading"}, {"type": "dialogue", "text": "很短的一句台词。"}]}
        self.assertFalse(_meets_contract(outline, contract))

    def test_single_chapter_story_generates_enough_scenes(self):
        # 单章故事的合同最低场次数是 3，生成必须按合同拆场，否则合并后永远验收失败。
        document = {
            "status": "success", "story_id": "s-1",
            "characters": [
                {"role_id": "role_01", "name": "甲"},
                {"role_id": "role_02", "name": "乙"},
            ],
            "story": {"title": "单章故事", "chapters": [
                {"chapter_number": 1, "title": "唯一章", "participating_role_ids": ["role_01", "role_02"], "content": "甲乙在灯塔下相遇，争论是否点亮星光。"},
            ]},
            "meta": {"schema_version": "story.v3.1"},
        }
        context = parse_story_document(document)
        settings = Settings(demo_mode=False, api_key="test-key")

        def fake_chat(system, user, *, settings=None, max_tokens=12000, json_mode=False):
            contract = json.loads(user)["performance_contract"]
            line = "这是一句足够长的台词，用来满足可演时长合同的最低字数要求。"
            blocks = [{"type": "scene_heading", "text": "场"}]
            while True:
                stats_chars = sum(len(b["text"]) for b in blocks if b["type"] == "dialogue")
                stats_turns = sum(b["type"] == "dialogue" for b in blocks)
                if stats_chars >= contract["minimum_dialogue_characters"] and stats_turns >= contract["minimum_dialogue_turns"]:
                    break
                blocks.append({"type": "dialogue", "character": "甲" if stats_turns % 2 == 0 else "乙", "text": line})
            return json.dumps({"blocks": blocks}, ensure_ascii=False)

        with patch("app.generator.chat", fake_chat):
            script = _generate_story_by_scenes(context, {"duration_minutes": 10}, settings)
        scenes = sum(block.get("type") == "scene_heading" for block in script["blocks"])
        self.assertGreaterEqual(scenes, 3)
        self.assertTrue(all(block.get("role_id") for block in script["blocks"] if block.get("type") == "dialogue"))
    def test_text_mode_plans_then_generates_scenes(self):
        # 纯文本输入也应先策划（角色表+场次任务）再逐场生成，单次调用写不出整场话剧。
        from app.generator import _generate_text_by_scenes
        settings = Settings(demo_mode=False, api_key="test-key")
        calls = {"plan": 0, "scene": 0}

        def fake_chat(system, user, *, settings=None, max_tokens=12000, json_mode=False):
            payload = json.loads(user)
            if "scene_count" in payload:
                calls["plan"] += 1
                return json.dumps({
                    "characters": [{"name": "甲", "description": "守灯人"}, {"name": "乙", "description": "旅人"}],
                    "scenes": [{"scene_number": i, "task": f"任务{i}", "location": "灯塔", "time": "夜"} for i in range(1, payload["scene_count"] + 1)],
                }, ensure_ascii=False)
            calls["scene"] += 1
            contract = payload["performance_contract"]
            line = "这是一句足够长的台词，用来满足可演时长合同的最低字数要求。"
            blocks = [{"type": "scene_heading", "text": "场"}]
            while True:
                stats_chars = sum(len(b["text"]) for b in blocks if b["type"] == "dialogue")
                stats_turns = sum(b["type"] == "dialogue" for b in blocks)
                if stats_chars >= contract["minimum_dialogue_characters"] and stats_turns >= contract["minimum_dialogue_turns"]:
                    break
                blocks.append({"type": "dialogue", "character": "甲" if stats_turns % 2 == 0 else "乙", "text": line})
            return json.dumps({"blocks": blocks}, ensure_ascii=False)

        with patch("app.generator.chat", fake_chat):
            script = _generate_text_by_scenes("灯塔", "第一章", "甲乙在灯塔下争论是否点亮星光。" * 10, {"duration_minutes": 10}, settings)
        self.assertEqual(calls["plan"], 1)
        self.assertEqual(calls["scene"], 3)
        scenes = sum(block.get("type") == "scene_heading" for block in script["blocks"])
        self.assertGreaterEqual(scenes, 3)
        self.assertEqual({c["name"] for c in script["characters"]}, {"甲", "乙"})


if __name__ == "__main__":
    unittest.main()
