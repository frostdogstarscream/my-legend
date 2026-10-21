import unittest

from app.tpm import compile_tpm, estimate_speech_ms, validate_tpm


class TpmTests(unittest.TestCase):
    def test_estimate_speech_duration_is_positive(self):
        self.assertGreater(estimate_speech_ms("先生不出，如苍生何？"), 1000)

    def test_compile_valid_tpm(self):
        script = {
            "blocks": [{
                "id": "blk_0001", "type": "dialogue", "character": "刘备",
                "text": "先生不出，如苍生何？", "emotion": "serious", "intent": "persuade",
                "delivery": "question", "intensity": 0.8,
                "actions": [{"action": "bow", "start_offset_ms": 200, "duration_ms": 800, "intensity": 0.7}],
            }]
        }
        tpm = compile_tpm("三顾茅庐", script)
        self.assertEqual(tpm["schema"], "tpm")
        self.assertEqual(tpm["timeline_status"], "estimated")
        self.assertEqual(tpm["events"][0]["actor_id"], "actor_刘备")
        self.assertEqual(validate_tpm(tpm), [])

    def test_stage_direction_compiles_to_narrator_speech(self):
        script = {
            "blocks": [
                {"id": "blk_0001", "type": "stage_direction", "text": "夜幕降临，灯塔的光渐渐暗了下来。"},
                {"id": "blk_0002", "type": "dialogue", "character": "小铁皮", "text": "我们得把灯修好。"},
                {"id": "blk_0003", "type": "stage_direction", "text": "   "},
            ]
        }
        tpm = compile_tpm("灯塔", script)
        narration = [event for event in tpm["events"] if event["actor_id"] == "narrator"]
        self.assertEqual(len(narration), 1)
        self.assertEqual(narration[0]["type"], "speech")
        self.assertEqual(narration[0]["performance"]["delivery"], "narration")
        narrator = next(actor for actor in tpm["actors"] if actor["actor_id"] == "narrator")
        self.assertEqual(narrator["character_name"], "旁白")
        self.assertEqual(validate_tpm(tpm), [])


if __name__ == "__main__":
    unittest.main()
