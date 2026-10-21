"""Build a schema-validated request from a downloaded immutable delivery.

Does not submit anything, call a model, or approve a Plan. Positions are 1-based
episode/scene/line array offsets; actual IDs are read from the delivery.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"4-引擎代码"))
from pydantic import TypeAdapter
from drama_engine.workflow.schemas import StoryDelivery, StoryOperationRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intent", choices=["revise", "continue", "remix"])
    parser.add_argument("source", type=Path)
    parser.add_argument("instruction")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=["line", "scene", "episode", "arc", "ending", "whole_story"], default="scene")
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--scene", type=int, default=1)
    parser.add_argument("--line", type=int, default=1)
    parser.add_argument("--through-episode", type=int)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--downstream-policy", choices=["branch_from_here", "replan_affected"], default="branch_from_here")
    parser.add_argument("--retain", action="append")
    parser.add_argument("--transform", action="append")
    args = parser.parse_args()
    if min(args.episode, args.scene, args.line, args.episodes) < 1:
        parser.error("Episode/scene/line positions and generated episode count must be positive")
    raw = json.loads(args.source.read_text(encoding="utf-8-sig"))
    source = StoryDelivery.model_validate(raw.get("response", raw))
    value = {"intent":args.intent, "request_id":uuid.uuid4().hex, "idempotency_key":uuid.uuid4().hex,
             "user_instruction":args.instruction,
             "source_selector":{"type":"by_id", "story_version_id":source.story_version_id}}
    if args.intent == "revise":
        if args.episode > len(source.episodes):
            parser.error("Episode position does not exist")
        episode = source.episodes[args.episode-1]
        if args.scene > len(episode.scenes):
            parser.error("Scene position does not exist")
        scene = episode.scenes[args.scene-1]
        target = {"episode_id":episode.episode_id}
        if args.scope in ("scene", "line"):
            target["scene_id"] = scene.scene_id
        if args.scope == "line":
            if args.line > len(scene.utterances):
                parser.error("Line position does not exist")
            target["line_id"] = scene.utterances[args.line-1].line_id
        if args.scope == "arc":
            end = args.through_episode or args.episode
            if end < args.episode or end > len(source.episodes):
                parser.error("--through-episode must be within the source range and >= --episode")
            target = {"episode_ids":[e.episode_id for e in source.episodes[args.episode-1:end]]}
        if args.scope == "ending":
            target["scene_ids"] = [s.scene_id for s in episode.scenes[args.scene-1:]]
        if args.scope == "whole_story":
            target = {}
        value["edit"] = {"scope":args.scope, "target":target, "instruction":args.instruction, "downstream_policy":args.downstream_policy}
    if args.intent == "continue":
        value["creation_preferences"] = {"target_episodes":args.episodes}
    if args.intent == "remix":
        if not args.retain or not args.transform:
            parser.error("Remix requires --retain and --transform, each repeatable")
        value["creation_preferences"] = {"target_episodes":args.episodes}
        value["remix"] = {"instruction":args.instruction, "retain_elements":args.retain,
                          "transform_elements":args.transform, "relation_to_source":"parallel_world"}
    TypeAdapter(StoryOperationRequest).validate_python(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Request saved. Review source, exact scope and new IDs before POST. No model call made.")


if __name__ == "__main__":
    main()
