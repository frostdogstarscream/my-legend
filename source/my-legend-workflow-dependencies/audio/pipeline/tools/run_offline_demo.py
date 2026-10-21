"""Run the real graph using synthetic local waveforms; never call providers."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import soundfile as sf

from lingban_pipeline.graph import Pipeline
from lingban_pipeline.io import load, save
from lingban_pipeline import __version__


def run(root: Path):
    root = root.resolve()
    if root.exists():
        raise ValueError("Choose a new demo directory; existing output is preserved")
    inputs = root / "input"
    inputs.mkdir(parents=True)
    rate = 48000
    t = np.arange(round(rate * .6)) / rate
    voice = .06 * np.sin(2 * np.pi * 440 * t)
    voice *= np.minimum(1, np.minimum(t / .03, (.6 - t) / .03))
    impact = np.zeros(round(rate * .6), dtype="float32")
    impact[9600:10080] = .15
    background = np.random.default_rng(42).normal(0, .002, round(rate * .25))
    for name, waveform in [("voice.wav", voice), ("impact.wav", impact), ("background.wav", background)]:
        sf.write(inputs / name, waveform, rate)
    script = {
        "schema_version": "lingban.script/1", "source_version_id": "offline-demo-v1", "title": "离线技术示例（合成波形，无真人或模型配音）",
        "roles": [{"role_id": "role", "name": "测试角色"}], "scenes": [{"scene_id": "scene", "description": "合成波形用于检查时间关系"}],
        "events": [
            {"block_id": "line", "scene_id": "scene", "kind": "speech", "role_id": "role", "text": "这是合成波形占位，不能用于评价朗读情绪。",
             "performance": {"instructions": "自然进入，明确表达，尾音轻收。"}, "media": {"file": "voice.wav"}},
            {"block_id": "action", "scene_id": "scene", "kind": "action", "role_id": "role", "text": "测试接触动作", "duration_ms": 600,
             "keypoints": [{"name": "touch", "frame": 19200, "sample_rate": rate, "method": "synthetic_fixture", "status": "candidate"}],
             "timing": [{"anchor": "line", "anchor_point": "end", "offset_ms": 100}]},
            {"block_id": "impact", "scene_id": "scene", "kind": "sfx", "text": "合成接触声占位", "sound": {"source": "synthetic_fixture"},
             "media": {"file": "impact.wav", "processing": {"keypoint_detection": {"name": "contact", "threshold_db": -35, "min_run_ms": 2}}},
             "timing": [{"anchor": "action", "anchor_point": "touch", "self_point": "contact"}]},
            {"block_id": "background", "scene_id": "scene", "kind": "ambience", "text": "合成背景噪声占位",
             "media": {"file": "background.wav", "loop_until": "impact"}, "timing": [{"anchor": None, "offset_ms": 0}]}
        ]
    }
    config = load(Path(__file__).resolve().parents[1] / "config.example.json")
    config.update(context_window=0, tail_ms=500, call_budget={"max_calls": 0, "batch_max_calls": 0, "max_by_kind": {"text": 0, "speech": 0, "sfx": 0}})
    config["project"] = {"genre": "技术示例", "performance": "仅检查管线，不验收声音效果"}
    config["mix"]["duck"]["enabled"] = True
    save(inputs / "script.json", script)
    save(inputs / "config.json", config)
    p = Pipeline(root / "production")
    try:
        p.run("offline-demo", script, config, inputs)
        status = p.status("offline-demo")
        preview = status["preview"]
        if status["status"] != "awaiting_review" or not preview["checks"]["technical_passed"] or status["call_budget"]["production_calls"]:
            raise ValueError("Offline technical verification failed")
        result = {"version": __version__, "synthetic_waveforms_only": True, "status": status["status"], "technical_passed": True,
                  "provider_calls": 0, "human_listening": "not_performed", "publication": "not_approved", "visual_execution": "not_required",
                  "preview": str(root / "production" / preview["preview"]), "required_point_confirmations": preview["bundle"]["required_point_confirmations"]}
        save(root / "verification.json", result)
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        p.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    run(parser.parse_args().root)
