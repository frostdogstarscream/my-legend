from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from my_legend_production.production.contracts import StageResult, ArtifactRef
from my_legend_production.production.visual_contracts import VisualContext
from my_legend_production.production.visual_plan import VisualPlanStage, required_checks
from my_legend_production.production.visual_catalog import catalog_from_paths
from my_legend_production.production.visual_model import VisualModel
from my_legend_production.production.worker import StageContext
from my_legend_production.production.repository import ProductionError
from test_production_contracts import production_request
from test_production_queue import env, result
from test_production_visual_context import sample


class Model:
    def __init__(self, context, *, dishonest=False, malformed=False):
        self.context = context
        self.dishonest, self.malformed = dishonest, malformed
        self.calls = []

    def complete(self, system, payload, schema, timeout):
        self.calls.append(payload)
        if "required_checks" not in payload:
            if self.malformed and len(self.calls) == 1:
                return {"content": "invalid json"}
            return {"content": json.dumps(self.context)}
        evidence = self.context["scenes"][0]["presence_evidence"][0]
        actions = {"action:ep:room:" + a["line_id"]: a["evidence"] for a in self.context["scenes"][0]["actions"]}
        return {"content": json.dumps({"source_version_id": "v", "passed": not self.dishonest,
            "checks": [{"key": key, "passed": not self.dishonest, "reason": "fixture review",
                        "advisories":[], "blocking_issues": ([{'code':'SOURCE_ACTION_OMITTED',
                            'source_requirement':actions.get(key,[evidence])[0]['quote'], 'actual_result':'fixture missing action',
                            'evidence':actions.get(key,[evidence])}] if self.dishonest else []),
                        "evidence": actions.get(key, [evidence])} for key in payload["required_checks"]]})}


def setup(env, model, *, repairs=1):
    repo, store = env
    delivery, _ = sample()
    for c in delivery["character_snapshot"]:
        c["character_id"] = "character-" + c["role_id"]
    job = repo.create("alice", production_request(outputs=["visual"], budget={"max_repairs_per_stage": repairs}))
    story = repo.claim("test")
    repo.finish(story, StageResult(status="succeeded", source_version_id="v", technical_quality="passed",
        artifacts=[store.put_json("StoryDelivery", "v", delivery), store.put_json("RenderManifest", "v", {"fixture_only": True})]))
    audio = repo.claim("test")
    repo.finish(audio, result(store, audio, "v"))
    stage = repo.claim("test")
    assert stage["name"] == "visual_plan"
    catalog = {"catalog_sha256": "a" * 64, "capabilities": {"actions": ["enter", "exit", "move", "wait"]}}
    return job, StageContext(repo, store, stage), VisualPlanStage(model, lambda profile: catalog), delivery


def test_visual_plan_preserves_raw_independent_responses_and_freezes_appearance(env):
    _, context = sample()
    model = Model(context)
    job, ctx, handler, delivery = setup(env, model)
    outcome = handler(ctx)
    assert outcome.status == "succeeded" and len(model.calls) == 2
    ctx.repository.finish(ctx.stage, outcome)
    state = ctx.repository.get(job, "alice")
    assert state["actual_calls"] == 2 and state["unsettled_calls"] == 0
    assert ctx.repository.visual_appearances(ctx.stage, delivery)["a"]["color_key"] == "blue"
    evidence = next(a for a in outcome.artifacts if a.kind == "VisualModelEvidence")
    assert ctx.store.json(evidence)["session_context_supplied"] is False


def test_completed_visual_calls_replay_without_new_model_submission(env):
    _, context = sample()
    model = Model(context)
    _, ctx, handler, _ = setup(env, model)
    # Simulate provider completion persisted before the stage result could commit.
    first = handler(ctx)
    # The binding has committed, but publication has not. Replay must use frozen inputs.
    second = handler(ctx)
    assert len(model.calls) == 2
    assert first.artifacts == second.artifacts


def test_schema_repair_is_bounded_and_uses_fixed_error_feedback(env):
    _, context = sample()
    model = Model(context, malformed=True)
    _, ctx, handler, _ = setup(env, model)
    outcome = handler(ctx)
    assert outcome.status == "succeeded" and len(model.calls) == 3
    assert model.calls[1]["previous_attempt"]["error_code"] == "VISUAL_MODEL_SCHEMA_INVALID"


def test_action_coverage_repair_receives_exact_missing_source_ids(env):
    _, context = sample()
    class MissingActionModel(Model):
        def complete(self, system, payload, schema, timeout):
            if not self.calls:
                self.calls.append(payload)
                incomplete = deepcopy(self.context)
                incomplete['scenes'][0]['actions'].pop(0)
                return {'content': json.dumps(incomplete)}
            return super().complete(system, payload, schema, timeout)
    model = MissingActionModel(context)
    _, ctx, handler, _ = setup(env, model)
    outcome = handler(ctx)
    assert outcome.status == 'succeeded'
    issue = model.calls[1]['previous_attempt']['coverage_issues'][0]
    assert issue['missing'] == [context['scenes'][0]['actions'][0]['line_id']]
    assert issue['extra'] == []
    assert model.calls[0]['required_action_slots'][0]['line_ids'] == [a['line_id'] for a in context['scenes'][0]['actions']]


