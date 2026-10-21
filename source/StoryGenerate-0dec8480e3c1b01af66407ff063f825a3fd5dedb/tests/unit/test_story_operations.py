"""Operation invariants using explicit test doubles, never live acceptance evidence."""
from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from drama_engine.workflow.adapters import (ApprovedPlanLLMExecutor, DeterministicPerformanceAnnotator, bound_quality_schema,
    canonical_check_evidence, bound_thread_schema,
    LLMPlanGenerator,
    validate_generated_plan_ending,
    DraftEpisode, DraftScene, DraftLine, EpisodeRevision, RevisionScene, RevisionLine, QualityDecision)
from drama_engine.workflow.operations import fold_continuity, operation_errors, operation_requirements, evidence_kind_errors
from drama_engine.workflow.repository import InMemoryWorkflowRepository, WorkflowConflictError
from drama_engine.workflow.schemas import OperationContext, StoryDelivery, OperationCheck
from drama_engine.workflow.schemas import CreateStoryRequest, CreationPreferences, PlanContent
from drama_engine.workflow.service import StoryWorkflowService
from test_phase10_workflow import FixedPlanGenerator


class ScriptedProvider:
    """A unit-test double that exposes writer inputs and scope escapes."""
    def __init__(self):
        self.calls = []
        self.call_history = []
        self.escape = False
        self.false_quote = False

    def complete_structured(self, spec, system, user, schema):
        payload = json.loads(user)
        self.calls.append((schema, payload))
        if issubclass(schema, DraftEpisode):
            i = payload["episode_outline"]["index"]
            return schema(title=f"第{i}集", synopsis="维修行动", preview_blurb="林岚与周野在老街广播站核对设备状态，合作修复故障。",
                scenes=[DraftScene(title=f"场景{s}", dramatic_goal="核对设备", lines=[DraftLine(
                    kind="dialogue", speaker_role_id="hero", text=(f"第{i}集先核对设备再维修。" * 100)[:round(payload["preferences"]["target_duration_sec"] * 1.75)]
                )]) for s in (1, 2)], episode_summary=f"第{i}集核对完成",
                fact_delta=[{"id": f"ep{i}", "value": "核对完成"}],
                relationship_delta=[{"id": "trust", "value": i}],
                new_open_threads=["三声铃"] if i == 1 else [], closed_threads=["三声铃"] if i == 3 else [])
        if issubclass(schema, EpisodeRevision):
            ep = payload["source_episode"]
            impact = payload["approved_scope"]
            selected = [s for s in ep["scenes"] if s["scene_id"] in impact["target_scene_ids"]]
            scenes = []
            for scene in selected:
                lines = scene["utterances"]
                if impact["scope"] == "line":
                    lines = [u for u in lines if u["line_id"] in impact["target_line_ids"]]
                scenes.append(RevisionScene(scene_id=scene["scene_id"], lines=[RevisionLine(
                    line_id=u["line_id"], kind=u["kind"], speaker_role_id=u["speaker_role_id"],
                    text="共同核对。" + u["text"][5:],
                ) for u in lines]))
            if self.escape:
                scenes[0].scene_id = "outside-scope"
            return schema(scenes=scenes, episode_summary="共同核对完成",
                fact_delta=[{"id": f"ep{ep['index']}", "value": "共同核对完成"}],
                relationship_delta=ep["relationship_delta"], new_open_threads=ep["new_open_threads"], closed_threads=ep["closed_threads"])
        if issubclass(schema, QualityDecision):
            changed = [ep for ep in payload["episodes"] if ep["episode_id"] in payload["generated_episode_ids"]]
            quote = changed[-1]["scenes"][0]["utterances"][0]["text"]
            return schema(outline_alignment_passed=True, continuity_passed=True, content_rating_passed=True,
                operation_checks=[{"requirement_id": k, "requirement": v, "passed": True,
                                   "evidence_quotes": ["invented quote" if self.false_quote else quote]}
                                  for k, v in payload["required_checks"].items()])
        raise AssertionError(schema)


def setup(repository=None):
    provider = ScriptedProvider()
    app = StoryWorkflowService(repository or InMemoryWorkflowRepository(), FixedPlanGenerator(),
        ApprovedPlanLLMExecutor(provider, DeterministicPerformanceAnnotator()))
    return app, provider


def req(intent="create", source=None, **kwargs):
    value = {"intent": intent, "request_id": uuid.uuid4().hex, "idempotency_key": uuid.uuid4().hex,
             "user_instruction": "核对维修广播站", **kwargs}
    if source:
        value["source_selector"] = {"type": "by_id", "story_version_id": source.story_version_id}
    return value


def generate(app, value, context=None):
    plan = app.prepare_story_plan(value, context)
    job = app.approve_story_plan(plan.plan_id, plan.plan_revision, plan.plan_fingerprint, uuid.uuid4().hex)
    return app.generate_from_approved_plan(job.job_id), job


