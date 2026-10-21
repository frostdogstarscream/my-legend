import json
from hashlib import sha256

import httpx
import pytest
from types import SimpleNamespace

from my_legend_production.production import acceptance
from test_production_contracts import production_request


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    code = tmp_path / "code.py"
    code.write_text("frozen-code", encoding="utf-8")
    monkeypatch.setattr(acceptance, "source_snapshot", lambda _: {"code.py": acceptance.digest(code)})
    monkeypatch.setattr(acceptance, "public_provider_config", lambda _: {"fixture_only": True})
    monkeypatch.setattr(acceptance, "tool_snapshot", lambda _: {"fixture_only": True})
    request = tmp_path / "input.json"
    request.write_text(production_request(outputs=["script"]).model_dump_json(), encoding="utf-8")
    root = acceptance.prepare(tmp_path, request, tmp_path / "run")
    return tmp_path, root, code


def transport_for(*, corrupt=False, change=None, false_output_claim=False):
    calls = []
    payload = json.dumps({"story_version_id": "v", "fixture_only": True, "episodes": [{"episode_id": "ep",
        "scenes": [{"scene_id": "sc", "utterances": [{"line_id": "line", "kind": "dialogue", "text": "fixture"}]}]}]}).encode()
    ref = {"kind": "StoryDelivery", "sha256": sha256(payload).hexdigest(), "size_bytes": len(payload),
           "media_type": "application/json", "source_version_id": "v"}
    def handle(request):
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/auth/local"):
            return httpx.Response(200, json={"data": {"accessToken": "private-fixture-token"}})
        assert request.headers["authorization"] == "Bearer private-fixture-token"
        if path.endswith("/production-capabilities"):
            data = {"outputs": ["script"], "fixture_only": True}
        elif path.endswith("/production-jobs"):
            assert request.method == "POST"
            data = {"id": "job", "status": "queued", "artifacts": []}
        elif path.endswith("/production-jobs/job"):
            if change:
                change()
            data = {"id": "job", "work_id": "work", "source_version_id": "v", "status": "succeeded", "artifacts": [ref]}
        elif path.endswith("/evidence"):
            data = {"resume_events": 0, "provider_calls": 0, "policy_repairs": 0}
        elif "/artifacts/" in path:
            return httpx.Response(200, content=b"corrupt" if corrupt else payload)
        elif path.endswith("/playback"):
            data = {"schema_version": "production_playback_view/1.0", "job_id": "job", "job_status": "succeeded",
                "available_outputs": ["script"], "manifest": {"job_id": "job", "work_id": "work", "source_version_id": "v",
                "script": {"artifact": ref, "content_path": f"/api/v1/production-jobs/job/artifacts/{ref['sha256']}"}}}
            if false_output_claim:
                data["available_outputs"].append("visual")
        elif path.endswith(("/auth/logout", "/cancel")):
            data = {}
        else:
            pytest.fail("unexpected API request: " + path)
        return httpx.Response(200, json={"data": data})
    return httpx.MockTransport(handle), calls


def test_preparation_is_offline_and_never_overwrites_existing_run(prepared):
    workspace, root, _ = prepared
    freeze, request = acceptance.frozen_inputs(workspace, root)
    assert request.idempotency_key.startswith("acceptance-")
    assert freeze["session_interventions"] is None
    assert freeze["financial_cost_cap_enforced"] is False
    with pytest.raises(FileExistsError):
        acceptance.prepare(workspace, workspace / "input.json", root)


def test_execution_requires_explicit_billing_ack_and_unchanged_inputs(prepared):
    workspace, root, code = prepared
    with pytest.raises(ValueError, match="EXPLICIT_PROVIDER_BILLING_ACK_REQUIRED"):
        acceptance.execute(workspace, root)
    code.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="ACCEPTANCE_FREEZE_CHANGED"):
        acceptance.execute(workspace, root, billing_acknowledged=True)
    assert not (root / "submission-intent.json").exists()


