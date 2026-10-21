"""Phase 6: Content Form Validators + Content-form-aware OutputGuard.

继承 Phase 4.5 的 Batch Judge / Repair Policy，为 prose_story 增加专用校验层。
"""
from __future__ import annotations

from typing import Any

from ..schemas import ProseStory


# ============================================================================
# Prose Structural Validator (0 LLM)
# ============================================================================
def validate_prose_structure(prose: ProseStory, target_chars: int = 2000) -> dict:
    """纯代码校验 ProseStory 结构完整性。

    Returns: {"passed": bool, "findings": list[dict], "warnings": list[dict]}
    """
    findings: list[dict] = []
    warnings: list[dict] = []

    # 1. 非空
    if not prose.title:
        findings.append({"rule_id": "P01", "severity": "error", "message": "Prose title is empty"})

    if not prose.paragraphs:
        findings.append({"rule_id": "P02", "severity": "error", "message": "Prose has no paragraphs"})

    if not prose.plain_text.strip():
        findings.append({"rule_id": "P03", "severity": "error", "message": "Prose plain_text is empty"})

    # 2. 最小长度
    if len(prose.plain_text) < 500:
        findings.append({
            "rule_id": "P04", "severity": "error",
            "message": f"Prose too short: {len(prose.plain_text)} chars (min 500)",
        })

    # 3. 段落数量
    if len(prose.paragraphs) < 3:
        warnings.append({
            "rule_id": "P05", "severity": "warning",
            "message": f"Only {len(prose.paragraphs)} paragraphs (recommend >=5)",
        })

    # 4. Audio label contamination
    audio_markers = ["SFX:", "NARRATOR:", "【音效】", "【旁白】"]
    for marker in audio_markers:
        if marker in prose.plain_text:
            findings.append({
                "rule_id": "P06", "severity": "error",
                "message": f"Audio label found in prose: '{marker}'",
            })

    # 5. Speaker-label format check (角色名：)
    import re
    speaker_lines = re.findall(r'^[^\s]{1,6}：', prose.plain_text, re.MULTILINE)
    if len(speaker_lines) > len(prose.paragraphs) * 0.5:
        warnings.append({
            "rule_id": "P07", "severity": "warning",
            "message": f"Too many speaker-label lines ({len(speaker_lines)}) — should use embedded dialogue",
        })

    # 6. Outline/summary detection
    bullet_lines = [l for l in prose.plain_text.split('\n') if l.strip().startswith(('- ', '* ', '1. ', '第'))]
    if len(bullet_lines) > len(prose.paragraphs) * 0.3:
        findings.append({
            "rule_id": "P08", "severity": "error",
            "message": f"Text looks like outline/bullets ({len(bullet_lines)} bullet lines), not narrative prose",
        })

    # 7. 重复检测
    lines = prose.plain_text.split('\n')
    unique_lines = set(l.strip() for l in lines if l.strip())
    if len(lines) > 10 and len(unique_lines) / max(len(lines), 1) < 0.5:
        warnings.append({
            "rule_id": "P09", "severity": "warning",
            "message": "High repetition detected in prose text",
        })

    passed = len(findings) == 0

    return {
        "passed": passed,
        "findings": findings,
        "warnings": warnings,
    }


# ============================================================================
# Prose OutputGuard
# ============================================================================
def prose_output_guard(prose: ProseStory | None, target_chars: int = 2000) -> dict[str, Any]:
    """Prose-specific OutputGuard。

    Returns: {"passed": bool, "reason": str}
    """
    if prose is None:
        return {"passed": False, "reason": "ProseStory is None"}

    if not prose.plain_text:
        return {"passed": False, "reason": "Prose plain_text is empty"}

    if len(prose.plain_text) < 150:
        return {"passed": False, "reason": f"Prose too short: {len(prose.plain_text)} chars"}

    if not prose.paragraphs:
        return {"passed": False, "reason": "No paragraphs"}

    # Phase 6.5: 音频标签污染检测（与 P06 对齐）
    full_text = prose.plain_text
    audio_markers = ["SFX:", "NARRATOR:", "【音效】", "【旁白】", "（音效）", "（旁白）"]
    for marker in audio_markers:
        if marker in full_text:
            return {"passed": False, "reason": f"Audio label contamination: {marker}"}

    return {"passed": True, "reason": ""}


# ============================================================================
# Unified OutputGuard (Content-Form-aware)
# ============================================================================
def output_guard_content_form_aware(
    content_form_key: str,
    episode: Any = None,  # Episode for audio_drama
    prose: ProseStory | None = None,  # ProseStory for prose_story
) -> dict[str, Any]:
    """根据 content_form 调用对应的 OutputGuard。

    audio_drama → 已有 episode-based guard
    prose_story → prose_output_guard
    """
    if content_form_key == "prose_story":
        return prose_output_guard(prose)
    # audio_drama — 委托给已有的 OutputGuard（在 contracts.py 中）
    if episode is None:
        return {"passed": False, "reason": "Episode is None for audio_drama"}
    return {"passed": True, "reason": ""}  # 由已有 guard 处理