def create(app):
    return generate(app, req(creation_preferences={"target_episodes": 2, "target_duration_sec": 120}))[0]


def edit(source, scope="scene", downstream="branch_from_here"):
    ep = source.episodes[0]
    target = {"episode_id": ep.episode_id, "scene_id": ep.scenes[0].scene_id,
              "line_id": ep.scenes[0].utterances[0].line_id}
    if scope == "arc":
        target = {"episode_ids": [e.episode_id for e in source.episodes]}
    if scope in ("ending", "whole_story"):
        target = {}
    return req("revise", source, edit={"scope": scope, "target": target, "instruction": "共同核对",
                                      "downstream_policy": downstream})


@pytest.mark.parametrize("scope", ["line", "scene", "episode", "arc", "ending", "whole_story"])
def test_all_revision_scopes_preserve_identity_and_protected_content(scope):
    app, _ = setup()
    source = create(app)
    revised, job = generate(app, edit(source, scope))
    assert not operation_errors(job.input_snapshot, revised)
    assert [e.episode_id for e in revised.episodes] == [e.episode_id for e in source.episodes]
    if scope in ("line", "scene"):
        assert revised.episodes[1] == source.episodes[1]
        assert revised.episodes[0].scenes[1] == source.episodes[0].scenes[1]
        assert {f["value"] for f in revised.continuity_state["facts"] if f["id"] == "ep1"} == {"共同核对完成"}
    assert app.get_story_version(source.story_version_id) == source


def test_continue_inherits_complete_content_and_feeds_each_generated_episode_forward():
    app, provider = setup()
    source = create(app)
    continued, job = generate(app, req("continue", source, creation_preferences={"target_episodes": 2}))
    assert continued.episodes[:2] == source.episodes
    assert [e.index for e in continued.episodes] == [1, 2, 3, 4]
    assert len(continued.generated_episode_ids) == 2
    assert "target_duration_sec" in job.input_snapshot.preview.inherited_fields
    assert "三声铃" not in continued.continuity_state["unresolved_threads"]
    fourth = next(p for schema, p in provider.calls if issubclass(schema, DraftEpisode) and p["episode_outline"]["index"] == 4)
    assert any(f["id"] == "ep3" for f in fourth["current_continuity"]["facts"])
    assert fourth["current_continuity"]["relationships"] == [{"id": "trust", "value": 3}]
    assert len(fourth["previous_generated_episodes"]) == 1
    again, _ = generate(app, req("continue", continued))
    assert [e.index for e in again.episodes] == [1, 2, 3, 4, 5]


def test_remix_resets_source_continuity_and_preserves_parent():
    app, provider = setup()
    source = create(app)
    remix, _ = generate(app, req("remix", source, creation_preferences={"target_duration_sec": 120},
        remix={"instruction": "改变事故", "retain_elements": ["广播站"], "transform_elements": ["事故"]}))
    writer = [p for schema, p in provider.calls if issubclass(schema, DraftEpisode)][-1]
    assert writer["current_continuity"] == {}
    assert not any(f["id"] == "ep2" for f in remix.continuity_state["facts"])
    assert remix.story_id != source.story_id and remix.version_no == 1
    assert remix.parent_story_version_id == source.story_version_id


def test_downstream_replanning_is_approved_and_executed():
    app, provider = setup()
    source = create(app)
    revised, job = generate(app, edit(source, downstream="replan_affected"))
    assert job.input_snapshot.preview.impact_analysis["downstream_episode_indexes"] == [2]
    assert len(revised.generated_episode_ids) == 2
    assert revised.episodes[0].scenes[1] == source.episodes[0].scenes[1]
    last_writer = [p for schema, p in provider.calls if issubclass(schema, DraftEpisode)][-1]
    assert last_writer["current_continuity"]["facts"][0]["value"] == "共同核对完成"


@pytest.mark.parametrize("bad_target", [{"scene_id": "missing"}, {"line_id": "missing"}])
def test_invalid_target_fails_before_model_call(bad_target):
    app, provider = setup()
    source = create(app)
    value = edit(source, "line")
    value["edit"]["target"].update(bad_target)
    count = len(provider.calls)
    with pytest.raises(WorkflowConflictError, match="REVISION_TARGET_NOT_FOUND"):
        app.prepare_story_plan(value)
    assert len(provider.calls) == count


def test_scope_escape_and_invented_judge_evidence_block_publication():
    app, provider = setup()
    source = create(app)
    provider.escape = True
    with pytest.raises(ValueError, match="REVISION_SCOPE_VIOLATION"):
        generate(app, edit(source))
    provider.escape = False
    provider.false_quote = True
    with pytest.raises(ValueError, match="OPERATION_REQUIREMENTS_NOT_PROVEN"):
        generate(app, req("continue", source))
    assert len(app.repository.list_story_versions("local")) == 1


