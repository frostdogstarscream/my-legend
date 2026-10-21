"""API-only acceptance runner. Preparation is offline; execution is an explicit operation."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import importlib.util
from pathlib import Path
import time
import sys
from uuid import uuid4

import httpx

from .contracts import ProductionRequest, fingerprint
from .acceptance_media import audit_downloads


SUFFIXES = {".py", ".dart", ".json", ".yaml", ".yml", ".toml", ".txt", ".j2", ".jinja", ".ps1", ".md", ".sql"}
EXCLUDED = {".git", ".venv", "__pycache__", "node_modules", "build", "dist", "tests", "outputs", "examples", "6-实测产物", "5-回归验证"}


def story_source_root(workspace):
    spec = importlib.util.find_spec("drama_engine")
    if spec is None or not spec.origin:
        raise ValueError("STORY_RUNTIME_SOURCE_UNAVAILABLE")
    module = Path(spec.origin).resolve().parent
    if not module.is_relative_to(workspace.resolve()):
        raise ValueError("STORY_RUNTIME_SOURCE_OUTSIDE_WORKSPACE")
    required = [module / "workflow/storycraft_prompts.py", module / "workflow/schemas.py"]
    if not all(path.is_file() for path in required):
        raise ValueError("STORY_RUNTIME_SOURCE_INCOMPLETE")
    for parent in module.parents:
        if not parent.is_relative_to(workspace.resolve()):
            break
        if (parent / "pyproject.toml").is_file():
            return parent
    raise ValueError("STORY_RUNTIME_PROJECT_UNAVAILABLE")


def digest(path):
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            result.update(chunk)
    return result.hexdigest()


def source_snapshot(workspace):
    roots = [workspace / "source/my-legend-production/src", workspace / "ops/my-legend"]
    roots.append(story_source_root(workspace))
    files = {}
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SUFFIXES or set(path.relative_to(root).parts) & EXCLUDED:
                continue
            if "evidence" in path.relative_to(root).parts or path.name.startswith(("credential-", ".env")):
                continue
            files[path.relative_to(workspace).as_posix()] = digest(path)
    for relative in ("source/my-legend-production/pyproject.toml", "ops/my-legend/requirements-story.lock.txt",
                     "runtime/my-legend-production/workflows.json", "runtime/my-legend-production/dependency-lock.json",
                     "runtime/my-legend-production/story-provider/profile.json", "runtime/my-legend-production/audio-provider/profile.json"):
        path = workspace / relative
        if not path.is_file():
            raise ValueError("FREEZE_INPUT_MISSING")
        files[relative] = digest(path)
    dependency = json.loads((workspace / "runtime/my-legend-production/dependency-lock.json").read_text(encoding="utf-8"))
    for relative, expected in dependency["files"].items():
        path = workspace / "runtime/my-legend-production" / relative
        if digest(path) != expected:
            raise ValueError("PINNED_DEPENDENCY_CHANGED")
        files[path.relative_to(workspace).as_posix()] = expected
    return dict(sorted(files.items()))


def public_provider_config(workspace):
    root = workspace / "runtime/my-legend-production"
    story = json.loads((root / "story-provider/profile.json").read_text(encoding="utf-8"))
    audio = json.loads((root / "audio-provider/profile.json").read_text(encoding="utf-8"))
    return {"story": {k: story[k] for k in ("schema_version", "base_url", "model")},
            "audio": {k: audio[k] for k in ("schema_version", "provider", "endpoint", "tts_model", "sfx_model")},
            "audio_arrangement": {"base_url": story["base_url"], "model": story["model"], "basis": "current_stack_configuration"},
            "visual": {"base_url": story["base_url"], "model": story["model"], "basis": "current_stack_configuration"}}


def tool_snapshot(workspace):
    config = json.loads((workspace / "runtime/my-legend-production/workflows.json").read_text(encoding="utf-8"))
    paths = {key: config[key] for key in ("dart", "dart_packages", "ffmpeg")}
    paths["runner_python"] = getattr(sys, "_base_executable", sys.executable)
    return {key: {"path": str(Path(value).resolve()), "sha256": digest(Path(value))} for key, value in paths.items()}


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def validate_run_scope(request, run_scope):
    if run_scope == "new_creative_input":
        if request.mode != "automatic" or request.source.kind != "creative":
            raise ValueError("ACCEPTANCE_REQUIRES_AUTOMATIC_NEW_INPUT")
    elif run_scope == "story_operation":
        if request.mode != "automatic" or request.source.kind != "operation":
            raise ValueError("ACCEPTANCE_REQUIRES_STORY_OPERATION")
    elif run_scope == "cached_downstream_diagnostic":
        if (request.mode != "automatic" or request.source.kind != "delivery"
                or request.audio_revision is None or request.audio_revision.regenerate
                or "visual" not in request.outputs):
            raise ValueError("DIAGNOSTIC_REQUIRES_FROZEN_DELIVERY_AND_AUDIO_CACHE")
    elif run_scope == 'frozen_story_audio_diagnostic':
        if (request.mode!='automatic' or request.source.kind!='delivery' or 'audio' not in request.outputs
                or 'visual' in request.outputs or request.audio_revision is not None):
            raise ValueError('DIAGNOSTIC_REQUIRES_FROZEN_STORY_AND_FRESH_AUDIO')
    else:
        raise ValueError("INVALID_RUN_SCOPE")


def prepare(workspace, request_path, destination, *, run_scope="new_creative_input"):
    request = ProductionRequest.model_validate_json(request_path.read_bytes())
    validate_run_scope(request, run_scope)
    request.idempotency_key = "acceptance-" + uuid4().hex
    files = source_snapshot(workspace)
    destination.mkdir(parents=True, exist_ok=False)
    write_new(destination / "request.json", request.model_dump(mode="json"))
    write_new(destination / "freeze.json", {"schema_version": "production_acceptance_freeze/1.0",
        "run_scope": run_scope, "new_input_acceptance_eligible": run_scope == "new_creative_input",
        "created_at": datetime.now(timezone.utc).isoformat(), "files": files, "source_hash": fingerprint(files),
        "request_hash": fingerprint(request), "providers": public_provider_config(workspace), "tools": tool_snapshot(workspace),
        "budget": request.budget.model_dump(), "financial_cost_cap": None, "financial_cost_cap_enforced": False,
        "repair_policy": "only_preimplemented_stage_repairs", "api_mutations_allowed": ["submit", "cancel_on_freeze_change_or_timeout"],
        "session_interventions": None, "human_content_acceptance": "pending"})
    return destination


def frozen_inputs(workspace, root):
    if (root / "invalidated.json").exists():
        raise ValueError("ACCEPTANCE_FREEZE_INVALIDATED")
    freeze = json.loads((root / "freeze.json").read_text(encoding="utf-8"))
    request = ProductionRequest.model_validate_json((root / "request.json").read_bytes())
    validate_run_scope(request, freeze.get("run_scope", "new_creative_input"))
    if fingerprint(request) != freeze["request_hash"] or fingerprint(source_snapshot(workspace)) != freeze["source_hash"] or tool_snapshot(workspace) != freeze["tools"]:
        raise ValueError("ACCEPTANCE_FREEZE_CHANGED")
    return freeze, request


def execute(workspace, root, *, billing_acknowledged=False, poll_seconds=10, transport=None):
    if not billing_acknowledged:
        raise ValueError("EXPLICIT_PROVIDER_BILLING_ACK_REQUIRED")
    freeze, request = frozen_inputs(workspace, root)
    # An existing submission intent is never overwritten after a crash or ambiguous response.
    write_new(root / "submission-intent.json", {"idempotency_key": request.idempotency_key, "request_hash": freeze["request_hash"]})
    job_id, token, failure = None, None, None
    with httpx.Client(base_url="http://127.0.0.1:8895", trust_env=False, follow_redirects=False, timeout=30, transport=transport) as client:
        def api(method, path, body=None):
            response = client.request(method, "/api/v1"+path, json=body) if body is not None else client.request(method, "/api/v1"+path)
            if response.status_code >= 300:
                raise ValueError("ACCEPTANCE_API_"+str(response.status_code))
            return response.json()["data"]
        try:
            token = api("POST", "/auth/local")["accessToken"]
            client.headers["Authorization"] = "Bearer " + token
            caps = api("GET", "/production-capabilities")
            write_new(root / "capabilities.json", caps)
            job = api("POST", "/production-jobs", request.model_dump(mode="json"))
            job_id = job["id"]
            write_new(root / "submitted.json", {"job_id": job_id})
            end = time.monotonic() + request.budget.max_elapsed_seconds + 60
            observation = 0
            while True:
                unchanged = source_snapshot(workspace) == freeze["files"]
                write_new(root / f"observation-{observation:04d}.json", {"time": datetime.now(timezone.utc).isoformat(),
                    "source_unchanged": unchanged, "job": job})
                observation += 1
                if not unchanged or time.monotonic() > end:
                    api("POST", f"/production-jobs/{job_id}/cancel", {})
                    raise ValueError("ACCEPTANCE_FREEZE_CHANGED" if not unchanged else "ACCEPTANCE_WAIT_EXCEEDED")
                if job["status"] in ("succeeded", "failed", "needs_input", "cancelled"):
                    break
                time.sleep(poll_seconds)
                job = api("GET", f"/production-jobs/{job_id}")
            evidence = api("GET", f"/production-jobs/{job_id}/evidence")
            write_new(root / "execution-evidence.json", evidence)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            verified = []
            for ref in job["artifacts"]:
                response = client.get(f"/api/v1/production-jobs/{job_id}/artifacts/{ref['sha256']}")
                if response.status_code != 200 or len(response.content) != ref["size_bytes"] or sha256(response.content).hexdigest() != ref["sha256"]:
                    raise ValueError("ACCEPTANCE_ARTIFACT_INVALID")
                path = artifacts / ref["sha256"]
                if not path.exists():
                    with path.open("xb") as stream:
                        stream.write(response.content)
                verified.append(ref)
            playback = api("GET", f"/production-jobs/{job_id}/playback")
            write_new(root / "playback.json", playback)
            media_audit = audit_downloads(job, playback, artifacts, ffmpeg=freeze["tools"].get("ffmpeg", {}).get("path"), report_directory=root)
            write_new(root / "media-audit.json", media_audit)
            write_new(root / "result.json", {"job_id": job_id, "execution_status": job["status"], "verified_artifacts": verified,
                "run_scope": freeze.get("run_scope", "new_creative_input"),
                "new_input_acceptance_eligible": freeze.get("run_scope", "new_creative_input") == "new_creative_input",
                "requested_outputs_available": set(request.outputs) <= set(media_audit["available_outputs"]),
                "technical_media_audit": media_audit["technical_media_audit"],
                "source_unchanged_at_finish": source_snapshot(workspace) == freeze["files"],
                "tools_unchanged_at_finish": tool_snapshot(workspace) == freeze["tools"],
                "unexpected_resume_events": evidence["resume_events"], "provider_calls": evidence["provider_calls"],
                "policy_repairs": evidence["policy_repairs"], "financial_cost": None,
                "session_interventions": None, "manual_artifact_edits": None, "out_of_band_context_injections": None,
                "independence_attestation": "pending", "human_content_acceptance": "pending", "device_acceptance": "pending"})
        except Exception as exc:
            failure = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("ACCEPTANCE_") else "ACCEPTANCE_RUN_INTERRUPTED"
            write_new(root / "failure.json", {"job_id": job_id, "error_code": failure,
                "automatic_resubmission": False, "upstream_result_may_require_reconciliation": job_id is None})
        finally:
            if token:
                try:
                    api("POST", "/auth/logout")
                except Exception:
                    pass
    return {"directory": str(root), "job_id": job_id, "runner_error": failure}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "execute"])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--run-scope", choices=["new_creative_input", "cached_downstream_diagnostic", "frozen_story_audio_diagnostic"], default="new_creative_input")
    parser.add_argument("--acknowledge-provider-billing", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.request:
            parser.error("--request is required for preparation")
        print(json.dumps({"prepared": str(prepare(args.workspace, args.request, args.directory, run_scope=args.run_scope)), "model_calls": 0}))
    else:
        print(json.dumps(execute(args.workspace, args.directory, billing_acknowledged=args.acknowledge_provider_billing)))


if __name__ == "__main__":
    main()
