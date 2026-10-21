from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

from lingban_pipeline import adapters
from lingban_pipeline.adapters import QianwenAudioTTS, QianwenSFX, StructuredTextModel
from lingban_pipeline.budget import BudgetExceeded, CallBudget
from lingban_pipeline.content import normalize, revise
from lingban_pipeline.graph import Pipeline
from lingban_pipeline.instructions import InstructionLengthError, validate_instruction, weighted_length
from lingban_pipeline.io import digest, safe_config, sensitive_artifact_text, file_hash, save
from lingban_pipeline.media import effects, detect_onset, process, store_raw, read, mix, compose
from lingban_pipeline.performance import FIELDS, design, voice_instruction
from lingban_pipeline.registry import CapabilityError, UnknownResult
from lingban_pipeline.timeline import schedule
from lingban_pipeline.visual import obtain
from test_pipeline import SpyAudio, live_fixture


def test_weighted_budget_120_and_adapter_preflight():
    assert weighted_length("汉字A1，𠀀") == 9
    adapter = QianwenAudioTTS({"model": "qwen-audio-3.1-tts-flash"})
    payload = {"voice": "test", "text": "台词不变。", "instructions": "平" * 60, "instruction_max_weight": 120}
    assert adapter.request_plan(payload)["input"]["instruction"] == payload["instructions"]
    with pytest.raises(CapabilityError) as caught:
        adapter.request_plan(payload | {"instructions": "平" * 61})
    assert caught.value.diagnostics == {"category": "instruction_weight_exceeded", "weight": 122, "limit": 120}
    assert adapter.request_plan(payload | {"instructions": "平" * 61, "instruction_max_weight": 140})["input"]["instruction"]


def test_compaction_contains_rejected_candidate_and_never_changes_text(monkeypatch):
    monkeypatch.setenv("TEST_TEXT_KEY", "not-a-real-key")
    sent = []
    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        instruction = "压" * 70 if len(sent) == 1 else "先试探，再短促释放，最后压住尾音。"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"instruction": instruction}, ensure_ascii=False)}}]})
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    model = StructuredTextModel({"model": "test", "url": "https://example.org/text", "credential_env": "TEST_TEXT_KEY"})
    result = model.generate("Original spoken text is locked.", lambda value: {"instruction": validate_instruction(value["instruction"], 120)})
    assert len(sent) == 2
    correction = sent[1]["messages"][-1]["content"]
    assert "140" in correction and "120" in correction and "20" in correction and "压" * 70 in correction
    assert weighted_length(result["instruction"]) <= 120
    assert model.last_request_record["repair_prompts"] == [correction]


class DriftingDesigner:
    def __init__(self): self.calls = 0
    def generate(self, prompt, validate):
        self.calls += 1
        lines = json.loads(prompt)["lines"]
        return validate({"lines": {key: {**{field: "自然" for field in FIELDS}, "emphasis": [], "pauses": [],
                                         "instructions": f"自然交流，第{self.calls}次设计。"} for key in lines}})


def test_one_line_edit_reuses_unaffected_designs_and_audio(tmp_path, make_project):
    script, cfg, base = live_fixture(make_project, count=3)
    cfg["text_model"] = {"enabled": True, "model": "mock"}
    designer, audio = DriftingDesigner(), SpyAudio()
    pipeline = Pipeline(tmp_path / "production", text_model=designer, audio_adapters={"spy": audio})
    try:
        pipeline.run("job", script, cfg, base)
        first = pipeline.graph.get_state(pipeline.invocation("job")).values
        assert designer.calls == 3 and audio.calls == 3
        bundle = first["preview"]["bundle"]
        pipeline.resume("job", {"decision": "changes", "bundle_sha256": bundle["bundle_sha256"],
                                "changes": {"events": [{"block_id": "line1", "text": "修改这句台词。"}]}})
        second = pipeline.graph.get_state(pipeline.invocation("job")).values
        assert designer.calls == 4 and audio.calls == 4
        for key in ["line0", "line2"]:
            assert first["performance"]["lines"][key] == second["performance"]["lines"][key]
            assert first["assets"][key]["processed"]["sha256"] == second["assets"][key]["processed"]["sha256"]
        assert first["script"]["source_version_id"] != second["script"]["source_version_id"]
        assert pipeline.status("job")["call_budget"]["production_calls"] == 8
    finally:
        pipeline.close()


