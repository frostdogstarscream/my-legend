"""Run in the audio environment: actual pinned Pipeline cache/media, synthetic adapter."""
from hashlib import sha256
from pathlib import Path
import json
import socket
import time

import pytest

from my_legend_production.production.audio_cache import export_cache, load_cache, seed_cache


def test_native_request_identity_reuses_unchanged_audio_and_regenerates_changed_voice_or_candidate(tmp_path, monkeypatch):
    sf = pytest.importorskip("soundfile", reason="Run this native media test in the audio Python environment")
    np = pytest.importorskip("numpy")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "runtime/my-legend-production/dependencies/audio/pipeline/src"))
    from lingban_pipeline.graph import Pipeline
    calls = []
    class Audio:
        external = True
        def generate(self, payload, path):
            calls.append((payload["voice"], payload["media"].get("candidate_version", 1)))
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, .1*np.sin(np.arange(4800)*.12), 48000)
            return {"fixture_only": True}

    config = dict(sample_rate=48000, channels=1, call_budget=dict(max_calls=10))
    def task(voice, candidate=1):
        return dict(task_id=f"task-{voice}-{candidate}", block_id="line", source_version_id="v", artifact_revision=1,
            kind="speech", adapter="fixture_audio", candidate_version=candidate,
            payload=dict(provider={}, media={"candidate_version": candidate}, voice=voice, text="同一台词", instructions="平静"))
    original = tmp_path / "old"
    (original / "output").mkdir(parents=True)
    pipeline = Pipeline(original / "scenes/000/audio-workflow", audio_adapters={"fixture_audio": Audio()})
    try:
        first = pipeline.audio_task(dict(task=task("one"), config=config))["results"]["task-one-1"]
        assert first["status"] == "success", first
    finally:
        pipeline.close()

    binding = dict(source_version_id="v", delivery_sha256="d"*64, manifest_sha256="e"*64)
    export_cache(original, [dict(path=str(original / "scenes/000"))], binding, {})
    document, blobs = load_cache(original / "output/raw-cache.zip", binding)
    second = tmp_path / "new"
    seed_cache(second, document, blobs)
    pipeline = Pipeline(second, audio_adapters={"fixture_audio": Audio()})
    try:
        unchanged = pipeline.audio_task(dict(task=task("one"), config=config))["results"]["task-one-1"]
        assert unchanged["status"] == "success", unchanged
        assert len(calls) == 1
        assert unchanged["asset"]["processed"]["sha256"] == first["asset"]["processed"]["sha256"]
        changed_voice = pipeline.audio_task(dict(task=task("two"), config=config))["results"]["task-two-1"]
        changed_candidate = pipeline.audio_task(dict(task=task("one", 2), config=config))["results"]["task-one-2"]
        assert changed_voice["status"] == changed_candidate["status"] == "success"
        assert calls == [("one", 1), ("two", 1), ("one", 2)]
    finally:
        pipeline.close()


def test_full_audio_child_regenerates_one_event_and_remeasures_mix(tmp_path, monkeypatch):
    sf = pytest.importorskip("soundfile", reason="Run in the audio environment")
    np = pytest.importorskip("numpy")
    workspace = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(workspace / "runtime/my-legend-production/dependencies/audio/pipeline/src"))
    from lingban_pipeline.adapters import QianwenAudioTTS, QianwenSFX
    from my_legend_production.production.audio_job import run
    def deny(*args, **kwargs):
        raise AssertionError("Offline audio test attempted network access")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    calls = []
    def generate(self, payload, path):
        self.budget()
        calls.append(payload.get("text", payload.get("prompt")))
        frames = 9600 if payload["media"].get("candidate_version", 1) != 1 else 4800
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, .1*np.sin(np.arange(frames)*.12), 48000)
        return {"fixture_only": True}
    monkeypatch.setattr(QianwenAudioTTS, "generate", generate)
    monkeypatch.setattr(QianwenSFX, "generate", generate)
    examples = workspace / "source/my-legend-production/examples/source"
    delivery_path, manifest_path = examples / "story_delivery.json", examples / "render_manifest.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    request = dict(source_version_id=delivery["story_version_id"], delivery_path=str(delivery_path), manifest_path=str(manifest_path),
        delivery_sha256=sha256(delivery_path.read_bytes()).hexdigest(), manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        voices={u["speaker_role_id"] if u["kind"] == "dialogue" else u["narrator_id"]: "longanyang_v3.1"
            for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"] if u["kind"] in ("dialogue", "narration")})
    paths = workspace / "runtime/my-legend-production/workflows.json"
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        root.mkdir()
        (root / "control.json").write_text(json.dumps({"deadline_epoch": time.time()+120}), encoding="utf-8")
    run(old, paths, request)
    first = json.loads((old / "result.json").read_text(encoding="utf-8"))
    original_mix = (old / "output/mix.wav").read_bytes()
    first_calls = len(calls)
    event = next(dict(episode_id=ep["episode_id"], scene_id=sc["scene_id"], line_id=u["line_id"])
        for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"] if u["kind"] in ("dialogue", "narration"))
    request.update(audio_revision=dict(cache=dict(sha256=first["raw_cache_sha256"]), regenerate=[event]),
        audio_cache_path=str(old / "output/raw-cache.zip"), revision_nonce="second-job")
    run(new, paths, request)
    second = json.loads((new / "result.json").read_text(encoding="utf-8"))
    assert len(calls) == first_calls + 1
    assert second["provider_calls"] == 1 and second["unsettled_tasks"] == 0
    assert second["total_frames"] == first["total_frames"] + 4800
    assert second["audio_sha256"] != first["audio_sha256"]
    assert (old / "output/mix.wav").read_bytes() == original_mix
    timeline = json.loads((new / "output/timeline.json").read_text(encoding="utf-8"))
    assert timeline["audio_sha256"] == second["audio_sha256"]
    # A subsequent remake must preserve the first remake's candidate, not revert it.
    third_root = tmp_path / "third"
    third_root.mkdir()
    (third_root / "control.json").write_text(json.dumps({"deadline_epoch": time.time()+120}), encoding="utf-8")
    another = next(dict(episode_id=ep["episode_id"], scene_id=sc["scene_id"], line_id=u["line_id"])
        for ep in delivery["episodes"] for sc in ep["scenes"] for u in sc["utterances"]
        if u["kind"] in ("dialogue", "narration", "sfx") and u["line_id"] != event["line_id"])
    request.update(audio_revision=dict(cache=dict(sha256=second["raw_cache_sha256"]), regenerate=[another]),
        audio_cache_path=str(new / "output/raw-cache.zip"), revision_nonce="third-job")
    run(third_root, paths, request)
    third = json.loads((third_root / "result.json").read_text(encoding="utf-8"))
    assert len(calls) == first_calls + 2 and third["provider_calls"] == 1
    assert third["total_frames"] == second["total_frames"] + 4800
