"""Audio Drama form handler — adapter for legacy routing."""
from __future__ import annotations


class AudioDramaHandler:
    key: str = "audio_drama"

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        return _default_writer_prompt(plan, characters, "audio drama script")

    def validators(self) -> list:
        return []  # handled by existing graph validators

    def rule_packs(self) -> list[str]:
        return ["audio_story_constraints"]

    def package(self, raw_output: str, plan: dict) -> dict:
        return {"audio_drama": raw_output, "prose": ""}


class ProseStoryHandler:
    key: str = "prose_story"

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        return _default_writer_prompt(plan, characters, "prose story")

    def validators(self) -> list:
        return []

    def rule_packs(self) -> list[str]:
        return ["story_progression"]

    def package(self, raw_output: str, plan: dict) -> dict:
        return {"prose": raw_output, "audio_drama": ""}


class NovelHandler:
    key: str = "novel"

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        return _default_writer_prompt(plan, characters, "novel-style narrative with paragraphs, scenes, and embedded dialogue")

    def validators(self) -> list:
        return [NovelValidator()]

    def rule_packs(self) -> list[str]:
        return ["novel_adaptation", "story_progression"]

    def package(self, raw_output: str, plan: dict) -> dict:
        return {"novel": raw_output, "prose": raw_output}


class StorytellingHandler:
    key: str = "storytelling"

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        return _default_writer_prompt(plan, characters, "oral storytelling style, narrator-led with dialogue inserts and pacing beats")

    def validators(self) -> list:
        return [StorytellingValidator()]

    def rule_packs(self) -> list[str]:
        return ["storytelling_oral"]

    def package(self, raw_output: str, plan: dict) -> dict:
        return {"storytelling": raw_output, "prose": raw_output}


class StandupHandler:
    key: str = "standup"

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        return _default_writer_prompt(plan, characters, "standup comedy performance: single performer, setup-punchline-callback structure")

    def validators(self) -> list:
        return [StandupValidator()]

    def rule_packs(self) -> list[str]:
        return ["standup_performance"]

    def package(self, raw_output: str, plan: dict) -> dict:
        return {"standup": raw_output, "prose": raw_output}


class CrosstalkHandler:
    key: str = "crosstalk"

    def writer_prompt(self, plan: dict, characters: list, mode_key: str) -> str:
        return _default_writer_prompt(plan, characters, "crosstalk comedy dialogue: two performers (dougen/penggen), exchange-based structure")

    def validators(self) -> list:
        return [CrosstalkValidator()]

    def rule_packs(self) -> list[str]:
        return ["crosstalk_dialogue"]

    def package(self, raw_output: str, plan: dict) -> dict:
        return {"crosstalk": raw_output, "prose": raw_output}


# ═══════════════════════════════════════════════════════════════════
# Validators
# ═══════════════════════════════════════════════════════════════════
class NovelValidator:
    def validate(self, output: str) -> tuple[bool, str]:
        if not output or not output.strip():
            return False, "novel: empty output"
        if len(output.strip()) < 50:
            return False, "novel: output too short"
        return True, ""


class StorytellingValidator:
    VALID_SEGMENT_KINDS = {"narration", "dialogue", "quote", "pacing_beat"}

    def validate(self, output: str) -> tuple[bool, str]:
        if not output or not output.strip():
            return False, "storytelling: empty output"
        if len(output.strip()) < 30:
            return False, "storytelling: output too short"
        # Must have a narrator-led structure — cannot be pure multi-speaker drama
        lines = output.strip().split("\n")
        has_narration = False
        for line in lines[:20]:
            line = line.strip()
            if not line:
                continue
            # Pure audio drama pattern: "角色A：" — check if ALL content lines are speaker-prefixed
            if line and not line.startswith("角色") and not line.startswith("说"):
                has_narration = True
                break
        if not has_narration:
            # May still be valid with explicit narrator — check for keyword
            text_lower = output.lower()
            if "narrator" not in text_lower and "讲述" not in text_lower:
                if _all_lines_are_speaker_format(lines):
                    return False, "storytelling: pure multi-speaker drama — needs narrator-led structure"
        return True, ""


