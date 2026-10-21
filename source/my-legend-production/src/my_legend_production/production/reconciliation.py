"""Reconcile recorded upstream jobs by read-only queries; never resubmit generation."""
from urllib.parse import quote

from .contracts import fingerprint
from .repository import ProductionError


class ProductionReconciler:
    def __init__(self, repository, story_client=None):
        self.repository, self.story = repository, story_client

    def run(self, job_id, owner_id, revision):
        calls = self.repository.reconciliation_snapshot(job_id, owner_id, revision)
        settled, unresolved = [], []
        for call in calls:
            key, state = call["call_key"], call["checkpoint"]
            if key not in ("story:planning-budget", "story:writing-budget"):
                unresolved.append({"call_key": key, "reason": "RESULT_QUERY_UNAVAILABLE"})
                continue
            expected_phase = "planning" if key == "story:planning-budget" else "writing"
            id_key, route = ("task_id", "/v1/tasks/") if expected_phase == "planning" else ("upstream_id", "/v1/jobs/")
            if state.get("phase") != expected_phase or not isinstance(state.get(id_key), str) or not state[id_key]:
                unresolved.append({"call_key": key, "reason": "RECORDED_UPSTREAM_ID_MISSING"})
                continue
            if self.story is None:
                unresolved.append({"call_key": key, "reason": "STORY_SERVICE_UNAVAILABLE"})
                continue
            try:
                response = self.story.call("GET", route + quote(state[id_key], safe=""))
                usage = response.get("usage", {})
                units = usage.get("calls")
                if response.get("status") not in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    reason = "UPSTREAM_STILL_ACTIVE"
                elif not isinstance(units, int) or isinstance(units, bool) or units < 0 or usage.get("unknown_usage_calls", 0) or usage.get("inflight_usage_unknown", False):
                    reason = "EXTERNAL_RESULT_UNKNOWN"
                else:
                    self.repository.settle_reconciliation(job_id, owner_id, revision, call, usage, units,
                        {"basis": "recorded_upstream_terminal_query", "upstream_id": state[id_key],
                         "upstream_status": response["status"], "response_sha256": fingerprint(response)})
                    settled.append(key)
                    continue
            except ProductionError as exc:
                reason = exc.code
            unresolved.append({"call_key": key, "reason": reason})
        return {"job_id": job_id, "settled": settled, "unresolved": unresolved,
                "generation_submissions": 0, "job": self.repository.get(job_id, owner_id)}
