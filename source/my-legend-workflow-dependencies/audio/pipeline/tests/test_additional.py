from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from lingban_pipeline.contracts import PerformanceDocument
from lingban_pipeline.delivery import verify_bundle
from lingban_pipeline.graph import Pipeline
from lingban_pipeline.io import digest, file_hash
from lingban_pipeline.media import mix, read
from lingban_pipeline.performance import design

from test_pipeline import SpyAudio, VisualDouble, approve, live_fixture, run


def test_same_role_identical_lines_keep_separate_event_tasks(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=2)
    script["events"][1].update(text=script["events"][0]["text"], role_id="role0")
    spy = SpyAudio()
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    state = p.graph.get_state(p.invocation("job")).values
    assert len(state["tasks"]) == len(state["assets"]) == 2
    assert len({t["task_id"] for t in state["tasks"]}) == 2
    # Distinct events may reuse identical raw audio without merging event identity.
    assert spy.calls == 1
    assert state["preview"]["checks"]["technical_passed"]
    p.close()


def test_processing_change_reuses_paid_raw_result(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=1)
    spy = SpyAudio()
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    old = p.status("job")["preview"]["bundle"]["mix_sha256"]
    p.resume("job", dict(decision="changes", bundle_sha256=p.status("job")["preview"]["bundle"]["bundle_sha256"], changes={"events": [{"block_id": "line0", "media": {"processing": {"gain_db": -6}}}]}))
    assert spy.calls == 1
    assert p.status("job")["preview"]["bundle"]["mix_sha256"] != old
    p.close()


def test_confirmation_can_correct_a_rejected_approval(tmp_path, make_project):
    script, cfg, base = make_project()
    e = script["events"][-1]
    e["keypoints"] = [dict(name="contact", frame=2400, sample_rate=48000, method="candidate", status="candidate")]
    e["timing"] = [dict(anchor="line0", anchor_point="start", self_point="contact", offset_ms=100)]
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    with pytest.raises(ValueError, match="Unconfirmed"):
        p.resume("job", approve(p))
    p.resume("job", approve(p, confirmed_points=["effect:contact"]))
    assert p.status("job")["status"] == "delivered"
    p.close()


def test_scene_default_and_explicit_relation_precedence(tmp_path, make_project):
    script, cfg, base = make_project(count=3, effects=False)
    cfg["scene_defaults"] = {"gap_ms": 100}
    cfg["scene_overrides"] = {"scene0": {"gap_ms": 250}}
    script["events"][2]["timing"] = [dict(anchor="line1", anchor_point="end", offset_ms=20)]
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    positions = {x["block_id"]: x for x in p.graph.get_state(p.invocation("job")).values["timeline"]["placements"]}
    assert positions["line1"]["start_frame"] - positions["line0"]["end_frame"] == 12000
    assert positions["line2"]["start_frame"] - positions["line1"]["end_frame"] == 960
    p.close()


def test_background_recipe_extends_and_duck_changes_only_mix(tmp_path, make_project):
    script, cfg, base = make_project(count=1, effects=False)
    script["events"].append(dict(block_id="room", scene_id="scene0", kind="ambience", text="环境持续", media={"strategy": "compose", "layers": [{"file": str(base / "local0.wav"), "repeat": 4}]}, timing=[dict(anchor=None, offset_ms=0)]))
    cfg["mix"]["duck"] = dict(enabled=True, gain_db=-12, attack_ms=0, release_ms=0)
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    state = p.graph.get_state(p.invocation("job")).values
    assert state["assets"]["room"]["processed"]["frames"] == 4 * state["assets"]["line0"]["processed"]["frames"]
    actual, _ = read(p.root / state["preview"]["checks"]["mix"]["file"])
    plain = tmp_path / "production/plain.wav"
    no_duck = copy.deepcopy(cfg)
    no_duck["mix"]["duck"]["enabled"] = False
    mix(p.root, state["timeline"], no_duck, plain)
    other, _ = read(plain)
    n = state["assets"]["line0"]["processed"]["frames"]
    assert np.abs(actual[:n]).max() < np.abs(other[:n]).max()
    assert np.allclose(actual[n:], other[n:], atol=2e-7)
    assert len(actual) == 4 * n
    p.close()


def test_role_voice_change_invalidates_only_that_roles_raw_audio(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=3)
    spy = SpyAudio()
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    voices = dict(cfg["role_voices"], role1="VoiceNew")
    p.resume("job", dict(decision="changes", bundle_sha256=p.status("job")["preview"]["bundle"]["bundle_sha256"], changes={"config": {"role_voices": voices}}))
    assert spy.calls == 4
    p.close()


def test_real_visual_adapter_contract_stale_input_fails(tmp_path, make_project):
    script, cfg, base = make_project()
    cfg["visual"] = dict(enabled=True)
    class WrongVisual(VisualDouble):
        def feedback(self, *args):
            return super().feedback(*args) | {"source_version_id": "stale"}
    p = Pipeline(tmp_path / "production", visual_adapter=WrongVisual())
    with pytest.raises(ValueError, match="different script"):
        p.run("job", script, cfg, base)
    assert not (p.root / "releases").exists()
    p.close()


