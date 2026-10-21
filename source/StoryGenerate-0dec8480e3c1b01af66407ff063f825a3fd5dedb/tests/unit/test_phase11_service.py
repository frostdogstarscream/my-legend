"""HTTP/consumer unit contracts with explicit scripted models, not live evidence."""

import json, sys, uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from drama_engine.persistence.models import Base, StoryJobModel, StoryVersionModel
from drama_engine.service.api import create_app
from drama_engine.service.config import ServiceSettings, redact
from drama_engine.service.worker import DurableWorker
from drama_engine.service.runtime import usage
from drama_engine.service.downstream import rendering_manifest, SimulatedTTSConsumer
from drama_engine.workflow.repository import SqlAlchemyWorkflowRepository
from drama_engine.workflow.adapters import (
    ApprovedPlanLLMExecutor,
    DeterministicPerformanceAnnotator,
)
from test_story_operations import ScriptedProvider, create, setup
from test_phase10_workflow import FixedPlanGenerator


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    settings = ServiceSettings(
        "sqlite://",
        {
            "unit-admin": {"principal_id": "local", "admin": True},
            "unit-other": {"principal_id": "other"},
        },
        testing=True,
    )
    app = create_app(settings, engine)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer unit-admin"
        yield client, engine, settings
    engine.dispose()


def worker(engine, settings):
    return DurableWorker(
        engine,
        settings,
        provider_factory=ScriptedProvider,
        planner_factory=lambda p: FixedPlanGenerator(),
        executor_factory=lambda p: ApprovedPlanLLMExecutor(
            p, DeterministicPerformanceAnnotator()
        ),
    )


def request():
    return {
        "intent": "create",
        "request_id": uuid.uuid4().hex,
        "idempotency_key": uuid.uuid4().hex,
        "user_instruction": "修复广播站",
        "creation_preferences": {"target_episodes": 2, "target_duration_sec": 120},
    }


def prepared(client, engine, settings):
    value = request()
    task = client.post("/v1/plans", json=value)
    assert task.status_code == 202, task.text
    assert task.json()["status"] == "QUEUED"
    assert worker(engine, settings).run_once()
    result = client.get("/v1/tasks/" + task.json()["task_id"]).json()
    assert result["status"] == "SUCCEEDED", result
    plan = client.get("/v1/plans/" + result["result"]["plan_id"]).json()
    return value, task.json(), plan


def approve(client, plan):
    return client.post(
        "/v1/plans/" + plan["plan_id"] + "/approve",
        json={
            "expected_revision": plan["plan_revision"],
            "fingerprint": plan["plan_fingerprint"],
            "idempotency_key": uuid.uuid4().hex,
        },
    )


def test_http_async_approval_generation_idempotency_and_exports(api):
    client, engine, settings = api
    value, task, plan = prepared(client, engine, settings)
    again = client.post("/v1/plans", json=value)
    assert again.json()["task_id"] == task["task_id"]
    assert client.post("/v1/plans/" + plan["plan_id"] + "/generate").status_code == 409
    with Session(engine) as s:
        assert s.query(StoryJobModel).count() == 0
    response = approve(client, plan)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "QUEUED"
    assert worker(engine, settings).run_once()
    restored = client.get("/v1/jobs/" + job["job_id"]).json()
    assert restored["status"] == "SUCCEEDED", restored
    assert (
        restored["audio_state"] == "NOT_SYNTHESIZED"
        and restored["playback_state"] == "NOT_REPORTED"
    )
    version = restored["result_story_version_id"]
    d = client.get("/v1/story-versions/" + version).json()
    assert client.get("/v1/story-versions/" + version + "/export").json() == d
    assert (
        client.get(
            "/v1/story-versions/" + version + "/export?format=script"
        ).status_code
        == 200
    )
    assert client.get(
        "/v1/story-versions/" + version + "/export?format=characters"
    ).json()
    manifest = client.get("/v1/story-versions/" + version + "/render-manifest")
    assert manifest.status_code == 200, manifest.text
    simulation = client.post("/v1/story-versions/" + version + "/simulate-tts").json()
    assert (
        simulation["simulation_only"] is True
        and simulation["audio_state"] == "NOT_SYNTHESIZED"
    )
    assert "input_snapshot" not in restored and "execution_token" not in restored