def test_stale_instruction_removed_when_editing_release_or_text(make_project):
    script, cfg, _ = make_project(count=1, effects=False)
    old = script["events"][0]["performance"]
    old["instructions"] = "原来的平静指令。"
    changed = copy.deepcopy(old) | {"release": "短促地爆发"}
    new, _, _ = revise(script, cfg, {"events": [{"block_id": "line0", "performance": changed}]}, 2)
    assert "instructions" not in new["events"][0]["performance"]
    perf = design(new, cfg)
    assert "短促地爆发" in voice_instruction(perf["lines"]["line0"], perf)
    text, _, _ = revise(script, cfg, {"events": [{"block_id": "line0", "text": "新的台词。"}]}, 2)
    assert "instructions" not in text["events"][0]["performance"]
    text_with_perf, _, _ = revise(script, cfg, {"events": [{"block_id": "line0", "text": "新的内容。", "performance": copy.deepcopy(old)}]}, 2)
    assert "instructions" not in text_with_perf["events"][0]["performance"]
    with pytest.raises(ValueError, match="Unsupported changes"):
        revise(script, cfg, {"script": []}, 2)


def test_budget_atomic_and_restart_does_not_reset(tmp_path):
    budget = CallBudget(tmp_path)
    config = {"max_calls": 2, "batch_max_calls": 1, "max_by_kind": {"text": 2}}
    budget.reserve("r1", "text", config)
    with pytest.raises(BudgetExceeded): budget.reserve("r1", "text", config)
    restarted = CallBudget(tmp_path)
    with pytest.raises(BudgetExceeded): restarted.reserve("r1", "text", config)
    restarted.reserve("r2", "text", config)
    with pytest.raises(BudgetExceeded): restarted.reserve("r3", "text", config)
    assert restarted.stats("r2")["production_calls"] == 2


def test_budget_parallel_reservations_do_not_overspend(tmp_path):
    budget = CallBudget(tmp_path)
    def reserve(_):
        try: budget.reserve("r1", "speech", {"max_calls": 3}); return True
        except BudgetExceeded: return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(reserve, range(8))) == 3


def test_budget_block_resume_keeps_successful_line_cache(tmp_path, make_project):
    script, cfg, base = make_project(count=2, effects=False)
    cfg.update(text_model={"enabled": True, "model": "mock"}, call_budget={"max_calls": 1})
    designer = DriftingDesigner()
    p = Pipeline(tmp_path / "production", text_model=designer)
    try:
        p.run("job", script, cfg, base)
        assert p.status("job")["status"] == "needs_input" and designer.calls == 1
        cfg["call_budget"] = {"max_calls": 4}
        p.resume("job", {"input_script": script, "config": cfg})
        assert p.status("job")["status"] == "awaiting_review" and designer.calls == 2
        assert p.status("job")["call_budget"]["production_calls"] == 2
    finally: p.close()


