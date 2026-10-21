from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .io import digest, file_hash, load, safe_config
import shutil


class VisualAdapter(Protocol):
    def feedback(self, script: dict, performance: dict, timeline: dict) -> dict: ...


def prepare(script: dict, performance: dict, config: dict):
    if not config.get("visual", {}).get("enabled"):
        return {"status": "not_required", "engine_validation": "not_required", "actions": {}}
    return {"status": "semantic_only", "engine_validation": "unverified", "actions": {}, "requests": performance["visual_semantics"], "source_version_id": script["source_version_id"], "script_sha256": digest(script)}


def obtain(script: dict, performance: dict, timeline: dict, config: dict, input_root: Path, adapter=None, production_root: Path | None = None):
    cfg = config.get("visual", {})
    if not cfg.get("enabled"):
        return prepare(script, performance, config)
    if adapter:
        data = adapter.feedback(script, performance, timeline)
        evidence = {"origin": "adapter_feedback"}
    elif cfg.get("feedback_file"):
        p = (input_root / cfg["feedback_file"]).resolve()
        data, evidence = load(p), {"origin": "feedback_file", "sha256": file_hash(p)}
    else:
        return prepare(script, performance, config)
    safe_config(data)
    if evidence["origin"] == "feedback_file" and production_root is not None:
        snapshot = production_root / "sources" / ("visual_" + evidence["sha256"] + ".json")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            shutil.copyfile(p, snapshot)
        if file_hash(snapshot) != evidence["sha256"]:
            raise ValueError("Visual source snapshot changed")
        evidence["snapshot_file"] = snapshot.relative_to(production_root).as_posix()
    if data.get("source_version_id") != script["source_version_id"] or data.get("script_sha256") != digest(script):
        raise ValueError("Visual feedback uses a different script version")
    if data.get("timeline_version") != timeline["timeline_version"]:
        raise ValueError("Visual feedback must reference the supplied draft timeline")
    ids = {e["block_id"] for e in script["events"] if e["kind"] == "action"}
    actions = {}
    for a in data.get("actions", []):
        if a["block_id"] not in ids or a["block_id"] in actions:
            raise ValueError("Unknown or duplicate visual action")
        basis = a.get("duration_basis")
        if basis not in {"measured", "external_locked"} or a.get("duration_ms", -1) <= 0:
            raise ValueError("Visual duration requires measured or sourced external_locked basis")
        if basis == "external_locked" and (not a.get("provenance", {}).get("version") or not a.get("provenance", {}).get("locked_by")):
            raise ValueError("External duration needs version and locking authority")
        points = []
        for p in a.get("keypoints", []):
            if "animation_frame" in p:
                fps = p.get("animation_fps", 0)
                if fps <= 0:
                    raise ValueError("Animation frame rate missing")
                ms = p["animation_frame"] * 1000 / fps
            elif "ms" in p:
                ms = p["ms"]
            else:
                ms = p["frame"] * 1000 / p["sample_rate"]
            if ms < 0 or ms >= a["duration_ms"]:
                raise ValueError("Visual keypoint outside action")
            points.append(dict(name=p["name"], frame=round(ms * timeline["sample_rate"] / 1000), sample_rate=timeline["sample_rate"], kind=p.get("kind", "action"), status=p.get("status", "candidate"), method=p.get("method", "engine_feedback"), original_coordinates=p))
        actions[a["block_id"]] = a | {"keypoints": points}
    external = any(a["duration_basis"] == "external_locked" for a in actions.values())
    executed = not external and data.get("engine_executed") is True and bool(data.get("engine_id")) and bool(data.get("execution_evidence")) and ids <= set(actions)
    return {"status": "external_locked_feedback" if external else "measured_feedback", "engine_validation": "reported_executed" if executed else "unverified", "actions": actions, "capability_gaps": data.get("capability_gaps", []), "evidence": evidence, "engine_id": data.get("engine_id"), "execution_evidence": data.get("execution_evidence"), "input_timeline_version": data["timeline_version"], "engine_final_timeline_version": data.get("engine_final_timeline_version", data["timeline_version"] if executed else None)}
