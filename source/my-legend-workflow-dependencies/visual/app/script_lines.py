"""script_lines 输出：面向 Java 后端播放链路的线性剧本行。

与 TPM 的关系：TPM 是玩具执行的完整协议（时间轴、韵律、设备能力）；
script_lines 是更轻的逐行清单——按行号排列、区分旁白与对白、
带 role_code 播放路由和 pause_after_ms，供 Java 后端校验后接 TTS/Gateway。
两者都从同一份结构化剧本编译，内容天然一致。
"""
from __future__ import annotations

from .tpm import estimate_speech_ms

DEFAULT_PAUSE_AFTER_MS = 350


def compile_script_lines(title: str, script: dict) -> dict:
    roles: dict[str, dict] = {}
    lines: list[dict] = []
    act = 0
    scene = 0
    for block in script.get("blocks", []):
        block_type = block.get("type", "stage_direction")
        if block_type == "scene_heading":
            act = int(block.get("act", act))
            scene = int(block.get("scene", scene + 1))
            continue
        is_dialogue = block_type == "dialogue"
        is_narration = block_type == "stage_direction" and str(block.get("text", "")).strip()
        if not (is_dialogue or is_narration):
            continue
        if is_dialogue:
            character = str(block.get("character", "旁白"))
            role_code = str(block.get("role_id") or character)
        else:
            character = "旁白"
            role_code = "narrator"
        if role_code not in roles:
            roles[role_code] = {"role_code": role_code, "name": character}
        text = str(block.get("text", "")).strip()
        pause_after = int(block.get("pause_after_ms", DEFAULT_PAUSE_AFTER_MS))
        lines.append({
            "line_no": len(lines) + 1,
            "type": "dialogue" if is_dialogue else "narration",
            "role_code": role_code,
            "character": character,
            "text": text,
            "emotion": block.get("emotion", "neutral"),
            "intensity": block.get("intensity", 0.55),
            "delivery": block.get("delivery", "statement") if is_dialogue else "narration",
            "pause_after_ms": pause_after,
            "estimated_duration_ms": estimate_speech_ms(text),
            "act": act,
            "scene": scene,
        })
    return {
        "schema": "script_lines",
        "version": "1.0.0",
        "title": title,
        "locale": "zh-CN",
        "source_story": script.get("source_story", {}),
        "roles": list(roles.values()),
        "lines": lines,
        "line_count": len(lines),
        "timeline_status": "estimated",
        "notes": "estimation 基于字数与标点；接入 TTS 后应以真实音频时长与字词时间戳替换。role_code 与上游 story.v3 角色 role_id 一致，播放设备路由由后端另行映射。",
    }


def validate_script_lines(document: dict) -> list[str]:
    errors: list[str] = []
    lines = document.get("lines", [])
    role_codes = {role.get("role_code") for role in document.get("roles", [])}
    for expected, line in enumerate(lines, start=1):
        if line.get("line_no") != expected:
            errors.append(f"line_no 不连续：第 {expected} 行实际为 {line.get('line_no')}")
        if line.get("role_code") not in role_codes:
            errors.append(f"第 {line.get('line_no')} 行引用了不存在的 role_code：{line.get('role_code')}")
        if not str(line.get("text", "")).strip():
            errors.append(f"第 {line.get('line_no')} 行文本为空")
    return errors