class StandupValidator:
    VALID_SEGMENT_KINDS = {"setup", "punchline", "callback", "transition"}

    def validate(self, output: str) -> tuple[bool, str]:
        if not output or not output.strip():
            return False, "standup: empty output"
        if len(output.strip()) < 20:
            return False, "standup: output too short"
        # Must be single-performer — reject multi-speaker dialogue patterns
        speaker_count = self._count_speakers(output)
        if speaker_count > 1:
            return False, f"standup: requires single performer, detected {speaker_count} speakers"
        # Must have at least one setup and one punchline
        text_lower = output.lower()
        has_setup = "setup" in text_lower or "铺垫" in output or "开场" in output
        has_punchline = "punchline" in text_lower or "笑点" in output or "包袱" in output or "段子" in output
        if not has_setup:
            return False, "standup: no setup segment found"
        if not has_punchline:
            return False, "standup: no punchline segment found"
        # Setup must appear before punchline
        setup_pos = _find_first(output, ["setup", "铺垫", "开场"])
        punch_pos = _find_first(output, ["punchline", "笑点", "包袱", "段子"])
        if setup_pos >= 0 and punch_pos >= 0 and setup_pos > punch_pos:
            return False, "standup: setup must appear before punchline"
        return True, ""

    def _count_speakers(self, text: str) -> int:
        """Count distinct speaker prefixes (甲：, 乙：, etc.)."""
        lines = text.strip().split("\n")
        speakers = set()
        for line in lines:
            line = line.strip()
            if "：" in line or ":" in line:
                prefix = line.split("：")[0].split(":")[0].strip()
                if prefix and len(prefix) <= 4:
                    speakers.add(prefix)
        return len(speakers)


class CrosstalkValidator:
    VALID_ROLES = {"dougen", "penggen", "逗哏", "捧哏", "甲", "乙", "p1", "p2"}

    def validate(self, output: str) -> tuple[bool, str]:
        if not output or not output.strip():
            return False, "crosstalk: empty output"
        if len(output.strip()) < 20:
            return False, "crosstalk: output too short"
        # Detect declared performer roles
        text_lower = output.lower()
        found_roles = {r for r in self.VALID_ROLES if r.lower() in text_lower}
        dougen_present = any(r in found_roles for r in {"dougen", "逗哏", "p1", "甲"})
        penggen_present = any(r in found_roles for r in {"penggen", "捧哏", "p2", "乙"})
        if not dougen_present or not penggen_present:
            return False, "crosstalk: requires two performer roles (dougen/penggen or equivalent)"
        # Both performers must have dialogue
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        speaker_lines = [l for l in lines if "：" in l or ":" in l]
        if len(speaker_lines) < 2:
            return False, "crosstalk: both performers must have dialogue"
        # Explicitly check for undeclared speakers
        for line in speaker_lines:
            prefix = line.split("：")[0].split(":")[0].strip().lower()
            if prefix and len(prefix) <= 10:
                if prefix not in self.VALID_ROLES:
                    return False, f"crosstalk: unknown exchange speaker '{prefix}'"
        return True, ""


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════
def _all_lines_are_speaker_format(lines: list[str]) -> bool:
    """Check if text is pure multi-speaker drama format (角色A：...)."""
    content_lines = [l.strip() for l in lines if l.strip() and len(l.strip()) > 3]
    if len(content_lines) < 3:
        return False
    speaker_count = 0
    for line in content_lines:
        if "：" in line or ":" in line:
            prefix = line.split("：")[0].split(":")[0].strip()
            # Short prefix (≤10 chars) looks like a speaker name
            if len(prefix) <= 10:
                speaker_count += 1
    return speaker_count >= len(content_lines) * 0.6


def _find_first(text: str, keywords: list[str]) -> int:
    """Find first occurrence position of any keyword; -1 if none."""
    for kw in keywords:
        pos = text.lower().find(kw.lower())
        if pos >= 0:
            return pos
    return -1


# ═══════════════════════════════════════════════════════════════════
# Default prompt builder
# ═══════════════════════════════════════════════════════════════════
def _default_writer_prompt(plan: dict, characters: list, style: str) -> str:
    char_text = ", ".join(c.get("name", c.get("canon", {}).get("name", "?")) for c in characters[:3])
    premise = plan.get("premise", plan.get("central_question", ""))
    return f"Write a {style} about {char_text}. Premise: {premise}"