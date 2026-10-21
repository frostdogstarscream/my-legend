from __future__ import annotations

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

from lingban_pipeline import adapters
from lingban_pipeline.adapters import StructuredTextModel, QianwenSFX, QianwenTTS
from lingban_pipeline.content import import_handoff, normalize, revise
from lingban_pipeline.contracts import StandardScript
from lingban_pipeline.delivery import verify_bundle
from lingban_pipeline.graph import Pipeline, merge_results
from lingban_pipeline.io import digest, file_hash, load, safe_config, save
from lingban_pipeline.media import compose, measure, process, read, store_raw
from lingban_pipeline.registry import CapabilityError, Registry, TransientFailure, UnknownResult, verify_file
from lingban_pipeline.timeline import schedule


def approve(pipeline, thread="job", **kwargs):
    bundle = pipeline.status(thread)["preview"]["bundle"]
    return dict(decision="approve", reviewer="automated_test_only", scope=["dialogue", "performance", "sfx", "mix", "synchronization"], bundle_sha256=bundle["bundle_sha256"], **kwargs)


def run(root, make_project, **options):
    script, cfg, base = make_project(**options)
    pipeline = Pipeline(root)
    pipeline.run("job", script, cfg, base)
    return pipeline


@pytest.mark.parametrize("genre,count,scenes,effects", [("悬疑", 2, 1, True), ("治愈", 1, 1, False), ("喜剧", 7, 3, True), ("科幻", 4, 2, False)])
def test_generic_pipeline_dynamic_inputs(tmp_path, make_project, genre, count, scenes, effects):
    p = run(tmp_path / "production", make_project, count=count, genre=genre, scenes=scenes, effects=effects)
    state = p.graph.get_state(p.invocation("job")).values
    assert p.status("job")["status"] == "awaiting_review"
    assert len(state["assets"]) == count + int(effects)
    assert state["preview"]["checks"]["technical_passed"]
    assert len(state["timeline"]["subtitles"]) == count
    assert not (p.root / "releases").exists()
    assert state["timeline"]["visual"]["status"] == "not_required"
    for pos in state["timeline"]["placements"]:
        if pos.get("file"):
            a, rate = read(p.root / pos["file"])
            assert len(a) == pos["end_frame"] - pos["start_frame"]
            assert rate == 48000
    p.close()


def test_sqlite_resume_publish_and_immutable_baseline(tmp_path, make_project):
    root = tmp_path / "production"
    p = run(root, make_project)
    decision = approve(p)
    expected = p.status("job")["preview"]["bundle"]["mix_sha256"]
    p.close()
    p = Pipeline(root)
    p.resume("job", decision)
    delivered = p.status("job")
    assert delivered["status"] == "delivered"
    package = root / "releases" / delivered["delivery"]["release_id"]
    assert file_hash(package / "final.wav") == expected
    assert load(package / "timeline.json")["stage"] == "audio_locked"
    for file, sha in delivered["delivery"]["files"].items():
        assert file_hash(package / file) == sha
    assert not (root / "drafts/r1/review.json").exists()
    p.close()


def test_cancel_has_no_release(tmp_path, make_project):
    p = run(tmp_path / "production", make_project)
    p.resume("job", dict(decision="cancel", bundle_sha256=p.status("job")["preview"]["bundle"]["bundle_sha256"]))
    assert p.status("job")["status"] == "cancelled"
    assert not (p.root / "releases").exists()
    p.close()


def test_stale_approval_rejected(tmp_path, make_project):
    p = run(tmp_path / "production", make_project)
    decision = approve(p)
    decision["bundle_sha256"] = "wrong-version"
    with pytest.raises(ValueError, match="bound"):
        p.resume("job", decision)
    assert not (p.root / "releases").exists()
    p.close()


