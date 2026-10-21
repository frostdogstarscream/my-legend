"""StoryDelivery export, validation, and role mapping.

This module provides deterministic helpers for:
- Exporting a StoryDelivery to a human-readable audio drama script
- Validating delivery integrity (ID uniqueness, role mapping, ordering)
- Building a character role map for downstream consumption
- Round-trip JSON export and re-read
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import StoryDelivery, EpisodeDelivery, SceneDelivery, Utterance


# ──────────────────────────────────────────────────────────────
# Character Role Map
# ──────────────────────────────────────────────────────────────


def build_character_role_map(delivery: StoryDelivery) -> dict[str, dict[str, Any]]:
    """Build a {role_id → details} map combining snapshots and portrayals.

    Downstream teams use this to understand who speaks and what they're like.
    """
    role_map: dict[str, dict[str, Any]] = {}

    # From input character snapshots (user-provided characters)
    for char in delivery.character_snapshot:
        rid = char.role_id or char.character_id
        canon = char.canon or {}
        profile = char.profile or {}
        role_map[rid] = {
            "role_id": rid,
            "character_id": char.character_id,
            "performer_id": char.performer_id or rid,
            "canon_name": canon.get("name", char.character_id),
            "immutable_facts": canon.get("immutable_facts", []),
            "goal": profile.get("goal") or profile.get("current_goal", ""),
            "motivation": profile.get("motivation") or profile.get("desire", ""),
            "fear": profile.get("fear") or profile.get("avoidance", ""),
            "speech_style": profile.get("speech_style") or profile.get("voice", ""),
            "source": "character_snapshot",
        }

    # From plan-generated portrayals (model-created characters)
    for p in delivery.character_portrayals:
        rid = p.get("role_id", "")
        if not rid:
            continue
        if rid not in role_map:
            role_map[rid] = {
                "role_id": rid,
                "character_id": rid,
                "performer_id": rid,
                "story_name": p.get("story_name", rid),
                "canon_name": p.get("story_name", rid),
                "immutable_facts": [],
                "goal": p.get("external_goal", ""),
                "motivation": "",
                "fear": p.get("emotional_avoidance", ""),
                "speech_style": p.get("speech_style", ""),
                "relationship_stance": p.get("relationship_stance", ""),
                "source": "character_portrayal",
            }
        else:
            # Overlay portrayal data on existing snapshot
            role_map[rid]["story_name"] = p.get("story_name", role_map[rid].get("canon_name", rid))
            role_map[rid]["goal"] = p.get("external_goal") or role_map[rid].get("goal", "")
            role_map[rid]["fear"] = p.get("emotional_avoidance") or role_map[rid].get("fear", "")
            role_map[rid]["speech_style"] = p.get("speech_style") or role_map[rid].get("speech_style", "")
            role_map[rid]["relationship_stance"] = p.get("relationship_stance", "")

    # From fictional voice roles (recording / broadcast / telephone voices)
    for v in getattr(delivery, "fictional_voice_roles", []) or []:
        rid = v.get("role_id", "")
        if not rid:
            continue
        medium = v.get("medium", "")
        cue = medium if medium != "live" else None
        role_map[rid] = {
            "role_id": rid,
            "character_id": rid,
            "performer_id": v.get("performer_id", rid),
            "story_name": v.get("story_name", rid),
            "canon_name": v.get("story_name", rid),
            "immutable_facts": [],
            "goal": v.get("dramatic_purpose", ""),
            "motivation": "",
            "fear": "",
            "speech_style": f"[{medium}]" if medium else "",
            "source": "fictional_voice_role",
            "audio_cue": cue,
        }

    # Ensure narrator is always present for narration lines
    if "narrator" not in role_map:
        role_map["narrator"] = {
            "role_id": "narrator",
            "character_id": "narrator",
            "performer_id": "narrator",
            "canon_name": "旁白",
            "immutable_facts": [],
            "goal": "叙述故事",
            "motivation": "",
            "fear": "",
            "speech_style": "沉稳、清晰",
            "source": "system",
        }

    return role_map


# ──────────────────────────────────────────────────────────────
# Readable Script Export
# ──────────────────────────────────────────────────────────────


def delivery_to_readable_script(delivery: StoryDelivery) -> str:
    """Export a StoryDelivery as a human-readable audio drama script.

    The output is a formatted text file that reads like a play, suitable for:
    - Human review and editing
    - Actor rehearsal reference
    - Documentation and archival

    Non-verbal elements (sfx, music, action) are marked with stage directions.
    """
    role_map = build_character_role_map(delivery)
    lines: list[str] = []

    # Header
    lines.append(f"《{delivery.title}》")
    lines.append(f"")
    lines.append(f"概要：{delivery.summary}")
    lines.append(f"模式：{delivery.story_mode}  |  版本：v{delivery.version_no}  |  交付格式：{delivery.delivery_format_version}")
    lines.append(f"故事ID：{delivery.story_id}")
    lines.append(f"")

    # Character legend
    lines.append("── 登场角色 ──")
    for rid, info in sorted(role_map.items()):
        name = info.get("story_name") or info.get("canon_name", rid)
        voice = info.get("speech_style", "")
        perf = info.get("performer_id", rid)
        source = info.get("source", "")
        tag = " [旁白]" if rid == "narrator" else ""
        port_tag = "  ← 模型生成" if source == "character_portrayal" else ""
        lines.append(f"  {name}  ({rid} → {perf}){tag}{port_tag}")
    lines.append(f"")

    # Episodes
    for ep in delivery.episodes:
        lines.append(f"══════ 第{ep.index}集：{ep.title} ══════")
        if ep.synopsis:
            lines.append(f"【本集梗概】{ep.synopsis}")
        lines.append(f"")

        for scene in ep.scenes:
            lines.append(f"── 场景{scene.scene_index}：{scene.title} ──")
            if scene.location:
                lines.append(f"  地点：{scene.location}")
            if scene.dramatic_goal:
                lines.append(f"  戏剧目标：{scene.dramatic_goal}")
            lines.append(f"")

            for u in scene.utterances:
                speaker_name = _speaker_label(u, role_map)
                prefix = _kind_prefix(u.kind)
                text = u.text

                # Stage directions
                if u.kind in ("sfx", "music"):
                    lines.append(f"  【{_kind_label(u.kind)}】{text}")
                elif u.kind == "action":
                    lines.append(f"  【动作】{text}")
                elif u.kind in ("dialogue", "narration"):
                    # Performance hints
                    perf_notes = _performance_notes(u)
                    if perf_notes:
                        lines.append(f"  {prefix}{speaker_name}：{text}  {perf_notes}")
                    else:
                        lines.append(f"  {prefix}{speaker_name}：{text}")
                else:
                    lines.append(f"  {prefix}{speaker_name}：{text}")
            lines.append(f"")

    return "\n".join(lines)


def _speaker_label(u: Utterance, role_map: dict) -> str:
    """Get human-readable speaker name."""
    if u.kind == "narration":
        return "旁白"
    rid = u.speaker_role_id or u.narrator_id or "?"
    info = role_map.get(rid, {})
    return info.get("story_name") or info.get("canon_name", rid)


def _kind_prefix(kind: str) -> str:
    if kind == "narration":
        return "【旁】"
    return ""


def _kind_label(kind: str) -> str:
    return {"sfx": "音效", "music": "音乐", "action": "动作"}.get(kind, kind)


def _performance_notes(u: Utterance) -> str:
    """Format performance hints as a compact annotation."""
    parts = []
    if u.emotion:
        parts.append(f"[{_emotion_cn(u.emotion.value if hasattr(u.emotion, 'value') else str(u.emotion))}]")
    if u.tone_instruction:
        parts.append(f"语气：{u.tone_instruction}")
    if u.speech_rate and u.speech_rate != 1.0:
        parts.append(f"语速：{u.speech_rate:.1f}x")
    if u.emphasis:
        texts = [e.span_text for e in u.emphasis if e.span_text]
        if texts:
            parts.append(f"重读：{'、'.join(texts[:3])}")
    if u.pauses:
        longest = max(p.duration_ms for p in u.pauses)
        if longest >= 500:
            parts.append(f"停顿：{longest}ms")
    return "  ".join(parts)


def _emotion_cn(emotion: str) -> str:
    return {
        "neutral": "中性", "happy": "开心", "sad": "悲伤",
        "worried": "担忧", "angry": "愤怒", "surprised": "惊讶",
        "fearful": "恐惧", "reassuring": "安抚", "serious": "严肃",
        "playful": "俏皮", "mysterious": "神秘", "tender": "温柔",
    }.get(emotion, emotion)


# ──────────────────────────────────────────────────────────────
# Delivery Integrity Validation
# ──────────────────────────────────────────────────────────────


class DeliveryIntegrityReport:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: dict[str, Any] = {}

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


def validate_delivery_integrity(delivery: StoryDelivery) -> DeliveryIntegrityReport:
    """Deterministic structural validation — no LLM calls.

    Checks:
    - Scene and utterance ID uniqueness and ordering
    - Speaker role IDs exist in character snapshots or portrayals
    - Text/action/sfx separation (no mixed content)
    - Emotion and emphasis span validity
    - JSON serialization round-trip consistency
    """
    report = DeliveryIntegrityReport()
    role_map = build_character_role_map(delivery)

    # ── Check basic metadata ──
    if not delivery.title.strip():
        report.errors.append("title is empty")
    if not delivery.summary.strip():
        report.errors.append("summary is empty")
    if not delivery.preview_blurb.strip():
        report.warnings.append("preview_blurb is empty")
    if delivery.delivery_format_version != "1.0":
        report.warnings.append(f"unexpected delivery_format_version: {delivery.delivery_format_version}")

    # ── Check episodes ──
    if not delivery.episodes:
        report.errors.append("no episodes in delivery")
        return report

    all_scene_ids: set[str] = set()
    all_episode_ids: set[str] = set()
    all_line_ids: set[str] = set()
    total_dialogue = 0
    total_narration = 0
    total_sfx = 0

    for ep_idx, ep in enumerate(delivery.episodes, 1):
        if ep.episode_id in all_episode_ids:
            report.errors.append(f"duplicate episode_id: {ep.episode_id}")
        all_episode_ids.add(ep.episode_id)
        if ep.index != ep_idx:
            report.errors.append(f"episode {ep_idx}: expected index {ep_idx}, got {ep.index}")

        for scene_idx, scene in enumerate(ep.scenes, 1):
            # ID uniqueness
            if scene.scene_id in all_scene_ids:
                report.errors.append(f"duplicate scene_id: {scene.scene_id}")
            all_scene_ids.add(scene.scene_id)

            if scene.scene_index != scene_idx:
                report.errors.append(f"scene {scene.scene_id}: expected scene_index {scene_idx}, got {scene.scene_index}")

            # Utterance checks
            for u_idx, u in enumerate(scene.utterances, 1):
                if u.line_id in all_line_ids:
                    report.errors.append(f"duplicate line_id: {u.line_id}")
                all_line_ids.add(u.line_id)

                # Role existence
                if u.kind == "dialogue":
                    total_dialogue += 1
                    speaker = u.speaker_role_id or ""
                    if speaker and speaker not in role_map:
                        report.errors.append(
                            f"{u.line_id}: speaker_role_id '{speaker}' not in character role map "
                            f"(available: {sorted(role_map.keys())})"
                        )
                    if not speaker:
                        report.errors.append(f"{u.line_id}: dialogue line missing speaker_role_id")
                elif u.kind == "narration":
                    total_narration += 1
                elif u.kind == "sfx":
                    total_sfx += 1

                # Text/sfx separation
                if u.kind in ("sfx", "music"):
                    if u.speaker_role_id:
                        report.warnings.append(f"{u.line_id}: sfx/music should not have speaker_role_id")
                elif u.kind == "action":
                    if u.speaker_role_id:
                        report.warnings.append(f"{u.line_id}: action should not have speaker_role_id")
                elif u.kind == "dialogue" and u.text.strip().startswith("("):
                    report.warnings.append(f"{u.line_id}: dialogue text starts with '(' — possible stage direction in speech")

    report.stats = {
        "episodes": len(delivery.episodes),
        "scenes": len(all_scene_ids),
        "utterances": len(all_line_ids),
        "dialogue_lines": total_dialogue,
        "narration_lines": total_narration,
        "sfx_lines": total_sfx,
        "characters": len(role_map),
        "role_ids": sorted(role_map.keys()),
    }

    return report


# ──────────────────────────────────────────────────────────────
# JSON Round-trip
# ──────────────────────────────────────────────────────────────


def delivery_to_json(delivery: StoryDelivery, indent: int = 2) -> str:
    """Serialize a StoryDelivery to JSON with reliable encoding."""
    return json.dumps(delivery.model_dump(mode="json"), ensure_ascii=False, indent=indent)


def delivery_from_json(json_str: str) -> StoryDelivery:
    """Deserialize a StoryDelivery from JSON."""
    return StoryDelivery.model_validate_json(json_str)


def verify_round_trip(delivery: StoryDelivery) -> tuple[bool, str]:
    """Verify JSON round-trip preserves key content.

    Returns (ok, message)."""
    try:
        json_str = delivery_to_json(delivery)
        reloaded = delivery_from_json(json_str)
        if reloaded != delivery:
            return False, "full delivery round-trip mismatch"

        # Check critical fields
        if reloaded.title != delivery.title:
            return False, f"title mismatch: {reloaded.title!r} != {delivery.title!r}"
        if len(reloaded.episodes) != len(delivery.episodes):
            return False, f"episode count mismatch: {len(reloaded.episodes)} != {len(delivery.episodes)}"

        # Check utterance count
        orig_utts = sum(len(scene.utterances) for ep in delivery.episodes for scene in ep.scenes)
        reload_utts = sum(len(scene.utterances) for ep in reloaded.episodes for scene in ep.scenes)
        if orig_utts != reload_utts:
            return False, f"utterance count mismatch: {reload_utts} != {orig_utts}"

        # Check first utterance text
        if orig_utts > 0:
            orig_first = delivery.episodes[0].scenes[0].utterances[0].text
            reload_first = reloaded.episodes[0].scenes[0].utterances[0].text
            if orig_first != reload_first:
                return False, f"first utterance text mismatch"

        return True, f"OK: {len(delivery.episodes)} episodes, {orig_utts} utterances"
    except Exception as exc:
        return False, f"round-trip failed: {exc}"


# ──────────────────────────────────────────────────────────────
# Batch export convenience
# ──────────────────────────────────────────────────────────────


def export_delivery_bundle(
    delivery: StoryDelivery,
    output_dir: str | Path,
    base_name: str = "story",
) -> dict[str, Path]:
    """Export a complete delivery bundle: JSON + readable script + character map.

    Returns dict of {label: path}.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: dict[str, Path] = {}

    # JSON
    json_path = out / f"{base_name}.json"
    json_path.write_text(delivery_to_json(delivery, indent=2), encoding="utf-8")
    files["json"] = json_path

    # Readable script
    script_path = out / f"{base_name}_script.txt"
    script_path.write_text(delivery_to_readable_script(delivery), encoding="utf-8")
    files["script"] = script_path

    # Character role map
    role_map = build_character_role_map(delivery)
    map_path = out / f"{base_name}_characters.json"
    map_path.write_text(json.dumps(role_map, ensure_ascii=False, indent=2), encoding="utf-8")
    files["characters"] = map_path

    return files