def test_download_retries_get_without_duplicate_generation_or_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SFX_KEY", "not-a-real-key")
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "mock", "output": {"audio": {"url": "http://example.org/a?Signature=private"}}})
        assert request.url.scheme == "https" and "authorization" not in request.headers
        return httpx.Response(503) if len(calls) == 2 else httpx.Response(200, content=b"audio")
    real = httpx.Client
    monkeypatch.setattr(adapters.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    result = QianwenSFX({"credential_env": "TEST_SFX_KEY", "download_retry_delay_seconds": 0}).generate({"prompt": "关门一次。"}, tmp_path / "audio.wav")
    assert [r.method for r in calls] == ["POST", "GET", "GET"]
    assert result["retrieval"]["attempts"] == 2 and "private" not in json.dumps(result)


@pytest.mark.parametrize("url", ["https://storage.invalid/a?Signature=secret", "https://storage.invalid/a?X-Amz-Signature=secret"])
def test_signed_urls_rejected_before_state(url):
    with pytest.raises(ValueError, match="signed URL"): safe_config({"nested": [url]})


def test_bandpass_reduces_out_of_band_and_normalizes_peak():
    rate = 16000
    t = np.arange(rate) / rate
    a = (.2 * np.sin(2*np.pi*100*t) + .2 * np.sin(2*np.pi*1000*t) + .2 * np.sin(2*np.pi*6000*t))[:, None].astype("float32")
    out = effects(a.copy(), rate, {"bandpass_hz": [300, 3400]})
    def amplitude(x, hz): return abs(np.fft.rfft(x[:, 0])[hz])
    assert amplitude(out, 100) < amplitude(a, 100) * .1
    assert amplitude(out, 6000) < amplitude(a, 6000) * .1
    assert amplitude(out, 1000) > amplitude(a, 1000) * .8
    normalized = effects(out, rate, {"normalize_peak_db": -3})
    assert np.max(np.abs(normalized)) == pytest.approx(10**(-3/20), rel=1e-5)


def test_selected_onset_is_candidate_not_loudest_peak():
    a = np.zeros((1000, 1), dtype="float32")
    a[200:220] = .1; a[700:730] = .8
    point = detect_onset(a, 1000, {"name": "door_close", "threshold_db": -30, "min_run_ms": 5})
    assert point["frame"] == 200 and point["status"] == "candidate"


def test_explicit_tail_changes_axis_and_mix_length(tmp_path, make_project):
    script, cfg, base = make_project(count=1, effects=False)
    p = Pipeline(tmp_path / "production")
    try:
        p.run("job", script, cfg | {"tail_ms": 500}, base)
        state = p.graph.get_state(p.invocation("job")).values
        assert state["timeline"]["total_frames"] == state["timeline"]["placements"][0]["end_frame"] + 24000
        assert state["preview"]["checks"]["mix"]["frames"] == state["timeline"]["total_frames"]
    finally: p.close()


def test_external_visual_duration_never_claims_measured_or_executed(tmp_path, make_project):
    original, cfg, base = make_project(count=1, effects=False)
    original["events"].append({"block_id": "act", "scene_id": "scene0", "kind": "action", "text": "关门", "role_id": "role0"})
    script, _ = normalize(original)
    timeline = schedule(script, {}, cfg, {"actions": {}, "engine_validation": "unverified"})
    class External:
        def feedback(self, *args):
            return {"source_version_id": script["source_version_id"], "script_sha256": digest(script), "timeline_version": timeline["timeline_version"],
                    "engine_executed": True, "engine_id": "test", "execution_evidence": {"not_real": True},
                    "actions": [{"block_id": "act", "duration_basis": "external_locked", "duration_ms": 2400,
                                 "provenance": {"version": "v1", "locked_by": "test reviewer"},
                                 "keypoints": [{"name": "door_close", "ms": 2000}]}]}
    feedback = obtain(script, {}, timeline, cfg | {"visual": {"enabled": True}}, base, External())
    assert feedback["engine_validation"] == "unverified"
    assert feedback["actions"]["act"]["keypoints"][0]["frame"] == 96000
    final = schedule(script, {}, cfg, feedback)
    action = next(p for p in final["placements"] if p["block_id"] == "act")
    assert action["duration_basis"] == "external_locked"
    assert any(i.get("confirmation_id") == "visual:act:external_duration" for i in final["issues"])


def test_duck_respects_render_and_attack_release(tmp_path):
    rate = 8000
    bg = tmp_path / "bg.wav"
    voice = tmp_path / "voice.wav"
    sf.write(bg, np.full((8000, 1), .1, dtype="float32"), rate, subtype="FLOAT")
    sf.write(voice, np.zeros((1000, 1), dtype="float32"), rate, subtype="FLOAT")
    timeline = {"sample_rate": rate, "total_frames": 8000, "placements": [
        {"block_id": "bg", "kind": "ambience", "file": "bg.wav", "sha256": file_hash(bg), "start_frame": 0, "end_frame": 8000},
        {"block_id": "dialogue", "kind": "speech", "render": "dialogue", "file": "voice.wav", "sha256": file_hash(voice), "start_frame": 1000, "end_frame": 2000},
        {"block_id": "radio", "kind": "speech", "render": "old_broadcast", "file": "voice.wav", "sha256": file_hash(voice), "start_frame": 4000, "end_frame": 5000}]}
    mix(tmp_path, timeline, {"mix": {"duck": {"enabled": True, "attack_ms": 20, "release_ms": 40,
                                               "gain_by_render_db": {"dialogue": 20*np.log10(.6), "old_broadcast": 20*np.log10(.25)}}}}, tmp_path / "mix.wav")
    result, _ = sf.read(tmp_path / "mix.wav")
    assert result[1500] == pytest.approx(.06, abs=1e-4)
    assert result[4500] == pytest.approx(.025, abs=1e-4)
    assert .06 < result[900] < .1 and .06 < result[2100] < .1
    assert result[6000] == pytest.approx(.1, abs=1e-4)


def test_processing_rate_remeasures_and_requires_point_confirmation(tmp_path):
    path = tmp_path / "raw.wav"
    sf.write(path, np.full((8000, 1), .1, dtype="float32"), 8000)
    raw = store_raw(tmp_path, path)
    task = {"task_id": "test", "block_id": "event", "payload": {"processing": {"rate_factor": .5},
            "keypoints": [{"name": "contact", "frame": 2000, "sample_rate": 8000, "method": "manual", "status": "confirmed"}]}}
    asset = process(tmp_path, task, raw, {"sample_rate": 8000})
    assert asset["processed"]["frames"] == 16000
    assert asset["keypoints"][0]["frame"] == 4000 and asset["keypoints"][0]["status"] == "candidate"
    task["payload"]["processing"] = {"unsupported_effect": True}
    with pytest.raises(ValueError, match="Unsupported media processing"):
        process(tmp_path, task, raw, {"sample_rate": 8000})


def test_composition_layer_points_survive_default_empty_event_points(tmp_path):
    path = tmp_path / "raw.wav"
    sf.write(path, np.full((800, 1), .1, dtype="float32"), 8000)
    raw = compose(tmp_path, [{"file": str(path), "repeat": 2, "gap_ms": 20,
                  "keypoints": [{"name": "contact", "frame": 200, "sample_rate": 8000, "method": "manual", "status": "confirmed"}]}], 8000, 1)
    task = {"task_id": "test", "block_id": "event", "payload": {"processing": {}, "keypoints": []}}
    asset = process(tmp_path, task, raw, {"sample_rate": 8000})
    assert [point["frame"] for point in asset["keypoints"]] == [200, 1160]


def test_artifact_scanner_allows_auth_code_but_rejects_real_shaped_values():
    assert not sensitive_artifact_text('headers={"Authorization": "Bearer " + key}')
    assert sensitive_artifact_text("Bearer " + "A" * 32)
    assert sensitive_artifact_text("sk-" + "A" * 32)
    assert sensitive_artifact_text("https://media.provider.test/a" + "?Signature=private")
    assert not sensitive_artifact_text("https://example.org/a?Signature=private")


def test_unknown_per_line_result_can_be_recovered_by_cli(tmp_path, make_project, monkeypatch):
    import sys
    from lingban_pipeline.cli import main
    script, cfg, base = make_project(count=1, effects=False)
    cfg["text_model"] = {"enabled": True, "model": "mock"}
    class UnknownDesigner:
        calls = 0
        def generate(self, *args):
            self.calls += 1
            raise UnknownResult("Result is unknown")
    designer = UnknownDesigner()
    root = tmp_path / "production"
    p = Pipeline(root, text_model=designer)
    p.run("job", script, cfg, base)
    task = p.status("job")["problems"][0]["task_id"]
    p.close()
    assert task.startswith("text_line_")
    line = design(script, cfg | {"text_model": {"enabled": False}})["lines"]["line0"]
    line["performance"]["instructions"] = "平静进入，自然表达，尾音轻收。"
    recovered = tmp_path / "recovered.json"
    save(recovered, {"line": line, "generation_record": {"retrieved_by": "test"}})
    monkeypatch.setattr(sys, "argv", ["lingban", "recover", "--root", str(root), "--task", task, "--kind", "performance", "--file", str(recovered)])
    main()
    p = Pipeline(root, text_model=designer)
    try:
        p.resume("job", {"input_script": script, "config": cfg})
        assert p.status("job")["status"] == "awaiting_review" and designer.calls == 1
        state = p.graph.get_state(p.invocation("job")).values
        assert state["performance"]["generation_record"]["lines"]["line0"]["origin"] == "operator_recovery"
    finally:
        p.close()