def test_artifact_tampering_blocks_publication(tmp_path, make_project):
    p = run(tmp_path / "production", make_project)
    (p.root / "drafts/r1/prompts.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        p.resume("job", approve(p))
    assert not (p.root / "releases").exists()
    p.close()


def test_missing_speaker_requires_input_then_resumes(tmp_path, make_project):
    script, cfg, base = make_project(effects=False)
    bad = copy.deepcopy(script)
    bad["events"][0]["role_id"] = None
    p = Pipeline(tmp_path / "production")
    p.run("job", bad, cfg, base)
    assert p.status("job")["next"] == ["input_review"]
    assert not (p.root / "assets").exists()
    p.resume("job", {"input_script": script})
    assert p.status("job")["status"] == "awaiting_review"
    p.close()


def test_task_merge_is_deterministic_and_rejects_conflicts():
    assert merge_results({"a": {"x": 1}}, {"a": {"x": 1}, "b": {"x": 2}}) == {"a": {"x": 1}, "b": {"x": 2}}
    with pytest.raises(ValueError, match="Conflicting"):
        merge_results({"a": {"x": 1}}, {"a": {"x": 2}})


def test_budget_overrun_does_not_cut(tmp_path, make_project):
    script, cfg, base = make_project()
    cfg["budget_ms"] = 10
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    state = p.graph.get_state(p.invocation("job")).values
    assert state["timeline"]["budget"]["excess_ms"] > 0
    assert state["preview"]["checks"]["technical_passed"]
    assert state["preview"]["checks"]["mix"]["frames"] == state["timeline"]["total_frames"]
    p.close()


@pytest.mark.parametrize("point,code", [("contact", "missing_keypoint"), ("word:见面", "missing_word_position")])
def test_missing_anchor_point_blocks_release(tmp_path, make_project, point, code):
    script, cfg, base = make_project()
    script["events"][-1]["timing"] = [dict(anchor="line0", anchor_point=point, self_point="start", offset_ms=0, mode="equal", provenance="explicit")]
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    state = p.graph.get_state(p.invocation("job")).values
    assert any(i["code"] == code for i in state["timeline"]["issues"])
    assert not state["preview"]["checks"]["technical_passed"]
    with pytest.raises(ValueError, match="Technical"):
        p.resume("job", approve(p))
    p.close()


def test_internal_point_alignment_and_confirmation(tmp_path, make_project):
    script, cfg, base = make_project()
    e = script["events"][-1]
    e["keypoints"] = [dict(name="contact", frame=2400, sample_rate=48000, method="test_fixture", status="candidate", kind="contact")]
    e["timing"] = [dict(anchor="line0", anchor_point="start", self_point="contact", offset_ms=100, mode="equal", provenance="explicit")]
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    timeline = p.graph.get_state(p.invocation("job")).values["timeline"]
    effect = next(e for e in timeline["placements"] if e["block_id"] == "effect")
    assert effect["start_frame"] + 2400 == 4800
    assert p.status("job")["preview"]["bundle"]["required_point_confirmations"] == ["effect:contact"]
    with pytest.raises(ValueError, match="Unconfirmed"):
        p.resume("job", approve(p))
    p.close()


@pytest.mark.parametrize("relations,code", [
    ([dict(anchor=None, offset_ms=0), dict(anchor=None, offset_ms=100)], "timing_constraint_conflict"),
    ([dict(anchor="line0", anchor_point="end")], "dependency_cycle"),
    ([dict(anchor=None, offset_ms=-100)], "negative_start")
])
def test_constraints_fail_locatably(tmp_path, make_project, relations, code):
    script, cfg, base = make_project(effects=False)
    script["events"][0]["timing"] = relations
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    timeline = p.graph.get_state(p.invocation("job")).values["timeline"]
    assert any(i["code"] == code and i["block_id"] == "line0" for i in timeline["issues"])
    assert not p.status("job")["preview"]["checks"]["technical_passed"]
    p.close()


def test_overlap_and_explicit_pause_preserve_full_tail(tmp_path, make_project):
    script, cfg, base = make_project()
    script["events"][-1]["timing"] = [dict(anchor="line0", anchor_point="start", offset_ms=10)]
    script["events"].append(dict(block_id="rest", scene_id="scene0", kind="pause", duration_ms=500))
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    t = p.graph.get_state(p.invocation("job")).values["timeline"]
    effect = next(x for x in t["placements"] if x["block_id"] == "effect")
    rest = next(x for x in t["placements"] if x["block_id"] == "rest")
    assert effect["start_frame"] == 480
    assert rest["end_frame"] - rest["start_frame"] == 24000
    assert t["total_frames"] == max(x["end_frame"] for x in t["placements"])
    p.close()


def test_compose_repeat_gap_points_and_raw_lineage(tmp_path):
    path = tmp_path / "one.wav"
    sf.write(path, np.full(100, .1), 1000)
    root = tmp_path / "assets-root"
    result = compose(root, [dict(file=str(path), repeat=2, gap_ms=20, keypoints=[dict(name="tap", frame=10, sample_rate=1000, method="manual", status="confirmed")])], 48000, 1)
    assert result["frames"] == 10560
    assert [p["frame"] for p in result["keypoints"]] == [480, 6240]
    assert len(result["recipe"]) == 1
    verify_file(root, result)


def test_trim_preserves_or_rejects_points_and_remeasures(tmp_path):
    path = tmp_path / "trim.wav"
    sf.write(path, np.r_[np.zeros(100), np.full(100, .1), np.zeros(100)], 1000)
    root = tmp_path / "production"
    raw = store_raw(root, path)
    task = dict(task_id="task", block_id="event", payload={"keypoints": [dict(name="contact", frame=120, sample_rate=1000, method="manual", status="confirmed")], "processing": {}})
    cfg = dict(sample_rate=1000, channels=1, processing={"trim_enabled": True, "trim_confirmed": True, "padding_ms": 0})
    asset = process(root, task, raw, cfg)
    assert asset["processed"]["frames"] == 100
    assert asset["keypoints"][0]["frame"] == 20
    assert raw["frames"] == 300
    task["payload"]["keypoints"][0]["frame"] = 50
    with pytest.raises(ValueError, match="removed"):
        process(root, task, raw, cfg)
    cfg["processing"]["trim_confirmed"] = False
    with pytest.raises(ValueError, match="confirmation"):
        process(root, task, raw, cfg)


class SpyAudio:
    external = True
    def __init__(self, failure=None):
        self.calls, self.active, self.maximum, self.failure = 0, 0, 0, failure
        self.lock = threading.Lock()
    def generate(self, payload, destination):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        try:
            if self.failure:
                raise self.failure("test only")
            time.sleep(.02)
            destination.parent.mkdir(parents=True, exist_ok=True)
            sf.write(destination, np.full(4800, .01), 48000)
            return {"model": "test_double"}
        finally:
            with self.lock:
                self.active -= 1


def live_fixture(make_project, count=3):
    script, cfg, base = make_project(count=count, effects=False)
    for e in script["events"]:
        e["media"] = {}
    cfg["tts"] = dict(adapter="spy", model="test", concurrency=2)
    return script, cfg, base


def test_model_concurrency_cache_revision_and_resample(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project)
    spy = SpyAudio()
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    assert spy.calls == 3 and spy.maximum <= 2
    t1 = p.graph.get_state(p.invocation("job")).values["timeline"]
    bundle = p.status("job")["preview"]["bundle"]
    p.resume("job", dict(decision="changes", bundle_sha256=bundle["bundle_sha256"], changes={"config": {"mix": {"master_gain_db": -6}}}))
    assert spy.calls == 3
    t2 = p.graph.get_state(p.invocation("job")).values["timeline"]
    assert [(x["start_frame"], x["end_frame"]) for x in t1["placements"]] == [(x["start_frame"], x["end_frame"]) for x in t2["placements"]]
    bundle = p.status("job")["preview"]["bundle"]
    p.resume("job", dict(decision="changes", bundle_sha256=bundle["bundle_sha256"], changes={"events": [{"block_id": "line1", "text": "修改第二句。"}]}))
    assert spy.calls == 4
    assert (p.root / "drafts/r1/draft.wav").exists()
    bundle = p.status("job")["preview"]["bundle"]
    p.resume("job", dict(decision="changes", bundle_sha256=bundle["bundle_sha256"], changes={"events": [{"block_id": "line0", "media": {"candidate_version": 2}}]}))
    assert spy.calls == 5
    p.close()


def test_unknown_not_retried_and_recovery_resumes(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=1)
    spy = SpyAudio(UnknownResult)
    root = tmp_path / "production"
    p = Pipeline(root, audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    bundle = p.status("job")["preview"]["bundle"]
    failure = next(iter(p.graph.get_state(p.invocation("job")).values["failures"].values()))
    assert spy.calls == 1
    p.resume("job", dict(decision="retry_after_recovery", bundle_sha256=bundle["bundle_sha256"]))
    assert spy.calls == 1
    recovered = root / "recovered.wav"
    sf.write(recovered, np.full(4800, .01), 48000)
    p.registry.recover(failure["raw_task_id"], store_raw(root, recovered))
    bundle = p.status("job")["preview"]["bundle"]
    p.resume("job", dict(decision="retry_after_recovery", bundle_sha256=bundle["bundle_sha256"]))
    assert spy.calls == 1 and p.status("job")["preview"]["checks"]["technical_passed"]
    p.close()


def test_confirmed_transient_failure_has_three_attempts(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=1)
    spy = SpyAudio(TransientFailure)
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    assert spy.calls == 3
    p.close()


def test_registry_concurrent_dedup_and_crash_unknown(tmp_path):
    registry = Registry(tmp_path)
    calls = []
    def action():
        calls.append(1)
        time.sleep(.02)
        return {"ok": True}
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: registry.run("one", True, action, lambda _: None), range(4)))
    assert len(calls) == 1 and sum(v["cache_reused"] for v in values) == 3
    registry.set("crashed", "running", 1)
    with pytest.raises(UnknownResult):
        Registry(tmp_path).run("crashed", True, action, lambda _: None)
    assert len(calls) == 1