def test_schema_repair_receives_field_path_and_allowed_enum_values(env):
    _, context = sample()
    class BadEmotionModel(Model):
        def complete(self, system, payload, schema, timeout):
            if not self.calls:
                self.calls.append(payload)
                invalid = deepcopy(self.context)
                invalid['scenes'][0]['actions'][0]['emotion'] = 'calm'
                return {'content': json.dumps(invalid)}
            return super().complete(system, payload, schema, timeout)
    model = BadEmotionModel(context)
    _, ctx, handler, _ = setup(env, model)
    assert handler(ctx).status == 'succeeded'
    issue = model.calls[1]['previous_attempt']['schema_issues'][0]
    assert issue['path'] == ['scenes',0,'actions',0,'emotion']
    assert issue['type'] == 'literal_error' and 'neutral' in issue['message']
    assert 'input' not in issue and len(model.calls)==3


def test_unsupported_action_is_not_rewritten_or_retried(env):
    _, context = sample()
    context["scenes"][0]["actions"][0]["action"] = "repair_lamp"
    model = Model(context)
    _, ctx, handler, _ = setup(env, model)
    outcome = handler(ctx)
    assert outcome.status == "failed" and outcome.error_code == "VISUAL_ACTION_UNSUPPORTED"
    assert len(model.calls) == 1 and {a.kind for a in outcome.artifacts} == {"VisualIssueReport"}
    assert ctx.store.json(outcome.artifacts[0])["locations"][0]["line_id"] == "l1"


def test_one_repair_receives_all_parameter_errors_despite_invalid_enum(env):
    _, context = sample()
    class MixedInvalidModel(Model):
        def complete(self, system, payload, schema, timeout):
            if not self.calls:
                self.calls.append(payload)
                invalid = deepcopy(self.context)
                first, last = invalid['scenes'][0]['actions']
                first.update(target_anchor='slot_2', emotion='tender', target_role_id='a')
                last.update(target_anchor='exit', direction='left')
                return {'content': json.dumps(invalid)}
            return super().complete(system, payload, schema, timeout)
    model = MixedInvalidModel(context)
    _, ctx, handler, _ = setup(env, model)
    assert handler(ctx).status == 'succeeded'
    feedback = model.calls[1]['previous_attempt']
    assert feedback['schema_issues'][0]['path'][-1] == 'emotion'
    assert {(i['line_id'], i['field']) for i in feedback['parameter_issues']} == {
        ('l1','target_anchor'), ('l1','emotion'), ('l1','target_role_id'),
        ('l3','target_anchor'), ('l3','direction')}
    assert len(model.calls) == 3  # initial plan, single repair, independent semantic review


def test_bad_target_role_is_repairable_but_never_silently_removed(env):
    _, context = sample()
    context['scenes'][0]['actions'][0]['target_role_id'] = 'a'
    model = Model(context)
    _, ctx, handler, _ = setup(env, model)
    outcome = handler(ctx)
    assert outcome.status == 'failed' and outcome.error_code == 'VISUAL_TARGET_ROLE_INVALID'
    assert len(model.calls) == 2
    report = ctx.store.json(outcome.artifacts[0])
    assert report['validation_feedback']['parameter_issues'][0]['field'] == 'target_role_id'
    assert json.loads(report['responses'][0]['content'])['scenes'][0]['actions'][0]['target_role_id'] == 'a'


def test_semantic_rejection_never_publishes_visual_plan(env):
    _, context = sample()
    model = Model(context, dishonest=True)
    _, ctx, handler, _ = setup(env, model, repairs=0)
    outcome = handler(ctx)
    assert outcome.status == "failed" and outcome.error_code == "VISUAL_SEMANTIC_REVIEW_FAILED"
    assert {a.kind for a in outcome.artifacts} == {"VisualIssueReport"}


def test_native_preflight_rejection_consumes_only_one_existing_repair(env):
    _, context = sample()
    model=Model(context)
    _, ctx, handler, _=setup(env,model)
    def preflight(delivery, context, catalog, check):
        return {'clock_basis':'planning_estimate','effects':[], 'issues':[
            {'source':['ep','room','l1'],'code':'VISUAL_ACTION_NO_DISPLACEMENT'}]}
    handler.execution_preview=preflight
    outcome=handler(ctx)
    assert outcome.status=='failed' and outcome.error_code=='VISUAL_EXECUTION_PREFLIGHT_FAILED'
    assert len(model.calls)==2 and not any('required_checks' in c for c in model.calls)
    assert model.calls[1]['previous_attempt']['execution_preview']['issues'][0]['source'][-1]=='l1'


def test_real_catalog_is_verified_from_native_pinned_assets():
    workspace = Path(__file__).resolve().parents[3]
    catalog = catalog_from_paths(workspace / "runtime/my-legend-production/workflows.json", "pixel_performance/1")
    assert set(catalog["capabilities"]["actions"]) == {"enter", "exit", "move", "face", "wait", "nod", "shake_head", "react"}
    assert catalog["capabilities"]["supports_mp4"] is False
    assert catalog["catalog"]["registry"]["backgrounds"] == ["indoor", "courtyard", "outdoor"]
    assert len(catalog["catalog"]["file_hashes"]) == 5


def test_model_transport_is_one_request_without_session_messages():
    seen = []
    def transport(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 12}})
    model = VisualModel("https://example.test/v1", "fixture-secret", "fixture-model", transport=httpx.MockTransport(transport))
    result = model.complete("fixed policy", {"source": "fixture"}, {}, 10)
    assert result["usage"]["total_tokens"] == 12
    assert len(seen) == 1 and len(seen[0]["messages"]) == 2
