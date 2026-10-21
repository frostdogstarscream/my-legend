"""Real native compiler and MP4 encoder, with explicitly synthetic source/audio/model fixtures."""
from hashlib import sha256
from pathlib import Path
import wave
import pytest

from my_legend_production.production.contracts import StageResult, ArtifactRef
from my_legend_production.production.worker import ProductionWorker
from my_legend_production.production.visual_catalog import catalog_from_paths
from my_legend_production.production.visual_plan import VisualPlanStage
from my_legend_production.production.visual_render import VisualRenderStage
from my_legend_production.production.integrate import IntegrateStage
from my_legend_production.production.playback import PlaybackManifest, playback_view
from test_production_queue import env
from test_production_contracts import production_request
from test_production_visual_context import sample
from test_production_visual_plan import Model


@pytest.mark.parametrize("segment_limit", [None, 2])
def test_worker_runs_all_media_stages_without_frontend_or_status_polling(env, tmp_path, segment_limit):
    repo, store = env
    workspace = Path(__file__).resolve().parents[3]
    paths = workspace / "runtime/my-legend-production/workflows.json"
    def catalog_loader(profile):
        value = catalog_from_paths(paths, profile)
        if segment_limit:
            value["capabilities"]["max_events"] = segment_limit
        return value
    catalog = catalog_loader("pixel_performance/1")
    delivery, context = sample()
    context["asset_catalog_sha256"] = catalog["catalog_sha256"]
    for c in delivery["character_snapshot"]:
        c["character_id"] = "character-" + c["role_id"]
    def story(ctx):
        return StageResult(status="succeeded", source_version_id="v", technical_quality="passed", artifacts=[
            store.put_json("StoryDelivery", "v", delivery), store.put_json("RenderManifest", "v", {"fixture_only": True})])
    def audio(ctx):
        path = tmp_path / "synthetic.wav"
        with wave.open(str(path), "wb") as f:
            f.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            f.writeframes(b"\x00\x00" * 144000)
        wav = path.read_bytes()
        events = [{"sequence": i+1, "episode_id": "ep", "scene_id": "room", "line_id": line["line_id"],
            "kind": line["kind"], "text": line["text"], "identity_id": line.get("speaker_role_id"),
            "start_frame": i*48000, "end_frame": (i+1)*48000,
            "duration_basis": "planned_silent_hold" if line["kind"] == "action" else "measured_audio", "source_audio_sha256": "a"*64}
            for i, line in enumerate(delivery["episodes"][0]["scenes"][0]["utterances"])]
        return StageResult(status="succeeded", source_version_id="v", technical_quality="passed", artifacts=[
            store.put("AudioMix", "v", wav, "audio/wav"), store.put_json("AudioReport", "v", {"fixture_only": True}),
            store.put_json("MeasuredTimeline", "v", {"source_version_id": "v", "sample_rate": 48000, "channels": 1,
                "total_frames": 144000, "audio_sha256": sha256(wav).hexdigest(), "events": events})])
    model = Model(context)
    worker = ProductionWorker(repo, store, {"story": story, "audio": audio,
        "visual_plan": VisualPlanStage(model, catalog_loader), "visual_render": VisualRenderStage(paths, catalog_loader),
        "integrate": IntegrateStage(store.root.parent, paths, workspace / "runtime/my-legend-production/.venv/Scripts/python.exe")})
    job = repo.create("alice", production_request(outputs=["visual"], visual_profile="pixel_performance/1"))
    for _ in range(5):
        assert worker.tick()
    state = repo.get(job, "alice")
    assert state["status"] == "succeeded", state
    refs = {r["kind"]: ArtifactRef.model_validate(r) for r in state["artifacts"]}
    track = store.json(refs["VisualTrack"])
    if segment_limit:
        assert len(track["segments"]) == 2
        assert track["segments"][0]["final_state"] == track["segments"][1]["initial_state"]
    playback = store.json(refs["PlaybackManifest"])
    assert PlaybackManifest.model_validate(playback).job_id == job
    view = playback_view(state, store)
    assert view["available_outputs"] == ["script", "audio", "visual"]
    assert view["manifest"] == playback
    assert playback["duration_seconds"] == 3
    assert playback["video"]["artifact"]["sha256"] == refs["Video"].sha256
    assert refs["Video"].size_bytes > 1000 and refs["Poster"].size_bytes > 1000
    report = store.json(refs["RenderReport"])
    assert report["decode_validation"] == "passed" and report["synthetic_audio"] is True
    assert report["human_review"] == "pending"
    assert len(model.calls) == 2