def test_cache_hash_mismatch_does_not_generate_again(tmp_path):
    path = tmp_path / "media.wav"
    sf.write(path, np.full(100, .01), 1000)
    root = tmp_path / "production"
    registry = Registry(root)
    calls = []
    def action():
        calls.append(1)
        return store_raw(root, path)
    result = registry.run("task", True, action, lambda r: verify_file(root, r))
    (root / result["file"]).write_bytes(b"altered")
    with pytest.raises(ValueError, match="mismatch"):
        registry.run("task", True, action, lambda r: verify_file(root, r))
    assert len(calls) == 1


def test_structured_text_two_repairs_max(monkeypatch):
    monkeypatch.setenv("TEST_TEXT_KEY", "test-value")
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    model = StructuredTextModel(dict(model="test", url="https://example.org/chat", credential_env="TEST_TEXT_KEY"))
    def invalid(value):
        raise ValueError("Invalid")
    with pytest.raises(CapabilityError, match="two correction"):
        model.generate("schema", invalid)
    assert len(calls) == 3


def test_sfx_adapter_separates_prompt_and_discards_download_url(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIO_KEY", "test-value")
    path = tmp_path / "valid.wav"
    sf.write(path, np.full(100, .01), 1000)
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["input"]["text_prompt"] == "关门，啪；无台词"
            return httpx.Response(200, json={"request_id": "request", "output": {"audio": {"url": "https://example.org/audio?signature=private"}}})
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=path.read_bytes())
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    result = QianwenSFX(dict(credential_env="TEST_AUDIO_KEY")).generate({"prompt": "关门，啪；无台词", "seed": 8}, tmp_path / "out.wav")
    assert len(calls) == 2 and "signature" not in json.dumps(result)
    assert file_hash(path) == file_hash(tmp_path / "out.wav")