def test_api_run_verifies_artifacts_without_inventing_independence_attestation(prepared):
    workspace, root, _ = prepared
    transport, calls = transport_for()
    result = acceptance.execute(workspace, root, billing_acknowledged=True, poll_seconds=0, transport=transport)
    assert result["runner_error"] is None
    report = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert report["execution_status"] == "succeeded" and report["requested_outputs_available"]
    assert report["technical_media_audit"] == "passed"
    assert json.loads((root / "media-audit.json").read_text())["source_events"] == 1
    assert report["session_interventions"] is None and report["independence_attestation"] == "pending"
    assert sum(method == "POST" and path.endswith("/production-jobs") for method, path in calls) == 1
    assert not any(path.endswith(("/resume", "/reconcile")) for _, path in calls)
    assert not any("private-fixture-token" in p.read_text(encoding="utf-8") for p in root.glob("*.json"))
    with pytest.raises(FileExistsError):
        acceptance.execute(workspace, root, billing_acknowledged=True, transport=transport)


def test_corrupt_download_is_not_accepted(prepared):
    workspace, root, _ = prepared
    transport, _ = transport_for(corrupt=True)
    result = acceptance.execute(workspace, root, billing_acknowledged=True, poll_seconds=0, transport=transport)
    assert result["runner_error"] == "ACCEPTANCE_ARTIFACT_INVALID"
    assert not (root / "result.json").exists()


def test_false_output_claim_is_saved_as_failure_not_a_success_report(prepared):
    workspace, root, _ = prepared
    transport, _ = transport_for(false_output_claim=True)
    result = acceptance.execute(workspace, root, billing_acknowledged=True, poll_seconds=0, transport=transport)
    assert result["runner_error"] == "ACCEPTANCE_OUTPUT_CLAIM_MISMATCH"
    assert (root / "failure.json").exists() and (root / "playback.json").exists()
    assert not (root / "result.json").exists()


def test_source_edit_during_execution_cancels_and_preserves_failure(prepared):
    workspace, root, code = prepared
    transport, calls = transport_for(change=lambda: code.write_text("manual-fix", encoding="utf-8"))
    result = acceptance.execute(workspace, root, billing_acknowledged=True, poll_seconds=0, transport=transport)
    assert result["runner_error"] == "ACCEPTANCE_FREEZE_CHANGED"
    assert ("POST", "/api/v1/production-jobs/job/cancel") in calls
    assert (root / "failure.json").exists() and not (root / "result.json").exists()


def test_story_source_uses_installed_module_in_nonstandard_directory(tmp_path, monkeypatch):
    project = tmp_path / "source/StoryGenerate-fixture"
    module = project / "4-引擎代码/drama_engine"
    (module / "workflow").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='fixture'", encoding="utf-8")
    (module / "__init__.py").touch()
    prompt = module / "workflow/storycraft_prompts.py"
    prompt.write_text("PROMPT='one'", encoding="utf-8")
    (module / "workflow/schemas.py").write_text("SCHEMA=1", encoding="utf-8")
    monkeypatch.setattr(acceptance.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(module / "__init__.py")))
    assert acceptance.story_source_root(tmp_path) == project
    for relative in ("source/my-legend-production/pyproject.toml", "ops/my-legend/requirements-story.lock.txt",
        "runtime/my-legend-production/workflows.json", "runtime/my-legend-production/story-provider/profile.json",
        "runtime/my-legend-production/audio-provider/profile.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "runtime/my-legend-production/dependency-lock.json").write_text('{"files": {}}', encoding="utf-8")
    before = acceptance.source_snapshot(tmp_path)
    key = prompt.relative_to(tmp_path).as_posix()
    assert key in before
    prompt.write_text("PROMPT='changed'", encoding="utf-8")
    assert acceptance.source_snapshot(tmp_path)[key] != before[key]


def test_missing_story_runtime_is_not_silently_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ValueError, match="STORY_RUNTIME_SOURCE_UNAVAILABLE"):
        acceptance.source_snapshot(tmp_path)


