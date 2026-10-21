"""HTTP + durable worker + PostgreSQL16; model doubles are explicitly scripted."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sys, uuid, time, threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import text
from drama_engine.persistence.models import (
    ServicePlanTaskModel,
    StoryJobModel,
    StoryVersionModel,
)
from drama_engine.service.config import ServiceSettings
from drama_engine.service.api import create_app
from drama_engine.service.runtime import (
    PlanningQueue,
    recover_expired,
    WorkerRepository,
    db_now,
)
from drama_engine.service.worker import DurableWorker
from drama_engine.workflow.repository import (
    SqlAlchemyWorkflowRepository,
    WorkflowConflictError,
)
from drama_engine.workflow.service import StoryWorkflowService
from drama_engine.workflow.adapters import (
    ApprovedPlanLLMExecutor,
    DeterministicPerformanceAnnotator,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_phase11_service import request, worker, prepared, approve
from test_story_operations import ScriptedProvider
from test_phase10_workflow import FixedPlanGenerator
from test_postgres_workflow import pg_engine

pytestmark = pytest.mark.postgres


@pytest.fixture
def http(pg_engine):
    settings = ServiceSettings(
        str(pg_engine.url),
        {
            "fixture-admin": {"principal_id": "tenant-a", "admin": True},
            "fixture-other": {"principal_id": "tenant-b"},
        },
        testing=True,
    )
    with TestClient(create_app(settings, pg_engine)) as client:
        client.headers["Authorization"] = "Bearer fixture-admin"
        yield client, pg_engine, settings


def test_http_restart_lineage_four_operations_and_fresh_sessions(http):
    client, engine, settings = http
    _, _, plan = prepared(client, engine, settings)
    job = approve(client, plan).json()
    assert worker(engine, settings).run_once()
    created = client.get(
        "/v1/story-versions/"
        + client.get("/v1/jobs/" + job["job_id"]).json()["result_story_version_id"]
    ).json()
    source = created
    outputs = [created]
    for intent in ("revise", "continue", "remix"):
        value = request()
        value.update(
            intent=intent,
            source_selector={
                "type": "by_id",
                "story_version_id": source["story_version_id"],
            },
        )
        if intent == "revise":
            value["edit"] = {
                "scope": "scene",
                "target": {
                    "episode_id": source["episodes"][0]["episode_id"],
                    "scene_id": source["episodes"][0]["scenes"][0]["scene_id"],
                },
                "instruction": "共同核对",
                "downstream_policy": "branch_from_here",
            }
        if intent == "continue":
            value["creation_preferences"] = {"target_episodes": 2}
        if intent == "remix":
            value["remix"] = {
                "instruction": "改变事故",
                "retain_elements": ["广播站"],
                "transform_elements": ["事故"],
            }
        task = client.post("/v1/plans", json=value).json()
        assert worker(engine, settings).run_once()
        completed = client.get("/v1/tasks/" + task["task_id"]).json()
        assert completed["status"] == "SUCCEEDED", completed
        p = client.get("/v1/plans/" + completed["result"]["plan_id"]).json()
        r = approve(client, p)
        assert r.status_code == 202, r.text
        assert worker(engine, settings).run_once()
        state = client.get("/v1/jobs/" + r.json()["job_id"]).json()
        assert state["status"] == "SUCCEEDED", state
        source = client.get(
            "/v1/story-versions/" + state["result_story_version_id"]
        ).json()
        outputs.append(source)
    assert outputs[2]["episodes"][:2] == outputs[1]["episodes"]
    assert outputs[1]["episodes"][1] == outputs[0]["episodes"][1]
    assert outputs[3]["story_id"] != outputs[2]["story_id"]
    assert outputs[3]["parent_story_version_id"] == outputs[2]["story_version_id"]
    assert (
        len(
            client.get(
                "/v1/story-versions/" + source["story_version_id"] + "/lineage"
            ).json()["versions"]
        )
        == 4
    )
    # New app and session factory read from SQL, without relying on the old process.
    with TestClient(create_app(settings, engine)) as restarted:
        restarted.headers["Authorization"] = "Bearer fixture-admin"
        assert (
            restarted.get("/v1/story-versions/" + source["story_version_id"]).json()
            == source
        )
        assert (
            restarted.post(
                "/v1/story-versions/" + source["story_version_id"] + "/simulate-tts"
            ).json()["audio_state"]
            == "NOT_SYNTHESIZED"
        )
    client.headers["Authorization"] = "Bearer fixture-other"
    assert (
        client.get("/v1/story-versions/" + source["story_version_id"]).status_code
        == 404
    )
    assert client.get("/v1/jobs/" + job["job_id"]).status_code == 404


def test_expired_generation_lease_fences_old_worker_and_requires_explicit_retry(http):
    client, engine, settings = http
    _, _, plan = prepared(client, engine, settings)
    job = approve(client, plan).json()
    owner = "interrupted-test-worker"
    with Session(engine) as s:
        repo = WorkerRepository(s, settings, owner)
        claimed = repo.claim_job(job["job_id"])
        s.commit()
        row = s.query(StoryJobModel).filter_by(job_id=claimed.job_id).first()
        row.lease_expires_at = db_now(s) - timedelta(seconds=1)
        s.commit()
    with Session(engine) as s:
        assert job["job_id"] in recover_expired(s)
        s.commit()
    state = client.get("/v1/jobs/" + job["job_id"]).json()
    assert state["status"] == "FAILED" and state["error_code"] == "WORKER_INTERRUPTED"
    assert state["usage"]["inflight_usage_unknown"] is True
    with Session(engine) as s:
        with pytest.raises(WorkflowConflictError, match="FENCED"):
            SqlAlchemyWorkflowRepository(s).save_job(claimed)
    assert client.post("/v1/jobs/" + job["job_id"] + "/retry").status_code == 202
    assert worker(engine, settings).run_once()
    assert client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
    with Session(engine) as s:
        assert s.query(StoryVersionModel).count() == 1


def test_planning_lease_uses_wall_clock_and_cancel_cannot_publish(http):
    client, engine, settings = http
    task = client.post("/v1/plans", json=request()).json()
    with Session(engine) as s:
        row = s.query(ServicePlanTaskModel).filter_by(task_id=task["task_id"]).first()
        row.status = "RUNNING"
        row.execution_token = "old"
        row.lease_owner = "old-worker"
        row.lease_expires_at = db_now(s) + timedelta(seconds=0.2)
        s.commit()
    with Session(engine) as s:
        # Keep this transaction open past the deadline: now() would remain stale.
        before = db_now(s)
        time.sleep(0.3)
        assert db_now(s) > before
        with pytest.raises(WorkflowConflictError, match="FENCED"):
            WorkerRepository(s, settings, "old-worker", task["task_id"], "old").task()
    with Session(engine) as s:
        recover_expired(s)
        s.commit()
    assert client.get("/v1/tasks/" + task["task_id"]).json()["status"] == "FAILED"
    assert client.post("/v1/tasks/" + task["task_id"] + "/retry").status_code == 202
    assert (
        client.post("/v1/tasks/" + task["task_id"] + "/cancel").json()["status"]
        == "CANCELLED"
    )
    assert not worker(engine, settings).run_once()


def test_atomic_workers_do_not_duplicate_jobs_and_running_cancel_no_version(http):
    client, engine, settings = http
    _, _, plan = prepared(client, engine, settings)
    job = approve(client, plan).json()
    started = threading.Event()
    release = threading.Event()

    class BlockingExecutor:
        def __init__(self, p):
            self.inner = ApprovedPlanLLMExecutor(p, DeterministicPerformanceAnnotator())

        def execute(self, snapshot):
            started.set()
            assert release.wait(10)
            return self.inner.execute(snapshot)

    slow = DurableWorker(
        engine,
        settings,
        ScriptedProvider,
        lambda p: FixedPlanGenerator(),
        BlockingExecutor,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(slow.run_once)
        assert started.wait(5)
        assert not worker(engine, settings).run_once()
        assert (
            client.post("/v1/jobs/" + job["job_id"] + "/cancel").json()["status"]
            == "CANCEL_REQUESTED"
        )
        release.set()
        assert future.result()
    assert client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "CANCELLED"
    with Session(engine) as s:
        assert s.query(StoryVersionModel).count() == 0


def test_failed_model_job_rolls_back_can_retry_and_approval_dedup_is_tenant_scoped(
    http,
):
    client, engine, settings = http
    _, _, plan = prepared(client, engine, settings)
    value = {
        "expected_revision": 1,
        "fingerprint": plan["plan_fingerprint"],
        "idempotency_key": "shared-client-key",
    }
    first = client.post("/v1/plans/" + plan["plan_id"] + "/approve", json=value)
    second = client.post("/v1/plans/" + plan["plan_id"] + "/approve", json=value)
    assert first.json()["job_id"] == second.json()["job_id"]

    class Reject:
        def __init__(self, p):
            pass

        def execute(self, snapshot):
            raise RuntimeError("explicit scripted provider failure")

    failing = DurableWorker(
        engine, settings, ScriptedProvider, lambda p: FixedPlanGenerator(), Reject
    )
    assert failing.run_once()
    assert client.get("/v1/jobs/" + first.json()["job_id"]).json()["status"] == "FAILED"
    with Session(engine) as s:
        assert s.query(StoryVersionModel).count() == 0
    assert (
        client.post("/v1/jobs/" + first.json()["job_id"] + "/retry").status_code == 202
    )
    assert worker(engine, settings).run_once()
    client.headers["Authorization"] = "Bearer fixture-other"
    _, _, other = prepared(client, engine, settings)
    value.update(fingerprint=other["plan_fingerprint"])
    assert (
        client.post(
            "/v1/plans/" + other["plan_id"] + "/approve", json=value
        ).status_code
        == 202
    )


def test_cancel_prevents_further_billed_requests(http):
    from types import SimpleNamespace
    from drama_engine.workflow.service import GenerationCancelled

    client, engine, settings = http
    _, _, plan = prepared(client, engine, settings)
    job = approve(client, plan).json()

    class NetworkLikeProvider:
        call_history = []
        called = 0

        def complete(self, *args, **kwargs):
            self.called += 1
            raise AssertionError("cancelled request must not be sent")

    provider = NetworkLikeProvider()
    w = DurableWorker(engine, settings, lambda: provider)
    with Session(engine) as s:
        claimed = WorkerRepository(s, settings, w.owner).claim_job(job["job_id"])
        s.commit()
    assert (
        client.post("/v1/jobs/" + job["job_id"] + "/cancel").json()["status"]
        == "CANCEL_REQUESTED"
    )
    wrapped = w.provider("job", job["job_id"], claimed.execution_token)
    with pytest.raises(GenerationCancelled):
        wrapped.complete(
            SimpleNamespace(role="writer", model="test-only"), "system", "user"
        )
    assert (
        provider.called == 0
        and client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "CANCELLED"
    )


def test_upgrade_existing003_preserves_versions_and_canonical_records(http):
    from alembic import command
    from alembic.config import Config

    client, engine, settings = http
    _, _, plan = prepared(client, engine, settings)
    job = approve(client, plan).json()
    worker(engine, settings).run_once()
    vid = client.get("/v1/jobs/" + job["job_id"]).json()["result_story_version_id"]
    before = client.get("/v1/story-versions/" + vid).json()
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    with engine.begin() as c:
        config.attributes["connection"] = c
        command.downgrade(config, "003")
    with engine.begin() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "head")
    assert client.get("/v1/story-versions/" + vid).json() == before


def test_killed_worker_requires_explicit_retry_without_orphan_plan(http):
    """Real process termination; deliberately blocked test planner, no live model."""
    import os
    import subprocess

    client, engine, settings = http
    task = client.post("/v1/plans", json=request()).json()
    with engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(root / "4-引擎代码"),
        STORY_DATABASE_URL=engine.url.render_as_string(hide_password=False),
        STORY_DATABASE_SCHEMA=schema,
        STORY_API_KEYS='{"subprocess-test":{"principal_id":"tenant-a"}}',
        STORY_WORKER_LEASE_SECONDS="2",
        STORY_WORKER_HEARTBEAT_SECONDS="0.2",
    )
    script = """import time
from drama_engine.service.config import ServiceSettings
from drama_engine.service.worker import DurableWorker
class Blocked:
    def generate(self, *args, **kwargs):
        time.sleep(300)
settings=ServiceSettings.from_env()
DurableWorker(settings.engine(),settings,provider_factory=lambda:object(),
    planner_factory=lambda p:Blocked(),executor_factory=lambda p:object()).run_once()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 20
        while client.get("/v1/tasks/" + task["task_id"]).json()["status"] != "RUNNING":
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        time.sleep(
            2.3
        )  # Beyond initial lease; heartbeat must renew while planner blocks.
        with Session(engine) as session:
            row = session.get(ServicePlanTaskModel, task["task_id"])
            assert row.lease_expires_at > db_now(session)
        process.kill()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    time.sleep(2.2)
    assert not worker(engine, settings).run_once()
    failed = client.get("/v1/tasks/" + task["task_id"]).json()
    assert failed["status"] == "FAILED" and failed["usage"]["inflight_usage_unknown"]
    assert client.post("/v1/tasks/" + task["task_id"] + "/retry").status_code == 202
    assert worker(engine, settings).run_once()
    assert client.get("/v1/tasks/" + task["task_id"]).json()["status"] == "SUCCEEDED"