def test_credentials_rejected_before_state(tmp_path, make_project):
    script, cfg, base = make_project()
    cfg["api_key"] = "sensitive"
    p = Pipeline(tmp_path / "production")
    with pytest.raises(ValueError, match="environment"):
        p.run("job", script, cfg, base)
    assert not p.graph.get_state(p.invocation("job")).values
    p.close()


def test_upstream_adapter_reuses_script(make_project):
    script, cfg, base = make_project()
    class Upstream:
        def script(self, request):
            assert request["input_kind"] == "idea"
            return script
    normalized, problems = normalize({"input_kind": "idea", "text": "想法"}, Upstream())
    assert normalized == script and not problems
    with pytest.raises(ValueError, match="upstream"):
        normalize({"input_kind": "story"})


class VisualDouble:
    def feedback(self, script, performance, timeline):
        return dict(source_version_id=script["source_version_id"], script_sha256=digest(script), timeline_version=timeline["timeline_version"], engine_executed=True, engine_id="test_double", execution_evidence={"test_only": True}, actions=[dict(block_id="act", duration_basis="measured", duration_ms=300, keypoints=[dict(name="touch", animation_frame=3, animation_fps=30, status="confirmed")])])


def test_visual_feedback_internal_equation_and_separate_coordinates(tmp_path, make_project):
    script, cfg, base = make_project()
    script["events"].append(dict(block_id="act", scene_id="scene0", kind="action", target="物体", text="接触", timing=[dict(anchor=None, offset_ms=1000)]))
    effect = next(e for e in script["events"] if e["block_id"] == "effect")
    effect["keypoints"] = [dict(name="contact", frame=2400, sample_rate=48000, method="manual", status="confirmed")]
    effect["timing"] = [dict(anchor="act", anchor_point="touch", self_point="contact")]
    cfg["visual"] = dict(enabled=True)
    p = Pipeline(tmp_path / "production", visual_adapter=VisualDouble())
    p.run("job", script, cfg, base)
    t = p.graph.get_state(p.invocation("job")).values["timeline"]
    placements = {e["block_id"]: e for e in t["placements"]}
    assert placements["effect"]["start_frame"] + 2400 == placements["act"]["start_frame"] + 4800 == 52800
    assert t["visual"]["actions"]["act"]["keypoints"][0]["original_coordinates"]["animation_frame"] == 3
    assert t["visual"]["engine_validation"] == "reported_executed"
    assert p.status("job")["preview"]["checks"]["technical_passed"]
    p.close()


