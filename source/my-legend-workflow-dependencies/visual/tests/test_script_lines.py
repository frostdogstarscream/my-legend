import unittest

from app.script_lines import compile_script_lines, validate_script_lines


def sample_script():
    return {
        "title": "星光灯塔的重生",
        "source_story": {"story_id": "story-1"},
        "blocks": [
            {"id": "blk_0001", "type": "scene_heading", "act": 1, "scene": 1, "text": "第一场", "location": "灯塔", "time": "夜"},
            {"id": "blk_0002", "type": "stage_direction", "text": "海风停了，灯塔的轮廓在雾里显出来。"},
            {"id": "blk_0003", "type": "dialogue", "character": "阿稳", "role_id": "role_01", "text": "先把灯芯拆下来，别急着点火。", "emotion": "serious", "delivery": "command", "intensity": 0.7},
            {"id": "blk_0004", "type": "dialogue", "character": "小阳", "role_id": "role_02", "text": "我数三声，你那边一起松手。", "emotion": "excited", "delivery": "statement", "intensity": 0.6},
            {"id": "blk_0005", "type": "sfx", "name": "海浪", "duration_ms": 1200},
            {"id": "blk_0006", "type": "stage_direction", "text": "   "},
            {"id": "blk_0007", "type": "dialogue", "character": "灯灯", "role_id": "role_03", "text": "我感觉到光要回来了。", "pause_after_ms": 600},
        ],
    }


class ScriptLinesTests(unittest.TestCase):
    def test_lines_split_narration_and_dialogue_with_role_codes(self):
        document = compile_script_lines("星光灯塔的重生", sample_script())
        self.assertEqual(document["schema"], "script_lines")
        self.assertEqual(document["line_count"], 4)
        first = document["lines"][0]
        self.assertEqual((first["line_no"], first["type"], first["role_code"]), (1, "narration", "narrator"))
        self.assertEqual(first["delivery"], "narration")
        second = document["lines"][1]
        self.assertEqual((second["type"], second["role_code"], second["character"]), ("dialogue", "role_01", "阿稳"))
        # 空白舞台提示不产出行，sfx 不产出行
        self.assertEqual([line["line_no"] for line in document["lines"]], [1, 2, 3, 4])
        # 自定义 pause_after_ms 被保留
        last = document["lines"][-1]
        self.assertEqual(last["pause_after_ms"], 600)
        # 每行都有正估算时长与场次归属
        self.assertTrue(all(line["estimated_duration_ms"] > 0 for line in document["lines"]))
        self.assertTrue(all(line["act"] == 1 and line["scene"] == 1 for line in document["lines"]))
        # roles 表覆盖全部出现过的 role_code
        self.assertEqual({role["role_code"] for role in document["roles"]}, {"narrator", "role_01", "role_02", "role_03"})
        self.assertEqual(validate_script_lines(document), [])

    def test_validator_catches_broken_references(self):
        document = compile_script_lines("t", sample_script())
        document["lines"][1]["role_code"] = "ghost"
        document["lines"][2]["line_no"] = 99
        errors = validate_script_lines(document)
        self.assertTrue(any("ghost" in error for error in errors))
        self.assertTrue(any("line_no" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
