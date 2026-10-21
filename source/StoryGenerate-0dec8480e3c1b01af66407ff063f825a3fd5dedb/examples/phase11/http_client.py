"""Execute a request example against running API/Worker processes (real models).

Set STORY_CLIENT_API_KEY; choose --request create/revise/continue/remix.json.
For derived operations pass --source-version. Optional --feedback demonstrates
Plan Revision. A FAILED task is surfaced, never silently retried or mocked.
"""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--source-version")
    parser.add_argument("--feedback")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ["STORY_CLIENT_API_KEY"]
    with httpx.Client(
        base_url=args.url, headers={"Authorization": "Bearer " + key}, timeout=20
    ) as client:

        def call(method, path, body=None):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        def wait(path):
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                resource = call("GET", path)
                if resource["status"] == "SUCCEEDED":
                    return resource
                if resource["status"] in ("FAILED", "CANCELLED"):
                    raise RuntimeError(json.dumps(resource, ensure_ascii=False))
                time.sleep(1)
            raise TimeoutError("Task remains durable; query its ID before retrying")

        request = json.loads(args.request.read_text(encoding="utf-8"))
        request["request_id"] = uuid.uuid4().hex
        request["idempotency_key"] = uuid.uuid4().hex
        if request["intent"] != "create":
            if not args.source_version:
                parser.error("--source-version required")
            request["source_selector"] = {
                "type": "by_id",
                "story_version_id": args.source_version,
            }
            if request["intent"] == "revise":
                source = call("GET", "/v1/story-versions/" + args.source_version)
                episode = source["episodes"][0]
                scene = next(
                    s
                    for s in episode["scenes"]
                    if any(u["kind"] == "dialogue" for u in s["utterances"])
                )
                request["edit"]["target"] = {
                    "episode_id": episode["episode_id"],
                    "scene_id": scene["scene_id"],
                }
        args.output.mkdir(parents=True, exist_ok=True)
        # Keep exact request/idempotency key for recovery without exposing bearer credentials.
        (args.output / "request.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        task = call("POST", "/v1/plans", request)
        print("Planning task:", task["task_id"], flush=True)
        completed = wait("/v1/tasks/" + task["task_id"])
        plan_path = "/v1/plans/" + completed["result"]["plan_id"]
        plan = call("GET", plan_path)
        if args.feedback:
            task = call(
                "POST",
                plan_path + "/revisions",
                {
                    "expected_revision": plan["plan_revision"],
                    "feedback": args.feedback,
                    "idempotency_key": uuid.uuid4().hex,
                },
            )
            wait("/v1/tasks/" + task["task_id"])
            plan = call("GET", plan_path)
        (args.output / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        job = call(
            "POST",
            plan_path + "/approve",
            {
                "expected_revision": plan["plan_revision"],
                "fingerprint": plan["plan_fingerprint"],
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        print("Generation job:", job["job_id"], flush=True)
        completed = wait("/v1/jobs/" + job["job_id"])
        version = completed["result_story_version_id"]
        for name, path in {
            "delivery": "/export?format=json",
            "characters": "/export?format=characters",
            "render": "/render-manifest",
        }.items():
            value = call("GET", "/v1/story-versions/" + version + path)
            (args.output / (name + ".json")).write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print("Story version:", version)


if __name__ == "__main__":
    main()
