from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .contracts import StandardScript
from .io import digest, file_hash, load


def import_handoff(data: dict):
    """Preserve source identities and unresolved issues; do not infer hidden facts."""
    tts = {row["block_id"]: row for row in data.get("tts_production", {}).get("lines", [])}
    sfx = {row["block_id"]: row for row in data.get("sfx_production", {}).get("items", [])}
    events, assumptions = [], []
    for row in data["blocks"]:
        kind = {"dialogue": "speech"}.get(row["type"], row["type"])
        if kind not in {"speech", "sfx", "pause", "action", "ambience", "music", "transition"}:
            raise ValueError(f"{row['block_id']}: unsupported source event kind")
        e = dict(block_id=row["block_id"], scene_id=row["scene_id"], kind=kind,
                 text=row.get("text", ""), role_id=row.get("role_id"), target=row.get("target_object_ref"),
                 source_ref=row.get("source_ref", {}), lineage=[row["block_id"]])
        if kind == "speech":
            line = tts.get(row["block_id"], {})
            e["text"] = line.get("spoken_text", row.get("text", ""))
            e["performance"] = dict(source_annotation=row.get("original_annotation", ""), **line.get("performance", {}))
            if line.get("instructions_candidate"):
                e["performance"]["instructions"] = line["instructions_candidate"]
        elif kind == "sfx":
            cue = sfx.get(row["block_id"], {})
            e["sound"] = dict(description=row.get("text", ""), constraints=row.get("sound_constraints", {}))
            if cue.get("text_prompt_candidate"):
                e["sound"]["prompt"] = cue["text_prompt_candidate"]
        elif kind == "pause":
            e["duration_ms"] = row.get("requested_duration_ms")
            if not row.get("pause_mode"):
                assumptions.append(dict(block_id=row["block_id"], severity="warning", code="pause_interpretation", message="Draft treats the pause as a separate interval; source interpretation remains open"))
        elif kind == "action":
            e["performance"] = {"semantic_kind": row.get("semantic_kind")}
            if row.get("anchor_dialogue_block_id"):
                e["timing"] = [dict(anchor=row["anchor_dialogue_block_id"], anchor_point="start", provenance="draft_action_anchor")]
            assumptions.append(dict(block_id=row["block_id"], severity="warning", code="action_duration_unmeasured", message="Action has only semantic data; no engine timing measured"))
        events.append(e)
    return StandardScript(source_version_id=data["source_version_id"], artifact_revision=data["artifact_revision"],
                          title=data["title"], roles=[dict(role_id=r["role_id"], name=r["name"], description=r.get("description", "")) for r in data["characters"]],
                          scenes=[dict(scene_id=s["scene_id"], description=s.get("description", s.get("location", ""))) for s in data["scenes"]],
                          events=events, source={"original_schema": data["schema_version"], "original_acceptance": data.get("acceptance", {}), "original_scope": data.get("scope", {})},
                          boundaries=data.get("constraints", {}), issues=data.get("issues", []) + assumptions).model_dump()


def normalize(data: dict, upstream=None):
    if data.get("schema_version", "").startswith("script-production-handoff/"):
        result = import_handoff(data)
    elif data.get("schema_version") == "lingban.script/1":
        result = StandardScript.model_validate(data).model_dump()
    elif data.get("input_kind") in {"idea", "story"}:
        if upstream is None:
            raise ValueError("Idea/story input requires the configured upstream script adapter or an already generated script")
        return normalize(upstream.script(data))
    else:
        raise ValueError("Unsupported input contract")
    problems = []
    for e in result["events"]:
        if e["kind"] == "speech" and (not e["role_id"] or not e["text"].strip()):
            problems.append(dict(block_id=e["block_id"], code="missing_speaker_or_text", severity="blocking"))
        if e["kind"] == "action" and e["performance"].get("requires_target") and not e["target"]:
            problems.append(dict(block_id=e["block_id"], code="missing_action_target", severity="blocking"))
    problems.extend(i for i in result["issues"] if i.get("severity") == "blocking" and i.get("status") != "resolved")
    return result, problems


def snapshot_source(path: Path):
    data = load(path)
    return data, {"file_sha256": file_hash(path), "schema": data.get("schema_version"), "file_name": path.name}


def revise(script: dict, config: dict, changes: dict, revision: int):
    """Derived versions, stable event IDs and an explicit dependency impact report."""
    if set(changes) - {"events", "config"}:
        raise ValueError("Unsupported changes object; use changes.events and changes.config")
    out, cfg = deepcopy(script), deepcopy(config)
    events = {e["block_id"]: e for e in out["events"]}
    impact = {"events": [], "stages": [], "reasons": [], "parent_script_sha256": digest(script)}
    allowed = {"text", "performance", "sound", "media", "timing", "duration_ms", "keypoints", "target", "role_id"}
    content_changed = False
    for patch in changes.get("events", []):
        event_id = patch["block_id"]
        if event_id not in events or set(patch) - allowed - {"block_id"}:
            raise ValueError("Unknown event or unsupported patch field")
        patch = deepcopy(patch)
        if any(key in patch and patch[key] != events[event_id].get(key) for key in ("text", "role_id")) and "performance" not in patch:
            patch["performance"] = deepcopy(events[event_id]["performance"])
            patch["performance"].pop("instructions", None)
            patch["performance"].pop("instruction_origin", None)
        if "performance" in patch:
            old, new = events[event_id]["performance"], patch["performance"]
            fields = {"intent", "entry", "turn", "release", "close", "intensity", "emphasis", "pauses", "delivery", "emotion_arc", "stress"}
            source_changed = any(key in patch and patch[key] != events[event_id].get(key) for key in ("text", "role_id"))
            if (source_changed or any(old.get(key) != new.get(key) for key in fields)) and new.get("instructions") == old.get("instructions"):
                new.pop("instructions", None)
                new.pop("instruction_origin", None)
        changed = {k: v for k, v in patch.items() if k != "block_id" and events[event_id].get(k) != v}
        events[event_id].update(changed)
        if not changed:
            continue
        impact["events"].append(event_id)
        if set(changed) & {"text", "target", "role_id"}:
            content_changed = True
        stages = {"timeline", "mix", "review"}
        if set(changed) & {"text", "role_id", "performance", "sound", "media", "keypoints"}:
            stages |= {"performance", "media"}
        impact["stages"].extend(stages)
        impact["reasons"].append({"block_id": event_id, "fields": sorted(changed)})
    for key, value in changes.get("config", {}).items():
        if key not in {"mix", "processing", "project", "scene_defaults", "scene_overrides", "role_voices", "tts", "sfx", "visual", "gap_ms", "budget_ms", "sample_rate", "channels", "text_model", "instruction_policy", "call_budget", "processing_profiles", "tail_ms"}:
            raise ValueError("Unsupported configuration patch")
        if cfg.get(key) != value:
            cfg[key] = value
            impact["stages"].extend(["mix", "review"] if key == "mix" else ["performance", "media", "timeline", "mix", "review"])
    if content_changed:
        out["source_version_id"] = script["source_version_id"] + "." + digest(out["events"])[:10]
    out["artifact_revision"] = f"r{revision}"
    out["source"] = script.get("source", {}) | {"parent_source_version_id": script["source_version_id"], "parent_script_sha256": digest(script)}
    StandardScript.model_validate(out)
    impact["stages"] = sorted(set(impact["stages"]))
    impact["reuse_timeline_positions"] = bool(impact["stages"]) and set(impact["stages"]) <= {"mix", "review"}
    return out, cfg, impact
