"""Recheck saved HTTP/model evidence without changing text or calling a model."""

import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def lines(delivery):
    return {
        (episode["episode_id"], scene["scene_id"], line["line_id"]): line
        for episode in delivery["episodes"]
        for scene in episode["scenes"]
        for line in scene["utterances"]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory
    summary = load(root / "summary.json")
    assert len(summary["results"]) == 4 and all(
        r["status"] == "passed" for r in summary["results"]
    )
    assert not (root / "run_error.json").exists()
    deliveries = {
        operation: load(root / operation / "delivery.json")["response"]
        for operation in ("create", "revise", "continue", "remix")
    }
    written = set()
    for file in (root / "model_calls").rglob("call_*.json"):
        capture = load(file)
        if (
            capture["status"] == "completed"
            and capture["role"] == "storycraft_episode_writer"
        ):
            raw = capture["response_text"]
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            written.update(
                o["text"].strip()
                for o in objects(data)
                if isinstance(o.get("text"), str)
            )
    report = {"delivery_sha256": {}, "operations": {}, "checks": {}}
    for operation, delivery in deliveries.items():
        utterances = lines(delivery)
        manifest = load(root / operation / "render.json")["response"]
        assert (
            delivery["status"] == "READY"
            and delivery["quality_report"]["validation_status"] == "PASSED"
        )
        assert all(line["text"].strip() in written for line in utterances.values()), (
            "TEXT_WITHOUT_RAW_MODEL_PROVENANCE"
        )
        assert len(manifest["tasks"]) == len(utterances)
        for task in manifest["tasks"]:
            original = utterances[
                (task["episode_id"], task["scene_id"], task["line_id"])
            ]
            assert (
                task["text"] == original["text"]
                and task["performer_id"] == original["performer_id"]
            )
            assert task["source_kind"] == original["kind"]
            assert all(
                value == original[key] for key, value in task["performance"].items()
            )
        checks = delivery["quality_report"]["operation_checks"]
        texts = {line["line_id"]: line["text"] for line in utterances.values()}
        for check in checks:
            assert check["passed"] and check["evidence_quotes"]
            for quote, line_id in zip(
                check["evidence_quotes"], check["evidence_line_ids"]
            ):
                assert quote in texts[line_id], "SEMANTIC_QUOTE_WITHOUT_BODY_EVIDENCE"
        report["delivery_sha256"][operation] = digest(delivery)
        report["operations"][operation] = {
            "utterances": len(utterances),
            "episodes": len(delivery["episodes"]),
            "model_text_provenance_passed": True,
            "rendering_lossless": True,
            "semantic_checks": checks,
        }
    created, revised, continued, remix = (
        deliveries[key] for key in ("create", "revise", "continue", "remix")
    )
    target = load(root / "revise" / "request.json")["edit"]["target"]

    def retained(delivery):
        return [
            scene
            for episode in delivery["episodes"]
            for scene in episode["scenes"]
            if (episode["episode_id"], scene["scene_id"])
            != (target["episode_id"], target["scene_id"])
        ]

    assert retained(created) == retained(revised)
    assert lines(created) != lines(revised)
    assert continued["episodes"][:2] == revised["episodes"] and [
        e["index"] for e in continued["episodes"]
    ] == [1, 2, 3, 4]
    assert len(continued["generated_episode_ids"]) == 2
    assert not any(
        "三声铃" in t for t in continued["continuity_state"]["unresolved_threads"]
    )
    assert (
        remix["story_id"] != continued["story_id"]
        and remix["parent_story_version_id"] == continued["story_version_id"]
    )
    assert {c["character_id"]: c["canon"] for c in remix["character_snapshot"]} == {
        c["character_id"]: c["canon"] for c in continued["character_snapshot"]
    }
    report["checks"] = {
        "revise_retained_scenes_sha256": digest(retained(created)),
        "revise_retained_exact": True,
        "continue_source_prefix_sha256": digest(revised["episodes"]),
        "continue_source_prefix_exact": True,
        "continue_global_indices": [1, 2, 3, 4],
        "remix_independent_story_and_correct_parent": True,
        "canon_preserved": True,
    }
    # Verify actual writer input, rather than inferring handoff from final prose.
    continue_job = load(root / "continue" / "result.json")["job_id"]
    handoffs = []
    for file in (root / "model_calls" / continue_job).rglob("call_*.json"):
        capture = load(file)
        if (
            capture["status"] != "completed"
            or capture["role"] != "storycraft_episode_writer"
        ):
            continue
        try:
            payload = json.loads(capture["user"])
        except json.JSONDecodeError:
            continue
        if payload.get("episode_outline", {}).get("index") == 4:
            previous = payload["previous_generated_episodes"]
            generated = next(episode for episode in previous if episode["index"] == 3)
            state = payload["current_continuity"]
            assert any(item["index"] == 3 for item in state["episode_summaries"])
            assert all(fact in state["facts"] for fact in generated["fact_delta"])
            handoffs.append(
                {
                    "model_capture": str(file.relative_to(root)),
                    "previous_new_episode_id": generated["episode_id"],
                    "facts_received": generated["fact_delta"],
                    "relationships_received": generated["relationship_delta"],
                    "threads_received": state["unresolved_threads"],
                }
            )
    assert handoffs, "NO_REAL_SECOND_NEW_EPISODE_HANDOFF_EVIDENCE"
    report["continue_handoffs"] = handoffs
    (root / "evidence_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "Saved evidence verified: raw model text, semantic citations, preservation hashes and rendering fields"
    )


if __name__ == "__main__":
    main()
