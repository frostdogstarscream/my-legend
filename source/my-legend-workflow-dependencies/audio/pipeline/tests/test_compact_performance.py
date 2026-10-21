import copy
import json

import pytest

from lingban_pipeline.content import normalize
from lingban_pipeline.performance import FIELDS, design


class CompactDesigner:
    def __init__(self, mutate=None):
        self.mutate = mutate

    def generate(self, prompt, validator):
        source = json.loads(prompt)["lines"]
        response = {"lines": {key: {**{field: "自然表演" for field in FIELDS}, "emphasis": [], "pauses": [], "instructions": "先确认，再提出请求。"} for key in source}}
        if self.mutate:
            self.mutate(response, source)
        return validator(response)


def test_model_only_returns_performance_and_program_preserves_source(make_project):
    source, cfg, _ = make_project(count=2)
    script, _ = normalize(source)
    before = copy.deepcopy(script)
    cfg["text_model"] = {"enabled": True}
    result = design(script, cfg, CompactDesigner())
    for event in script["events"]:
        if event["kind"] == "speech":
            line = result["lines"][event["block_id"]]
            assert line["text"] == event["text"]
            assert line["role_id"] == event["role_id"]
            assert line["scene_id"] == event["scene_id"]
            assert line["context"]
            assert line["performance"]["instructions"] == "先确认，再提出请求。"
    assert script == before


@pytest.mark.parametrize("mode", ["source_rewrite", "invented_emphasis", "missing_line", "missing_instruction"])
def test_rewrites_and_unlocatable_emphasis_remain_rejected(make_project, mode):
    source, cfg, _ = make_project(count=1)
    script, _ = normalize(source)
    cfg["text_model"] = {"enabled": True}

    def mutate(response, source):
        key = next(iter(response["lines"]))
        if mode == "source_rewrite":
            response["lines"][key] = {**source[key], "text": "擅自改写。", "performance": response["lines"][key]}
        elif mode == "invented_emphasis":
            response["lines"][key]["emphasis"] = ["并不存在于台词中的重音"]
        elif mode == "missing_instruction":
            response["lines"][key].pop("instructions")
        else:
            response["lines"].pop(key)

    with pytest.raises(ValueError):
        design(script, cfg, CompactDesigner(mutate))
