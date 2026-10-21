from copy import deepcopy

import pytest
from pydantic import ValidationError

from my_legend_production.production.playback import PlaybackManifest, playback_view
from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.repository import ProductionError


def manifest():
    value = {"job_id": "job-1", "work_id": "work-1", "source_version_id": "v1",
             "sample_rate": 48000, "total_frames": 144000, "duration_seconds": 3.0,
             "visual_profile": "pixel_performance/1"}
    for index, (name, kind, mime) in enumerate([
        ("script", "StoryDelivery", "application/json"), ("audio", "AudioMix", "audio/wav"),
        ("timeline", "MeasuredTimeline", "application/json"), ("video", "Video", "video/mp4"),
        ("poster", "Poster", "image/png")
    ]):
        digest = str(index) * 64
        value[name] = {"artifact": {"kind": kind, "sha256": digest, "source_version_id": "v1",
                                   "media_type": mime, "size_bytes": 100},
                       "content_path": f"/api/v1/production-jobs/job-1/artifacts/{digest}"}
    return value


def test_three_output_combinations_have_the_same_versioned_contract():
    full = manifest()
    audio = {k: v for k, v in full.items() if k not in ("video", "poster", "visual_profile")}
    script = {k: v for k, v in audio.items() if k not in ("audio", "timeline", "sample_rate", "total_frames", "duration_seconds")}
    for value in (script, audio, full):
        result = PlaybackManifest.model_validate(value)
        assert result.human_review == "pending"
        assert result.publication == "not_requested"


@pytest.mark.parametrize("slot", ["script", "audio", "timeline", "video", "poster"])
def test_every_result_must_belong_to_the_same_source_version(slot):
    value = manifest()
    value[slot]["artifact"]["source_version_id"] = "another-version"
    with pytest.raises(ValidationError, match="SOURCE_VERSION_MISMATCH"):
        PlaybackManifest.model_validate(value)


@pytest.mark.parametrize("path", ["https://media.invalid/file", "/api/v1/production-jobs/other/artifacts/" + "3" * 64,
    "/api/v1/production-jobs/job-1/artifacts/" + "3" * 64 + "?token=secret",
    "/api/v1/production-jobs/job-1/artifacts/" + "4" * 64])
def test_download_reference_cannot_escape_job_digest_or_embed_credentials(path):
    value = manifest()
    value["video"]["content_path"] = path
    with pytest.raises(ValidationError, match="PLAYBACK_CONTENT_PATH_MISMATCH"):
        PlaybackManifest.model_validate(value)


@pytest.mark.parametrize("change,error", [
    ({"duration_seconds": 2.9}, "PLAYBACK_DURATION_MISMATCH"),
    ({"sample_rate": None}, "PLAYBACK_TIMING_REQUIRED"),
    ({"timeline": None}, "PLAYBACK_AUDIO_TIMELINE_REQUIRED"),
    ({"poster": None}, "PLAYBACK_VIDEO_DEPENDENCIES_REQUIRED"),
    ({"video": None}, "PLAYBACK_VISUAL_WITHOUT_VIDEO"),
])
def test_incomplete_or_inconsistent_media_is_not_playable(change, error):
    with pytest.raises(ValidationError, match=error):
        PlaybackManifest.model_validate(manifest() | change)


def test_reject_disguised_artifact_and_unearned_approval():
    value = manifest()
    for field, replacement in (("kind", "AudioMix"), ("media_type", "text/html")):
        changed = deepcopy(value)
        changed["video"]["artifact"][field] = replacement
        with pytest.raises(ValidationError, match="PLAYBACK_ARTIFACT_KIND_MISMATCH"):
            PlaybackManifest.model_validate(changed)
    for change in ({"human_review": "approved"}, {"publication": "published"}, {"duration_seconds": float("nan")}):
        with pytest.raises(ValidationError):
            PlaybackManifest.model_validate(value | change)


def test_failed_visual_job_retains_readable_script_and_audio_without_mutation(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    script = store.put_json("StoryDelivery", "v1", {"fixture_only": True})
    audio = store.put("AudioMix", "v1", b"test-only-audio-reference", "audio/wav")
    timing = store.put_json("MeasuredTimeline", "v1", {
        "source_version_id": "v1", "sample_rate": 48000, "channels": 1, "total_frames": 48000,
        "audio_sha256": audio.sha256, "events": [{"sequence": 1, "episode_id": "ep", "scene_id": "sc",
        "line_id": "line", "kind": "narration", "text": "测试", "start_frame": 0, "end_frame": 48000,
        "duration_basis": "measured_audio", "source_audio_sha256": audio.sha256}]})
    job = {"id": "job-1", "work_id": "work-1", "source_version_id": "v1", "status": "failed",
           "artifacts": [ref.model_dump() for ref in (script, audio, timing)]}
    before = deepcopy(job)
    paths = set(tmp_path.rglob("*"))
    result = playback_view(job, store)
    assert result["job_status"] == "failed"
    assert result["available_outputs"] == ["script", "audio"]
    assert result["manifest"]["duration_seconds"] == 1
    assert job == before and set(tmp_path.rglob("*")) == paths
    job["artifacts"] = [script.model_dump()]
    assert playback_view(job, store)["available_outputs"] == ["script"]
    job["artifacts"] = []
    assert playback_view(job, store)["manifest"] is None
    job["artifacts"] = before["artifacts"]
    job["artifacts"][1]["sha256"] = "a" * 64
    with pytest.raises(ProductionError, match="PLAYBACK_TIMELINE_BINDING_MISMATCH"):
        playback_view(job, store)


def test_playback_endpoint_checks_owner_before_reading_artifacts(tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from my_legend_production.production.api import register_production_routes
    class Repository:
        def get(self, job_id, owner_id):
            if owner_id != "alice" or job_id != "job-1":
                raise ProductionError("JOB_NOT_FOUND")
            return {"id": job_id, "status": "queued", "artifacts": []}
    def owner(request):
        value = request.headers.get("authorization")
        if not value:
            raise HTTPException(401, "UNAUTHORIZED")
        return value
    app = FastAPI()
    register_production_routes(app, Repository(), ArtifactStore(tmp_path / "artifacts"), owner, lambda: {})
    client = TestClient(app)
    path = "/api/v1/production-jobs/job-1/playback"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"authorization": "bob"}).status_code == 404
    response = client.get(path, headers={"authorization": "alice"})
    assert response.status_code == 200
    assert response.json()["data"]["available_outputs"] == []
