import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from my_legend_production.production.api import register_production_routes
from my_legend_production.production.cli import capabilities
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.story_guard import authorize_call
from test_production_contracts import production_request
from test_production_queue import env
from test_production_story import StoryService, StoryStage, ProductionWorker, advance


def test_visual_preflight_rejects_before_creating_or_spending(env, monkeypatch):
    repo, store = env
    monkeypatch.setenv("MY_LEGEND_PRODUCTION_MODULES", "story,audio")
    app = FastAPI()
    register_production_routes(app, repo, store, lambda request: "alice", capabilities)
    with TestClient(app) as client:
        response = client.post("/api/v1/production-jobs", json=production_request(outputs=["visual"]).model_dump())
        assert response.status_code == 409
        assert set(response.json()["detail"]["modules"]) == {"visual_plan", "visual_render", "integrate"}
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0


def bound_stage(env, units=5, writing=False):
    repo, _ = env
    job = repo.create("alice", production_request(outputs=["script"], budget={"max_repairs_per_stage": 1}))
    stage = repo.claim("native-policy-test", 30)
    repo.reserve_call(stage, "story:planning-budget", "hash", units)
    if writing:
        repo.reserve_call(stage, 'story:writing-budget', 'hash', units)
    return job, {"job_id": job, "request_hash": repo.get(job, "alice")["request_hash"]}


def test_native_calls_share_repair_limit_across_planning_and_writing(env):
    repo, _ = env
    job, binding = bound_stage(env)
    authorize_call(repo, binding, "planning", "initial")
    authorize_call(repo, binding, "planning", "json_schema_repair")
    with pytest.raises(ProductionError, match="REPAIR_BUDGET_EXCEEDED"):
        authorize_call(repo, binding, "planning", "targeted_duration_repair")
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) AS n FROM events WHERE job_id=%s AND event_type='story_model_intent'", (job,)).fetchone()["n"] == 2


def test_readiness_repair_consumes_same_story_repair_budget(env):
    repo, _ = env
    _, binding = bound_stage(env)
    authorize_call(repo, binding, 'planning', 'production_draft_review')
    authorize_call(repo, binding, 'planning', 'production_draft_repair')
    with pytest.raises(ProductionError, match='REPAIR_BUDGET_EXCEEDED'):
        authorize_call(repo, binding, 'planning', 'targeted_duration_repair')


@pytest.mark.parametrize('next_repair', ['production_draft_repair','performance_annotation_repair',
                                      'targeted_duration_repair','json_schema_repair'])
def test_outline_and_body_repairs_share_the_original_stage_limit(env, next_repair):
    repo, _ = env
    _, binding = bound_stage(env, writing=True)
    authorize_call(repo, binding, 'planning', 'production_plan_review')
    authorize_call(repo, binding, 'planning', 'production_plan_repair')
    authorize_call(repo, binding, 'writing', 'production_draft_review')
    with pytest.raises(ProductionError, match='REPAIR_BUDGET_EXCEEDED'):
        authorize_call(repo, binding, 'writing', next_repair)


def test_readiness_failure_surfaces_only_allowlisted_native_error():
    result = StoryStage.failed({}, {'error_code':'WORKFLOW_GENERATION_FAILED',
                                   'error_message':'STORY_PRODUCTION_READINESS_FAILED'})
    assert result.error_code == 'STORY_PRODUCTION_READINESS_FAILED'
    result = StoryStage.failed({}, {'error_code':'WORKFLOW_GENERATION_FAILED',
                                   'error_message':'private provider details'})
    assert result.error_code == 'WORKFLOW_GENERATION_FAILED'


@pytest.mark.parametrize("stop,expected", [("cancel", "PRODUCTION_NOT_ACTIVE"), ("deadline", "DEADLINE_EXCEEDED"),
                                          ("binding", "PRODUCTION_BINDING_INVALID"), ("budget", "CALL_BUDGET_EXCEEDED")])
def test_native_policy_checks_before_external_submission(env, stop, expected):
    repo, _ = env
    job, binding = bound_stage(env, units=0 if stop == "budget" else 5)
    if stop == "cancel":
        repo.cancel(job, "alice")
    elif stop == "deadline":
        with repo.transaction() as db:
            db.execute("UPDATE jobs SET deadline=now()-interval '1 second' WHERE id=%s", (job,))
    elif stop == "binding":
        binding["request_hash"] = "wrong"
    with pytest.raises(ProductionError, match=expected):
        authorize_call(repo, binding, "planning", "initial")


def test_unknown_story_usage_blocks_automatic_resubmission(env):
    class UnknownStory(StoryService):
        def call(self, method, path, body=None):
            response = super().call(method, path, body)
            if path == "/v1/tasks/t1":
                response["usage"]["inflight_usage_unknown"] = True
            return response
    repo, store = env
    job = repo.create("alice", production_request(outputs=["script"]))
    worker = ProductionWorker(repo, store, {"story": StoryStage(UnknownStory())})
    advance(worker, repo, 2)
    state = repo.get(job, "alice")
    assert state["status"] == "needs_input" and state["actual_calls"] == 3
    with pytest.raises(ProductionError, match="EXTERNAL_RESULT_UNKNOWN"):
        repo.resume(job, "alice", 1, {})
