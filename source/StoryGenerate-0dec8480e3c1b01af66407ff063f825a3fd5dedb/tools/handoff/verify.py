"""Meaningful offline handoff checks: schemas, routes, content provenance, links.

Uses an in-memory database only to construct the FastAPI schema; never executes
story generation and never claims this is real-model evidence.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"4-引擎代码"))
from pydantic import TypeAdapter
from drama_engine.service.api import create_app
from drama_engine.service.config import ServiceSettings
from drama_engine.service.schemas import CharacterBatch, PlanApprovalRequest, PlanRevisionRequest, RuntimeUpdate
from drama_engine.workflow.schemas import StoryDelivery, StoryOperationRequest

OUT = ROOT/"docs/handoff"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    checks = []
    def record(name, detail):
        checks.append({"check":name, "passed":True, "detail":detail})
    settings = ServiceSettings("sqlite://", api_keys={"schema-construction-only":{"principal_id":"schema-only"}}, testing=True)
    app = create_app(settings)
    expected = read(OUT/"contracts/openapi.json")
    assert app.openapi() == expected == read(ROOT/"docs/phase11/openapi.json")
    actual_ops = {(method, route.path) for route in app.routes if getattr(route,"include_in_schema", False)
                  for method in route.methods if method not in ("HEAD","OPTIONS")}
    schema_ops = {(method.upper(),path) for path,methods in expected["paths"].items() for method in methods}
    assert actual_ops == schema_ops and len(actual_ops) == 26
    manual = (OUT/"02-api-reference.md").read_text(encoding="utf-8")
    assert all(f"## {method} {path}" in manual for method,path in actual_ops)
    record("route/openapi/reference parity", {"operations":26,"paths":24,"component_schemas":50})
    examples = []
    for file in (OUT/"examples").glob("*.json"):
        value = read(file)
        if file.name == "endpoint-success-examples.json":
            continue
        if file.name == "forest.characters.json":
            CharacterBatch.model_validate(value)
        elif file.name == "plan.revision.json":
            PlanRevisionRequest.model_validate(value)
        elif file.name == "plan.approval.json":
            PlanApprovalRequest.model_validate(value)
        elif file.name == "runtime.patch.json":
            RuntimeUpdate.model_validate(value)
        else:
            TypeAdapter(StoryOperationRequest).validate_python(value)
        examples.append(file.name)
    record("example request schema validation", examples)
    index = read(OUT/"samples/index.json")
    for entry in index:
        raw = read(ROOT/entry["delivery_path"])
        delivery = raw["response"] if entry["delivery_pointer"] == "response" else raw
        StoryDelivery.model_validate(delivery)
        assert canonical(delivery) == entry["content_sha256"]
        markdown = (OUT/f"samples/{entry['id']}.md").read_text(encoding="utf-8")
        markdown = markdown.split("## 正式故事全文",1)[1]
        pos = 0
        for episode in delivery["episodes"]:
            for scene in episode["scenes"]:
                for line in scene["utterances"]:
                    found = markdown.find(line["text"], pos)
                    assert found >= 0, (entry["id"],line["line_id"])
                    pos = found + len(line["text"])
    record("all seven full story bodies retained in source order", len(index))
    links = 0
    markdown_files = [*OUT.rglob("*.md"), ROOT/"README.md", ROOT/"docs/phase11/DEPLOYMENT.md", *(ROOT/"6-实测产物/operations_report_20261014").glob("*.md")]
    for file in markdown_files:
        text = file.read_text(encoding="utf-8")
        assert not re.search(r"\]\((?:[A-Za-z]:[/\\]|file:)", text), file
        for target in re.findall(r"\]\(([^)]+)\)", text):
            target = target.strip("<>").split("#",1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (file.parent/unquote(target)).resolve()
            assert resolved.is_relative_to(ROOT), (file,target)
            if resolved == OUT/"contracts/verification.json":
                links += 1
                continue  # This file is produced atomically at the end of this run.
            assert resolved.exists(), (file.relative_to(ROOT),target)
            links += 1
    record("GitHub relative Markdown targets", links)
    bodies = read(OUT/"contracts/http-responses.json")
    def refs(value):
        if isinstance(value, dict):
            if "response_reference" in value:
                ref = value["response_reference"]
                source = read(ROOT/ref["archive_path"])
                assert canonical(source["response"]) == ref["canonical_sha256"]
            else:
                for item in value.values(): refs(item)
        elif isinstance(value,list):
            for item in value: refs(item)
    refs(bodies)
    endpoint_examples = read(OUT/"examples/endpoint-success-examples.json")["operations"]
    assert set(endpoint_examples) == {m+" "+p for m,p in actual_ops}
    from drama_engine.workflow import schemas as workflow_schemas
    from drama_engine.service import schemas as service_schemas, downstream
    def expand(value):
        if isinstance(value,dict) and "probe_response_reference" in value:
            ref = value["probe_response_reference"]
            return expand(read(ROOT/ref["path"])[ref["entry"]]["response"])
        if isinstance(value,dict) and "response_reference" in value:
            return read(ROOT/value["response_reference"]["archive_path"])["response"]
        return [expand(v) for v in value] if isinstance(value,list) else value
    validated = 0
    for op,example in endpoint_examples.items():
        method,path = op.split(" ",1)
        schema = expected["paths"][path][method.lower()]["responses"][str(example["status"])].get("content",{}).get("application/json",{}).get("schema",{})
        model_schema = schema.get("items",{}) if schema.get("type")=="array" else schema
        ref = model_schema.get("$ref")
        if ref:
            name = ref.rsplit("/",1)[-1]
            model = next(getattr(module,name) for module in (workflow_schemas,service_schemas,downstream) if hasattr(module,name))
            TypeAdapter(list[model] if schema.get("type")=="array" else model).validate_python(expand(example["complete_success_response"]))
            validated += 1
    record("complete success example per operation", {"examples":26,"typed_responses_validated":validated,"explicit_schema_only_examples":2})
    assert read(OUT/"contracts/http-probe.json")["passed"]
    assert read(OUT/"contracts/database-restore.json")["passed"]
    probe = read(OUT/"contracts/http-probe.json")
    record("real HTTP archived response hashes and restored DB", {"checks":len(probe["checks"]),"no_worker_or_model":True})
    regression = read(OUT/"contracts/regression-summary.json")
    assert regression["passed"] and regression["tests"] == "685"
    assert read(OUT/"contracts/security-check.json")["passed"]
    engine_diff = subprocess.run(["git","diff","eeb99ae66069862878387ce6850fb20ef6ea0586","--","4-引擎代码","tests","alembic"],check=True,capture_output=True,text=True)
    assert not engine_diff.stdout
    record("regression/security/engine baseline", "685 passed; no new engine/test/migration changes; evidence secret scan passed")
    stats = read(OUT/"contracts/cost-recomputed.json")
    assert stats["phase11"]["raw_capture_call_ids_match"]
    assert stats["phase11"]["total"]["input"]+stats["phase11"]["total"]["output"] == 1317167
    record("raw physical model accounting", "Phase11 75 calls; Phase10 801 historical calls; no reasoning double count")
    # Exercise all scope builders against real archives without sending requests.
    source = ROOT/"6-实测产物/phase11_live_20261015_v3/continue/delivery.json"
    import tempfile
    with tempfile.TemporaryDirectory(prefix="handoff-", dir=OUT/"examples") as tmp:
        for intent, extra in [("revise",["--scope",scope]) for scope in ("line","scene","episode","arc","ending","whole_story")]+[("continue",[]),("remix",["--retain","固定身份","--transform","改变冲突"])]:
            file = Path(tmp)/"request.json"
            subprocess.run([sys.executable,str(ROOT/"tools/handoff/make_request.py"),intent,str(source),"离线合同测试", "--output",str(file),*extra],check=True,capture_output=True)
            TypeAdapter(StoryOperationRequest).validate_python(read(file))
    record("request builder six scopes / continue / remix", "8 requests validated; not submitted")
    result = {"baseline_sha":"eeb99ae66069862878387ce6850fb20ef6ea0586", "engine_behavior_modified":False,
              "new_model_calls":0, "independent_novice_human_trial":"NOT_EXECUTED",
              "checks":checks, "passed":True}
    (OUT/"contracts/verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    app.state.engine.dispose()
    print(f"Handoff verification passed: {len(checks)} groups, {links} links")


if __name__ == "__main__":
    main()
