"""Index a complete successful response per endpoint, with honest evidence labels.

Two unavailable live state transitions are explicitly schema examples, never
claimed as HTTP/model execution. Large bodies link existing archive records.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/"docs/handoff"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    spec = read(OUT/"contracts/openapi.json")
    probe = read(OUT/"contracts/http-probe.json")
    bodies = read(OUT/"contracts/http-responses.json")
    statuses = {c["id"]:c["status"] for c in probe["checks"]}
    examples = {}
    for path, methods in spec["paths"].items():
        for method in methods:
            pattern = re.sub(r"\{[^}]+\}", "[^/]+", path)
            match = next((key for key in bodies if re.fullmatch(r"\d+ "+method.upper()+" "+pattern, key.split("?",1)[0]) and statuses[key] in (200,202)),None)
            op = method.upper()+" "+path
            if match:
                examples[op] = {"evidence_kind":"executed_no_model_http", "http_probe_id":match,
                    "status":statuses[match], "request":bodies[match]["request"],
                    "complete_success_response":{"probe_response_reference":{"path":"docs/handoff/contracts/http-responses.json", "entry":match,"json_pointer":"/response"}}}
            elif op == "POST /v1/plans/{plan_id}/revisions":
                archived = read(ROOT/"6-实测产物/phase11_live_20261015_v3/create/revision_submit.json")
                examples[op] = {"evidence_kind":"historical_real_model_http_chain", "status":202,
                    "request":archived["request"], "complete_success_response":archived["response"],
                    "evidence":"6-实测产物/phase11_live_20261015_v3/create/revision_submit.json"}
            elif op == "POST /v1/jobs/{job_id}/retry":
                archived = read(ROOT/"6-实测产物/phase11_live_20261015_v3/create/retry.json")
                examples[op] = {"evidence_kind":"historical_real_model_http_chain", "status":202,
                    "request":None, "complete_success_response":archived["response"],
                    "evidence":"6-实测产物/phase11_live_20261015_v3/create/retry.json"}
            elif op == "POST /v1/tasks/{task_id}/retry":
                examples[op] = {"evidence_kind":"schema_example_NOT_EXECUTED", "status":202,"request":None,
                    "complete_success_response":{"task_id":"EXAMPLE_FAILED_TASK", "kind":"prepare", "status":"QUEUED",
                    "progress_pct":0, "stage":"queued", "result":{}, "error_code":None, "error_message":None,
                    "usage":{}, "created_at":"2026-10-15T00:00:00Z", "updated_at":"2026-10-15T00:01:00Z"}}
            elif op == "POST /v1/jobs/{job_id}/cancel":
                examples[op] = {"evidence_kind":"schema_example_NOT_EXECUTED", "status":200,"request":None,
                    "complete_success_response":{"job_id":"EXAMPLE_QUEUED_JOB", "request_id":"EXAMPLE_ENGINE_HASHED_REQUEST",
                    "status":"CANCELLED", "stage":"cancelled", "progress_pct":0, "approved_plan_id":"EXAMPLE_APPROVED_PLAN",
                    "approved_plan_revision":1, "result_story_version_id":None,"error_code":None,"error_message":None,
                    "usage":{}, "story_state":"CANCELLED", "audio_state":"NOT_SYNTHESIZED", "playback_state":"NOT_REPORTED"}}
            else:
                raise AssertionError("MISSING_ENDPOINT_EXAMPLE "+op)
    result = {"description":"Complete success bodies for all 26 operations: probe_response_reference selects an entry and its response from http-responses.json; response_reference then resolves any archived JSON from repository root. NOT_EXECUTED entries are schema examples, not real executions.", "operations":examples}
    (OUT/"examples/endpoint-success-examples.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Indexed {len(examples)} complete endpoint success examples")


if __name__ == "__main__":
    main()
