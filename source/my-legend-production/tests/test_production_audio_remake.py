import json
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from my_legend_production.production.api import register_production_routes
from my_legend_production.production.audio import AudioStage
from my_legend_production.production.audio_cache import export_cache
from my_legend_production.production.contracts import ArtifactRef, RemakeAudioRequest, stage_graph
from my_legend_production.production.audio_remake import remake_request
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.story import StoryStage
from my_legend_production.production.worker import ProductionWorker
from test_production_audio import setup_audio, synthetic_audio
from test_production_story import advance
from test_production_queue import env


def cache_audio(root, request_path, timeout):
    synthetic_audio(root, request_path, timeout)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    binding = {key: request[key] for key in ("source_version_id", "delivery_sha256", "manifest_sha256", "voices")}
    cache = export_cache(root, [], binding, {})  # Explicit synthetic empty cache for API/ownership tests.
    path = root / "result.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    result["raw_cache_sha256"] = cache["sha256"]
    path.write_text(json.dumps(result), encoding="utf-8")
    return 0


@pytest.fixture
def original(env):
    repo, store = env
    identifier, worker = setup_audio(env, cache_audio)
    advance(worker, repo, 1)
    job = repo.get(identifier, "alice")
    assert job["status"] == "succeeded"
    refs = {r["kind"]: ArtifactRef.model_validate(r) for r in job["artifacts"]}
    delivery = store.json(refs["StoryDelivery"])
    episode = delivery["episodes"][0]
    scene = episode["scenes"][0]
    line = next(u for u in scene["utterances"] if u["kind"] in ("dialogue", "narration", "sfx"))
    body = RemakeAudioRequest(expected_revision=job["revision"], idempotency_key="remake-fixture-1",
        regenerate=[dict(episode_id=episode["episode_id"], scene_id=scene["scene_id"], line_id=line["line_id"])])
    return job, body, refs


def test_new_audio_job_preserves_story_and_history_without_reusing_timing_or_video(env, original):
    repo, store = env
    job, body, refs = original
    before = deepcopy(repo.get(job["id"], "alice"))
    request = remake_request(job, body, store)
    new_id = repo.create("alice", request)
    assert repo.create("alice", request) == new_id
    assert new_id != job["id"] and request.work_id == job["work_id"]
    assert stage_graph(request) == {"story": [], "audio": ["story"]}
    assert repo.get(new_id, "alice")["artifacts"] == []
    class NoStoryCalls:
        def call(self, *args):
            pytest.fail("An audio remake must not regenerate the story")
    def runner(root, request_path, timeout):
        child = json.loads(request_path.read_text(encoding="utf-8"))
        assert child["audio_revision"]["cache"]["sha256"] == refs["AudioCache"].sha256
        assert child["audio_revision"]["regenerate"] == [e.model_dump(exclude_none=True) for e in body.regenerate]
        return cache_audio(root, request_path, timeout)
    worker = ProductionWorker(repo, store, {"story": StoryStage(NoStoryCalls()),
        "audio": AudioStage(store.root.parent, store.root.parent, runner=runner)})
    advance(worker, repo, 2)
    current = repo.get(new_id, "alice")
    assert current["status"] == "succeeded", current
    assert current["source_version_id"] == job["source_version_id"]
    assert not any(ref["kind"] in ("Video", "PlaybackManifest", "VisualTrack") for ref in current["artifacts"])
    assert repo.get(job["id"], "alice") == before
    assert store.verify(refs["AudioMix"]).is_file()


def test_remake_rejects_no_change_stale_unknown_or_missing_cache(env, original):
    _, store = env
    job, body, refs = original
    with pytest.raises(ProductionError, match="NO_CHANGE"):
        remake_request(job, body.model_copy(update={"regenerate": []}), store)
    for changed, error in ((job | {"revision": job["revision"] + 1}, "REVISION_CONFLICT"),
        (job | {"unsettled_calls": 1}, "SOURCE_NOT_READY"),
        (job | {"artifacts": [r for r in job["artifacts"] if r["kind"] != "AudioCache"]}, "CACHE_UNAVAILABLE")):
        with pytest.raises(ProductionError, match=error):
            remake_request(changed, body, store)
    with pytest.raises(ProductionError, match="VOICE_BINDING_REQUIRED"):
        remake_request(job, body.model_copy(update={"voices": {"unknown": "missing"}}), store)


def test_source_cache_requires_ownership_even_when_script_is_owned(env, original):
    repo, store = env
    job, body, refs = original
    request = remake_request(job, body, store)
    # A registered story does not authorize an unregistered replacement cache.
    forged = store.put("AudioCache", job["source_version_id"], b"unowned fixture", "application/zip")
    request.audio_revision.cache = forged
    with pytest.raises(ProductionError, match="SOURCE_ARTIFACT_NOT_OWNED"):
        repo.create("alice", request)


def test_remake_api_enforces_owner_and_idempotency(env, original):
    repo, store = env
    job, body, _ = original
    app = FastAPI()
    register_production_routes(app, repo, store, lambda req: req.headers.get("x-fixture-user", "alice"), lambda: {})
    client = TestClient(app)
    path = f"/api/v1/production-jobs/{job['id']}/remake-audio"
    assert client.post(path, json=body.model_dump(), headers={"x-fixture-user": "bob"}).status_code == 404
    first = client.post(path, json=body.model_dump())
    assert first.status_code == 202, first.text
    assert client.post(path, json=body.model_dump()).json()["data"]["id"] == first.json()["data"]["id"]
    visual = body.model_copy(update={"outputs": ["visual"], "idempotency_key": "remake-visual-1"})
    derived = remake_request(job, visual, store)
    assert set(stage_graph(derived)) == {"story", "audio", "visual_plan", "visual_render", "integrate"}
