"""Import an existing media package as a fixture, without provider calls or source edits."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lingban_pipeline.content import import_handoff
from lingban_pipeline.io import file_hash, load, save


def build(package: Path, output: Path):
    package = package.resolve()
    source = load(package / "source_snapshot.json")
    script = import_handoff(source)
    requests, manifest, timeline = load(package / "requests.json"), load(package / "asset_manifest.json"), load(package / "timeline.json")
    assets = {a["id"]: a for a in manifest["assets"]}
    placements = [p for p in timeline["placements"] if p.get("file")]
    by_block = {e["block_id"]: e for e in script["events"]}
    sfx_requests = {s["id"]: s for s in requests["sfx"]}
    # Current sample uses one sound per event; a multi-sound block would need split event IDs.
    for p in placements:
        e = by_block[p["block_id"]]
        if p["kind"] == "sfx" and e["kind"] == "action":
            original = copy.deepcopy(e)
            original["block_id"] += ".visual"
            original["timing"] = [dict(anchor=e["block_id"], anchor_point="start", provenance="fixture_preserved_visual_semantic")]
            script["events"].append(original)
            script["id_mapping"][e["block_id"]] = [e["block_id"], original["block_id"]]
            e["kind"] = "sfx"
            e["lineage"] = [p["block_id"]]
        # Reuse processed media intentionally to compare timing with the old mix; upstream raw hash retained.
        asset = assets[p["id"]]
        path = package / asset["file"]
        e["media"] = {"strategy": "library", "file": str(path), "upstream_raw_sha256": asset.get("raw_sha256"), "upstream_processed_sha256": file_hash(path)}
        e["timing"] = [dict(anchor=None, offset_ms=p["start_frame"] * 1000 / timeline["sample_rate"], provenance="fixture_existing_timeline")]
        e["keypoints"] = []
        if p["kind"] == "sfx":
            e["sound"]["prompt"] = sfx_requests[p["id"]]["text_prompt"]
            candidate = asset.get("keypoint_candidates", {})
            if candidate.get("contact_frame") is not None:
                e["keypoints"] = [dict(name="contact", kind="contact", frame=candidate["contact_frame"], sample_rate=timeline["sample_rate"], method="upstream_energy_candidate", status="candidate")]
                visual_event = next((x for x in script["events"] if x["block_id"] == e["block_id"] + ".visual"), None)
                if visual_event:
                    visual_event["timing"][0]["anchor_point"] = "contact"
    for e in script["events"]:
        if e["kind"] == "action" and e.get("timing"):
            # The sample still has target-only actions, no fake engine execution data.
            pass
        elif e["kind"] == "pause":
            old = next((p for p in timeline["placements"] if p["kind"] == "pause" and p.get("block_id") == e["block_id"]), None)
            if old:
                e["timing"] = [dict(anchor=None, offset_ms=old["start_frame"] * 1000 / timeline["sample_rate"], provenance="fixture_existing_pause")]
    original_pauses = [e for e in script["events"] if e["kind"] == "pause"]
    used = set()
    for p in timeline["placements"]:
        if p["kind"] != "pause":
            continue
        duration_ms = (p["end_frame"] - p["start_frame"]) * 1000 / timeline["sample_rate"]
        e = next((x for x in original_pauses if x["block_id"] not in used and x["duration_ms"] == duration_ms), None)
        if e is None:
            e = dict(block_id="fixture." + p["id"], scene_id=script["scenes"][0]["scene_id"], kind="pause", text="既有混音留白", duration_ms=duration_ms, lineage=[])
            script["events"].append(e)
        used.add(e["block_id"])
        e["timing"] = [dict(anchor=None, offset_ms=p["start_frame"] * 1000 / timeline["sample_rate"], provenance="fixture_existing_pause")]
    last = max(p["end_frame"] for p in timeline["placements"])
    if timeline["total_frames"] > last:
        script["events"].append(dict(block_id="fixture.tail", scene_id=script["scenes"][0]["scene_id"], kind="transition", text="既有成品尾部留白", duration_ms=(timeline["total_frames"] - last) * 1000 / timeline["sample_rate"], timing=[dict(anchor=None, offset_ms=last * 1000 / timeline["sample_rate"], provenance="fixture_existing_tail")]))
    script["artifact_revision"] += ".compat-import"
    script["source"]["fixture"] = {"source_snapshot_sha256": file_hash(package / "source_snapshot.json"), "original_timeline_version": timeline["timeline_version"], "purpose": "compatibility_import_and_listening_only", "changes": "Media reuse, timeline intervals and derived visual-only event IDs; source dialogue unchanged"}
    config = load(Path(__file__).resolve().parents[1] / "config.example.json")
    config.update(gap_ms=0, budget_ms=60000, context_window=0)
    config["mix"].update(kind_gains_db={"sfx": -12}, peak_ceiling=.95)
    config["role_voices"] = {r["role_id"]: next((t["voice"] for t in requests["tts"] if t["role_id"] == r["role_id"]), None) for r in script["roles"]}
    output.mkdir(parents=True, exist_ok=True)
    save(output / "script.json", script)
    save(output / "config.json", config)
    return {"input": str(output / "script.json"), "config": str(output / "config.json"), "paid_calls": 0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--media-package", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.media_package, args.output), ensure_ascii=False))
