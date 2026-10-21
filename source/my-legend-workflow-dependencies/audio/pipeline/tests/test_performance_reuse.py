import json

from lingban_pipeline.graph import Pipeline
from lingban_pipeline.performance import FIELDS


class CountingDesigner:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt, validator):
        self.calls += 1
        lines = json.loads(prompt)["lines"]
        return validator({"lines": {key: {**{field: "自然" for field in FIELDS}, "emphasis": [], "pauses": [], "instructions": "自然交流"} for key in lines}})


def test_effect_candidate_and_mix_reuse_performance_but_text_edit_invalidates(tmp_path, make_project):
    script, cfg, base = make_project(count=2)
    cfg["text_model"] = {"enabled": True, "model": "offline-counting"}
    designer = CountingDesigner()
    pipeline = Pipeline(tmp_path / "production", text_model=designer)
    try:
        pipeline.run("job", script, cfg, base)
        assert designer.calls == 2
        def change(changes):
            bundle = pipeline.status("job")["preview"]["bundle"]
            pipeline.resume("job", {"decision": "changes", "bundle_sha256": bundle["bundle_sha256"], "changes": changes})
        original = pipeline.graph.get_state(pipeline.invocation("job")).values["performance"]["lines"]
        change({"events": [{"block_id": "effect", "media": {"file": str(base / "local0.wav"), "candidate_version": 2}}]})
        assert designer.calls == 2
        state = pipeline.graph.get_state(pipeline.invocation("job")).values
        assert state["performance"]["lines"] == original
        assert state["performance"]["script_sha256"] == state["timeline"]["script_sha256"]
        change({"config": {"mix": {"master_gain_db": -3}}})
        assert designer.calls == 2
        change({"events": [{"block_id": "line0", "text": "现在修改对白。"}]})
        assert designer.calls == 3
    finally:
        pipeline.close()


def test_text_model_configuration_change_does_not_reuse(tmp_path, make_project):
    script, cfg, base = make_project(count=1, effects=False)
    cfg["text_model"] = {"enabled": True, "model": "offline-counting", "temperature": .4}
    designer = CountingDesigner()
    pipeline = Pipeline(tmp_path / "production", text_model=designer)
    try:
        pipeline.run("job", script, cfg, base)
        bundle = pipeline.status("job")["preview"]["bundle"]
        pipeline.resume("job", {"decision": "changes", "bundle_sha256": bundle["bundle_sha256"], "changes": {"config": {"text_model": {"enabled": True, "model": "offline-counting", "temperature": .8}}}})
        assert designer.calls == 2
    finally:
        pipeline.close()