def test_invalidated_freeze_cannot_be_executed(prepared):
    workspace, root, _ = prepared
    acceptance.write_new(root / "invalidated.json", {"reason": "coverage gap"})
    with pytest.raises(ValueError, match="ACCEPTANCE_FREEZE_INVALIDATED"):
        acceptance.frozen_inputs(workspace, root)


def test_cached_diagnostic_cannot_count_as_new_input_acceptance(prepared):
    workspace, _, _ = prepared
    def ref(kind, media='application/json'):
        return dict(kind=kind,sha256='a'*64,source_version_id='v',media_type=media,size_bytes=1)
    request = production_request(source=dict(kind='delivery',source_version_id='v',
        delivery=ref('StoryDelivery'),manifest=ref('RenderManifest')),outputs=['visual'],
        audio_revision=dict(cache=ref('AudioCache','application/zip'),regenerate=[]))
    path = workspace / 'cached.json'
    path.write_text(request.model_dump_json(),encoding='utf-8')
    with pytest.raises(ValueError,match='ACCEPTANCE_REQUIRES_AUTOMATIC_NEW_INPUT'):
        acceptance.prepare(workspace,path,workspace / 'not-created')
    root = acceptance.prepare(workspace,path,workspace / 'diagnostic',run_scope='cached_downstream_diagnostic')
    freeze, _ = acceptance.frozen_inputs(workspace,root)
    assert freeze['new_input_acceptance_eligible'] is False
    transport, _ = transport_for()
    acceptance.execute(workspace,root,billing_acknowledged=True,poll_seconds=0,transport=transport)
    result = json.loads((root / 'result.json').read_text())
    assert result['run_scope']=='cached_downstream_diagnostic' and not result['new_input_acceptance_eligible']
    with pytest.raises(ValueError,match='DIAGNOSTIC_REQUIRES_FROZEN_DELIVERY_AND_AUDIO_CACHE'):
        acceptance.prepare(workspace,workspace / 'input.json',workspace / 'invalid-diagnostic',run_scope='cached_downstream_diagnostic')


def test_frozen_story_audio_diagnostic_is_explicit_and_never_counts_as_new_input(prepared):
    workspace,_,_=prepared
    def ref(kind):return dict(kind=kind,sha256='a'*64,source_version_id='v',size_bytes=1)
    request=production_request(source=dict(kind='delivery',source_version_id='v',delivery=ref('StoryDelivery'),manifest=ref('RenderManifest')),
        outputs=['script','audio'])
    path=workspace/'frozen-audio.json';path.write_text(request.model_dump_json(),encoding='utf-8')
    root=acceptance.prepare(workspace,path,workspace/'audio-diagnostic',run_scope='frozen_story_audio_diagnostic')
    freeze,_=acceptance.frozen_inputs(workspace,root)
    assert not freeze['new_input_acceptance_eligible']
    assert freeze['run_scope']=='frozen_story_audio_diagnostic'
    with pytest.raises(ValueError,match='ACCEPTANCE_REQUIRES_AUTOMATIC_NEW_INPUT'):
        acceptance.prepare(workspace,path,workspace/'invalid-new-input')
    with pytest.raises(ValueError,match='DIAGNOSTIC_REQUIRES_FROZEN_STORY_AND_FRESH_AUDIO'):
        acceptance.validate_run_scope(request.model_copy(update={'outputs':['visual']}),'frozen_story_audio_diagnostic')
    with pytest.raises(ValueError,match='DIAGNOSTIC_REQUIRES_FROZEN_STORY_AND_FRESH_AUDIO'):
        acceptance.validate_run_scope(production_request(outputs=['audio']),'frozen_story_audio_diagnostic')