def test_historical_parent_siblings_get_distinct_version_numbers():
    app, _ = setup()
    source = create(app)
    first, _ = generate(app, req("continue", source))
    second, _ = generate(app, req("continue", source))
    assert (first.version_no, second.version_no) == (2, 3)
    assert first.parent_story_version_id == second.parent_story_version_id == source.story_version_id


def test_claim_and_explicit_recovery_fence_stale_worker():
    app, _ = setup()
    plan = app.prepare_story_plan(req())
    job = app.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, uuid.uuid4().hex)
    running = app.repository.claim_job(job.job_id)
    with pytest.raises(WorkflowConflictError, match="JOB_NOT_RUNNABLE"):
        app.repository.claim_job(job.job_id)
    app.recover_interrupted_job(job.job_id, running.execution_token)
    with pytest.raises(WorkflowConflictError, match="JOB_EXECUTION_FENCED"):
        app.repository.save_job(running)
    app.retry_failed_job(job.job_id)
    completed = app.generate_from_approved_plan(job.job_id)
    assert app.generate_from_approved_plan(job.job_id) == completed


def test_recovery_during_execution_prevents_stale_publication():
    app, _ = setup()
    executor = app.story_executor
    class Interrupted:
        def execute(self, snapshot):
            running = next(j for j in app.repository._jobs.values() if j.approved_plan_id == snapshot.plan_id)
            result = executor.execute(snapshot)
            app.recover_interrupted_job(running.job_id, running.execution_token)
            return result
    app.story_executor = Interrupted()
    with pytest.raises(WorkflowConflictError, match="JOB_EXECUTION_FENCED"):
        generate(app, req())
    assert not app.repository.list_story_versions("local")


def test_continuity_replaces_identified_facts_instead_of_accumulating_contradictions():
    app, _ = setup()
    source = create(app)
    modified = source.episodes[1].model_copy(update={"fact_delta": [{"id": "ep1", "value": "更新"}]})
    state = fold_continuity([source.episodes[0], modified])
    assert state["facts"] == [{"id": "ep1", "value": "更新"}]


def test_continuation_rejects_recasting_existing_character():
    app, _ = setup()
    context = OperationContext(character_snapshots={"c": {"canon": {"name": "林岚"}}})
    source, _ = generate(app, req(creation_preferences={"target_duration_sec": 120},
        characters={"selected_character_ids": ["c"], "role_bindings": [
            {"character_id": "c", "story_role_id": "hero", "performer_id": "hero"}]}), context)
    with pytest.raises(WorkflowConflictError, match="CHARACTER_IDENTITY_CONFLICT"):
        app.prepare_story_plan(req("continue", source, characters={"selected_character_ids": ["c"],
            "role_bindings": [{"character_id": "c", "story_role_id": "other", "performer_id": "hero"}]}), context)


@pytest.mark.parametrize("ids", [["invented", "facts"], ["instruction", "instruction"], ["instruction"]])
def test_judge_schema_rejects_invented_duplicate_and_missing_requirement_ids(ids):
    schema = bound_quality_schema({"instruction": "执行指令", "facts": "继承事实"})
    with pytest.raises(ValidationError):
        schema(outline_alignment_passed=True, continuity_passed=True, content_rating_passed=True,
               operation_checks=[{"requirement_id": k, "requirement": "事实", "passed": True,
                                  "evidence_quotes": ["正文"]} for k in ids])


def test_failed_claim_releases_repository_transaction():
    class LostClaim(InMemoryWorkflowRepository):
        rolled_back = False
        def claim_job(self, job_id):
            raise WorkflowConflictError("JOB_NOT_RUNNABLE")
        def rollback(self):
            self.rolled_back = True
    repo = LostClaim()
    app, _ = setup(repo)
    plan = app.prepare_story_plan(req())
    job = app.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, uuid.uuid4().hex)
    with pytest.raises(WorkflowConflictError):
        app.generate_from_approved_plan(job.job_id)
    assert repo.rolled_back


def test_formatted_real_citations_are_resolved_but_paraphrases_are_rejected():
    app, _ = setup()
    source = create(app)
    line = source.episodes[0].scenes[0].utterances[0]
    wrapped = OperationCheck(requirement_id="instruction", requirement="事实", passed=True,
        evidence_quotes=[f"[{line.line_id}] {line.text}", f"{line.line_id} (speaker_role_id: hero): '{line.text}'"])
    result = canonical_check_evidence([wrapped], source.episodes)[0]
    assert result["evidence_quotes"] == [line.text, line.text]
    invented = wrapped.model_copy(update={"evidence_quotes": [f"[{line.line_id}] fabricated statement"]})
    assert canonical_check_evidence([invented], source.episodes)[0]["evidence_quotes"] == invented.evidence_quotes


