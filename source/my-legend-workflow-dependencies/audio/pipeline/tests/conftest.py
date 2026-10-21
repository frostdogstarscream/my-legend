from __future__ import annotations

from pathlib import Path
import numpy as np
import pytest
import soundfile as sf

from lingban_pipeline.contracts import StandardScript


@pytest.fixture
def make_project(tmp_path):
    def make(count=2, genre="生活", effects=True, scenes=1):
        roles = [dict(role_id=f"role{i}", name=f"人物{i}") for i in range(count)]
        scene_list = [dict(scene_id=f"scene{i}", description=f"场景{i}") for i in range(scenes)]
        events = []
        for i in range(count):
            path = tmp_path / f"local{i}.wav"
            sr = 24000 if i % 2 else 48000
            t = np.arange(round(sr * (.12 + i * .01))) / sr
            sf.write(path, .05 * np.sin(2 * np.pi * (230 + i * 10) * t), sr)
            events.append(dict(block_id=f"line{i}", scene_id=f"scene{i % scenes}", kind="speech", role_id=f"role{i}", text=f"第{i}句，今天见面。", media={"file": str(path)}, performance={"intent": "交流", "entry": "平静", "release": "自然收尾"}))
        if effects:
            events.append(dict(block_id="effect", scene_id="scene0", kind="sfx", text="物体轻碰", sound={"source": "木块", "action": "轻碰", "onomatopoeia": "嗒"}, media={"file": str(tmp_path / "local0.wav")}))
        script = StandardScript(source_version_id="source1", title="通用测试素材", roles=roles, scenes=scene_list, events=events).model_dump()
        config = dict(sample_rate=48000, channels=1, gap_ms=50, context_window=0, project={"genre": genre}, role_voices={f"role{i}": f"Voice{i}" for i in range(count)}, tts={"adapter": "local", "concurrency": 2}, sfx={"adapter": "local", "concurrency": 2}, processing={"trim_enabled": False}, mix={"limit": True, "peak_ceiling": .9}, visual={"enabled": False})
        return script, config, tmp_path
    return make
