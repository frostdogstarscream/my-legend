"""Real PostgreSQL concurrency/recovery tests. No providers, browser, or model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import os
import threading
import time
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from my_legend_production.production.api import register_production_routes
from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.contracts import StageResult, REQUIRED_ARTIFACTS, fingerprint, stage_graph
from my_legend_production.production.repository import ProductionRepository, ProductionError
from my_legend_production.production.worker import ProductionWorker, StageContext
from test_production_contracts import production_request


@pytest.fixture
def env(tmp_path):
    dsn = os.environ.get("MY_LEGEND_PRODUCTION_TEST_DSN")
    if not dsn:
        pytest.skip("Use Test-Production.ps1 to run against the isolated PostgreSQL instance")
    schema = "legend_prod_test_" + uuid4().hex
    repo = ProductionRepository(dsn, schema)
    repo.migrate()
    store = ArtifactStore(tmp_path / "artifacts")
    yield repo, store
    # Drop only the random namespace created by this fixture, never an existing schema.
    assert schema.startswith("legend_prod_test_") and len(schema) == 49
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def result(store, stage, version="test-v1"):
    artifacts = [store.put_json(k, version, {"fixture_only": True, "kind": k, "source_version_id": version})
                 for k in sorted(REQUIRED_ARTIFACTS[stage["name"]])]
    return StageResult(status="succeeded", artifacts=artifacts, source_version_id=version, technical_quality="passed", poll_after_seconds=0)


def test_migration_repeat_and_idempotency_under_concurrency(env):
    repo, _ = env
    repo.migrate()
    request = production_request()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: repo.create("alice", request), range(8)))
    assert len(set(ids)) == 1
    with pytest.raises(ProductionError, match="IDEMPOTENCY_CONFLICT"):
        repo.create("alice", production_request(outputs=["script"]))
    assert repo.create("bob", request) != ids[0]
    with pytest.raises(ProductionError, match="JOB_NOT_FOUND"):
        repo.get(ids[0], "bob")


def test_two_workers_cannot_claim_same_stage_and_stale_owner_is_fenced(env):
    repo, store = env
    repo.create("alice", production_request())
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(repo.claim, ["worker-a", "worker-b"]))
    assert sum(s is not None for s in claims) == 1
    old = next(s for s in claims if s)
    with repo.transaction() as db:
        db.execute("UPDATE stages SET lease_until=now()-interval '1 second' WHERE job_id=%s", (old["job_id"],))
    new = repo.claim("replacement")
    assert new["lease_token"] != old["lease_token"]
    assert not repo.heartbeat(old)
    with pytest.raises(ProductionError, match="LEASE_LOST"):
        repo.finish(old, result(store, old))
    repo.finish(new, result(store, new))


def test_background_worker_completes_without_any_status_poll(env):
    repo, store = env
    job = repo.create("alice", production_request())
    stop = threading.Event()
    count = []
    def handle(ctx):
        count.append(ctx.stage["name"])
        if ctx.stage["name"] == "integrate":
            stop.set()
        return result(ctx.store, ctx.stage)
    worker = ProductionWorker(repo, store, {k: handle for k in REQUIRED_ARTIFACTS})
    thread = threading.Thread(target=worker.run, kwargs={"stop": stop, "idle_seconds": .01})
    thread.start()
    thread.join(15)
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    state = repo.get(job, "alice")
    assert state["status"] == "succeeded"
    assert set(count) == set(stage_graph(production_request()))
    assert state["reserved_calls"] == state["actual_calls"] == 0
    assert all(s["human_review"] == "not_required" for s in state["stages"])


def test_job_listing_is_owner_scoped_paginated_and_does_not_advance(env):
    repo, store = env
    one = repo.create("alice", production_request(idempotency_key="list-first", work_id="work-a"))
    two = repo.create("alice", production_request(idempotency_key="list-second", work_id="work-b"))
    repo.create("bob", production_request(idempotency_key="list-other", work_id="work-a"))
    app = FastAPI()
    register_production_routes(app, repo, store, lambda request: "alice", lambda: {})
    client = TestClient(app)
    first = client.get("/api/v1/production-jobs?limit=1").json()["data"]
    assert first["items"][0]["id"] == two and first["next_offset"] == 1
    second = client.get("/api/v1/production-jobs?limit=1&offset=1").json()["data"]
    assert second["items"][0]["id"] == one and second["next_offset"] is None
    filtered = client.get("/api/v1/production-jobs?work_id=work-a").json()["data"]
    assert [item["id"] for item in filtered["items"]] == [one]
    assert all(stage["attempts"] == 0 for item in first["items"] for stage in item["stages"])
    assert client.get("/api/v1/production-jobs?limit=1000").status_code == 422


def test_checkpoint_and_successful_call_survive_new_worker(env):
    repo, store = env
    job = repo.create("alice", production_request(outputs=["script"]))
    effects = []
    def first(ctx):
        ctx.effect("submit", {"input": 1}, lambda: effects.append(1) or {"upstream_id": "one"}, replay_safe=True)
        return StageResult(status="running", checkpoint={"upstream_id": "one"}, poll_after_seconds=0)
    assert ProductionWorker(repo, store, {"story": first}).tick()
    def resumed(ctx):
        assert ctx.stage["checkpoint"] == {"upstream_id": "one"}
        assert ctx.effect("submit", {"input": 1}, lambda: pytest.fail("duplicate effect"), replay_safe=True) == {"upstream_id": "one"}
        return result(store, ctx.stage)
    assert ProductionWorker(repo, store, {"story": resumed}).tick()
    assert repo.get(job, "alice")["status"] == "succeeded"
    assert effects == [1]


def test_unknown_provider_result_cannot_be_blindly_retried(env):
    repo, store = env
    job = repo.create("alice", production_request(outputs=["script"]))
    calls = []
    def handle(ctx):
        def remote():
            calls.append(1)
            raise TimeoutError("sensitive provider body must not be logged")
        ctx.effect("provider", {}, remote, units=1)
    ProductionWorker(repo, store, {"story": handle}).tick()
    state = repo.get(job, "alice")
    assert state["status"] == "needs_input" and state["error_code"] == "EXTERNAL_RESULT_UNKNOWN"
    with pytest.raises(ProductionError, match="EXTERNAL_RESULT_UNKNOWN"):
        repo.resume(job, "alice", 1, {})
    assert calls == [1] and state["reserved_calls"] == 1


def test_pending_non_idempotent_call_after_crash_requires_reconciliation(env):
    repo, store = env
    repo.create("alice", production_request(outputs=["script"]))
    old = repo.claim("dead-worker")
    repo.reserve_call(old, "story:provider", fingerprint({}), 1)
    with repo.transaction() as db:
        db.execute("UPDATE stages SET lease_until=now()-interval '1 second'")
    new = repo.claim("replacement")
    ctx = StageContext(repo, store, new)
    with pytest.raises(ProductionError, match="EXTERNAL_RESULT_UNKNOWN"):
        ctx.effect("provider", {}, lambda: pytest.fail("must not submit again"), units=1)


def test_budget_is_atomic_and_successful_results_reused(env):
    repo, store = env
    job = repo.create("alice", production_request(budget={"max_provider_calls": 1}))
    stage = repo.claim("one")
    ctx = StageContext(repo, store, stage)
    assert ctx.effect("first", {}, lambda: {"ok": True}, units=1) == {"ok": True}
    assert ctx.effect("first", {}, lambda: pytest.fail("duplicate"), units=1) == {"ok": True}
    with pytest.raises(ProductionError, match="CALL_BUDGET_EXCEEDED"):
        ctx.effect("second", {}, lambda: pytest.fail("over budget"), units=1)
    assert repo.get(job, "alice")["actual_calls"] == 1


def test_cancel_and_deadline_do_not_remain_running(env):
    repo, _ = env
    job = repo.create("alice", production_request())
    stage = repo.claim("dead-worker")
    repo.cancel(job, "alice")
    with repo.transaction() as db:
        db.execute("UPDATE stages SET lease_until=now()-interval '1 second' WHERE job_id=%s", (job,))
    assert repo.claim("replacement") is None
    assert repo.get(job, "alice")["status"] == "cancelled"
    other = repo.create("alice", production_request(idempotency_key="deadline-test"))
    with repo.transaction() as db:
        db.execute("UPDATE jobs SET deadline=now()-interval '1 second' WHERE id=%s", (other,))
    assert repo.claim("worker") is None
    assert repo.get(other, "alice")["error_code"] == "DEADLINE_EXCEEDED"


@pytest.mark.parametrize("condition,code", [("cancel", "CANCEL_REQUESTED"), ("deadline", "DEADLINE_EXCEEDED"), ("lease", "LEASE_LOST")])
def test_running_child_guard_observes_database_state(env, condition, code):
    repo, _ = env
    job = repo.create("alice", production_request(outputs=["script"]))
    stage = repo.claim("active-worker")
    repo.check_active(stage)
    if condition == "cancel":
        repo.cancel(job, "alice")
    else:
        with repo.transaction() as db:
            if condition == "deadline":
                db.execute("UPDATE jobs SET deadline=now()-interval '1 second' WHERE id=%s", (job,))
            else:
                db.execute("UPDATE stages SET lease_until=now()-interval '1 second' WHERE job_id=%s", (job,))
    with pytest.raises(ProductionError, match=code):
        repo.check_active(stage)


def test_partial_outputs_retained_and_resume_does_not_redo_success(env):
    repo, store = env
    job = repo.create("alice", production_request(outputs=["audio"]))
    story = repo.claim("w")
    repo.finish(story, result(store, story))
    audio = repo.claim("w")
    repo.finish(audio, StageResult(status="failed", error_code="TEST_FAILURE"))
    state = repo.get(job, "alice")
    assert state["status"] == "failed" and len(state["artifacts"]) == 2
    with pytest.raises(ProductionError, match="RESUME_CONFLICT"):
        repo.resume(job, "alice", 2, {})
    repo.resume(job, "alice", 1, {})
    assert repo.claim("replacement")["name"] == "audio"


def test_versions_artifact_coverage_and_hash_are_enforced(env):
    repo, store = env
    repo.create("alice", production_request(outputs=["audio"]))
    story = repo.claim("w")
    wrong = result(store, story)
    wrong.artifacts = wrong.artifacts[:1]
    with pytest.raises(ProductionError, match="REQUIRED_ARTIFACT_MISSING"):
        repo.finish(story, wrong)
    repo.finish(story, result(store, story))
    audio = repo.claim("w")
    with pytest.raises(ProductionError, match="SOURCE_VERSION_MISMATCH"):
        repo.finish(audio, result(store, audio, "other-version"))
    ref = result(store, audio).artifacts[0]
    (store.root / ref.sha256).write_bytes(b"tampered")
    with pytest.raises(ProductionError, match="ARTIFACT_MISSING|ARTIFACT_HASH_MISMATCH"):
        store.verify(ref)


def test_visual_failure_does_not_stop_independent_audio(env):
    repo, store = env
    job = repo.create("alice", production_request(outputs=["visual"]))
    story = repo.claim("story")
    repo.finish(story, result(store, story))
    audio = repo.claim("audio")
    visual = repo.claim("visual")
    assert audio["name"] == "audio" and visual["name"] == "visual_plan"
    repo.finish(visual, StageResult(status="failed", error_code="VISUAL_CAPABILITY_MISSING"))
    assert repo.get(job, "alice")["status"] == "running"
    # Recovery of the independent branch after its worker disappears.
    with repo.transaction() as db:
        db.execute("UPDATE stages SET lease_until=now()-interval '1 second' WHERE job_id=%s AND name='audio'", (job,))
    replacement = repo.claim("replacement")
    assert replacement["name"] == "audio"
    repo.finish(replacement, result(store, replacement))
    state = repo.get(job, "alice")
    assert state["status"] == "failed" and state["error_code"] == "VISUAL_CAPABILITY_MISSING"
    assert "AudioMix" in {ref["kind"] for ref in state["artifacts"]}


def test_api_gets_are_pure_and_owner_scoped(env):
    repo, store = env
    app = FastAPI()
    def owner(request):
        value = request.headers.get("x-test-owner")
        if not value:
            raise HTTPException(401)
        return value
    register_production_routes(app, repo, store, owner, lambda: {"fixture_only": True})
    c = TestClient(app, headers={"x-test-owner": "alice"})
    response = c.post("/api/v1/production-jobs", json=production_request().model_dump())
    assert response.status_code == 202
    job = response.json()["data"]["id"]
    for _ in range(3):
        assert c.get("/api/v1/production-jobs/" + job).json()["data"]["status"] == "queued"
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) n FROM calls").fetchone()["n"] == 0
        assert db.execute("SELECT sum(attempts) n FROM stages").fetchone()["n"] == 0
    assert c.get("/api/v1/production-jobs/" + job, headers={"x-test-owner": "bob"}).status_code == 404
    assert c.post("/api/v1/production-jobs/" + job + "/cancel").json()["data"]["status"] == "cancelled"


def test_heartbeat_keeps_slow_handler_claim_exclusive(env):
    repo, store = env
    repo.create("alice", production_request(outputs=["script"]))
    entered = threading.Event()
    release = threading.Event()
    def slow(ctx):
        entered.set()
        release.wait(5)
        return result(store, ctx.stage)
    worker = ProductionWorker(repo, store, {"story": slow}, lease_seconds=1)
    thread = threading.Thread(target=worker.tick)
    thread.start()
    assert entered.wait(3)
    time.sleep(1.3)
    assert repo.claim("competitor") is None
    release.set()
    thread.join(5)
    assert not thread.is_alive()