def test_visual_neutral_does_not_claim_execution(tmp_path, make_project):
    script, cfg, base = make_project()
    cfg["visual"] = dict(enabled=True, delivery_mode="audiovisual")
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    assert p.status("job")["preview"]["checks"]["visual_validation"] == "unverified"
    with pytest.raises(ValueError, match="Audiovisual"):
        p.resume("job", approve(p))
    p.close()


def test_schema_rejects_unknown_ids_and_keeps_lineage(make_project):
    script, cfg, base = make_project()
    script["events"][0]["scene_id"] = "missing"
    with pytest.raises(ValueError, match="unknown scene"):
        StandardScript.model_validate(script)


def test_tts_adapter_spoken_text_separate_and_pcm_header(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIO_KEY", "test-value")
    sent = []
    events = iter([{"type": "session.created", "session": {"id": "sample"}}, {"type": "session.updated"}, {"type": "response.audio.delta", "delta": "AAABAAIAAwA="}, {"type": "session.finished"}])
    class Socket:
        def send(self, value): sent.append(json.loads(value))
        def recv(self): return json.dumps(next(events))
        def close(self): pass
    monkeypatch.setattr(adapters.websocket, "create_connection", lambda *a, **k: Socket())
    result = QianwenTTS(dict(model="qwen3-tts-instruct-flash-realtime", credential_env="TEST_AUDIO_KEY")).generate({"voice": "ConfiguredVoice", "text": "我没有说过。", "instructions": "重音放在‘没有’上。"}, tmp_path / "out.wav")
    assert sent[0]["session"]["instructions"] == "重音放在‘没有’上。"
    assert next(e for e in sent if e["type"] == "input_text_buffer.append")["text"] == "我没有说过。"
    assert sf.info(tmp_path / "out.wav").frames == 4
    assert result["session_id"] == "sample"
