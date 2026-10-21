from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone


EMOTION_PRESETS = {
    "neutral": {"rate": 1.0, "pitch": 0, "volume": 0.0},
    "happy": {"rate": 1.08, "pitch": 2, "volume": 0.8},
    "sad": {"rate": 0.86, "pitch": -2, "volume": -1.5},
    "angry": {"rate": 1.12, "pitch": 2, "volume": 2.0},
    "fearful": {"rate": 1.14, "pitch": 3, "volume": -0.5},
    "surprised": {"rate": 1.08, "pitch": 4, "volume": 1.0},
    "tender": {"rate": 0.9, "pitch": 1, "volume": -1.0},
    "serious": {"rate": 0.92, "pitch": -1, "volume": 0.5},
    "excited": {"rate": 1.18, "pitch": 3, "volume": 1.5},
    "nervous": {"rate": 1.1, "pitch": 2, "volume": -0.5},
    "mysterious": {"rate": 0.84, "pitch": -2, "volume": -2.0},
}


def estimate_speech_ms(text: str, rate: float = 1.0) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other_words = len(re.findall(r"[A-Za-z0-9]+", text))
    punctuation_pause = len(re.findall(r"[，、；：]", text)) * 160 + len(re.findall(r"[。！？]", text)) * 320
    return max(700, int((chinese * 245 + other_words * 420 + punctuation_pause) / max(rate, 0.5)))


def compile_tpm(title: str, script: dict) -> dict:
    actors: dict[str, dict] = {}
    events: list[dict] = []
    cursor = 0
    # 上游 story.v3.1 角色声线档案，原样传给下游 TTS/玩具适配器选用。
    voice_by_name = {
        character.get("name"): character.get("voice_profile")
        for character in script.get("characters", [])
        if isinstance(character, dict) and isinstance(character.get("voice_profile"), dict) and character.get("voice_profile")
    }
    for index, block in enumerate(script.get("blocks", []), start=1):
        block_type = block.get("type", "stage_direction")
        if block_type == "dialogue" or (block_type == "stage_direction" and str(block.get("text", "")).strip()):
            # 叙述性舞台提示由旁白演员朗读，避免表演时间轴出现大段无声空白。
            is_narration = block_type != "dialogue"
            actor_name = "旁白" if is_narration else block.get("character", "旁白")
            actor_id = str(block.get("role_id") or "actor_" + re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]", "", actor_name))
            if is_narration:
                actor_id = "narrator"
            actors.setdefault(actor_id, {
                "actor_id": actor_id,
                "character_name": actor_name,
                "voice_id": "default_zh_cn",
                "default_emotion": "neutral",
            })
            voice = voice_by_name.get(actor_name)
            if voice and "voice" not in actors[actor_id]:
                actors[actor_id]["voice"] = {
                    key: voice[key]
                    for key in ("voice_character", "speaking_rate", "pitch_tendency", "vocal_strength", "speech_rhythm", "warmth", "maturity", "vitality", "affinity")
                    if key in voice
                }
            emotion = block.get("emotion", "neutral")
            if emotion not in EMOTION_PRESETS:
                emotion = "neutral"
            preset = EMOTION_PRESETS[emotion]
            rate = float(block.get("rate", preset["rate"]))
            pause_before = int(block.get("pause_before_ms", 150))
            pause_after = int(block.get("pause_after_ms", 350))
            cursor += pause_before
            duration = estimate_speech_ms(block.get("text", ""), rate)
            event = {
                "event_id": f"evt_{index:04d}",
                "type": "speech",
                "actor_id": actor_id,
                "start_ms": cursor,
                "end_ms": cursor + duration,
                "timeline_status": "estimated",
                "text": block.get("text", ""),
                "performance": {
                    "emotion": {
                        "primary": emotion,
                        "secondary": block.get("secondary_emotion"),
                        "intensity": min(1.0, max(0.0, float(block.get("intensity", 0.55)))),
                    },
                    "intent": block.get("intent", "inform"),
                    "delivery": "narration" if is_narration else block.get("delivery", "statement"),
                    "prosody": {
                        "rate": min(1.4, max(0.7, rate)),
                        "pitch_semitones": min(6, max(-6, int(block.get("pitch_semitones", preset["pitch"])))),
                        "volume_db": min(6, max(-12, float(block.get("volume_db", preset["volume"])))),
                        "pause_before_ms": pause_before,
                        "pause_after_ms": pause_after,
                    },
                },
                "actions": block.get("actions", []),
                "source_block_id": block.get("id", f"blk_{index:04d}"),
            }
            events.append(event)
            cursor += duration + pause_after
        elif block_type in {"sfx", "light", "action"}:
            duration = int(block.get("duration_ms", 1000))
            events.append({
                "event_id": f"evt_{index:04d}", "type": block_type,
                "start_ms": cursor, "end_ms": cursor + duration,
                "timeline_status": "estimated", "name": block.get("name", block.get("text", "")),
                "parameters": block.get("parameters", {}),
                "source_block_id": block.get("id", f"blk_{index:04d}"),
            })
    return {
        "schema": "tpm",
        "version": "1.0.0",
        "show_id": "show_" + uuid.uuid4().hex,
        "title": title,
        "locale": "zh-CN",
        "timebase": 1000,
        "duration_ms": cursor,
        "timeline_status": "estimated",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "actors": list(actors.values()),
        "events": events,
        "device_requirements": {"speech": True, "head_rotation": False, "lights": False, "max_parallel_actions": 1},
    }


def validate_tpm(tpm: dict) -> list[str]:
    errors: list[str] = []
    last_start = -1
    actor_ids = {actor.get("actor_id") for actor in tpm.get("actors", [])}
    for event in tpm.get("events", []):
        start, end = event.get("start_ms"), event.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            errors.append(f"{event.get('event_id')} 的时间戳无效")
        if isinstance(start, int) and start < last_start:
            errors.append(f"{event.get('event_id')} 未按时间顺序排列")
        last_start = start if isinstance(start, int) else last_start
        if event.get("type") == "speech" and event.get("actor_id") not in actor_ids:
            errors.append(f"{event.get('event_id')} 引用了不存在的演员")
    return errors
