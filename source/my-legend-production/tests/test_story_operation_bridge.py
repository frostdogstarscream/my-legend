from types import SimpleNamespace
from copy import deepcopy
from hashlib import sha256
import pytest
from pydantic import TypeAdapter, ValidationError
from drama_engine.workflow.schemas import StoryOperationRequest
from my_legend_production.production.contracts import ProductionRequest
from my_legend_production.production.story import plan_body, StoryStage
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.worker import ProductionWorker
from test_production_contracts import production_request
from test_production_queue import env
from test_production_story import StoryService, advance


def operation_source(refs, intent):
    source = dict(kind="operation", intent=intent, instruction="保留人物，让对话更自然",
                  source_version_id=refs["StoryDelivery"]["source_version_id"],
                  delivery=refs["StoryDelivery"], manifest=refs["RenderManifest"])
    if intent == "revise":
        source["edit"] = dict(scope="whole_story")
    if intent == "remix":
        source["remix"] = dict(retain_elements=["人物"], transform_elements=["场景"])
    return source


@pytest.mark.parametrize("intent", ["revise", "continue", "remix"])
def test_operation_ownership_submission_and_new_version(env, intent):
    repo, store = env
    client = StoryService()
    base = repo.create("alice", production_request(outputs=["script"]))
    worker = ProductionWorker(repo, store, {"story": StoryStage(client)})
    advance(worker, repo, 4)
    refs = {a["kind"]: a for a in repo.get(base, "alice")["artifacts"]}
    source = operation_source(refs, intent)
    request = production_request(idempotency_key="operation-test-" + intent, outputs=["script"],
                                 source=source, review_policy="listen_first")
    with pytest.raises(ProductionError, match="SOURCE_ARTIFACT_NOT_OWNED"):
        repo.create("bob", request)
    job = repo.create("alice", request)
    client.delivery = deepcopy(client.delivery)
    client.delivery.update(story_version_id="new-version", parent_story_version_id=source["source_version_id"])
    if intent == "remix":
        client.delivery["story_id"] = "new-remix-story"
    client.manifest = deepcopy(client.manifest)
    client.manifest.update(story_version_id="new-version")
    for task in client.manifest["tasks"]:
        task["task_id"] = sha256(f"new-version:{task['episode_id']}:{task['scene_id']}:{task['line_id']}".encode()).hexdigest()
    advance(worker, repo, 4)
    state = repo.get(job, "alice")
    assert state["status"] == "succeeded", state.get("error_code")
    assert state["source_version_id"] == "new-version"
    body = [body for method, path, body in client.calls if path == "/v1/plans"][-1]
    parsed = TypeAdapter(StoryOperationRequest).validate_python(body)
    assert parsed.intent == intent
    assert body["source_selector"]["story_version_id"] == source["source_version_id"]
    assert body["client_context"]["production"]["job_id"] == job
    if intent == "remix":
        assert body["characters"]["selected_character_ids"] == [c["character_id"] for c in client.delivery["character_snapshot"]]
        assert body["characters"]["role_bindings"]
    assert repo.get(base, "alice")["source_version_id"] == source["source_version_id"]


def test_wrong_operation_fields_rejected():
    ref = dict(kind="StoryDelivery", source_version_id="v1", sha256="a"*64, size_bytes=10)
    refs = {"StoryDelivery": ref, "RenderManifest": {**ref, "kind": "RenderManifest"}}
    for source in (operation_source(refs, "continue") | {"edit": {"scope": "whole_story"}},
                   operation_source(refs, "remix") | {"remix": None},
                   operation_source(refs, "revise") | {"source_version_id": "other"}):
        with pytest.raises(ValidationError):
            production_request(source=source, outputs=["script"])
