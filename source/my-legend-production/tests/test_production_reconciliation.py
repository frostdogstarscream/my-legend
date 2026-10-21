import pytest
from pydantic import ValidationError

from my_legend_production.production.contracts import ReconcileRequest
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.reconciliation import ProductionReconciler
from my_legend_production.production.story import StoryStage
from my_legend_production.production.worker import ProductionWorker
from test_production_queue import env
from test_production_contracts import production_request
from test_production_story import StoryService, advance


class UncertainStory(StoryService):
    unknown = True
    active = False

    def call(self, method, path, body=None):
        result = super().call(method, path, body)
        if path == "/v1/tasks/t1":
            result["usage"]["unknown_usage_calls"] = int(self.unknown)
            if self.active:
                result["status"] = "RUNNING"
        return result


def waiting(env):
    repo, store = env
    client = UncertainStory()
    job = repo.create("alice", production_request(outputs=["script"]))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 2)
    assert repo.get(job, "alice")["unknown_calls"] == 1
    return job, client, worker


def test_read_only_upstream_reconciliation_allows_resume_without_duplicate_generation(env):
    repo, _ = env
    job, client, worker = waiting(env)
    client.unknown = False
    before = len(client.calls)
    result = ProductionReconciler(repo, client).run(job, "alice", 1)
    assert result["settled"] == ["story:planning-budget"] and result["unresolved"] == []
    assert result["job"]["status"] == "needs_input" and result["job"]["unknown_calls"] == 0
    assert all(method == "GET" for method, _, _ in client.calls[before:])
    assert repo.get(job, "alice")["actual_calls"] == 3
    assert ProductionReconciler(repo, client).run(job, "alice", 1)["settled"] == []
    repo.resume(job, "alice", 1, {})
    advance(worker, repo, 3)
    state = repo.get(job, "alice")
    assert state["status"] == "succeeded" and state["actual_calls"] == 9
    assert sum(method == "POST" for method, _, _ in client.calls) == 2
    evidence = repo.execution_evidence(job, "alice")
    assert evidence["provider_calls"] == 9 and evidence["reconciliation_events"] == 1 and evidence["resume_events"] == 1
    assert evidence["session_interventions"] is None
    assert all("result" not in call for call in evidence["calls"])
    with pytest.raises(ProductionError, match="JOB_NOT_FOUND"):
        repo.execution_evidence(job, "bob")


@pytest.mark.parametrize("active,unknown,reason", [(False, True, "EXTERNAL_RESULT_UNKNOWN"), (True, False, "UPSTREAM_STILL_ACTIVE")])
def test_unconfirmed_or_running_upstream_remains_blocked(env, active, unknown, reason):
    repo, _ = env
    job, client, _ = waiting(env)
    client.active, client.unknown = active, unknown
    result = ProductionReconciler(repo, client).run(job, "alice", 1)
    assert result["unresolved"][0]["reason"] == reason and result["job"]["unknown_calls"] == 1
    with pytest.raises(ProductionError, match="EXTERNAL_RESULT_UNKNOWN"):
        repo.resume(job, "alice", 1, {})


def test_owner_revision_and_recorded_identifier_are_required(env):
    repo, _ = env
    job, client, _ = waiting(env)
    service = ProductionReconciler(repo, client)
    count = len(client.calls)
    with pytest.raises(ProductionError, match="JOB_NOT_FOUND"):
        service.run(job, "bob", 1)
    with pytest.raises(ProductionError, match="RECONCILIATION_CONFLICT"):
        service.run(job, "alice", 2)
    assert len(client.calls) == count
    with repo.transaction() as db:
        db.execute("UPDATE stages SET checkpoint='{}' WHERE job_id=%s", (job,))
    result = service.run(job, "alice", 1)
    assert result["unresolved"][0]["reason"] == "RECORDED_UPSTREAM_ID_MISSING"
    assert len(client.calls) == count


def test_client_cannot_supply_paid_result_status_or_upstream_identity():
    with pytest.raises(ValidationError):
        ReconcileRequest.model_validate({"expected_revision": 1, "result": "completed", "upstream_id": "invented"})