def test_performance_document_has_four_levels_and_stress_not_spoken(make_project):
    script, cfg, base = make_project()
    script["events"][0]["performance"]["emphasis"] = ["见面"]
    perf = design(script, cfg)
    PerformanceDocument.model_validate(perf)
    assert perf["project"]["genre"] == "生活"
    assert perf["lines"]["line0"]["performance"]["emphasis"] == ["见面"]
    assert "entry" in perf["lines"]["line0"]["performance"]
    assert "release" in perf["lines"]["line0"]["performance"]
    assert "close" in perf["lines"]["line0"]["performance"]
    assert perf["lines"]["line0"]["text"] == script["events"][0]["text"]


def test_source_snapshot_tamper_blocks_publish(tmp_path, make_project):
    p = run(tmp_path / "production", make_project)
    bundle = p.status("job")["preview"]["bundle"]
    source = next(iter(bundle["source_files"]))
    (p.root / source).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        p.resume("job", approve(p))
    p.close()


def test_rerun_existing_thread_is_not_silent_resubmission(tmp_path, make_project):
    script, cfg, base = make_project()
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    with pytest.raises(ValueError, match="already exists"):
        p.run("job", script, cfg, base)
    p.close()


def test_no_fallback_on_capability_failure(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=1)
    spy = SpyAudio(failure=__import__("lingban_pipeline.registry", fromlist=["CapabilityError"]).CapabilityError)
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    assert spy.calls == 1
    assert not p.status("job")["preview"]["checks"]["technical_passed"]
    assert next(iter(p.graph.get_state(p.invocation("job")).values["failures"].values()))["status"] == "configuration_error"
    p.close()


def test_background_loop_until_measured_end_and_mix_revision(tmp_path, make_project):
    script, cfg, base = make_project(count=2, effects=False)
    script["events"].append(dict(block_id="room", scene_id="scene0", kind="ambience", text="持续环境", media={"file": str(base / "local0.wav"), "loop_until": "line1"}, timing=[dict(anchor=None, offset_ms=0)]))
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    state = p.graph.get_state(p.invocation("job")).values
    target = next(x for x in state["timeline"]["placements"] if x["block_id"] == "line1")["end_frame"]
    assert state["assets"]["room"]["processed"]["frames"] == target
    assert state["preview"]["checks"]["technical_passed"]
    original_sha = state["assets"]["room"]["processed"]["sha256"]
    p.resume("job", dict(decision="changes", bundle_sha256=p.status("job")["preview"]["bundle"]["bundle_sha256"], changes={"config": {"mix": {"master_gain_db": -3}}}))
    state = p.graph.get_state(p.invocation("job")).values
    assert state["assets"]["room"]["processed"]["sha256"] == original_sha
    assert state["preview"]["checks"]["technical_passed"]
    assert state["assets"]["room"]["processing"]["loop_parent"]["file"] in state["preview"]["bundle"]["media"]
    p.close()


def test_one_production_root_cannot_be_reused_for_other_thread(tmp_path, make_project):
    script, cfg, base = make_project()
    p = Pipeline(tmp_path / "production")
    p.run("job", script, cfg, base)
    with pytest.raises(ValueError, match="another production"):
        p.run("different-job", script, cfg, base)
    p.close()


def test_model_control_gap_does_not_submit(tmp_path, monkeypatch):
    from lingban_pipeline.adapters import QianwenTTS
    from lingban_pipeline.registry import CapabilityError
    monkeypatch.setenv("TEST_KEY", "test-value")
    calls = []
    monkeypatch.setattr("lingban_pipeline.adapters.websocket.create_connection", lambda *a, **k: calls.append(1))
    with pytest.raises(CapabilityError, match="unsupported"):
        QianwenTTS({"model": "qwen3-tts-instruct-flash-realtime", "credential_env": "TEST_KEY", "controls": ["instructions"]}).generate({"text": "对白", "voice": "ConfiguredVoice", "instructions": "自然", "required_controls": ["word_timestamps"]}, tmp_path / "none.wav")
    assert not calls


def test_context_change_invalidates_only_actual_neighbor_dependencies(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=4)
    cfg["context_window"] = 1
    # Provided instructions are unchanged on neighboring lines; logical tasks change,
    # but identical actual raw requests can still reuse their previous media.
    for e in script["events"]:
        e["performance"]["instructions"] = "自然交谈，保持当前角色表达习惯。"
    spy = SpyAudio()
    p = Pipeline(tmp_path / "production", audio_adapters={"spy": spy})
    p.run("job", script, cfg, base)
    old = p.graph.get_state(p.invocation("job")).values["tasks"]
    p.resume("job", dict(decision="changes", bundle_sha256=p.status("job")["preview"]["bundle"]["bundle_sha256"], changes={"events": [{"block_id": "line1", "text": "修改的对白。"}]}))
    new = p.graph.get_state(p.invocation("job")).values["tasks"]
    old_ids = {t["block_id"]: t["task_id"] for t in old}
    changed = {t["block_id"] for t in new if old_ids[t["block_id"]] != t["task_id"]}
    assert changed == {"line0", "line1", "line2"}
    assert spy.calls == 5
    p.close()
