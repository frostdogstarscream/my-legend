"""Audit a generated draft without invoking any model or accepting the performance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

from lingban_pipeline.io import load, save, under
from verify_delivery import verify


def audit(root: Path, script_path: Path, preview_url: str | None, revision: int = 1):
    root = root.resolve()
    basic = verify(root, preview_url, revision)
    basic.pop("new_provider_requests", None)
    directory = root / "drafts" / f"r{revision}"
    source = load(script_path)
    exported = load(directory / "script.json")
    performance = load(directory / "performance.json")
    assets = load(directory / "assets.json")
    prompts = load(directory / "prompts.json")
    timeline = load(directory / "timeline.json")
    source_events = {e["block_id"]: e for e in source["events"]}
    exported_events = {e["block_id"]: e for e in exported["events"]}
    assert set(source_events) == set(exported_events)
    assert all(e["text"] == exported_events[key]["text"] for key, e in source_events.items())
    placements = {p["block_id"]: p for p in timeline["placements"]}
    rate = timeline["sample_rate"]

    def point(block_id, name):
        placement = placements[block_id]
        if name == "start":
            return placement["start_frame"]
        if name == "end":
            return placement["end_frame"]
        marker = next(p for p in assets[block_id]["keypoints"] if p["name"] == name)
        return placement["start_frame"] + round(marker["frame"] * rate / marker["sample_rate"])

    relationships = []
    for event in source["events"]:
        for relation in event["timing"]:
            target = (point(relation["anchor"], relation["anchor_point"]) if relation["anchor"] else 0) + round(relation["offset_ms"] * rate / 1000)
            actual = point(event["block_id"], relation["self_point"])
            passed = actual >= target if relation["mode"] == "after" else actual == target
            assert passed, event["block_id"]
            relationships.append({"block_id": event["block_id"], "anchor": relation["anchor"], "anchor_point": relation["anchor_point"], "self_point": relation["self_point"], "target_frame": target, "actual_frame": actual, "mode": relation["mode"], "passed": passed})

    generation_rows = []
    for prompt in prompts:
        event = source_events[prompt["block_id"]]
        record = prompt["actual_generation"]
        params = record.get("actual_parameters", {})
        if event["kind"] == "speech":
            assert prompt["text"] == event["text"] == params.get("text")
            assert params.get("instruction") == prompt["instructions"]
            assert params.get("voice") == prompt["voice"]
        elif record.get("origin") == "generated":
            assert params.get("text_prompt") == prompt["prompt"]
        generation_rows.append({"block_id": prompt["block_id"], "kind": prompt["kind"], "model": record.get("model"), "voice": record.get("voice"), "origin": record.get("origin"), "request_id": record.get("request_id"), "usage": record.get("usage"), "raw_frames": assets[prompt["block_id"]]["raw"]["frames"], "raw_sample_rate": assets[prompt["block_id"]]["raw"]["sample_rate"], "processed_frames": assets[prompt["block_id"]]["processed"]["frames"], "processed_sample_rate": assets[prompt["block_id"]]["processed"]["sample_rate"]})

    with sqlite3.connect(root / "tasks.sqlite") as db:
        rows = db.execute("SELECT id,status,attempts,error,result FROM tasks ORDER BY id").fetchall()
    ledger = [{"task_id": row[0], "status": row[1], "attempts": row[2], "error": row[3]} for row in rows]
    generated = [row for row in generation_rows if row["origin"] == "generated"]
    audio_attempts = sum(row[2] for row in rows if row[0].startswith("raw_"))
    unknown_audio_tasks = [row[0] for row in rows if row[0].startswith("raw_") and row[1] in {"unknown", "running"}]
    text_calls = 1 + performance["generation_record"].get("correction_rounds", 0) if performance["generation_record"].get("model_called") else 0
    diagnostic_calls = 0
    recorded_failed_text_calls = 0
    unrecorded_call_tasks = []
    for task_id, status, attempts, error, result in rows:
        if task_id.startswith("probe_") and status == "success":
            saved = load(under(root, json.loads(result)["file"]))
            record = saved.get("generation_record", saved)
            diagnostic_calls += len(record.get("request_history", [])) or (1 + record.get("correction_rounds", 0))
        elif task_id.startswith("text_") and status != "success":
            details = json.loads(error) if error and error.startswith("{") else {}
            # A ledger attempt can contain multiple JSON correction requests.
            # Historic failures without request history do not establish a count.
            if not details.get("request_history"):
                unrecorded_call_tasks.append(task_id)
            else:
                recorded_failed_text_calls += len(details["request_history"])
    leaks = []
    pattern = re.compile(rb"sk-ws-[A-Za-z0-9_.-]{20,}|\bsk-[0-9a-f]{32}\b")
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".csv", ".html", ".vtt", ".sqlite", ".log"} and pattern.search(path.read_bytes()):
            leaks.append(path.relative_to(root).as_posix())
    assert not leaks, "Credential-like value detected; withhold delivery"
    return basic | {"revision": revision, "verification_provider_calls": 0, "source_text_unchanged": True, "all_relative_time_relations_passed": True, "relation_checks": relationships, "successful_audio_generations": len(generated), "audio_generation_attempts": audio_attempts, "unknown_historic_audio_tasks": unknown_audio_tasks, "text_model_responses": text_calls, "diagnostic_probe_responses": diagnostic_calls, "recorded_failed_text_responses": recorded_failed_text_calls, "minimum_confirmed_model_responses": len(generated) + text_calls + diagnostic_calls + recorded_failed_text_calls, "unrecorded_call_count_tasks": unrecorded_call_tasks, "total_provider_call_count": "not_fully_recorded" if unrecorded_call_tasks else "minimum_confirmed_model_responses", "generation_records": generation_rows, "task_ledger": ledger, "credential_pattern_leaks": leaks, "internal_contact_sync": "not_verified_unannotated", "publication": "not_approved"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--url")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.root, args.input, args.url, args.revision)
    save(args.output, result)
    print(json.dumps({key: result[key] for key in ("bundle_verified", "technical_passed", "source_text_unchanged", "all_relative_time_relations_passed", "duration_ms", "successful_audio_generations", "text_model_responses", "minimum_confirmed_model_responses", "human_acceptance", "internal_contact_sync")}, ensure_ascii=False))
