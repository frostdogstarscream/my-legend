"""Aggregate physical model calls across failed, resumed and copied evidence.

Calls are deduplicated globally by call_id, never by Plan/job trace inheritance.
No model calls are made; incomplete or failed captures remain visible.
"""

import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def metrics(records):
    reported = [c for c in records if c.get("usage_source") == "reported"]
    result = {
        "calls": len(records),
        "input_tokens": sum(c.get("input_tokens", 0) for c in reported),
        "output_tokens": sum(c.get("output_tokens", 0) for c in reported),
        "reasoning_tokens_in_output": sum(
            c.get("reasoning_tokens", 0) for c in reported
        ),
        "cache_read_tokens": sum(c.get("cache_read_tokens", 0) for c in reported),
        "cache_write_tokens": sum(c.get("cache_write_tokens", 0) for c in reported),
        "estimated_input_tokens": sum(
            c.get("input_tokens", 0)
            for c in records
            if c.get("usage_source") == "estimated"
        ),
        "unknown_usage_calls": sum(
            c.get("usage_source") == "unavailable" for c in records
        ),
        "request_failures": sum(c.get("status") != "success" for c in records),
        "model_request_seconds": round(
            sum(c.get("latency_ms", 0) for c in records) / 1000, 3
        ),
    }
    result["reported_total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calls, labels, failures, batches, captures = {}, {}, {}, [], {}
    resource_finishes, resource_labels = {}, {}
    for directory in args.directories:
        summary = load(directory / "summary.json")
        batches.append(
            {
                "directory": str(directory),
                "results": summary["results"],
                "wall_seconds": summary["wall_seconds"],
                "manifest": load(directory / "manifest.json"),
            }
        )
        for resource, detail in summary["resources"].items():
            resource_labels[resource] = {
                "operation": detail["operation"],
                "phase": detail["phase"],
            }
            for call in detail["call_records"]:
                call_id = call["call_id"]
                if call_id in calls:
                    assert calls[call_id] == call, "COPIED_MODEL_CALL_CHANGED"
                calls[call_id] = call
                labels[call_id] = {
                    "operation": detail["operation"],
                    "phase": detail["phase"],
                    "resource_id": resource,
                }
        for file in directory.glob("*/*.json"):
            view = load(file)
            if not isinstance(view, dict):
                continue
            resource = view.get("task_id") or view.get("job_id")
            data = view.get("usage", {})
            completed = data.get("attempt_completed_at")
            if (
                resource
                and completed
                and completed
                > resource_finishes.get(resource, {}).get("completed_at", "")
            ):
                resource_finishes[resource] = {
                    "completed_at": completed,
                    "wall_seconds": round(
                        (data.get("attempt_wall_seconds") or 0)
                        + sum(
                            (a.get("wall_seconds") or 0)
                            for a in data.get("attempt_history", [])
                        ),
                        3,
                    ),
                }
        for file in (directory / "model_calls").rglob("call_*.json"):
            capture = load(file)
            captures[capture["capture_id"]] = {
                "status": capture["status"],
                "calls": len(capture.get("calls", [])),
            }
        for file in directory.glob("*/generation_attempt*.json"):
            job = load(file)
            if job["status"] == "FAILED":
                current = tuple(
                    job.get("usage", {}).get("current_attempt_call_ids", [])
                )
                identity = (job["job_id"], current)
                failures[identity] = {
                    "operation": file.parent.name,
                    "job_id": job["job_id"],
                    "error_code": job["error_code"],
                    "error_message": job["error_message"],
                    "call_ids": list(current),
                    "evidence": str(file),
                }
    values = list(calls.values())
    record = {
        "deduplication": "global physical call_id; copied evidence and inherited Plan traces are not billed twice",
        "totals": metrics(values),
        "by_operation": {
            operation: metrics(
                [c for cid, c in calls.items() if labels[cid]["operation"] == operation]
            )
            for operation in sorted({v["operation"] for v in labels.values()})
        },
        "by_phase": {
            phase: metrics(
                [c for cid, c in calls.items() if labels[cid]["phase"] == phase]
            )
            for phase in sorted({v["phase"] for v in labels.values()})
        },
        "by_node": {
            node: metrics([c for c in values if c["node"] == node])
            for node in sorted({c["node"] for c in values})
        },
        "by_operation_phase": {
            operation: {
                phase: metrics(
                    [
                        c
                        for cid, c in calls.items()
                        if labels[cid]["operation"] == operation
                        and labels[cid]["phase"] == phase
                    ]
                )
                for phase in sorted({v["phase"] for v in labels.values()})
            }
            for operation in sorted({v["operation"] for v in labels.values()})
        },
        "by_model": {
            model: metrics([c for c in values if c.get("actual_model") == model])
            for model in sorted({c.get("actual_model", "unknown") for c in values})
        },
        "batches": batches,
        "generation_failures": list(failures.values()),
        "pending_captures_unknown_usage": sum(
            c["status"] == "pending" for c in captures.values()
        ),
        "failed_captures_without_returned_usage": sum(
            c["status"] == "failed" and not c["calls"] for c in captures.values()
        ),
        "wall_seconds_all_invocations": round(
            sum(b["wall_seconds"] for b in batches), 3
        ),
        "monetary_charge": "Provider did not return a bill or tariff; not estimated",
        "call_records": values,
        "call_labels": labels,
        "resource_end_to_end_wall": {
            resource: {**data, **resource_labels[resource]}
            for resource, data in resource_finishes.items()
            if resource in resource_labels
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(record["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
