from copy import deepcopy
import json
from pathlib import Path

import pytest

from my_legend_production.production.contracts import ProductionRequest
from my_legend_production.production.story import StoryStage
from my_legend_production.production.worker import ProductionWorker
from my_legend_production.production.repository import ProductionError
from test_production_contracts import production_request
from test_production_queue import env


class StoryService:
    def __init__(self):
        examples = Path(__file__).resolve().parents[1] / "examples/source"
        self.delivery = json.loads((examples / "story_delivery.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((examples / "render_manifest.json").read_text(encoding="utf-8"))
        self.calls = []
        self.plan = {"plan_id": "p1", "plan_revision": 1, "plan_fingerprint": "a" * 64,
                     "plan": {"title": "测试大纲", "premise": "仅用于模块契约测试"}}
        self.fail = False

    def call(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if path == "/v1/plans":
            from pydantic import TypeAdapter
            from drama_engine.workflow.schemas import StoryOperationRequest
            TypeAdapter(StoryOperationRequest).validate_python(body)
            return {"task_id": "t1"}
        if path == "/v1/tasks/t1":
            return {"status": "SUCCEEDED", "result": {"plan_id": "p1"}, "usage": {"calls": 3, "unknown_usage_calls": 0}}
        if path == "/v1/plans/p1":
            return self.plan
        if path == "/v1/plans/p1/approve":
            from drama_engine.service.schemas import PlanApprovalRequest
            PlanApprovalRequest.model_validate(body)
            assert body["fingerprint"] == self.plan["plan_fingerprint"]
            return {"job_id": "j1"}
        if path == "/v1/jobs/j1":
            return {"status": "FAILED" if self.fail else "SUCCEEDED", "error_code": "CONTENT_CHECK_FAILED",
                    "usage": {"calls": 6, "unknown_usage_calls": 0}, "result_story_version_id": self.delivery["story_version_id"]}
        if path.endswith("/render-manifest"):
            return self.manifest
        if path.startswith("/v1/story-versions/"):
            return self.delivery
        raise AssertionError((method, path))


def advance(worker, repo, n):
    for _ in range(n):
        with repo.transaction() as db:
            db.execute("UPDATE stages SET available_at=now()")
        assert worker.tick()


def test_automatic_story_is_driven_by_worker_with_explicit_policy_and_real_contracts(env):
    repo, store = env
    client = StoryService()
    job = repo.create("alice", production_request(outputs=["script"]))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 4)
    state = repo.get(job, "alice")
    assert state["status"] == "succeeded"
    assert state["actual_calls"] == 9 and state["reserved_calls"] == 24
    assert state["unsettled_calls"] == 0
    decision = next(a for a in state["artifacts"] if a["kind"] == "StoryPlanDecision")
    from my_legend_production.production.contracts import ArtifactRef
    record = store.json(ArtifactRef.model_validate(decision))
    assert record["authority"] == "configured_automation_policy" and record["human_approval"] is False
    assert record["request_hash"] == state["request_hash"]
    assert sum(method == "POST" for method, _, _ in client.calls) == 2


def test_reviewed_mode_requires_exact_app_decision(env):
    repo, store = env
    client = StoryService()
    job = repo.create("alice", production_request(outputs=["script"], mode="reviewed"))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 2)
    state = repo.get(job, "alice")
    assert state["status"] == "needs_input" and state["stages"][0]["input_request"]["fingerprint"] == "a" * 64
    assert not any(path.endswith("/approve") for _, path, _ in client.calls)
    repo.resume(job, "alice", 1, {"decision": "approve", "plan_id": "p1", "revision": 1, "fingerprint": "b" * 64})
    advance(worker, repo, 1)
    assert repo.get(job, "alice")["status"] == "needs_input"
    repo.resume(job, "alice", 2, {"decision": "approve", "plan_id": "p1", "revision": 1, "fingerprint": "a" * 64})
    advance(worker, repo, 2)
    assert repo.get(job, "alice")["status"] == "succeeded"


def test_budget_rejects_body_before_submission(env):
    repo, store = env
    client = StoryService()
    job = repo.create("alice", production_request(outputs=["script"], budget={"max_provider_calls": 12}))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 3)
    state = repo.get(job, "alice")
    assert state["status"] == "failed" and state["error_code"] == "CALL_BUDGET_EXCEEDED"
    assert not any(path.endswith("/approve") for _, path, _ in client.calls)


def test_existing_owned_delivery_import_has_no_provider_calls(env):
    repo, store = env
    client = StoryService()
    job = repo.create("alice", production_request(outputs=["script"]))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 4)
    artifacts = {a["kind"]: a for a in repo.get(job, "alice")["artifacts"]}
    request = production_request(idempotency_key="owned-source-import", outputs=["script"], source={
        "kind": "delivery", "source_version_id": client.delivery["story_version_id"],
        "delivery": artifacts["StoryDelivery"], "manifest": artifacts["RenderManifest"]})
    other = repo.create("alice", request)
    before = len(client.calls)
    advance(worker, repo, 1)
    assert repo.get(other, "alice")["status"] == "succeeded" and len(client.calls) == before
    with pytest.raises(ProductionError, match="SOURCE_ARTIFACT_NOT_OWNED"):
        repo.create("bob", request)


def test_failed_story_gate_never_yields_ready_artifacts(env):
    repo, store = env
    client = StoryService()
    client.fail = True
    job = repo.create("alice", production_request(outputs=["script"]))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 4)
    state = repo.get(job, "alice")
    assert state["status"] == "failed" and not state["artifacts"]
    assert state["actual_calls"] == 9