def test_ownership_auth_and_idempotency_conflict(api):
    client, engine, settings = api
    value, task, plan = prepared(client, engine, settings)
    changed = {**value, "user_instruction": "另一件事"}
    assert client.post("/v1/plans", json=changed).status_code == 409
    client.headers["Authorization"] = "Bearer unit-other"
    assert client.get("/v1/tasks/" + task["task_id"]).status_code == 404
    assert client.get("/v1/plans/" + plan["plan_id"]).status_code == 404
    assert approve(client, plan).status_code == 404
    client.headers.pop("Authorization")
    assert client.post("/v1/plans", json=value).status_code == 401
    assert client.get("/health").status_code == 200


def test_catalog_import_version_fixed_facts_runtime_and_bindings(api):
    client, engine, settings = api
    data = json.loads(
        (
            Path(__file__).resolve().parents[2] / "examples/phase11/characters.json"
        ).read_text(encoding="utf-8")
    )
    imported = client.post("/v1/characters/import", json=data)
    assert imported.status_code == 200, imported.text
    assert client.post("/v1/characters/import", json=data).json() == imported.json()
    altered = json.loads(json.dumps(data))
    altered["characters"][0]["expected_version"] = 1
    altered["characters"][0]["canon"]["identity"] = "记者"
    assert client.post("/v1/characters/import", json=altered).status_code == 409
    runtime = client.patch(
        "/v1/characters/linlan/runtime",
        json={"expected_revision": 0, "patch": {"goal": "完成修复"}},
    )
    assert runtime.status_code == 200 and runtime.json()["revision"] == 1
    assert (
        client.patch(
            "/v1/characters/linlan/runtime",
            json={"expected_revision": 1, "patch": {"identity": "记者"}},
        ).status_code
        == 422
    )
    value = request()
    value["characters"] = {
        "selected_character_ids": ["linlan"],
        "role_bindings": [
            {
                "character_id": "linlan",
                "role_id": "hero",
                "performer_id": "voice-linlan",
            }
        ],
    }
    task = client.post("/v1/plans", json=value)
    assert task.status_code == 202, task.text
    assert worker(engine, settings).run_once()
    plan_id = client.get("/v1/tasks/" + task.json()["task_id"]).json()["result"][
        "plan_id"
    ]
    plan = client.get("/v1/plans/" + plan_id).json()
    snapshot = plan["character_snapshots"][0]
    assert (
        snapshot["role_id"] == "hero"
        and snapshot["performer_id"] == "voice-linlan"
        and snapshot["profile_revision"] == 1
    )
    assert snapshot["profile"]["asset_refs"]["voice"]["asset_id"] == "linlan-voice"
    client.patch(
        "/v1/characters/linlan/runtime",
        json={"expected_revision": 1, "patch": {"goal": "新目标"}},
    )
    assert approve(client, plan).status_code == 409
    client.headers["Authorization"] = "Bearer unit-other"
    assert client.post("/v1/characters/import", json=data).status_code == 403


def test_plan_revision_cancel_and_retry(api):
    client, engine, settings = api
    _, _, plan = prepared(client, engine, settings)
    body = {
        "expected_revision": 1,
        "feedback": "增强行动",
        "idempotency_key": uuid.uuid4().hex,
    }
    response = client.post("/v1/plans/" + plan["plan_id"] + "/revisions", json=body)
    assert response.status_code == 202, response.text
    worker(engine, settings).run_once()
    assert client.get("/v1/plans/" + plan["plan_id"]).json()["plan_revision"] == 2
    assert (
        client.post("/v1/plans/" + plan["plan_id"] + "/revisions", json=body).json()[
            "task_id"
        ]
        == response.json()["task_id"]
    )
    assert approve(client, plan).status_code == 409
    queued = client.post("/v1/plans", json=request()).json()
    assert (
        client.post("/v1/tasks/" + queued["task_id"] + "/cancel").json()["status"]
        == "CANCELLED"
    )
    assert client.post("/v1/tasks/" + queued["task_id"] + "/retry").status_code == 409