def test_line_id_evidence_copies_actual_text_and_unknown_id_is_schema_error():
    app, _ = setup()
    source = create(app)
    line = source.episodes[0].scenes[0].utterances[0]
    check = OperationCheck(requirement_id="instruction", requirement="事实", passed=True, evidence_line_ids=[line.line_id])
    assert canonical_check_evidence([check], source.episodes)[0]["evidence_quotes"] == [line.text]
    schema = bound_quality_schema({"instruction": "事实"}, [line.line_id])
    with pytest.raises(ValidationError):
        schema(outline_alignment_passed=True, continuity_passed=True, content_rating_passed=True,
            operation_checks=[{"requirement_id": "instruction", "requirement": "事实", "passed": True,
                               "evidence_line_ids": ["made-up-line"]}])


def test_closed_thread_names_must_match_existing_names_without_explanation_suffix():
    app, _ = setup()
    source = create(app)
    schema = bound_thread_schema(EpisodeRevision, {"unresolved_threads": ["三声铃"]})
    data = {"scenes": [{"scene_id": "s", "lines": [{"line_id": "l", "kind": "action", "text": "动作"}]}],
            "episode_summary": "完成", "fact_delta": [], "relationship_delta": [], "new_open_threads": [],
            "closed_threads": ["三声铃（已兑现）"]}
    with pytest.raises(ValidationError):
        schema.model_validate(data)
    data["closed_threads"] = ["三声铃"]
    assert schema.model_validate(data).closed_threads == ["三声铃"]


@pytest.mark.parametrize("count,duration", [(2, 120), (1, 300)])
def test_real_planner_contract_rejects_wrong_count_or_duration(count, duration):
    request = CreateStoryRequest(request_id="contract", idempotency_key="contract", user_instruction="核对维修",
        creation_preferences=CreationPreferences(story_mode="general", target_duration_sec=120, target_episodes=1))
    wrong_request = request.model_copy(update={"creation_preferences": request.creation_preferences.model_copy(
        update={"target_episodes": count, "target_duration_sec": duration})})
    content = FixedPlanGenerator().generate(wrong_request, None, [])
    class ContractProvider:
        def complete_structured(self, spec, system, user, schema):
            return schema.model_validate(content.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        LLMPlanGenerator(ContractProvider()).generate(request, None, [])


def test_request_bound_plan_remains_public_plan_content_after_validation():
    request = CreateStoryRequest(request_id="contract", idempotency_key="contract", user_instruction="核对维修",
        creation_preferences=CreationPreferences(story_mode="general", target_duration_sec=120, target_episodes=1))
    content = FixedPlanGenerator().generate(request, None, [])
    class ContractProvider:
        def complete_structured(self, spec, system, user, schema):
            return schema.model_validate(content.model_dump(mode="json"))
    assert type(LLMPlanGenerator(ContractProvider()).generate(request, None, [])) is PlanContent


def test_closed_multi_episode_plan_allows_nonfinal_hook_but_rejects_final_tail_reveal():
    request=CreateStoryRequest(request_id="contract",idempotency_key="contract",user_instruction="广播站",
        creation_preferences=CreationPreferences(story_mode="general",target_episodes=2,ending_preference="closed"))
    content=FixedPlanGenerator().generate(request,None,[])
    first=content.episode_outlines[0].model_copy(update={"major_beats":["问题","行动","新危机让第二集继续处理"]})
    content=content.model_copy(update={"episode_outlines":[first,content.episode_outlines[1]]})
    validate_generated_plan_ending(content,request.creation_preferences)
    last=content.episode_outlines[1].model_copy(update={"major_beats":["问题","行动","新危机突然出现"]})
    with pytest.raises(ValueError,match="TAIL_REVEAL"):
        validate_generated_plan_ending(content.model_copy(update={"episode_outlines":[first,last]}),request.creation_preferences)


def test_required_audible_event_cannot_be_proven_only_by_dialogue_intention():
    app,_=setup(); source=create(app)
    line=source.episodes[0].scenes[0].utterances[0]
    value=req(client_context={"operation_requirements":{"bell_effect":"三声铃实际响起"},
        "evidence_kinds":{"constraint:bell_effect":["sfx","narration","action"]}})
    operation_requirements(value)
    checks=[{"requirement_id":"constraint:bell_effect","passed":True,"evidence_line_ids":[line.line_id]}]
    assert evidence_kind_errors(value,checks,source.episodes)
    changed_line=line.model_copy(update={"kind":"sfx","text":"叮、叮、叮，三声铃響。"})
    scene=source.episodes[0].scenes[0].model_copy(update={"utterances":[changed_line]})
    ep=source.episodes[0].model_copy(update={"scenes":[scene]})
    assert not evidence_kind_errors(value,checks,[ep])