def test_downstream_preserves_original_annotations():
    app, _ = setup()
    d = create(app)
    m = rendering_manifest(d)
    original = [u for e in d.episodes for s in e.scenes for u in s.utterances]
    assert len(m.tasks) == len(original)
    for task, u in zip(m.tasks, original):
        assert task.text == u.text and task.performer_id == u.performer_id
        assert task.performance["emphasis"] == u.model_dump(mode="json")["emphasis"]
        assert task.performance["tone_instruction"] == u.tone_instruction
    assert rendering_manifest(d) == m
    assert SimulatedTTSConsumer().consume(m)["playback_state"] == "NOT_REPORTED"


def test_token_accounting_does_not_repeat_reasoning_or_duplicate_calls():
    calls = [
        {
            "call_id": "one",
            "usage_source": "reported",
            "input_tokens": 10,
            "output_tokens": 20,
            "reasoning_tokens": 15,
            "cache_read_tokens": 3,
            "node": "writer",
        },
        {"call_id": "failed", "usage_source": "unavailable", "node": "writer"},
    ]
    result = usage(calls + calls)
    assert (
        result["calls"] == 2 and result["input_tokens"] + result["output_tokens"] == 30
    )
    assert (
        result["reasoning_tokens_in_output"] == 15
        and result["unknown_usage_calls"] == 1
    )


def test_service_production_configuration_has_no_mock_or_sqlite_fallback():
    with pytest.raises(ValueError, match="POSTGRESQL"):
        ServiceSettings("sqlite://", {"test": {"principal_id": "x"}}).validate()
    with pytest.raises(ValueError, match="AUTH"):
        ServiceSettings("postgresql://localhost/x", {}).validate()


def test_retry_accounting_keeps_failure_records_without_double_counting():
    from drama_engine.service.runtime import merge_usage, remember_attempt

    one = {
        "call_id": "first",
        "node": "writer",
        "usage_source": "reported",
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 3,
    }
    two = {
        "call_id": "second",
        "node": "writer",
        "usage_source": "reported",
        "input_tokens": 20,
        "output_tokens": 8,
        "reasoning_tokens": 4,
    }
    previous = remember_attempt(
        merge_usage({}, [one]), "FAILED", "TEST_FAILURE", "explicit fixture failure"
    )
    merged = merge_usage(previous, [two])
    again = merge_usage(merged, [two])
    assert (
        again["calls"] == 2
        and again["input_tokens"] == 30
        and again["output_tokens"] == 13
    )
    assert again["attempt_history"][0]["usage"]["calls"] == 1
    assert again["reasoning_tokens_in_output"] == 7


def test_all_http_errors_use_one_envelope_and_schema_hides_inputs(api):
    client, _, _ = api
    missing = client.get("/v1/not-existing")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
    bad = client.post(
        "/v1/plans", json={"intent": "unknown", "private_value": "do-not-echo"}
    )
    assert bad.status_code == 422 and "do-not-echo" not in bad.text


def test_catalog_asset_update_versions_and_frozen_task_idempotency(api):
    client, engine, settings = api
    data = json.loads(
        (
            Path(__file__).resolve().parents[2] / "examples/phase11/characters.json"
        ).read_text(encoding="utf-8")
    )
    client.post("/v1/characters/import", json=data)
    value = request()
    value["characters"] = {
        "selected_character_ids": ["linlan"],
        "role_bindings": [
            {
                "character_id": "linlan",
                "role_id": "hero",
                "performer_id": "voice-linlan",
            }
        ],
    }
    queued = client.post("/v1/plans", json=value).json()
    client.patch(
        "/v1/characters/linlan/runtime",
        json={"expected_revision": 0, "patch": {"goal": "新任务"}},
    )
    assert client.post("/v1/plans", json=value).json()["task_id"] == queued["task_id"]
    updated = data["characters"][0]
    updated["expected_version"] = 1
    updated["assets"]["image"]["revision"] = "2"
    assert (
        client.post("/v1/characters/import", json={"characters": [updated]}).json()[0][
            "version"
        ]
        == 2
    )
    assert len(client.get("/v1/characters/linlan/versions").json()) == 2
    mismatched = request()
    mismatched["characters"] = {
        "selected_character_ids": ["linlan"],
        "role_bindings": [
            {"character_id": "linlan", "role_id": "hero", "performer_id": "wrong-voice"}
        ],
    }
    assert (
        client.post("/v1/plans", json=mismatched).json()["error"]["code"]
        == "CHARACTER_PERFORMER_CONFLICT"
    )
