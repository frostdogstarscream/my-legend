from copy import deepcopy
from pathlib import Path
import json

from pydantic import BaseModel
import pytest

from my_legend_production.production.plan_readiness import (
    PlanAudit, ProductionPlanPolicy, plan_statements, plan_audit_issues)
from my_legend_production.production.repository import ProductionError


CAPS = {'actions':['enter','exit','move','face','wait'], 'max_scene_cast':4}
REQUEST = {'source':{'idea':'两人交谈后离开，不进行道具操作'}}


class ProposedPlan(BaseModel):
    title: str
    major_beats: list[str]
    performance_plan: dict


def sample():
    return ProposedPlan(title='两个朋友', major_beats=['甲走进院子。','两人交谈，甲离开。'],
                        performance_plan={'sfx':['雨声'], 'nested':{'x/y~z':'静止站立'}})


def review(plan):
    return {'original_requirements_preserved':True, 'requirements_reason':'符合原始需求',
            'capability_conflicts':[], 'statements':[
                {**row, 'actions':[], 'interpretation_faithful':True, 'blocking_issues':[]}
                for row in plan_statements(plan)]}


def test_nested_sound_notes_and_pose_fields_cannot_escape_review():
    plan = sample()
    plan.performance_plan['sfx'] = ['杯子被放到桌上的声音']
    audit = review(plan)
    note = next(r for r in audit['statements'] if r['path']=='/performance_plan/sfx/0')
    note['actions'] = ['prop_operation']
    issues = plan_audit_issues(plan, PlanAudit.model_validate(audit), REQUEST, CAPS)
    assert issues == [{'path':note['path'], 'code':'ACTION_UNSUPPORTED', 'action':'prop_operation', 'quote':note['quote']}]
    assert '/performance_plan/nested/x~1y~0z' in [s['path'] for s in plan_statements(plan)]


@pytest.mark.parametrize('change', ['missing','duplicate','quote','path','order','invented_requirement'])
def test_incomplete_or_fabricated_audit_cannot_approve_plan(change):
    plan = sample()
    audit = review(plan)
    if change == 'missing': audit['statements'].pop()
    if change == 'duplicate': audit['statements'].append(deepcopy(audit['statements'][0]))
    if change == 'quote': audit['statements'][0]['quote'] = '伪造'
    if change == 'path': audit['statements'][0]['path'] = '/unreviewed'
    if change == 'order': audit['statements'].reverse()
    if change == 'invented_requirement':
        audit['capability_conflicts'] = [{'origin':'user_required','path':'/source/idea','quote':'必须抱起孩子','reason':'不支持'}]
    with pytest.raises(ProductionError, match='STORY_PLAN_REVIEW_INVALID'):
        plan_audit_issues(plan, PlanAudit.model_validate(audit), REQUEST, CAPS)


def policy_run(responses, request=REQUEST):
    pytest.importorskip('drama_engine')
    from drama_engine.llm.usage_context import current_usage_context
    class Provider:
        def __init__(self): self.calls = []
        def complete_structured(self, spec, system, user, schema):
            self.calls.append((current_usage_context()['attempt_kind'],json.loads(user)))
            return schema.model_validate(responses.pop(0))
    provider, records = Provider(), []
    policy = ProductionPlanPolicy(provider, {'capabilities':CAPS, 'catalog':{'registry':{}},
                                  'catalog_sha256':'a'*64}, request, records.append)
    def execute(plan):
        return policy(plan, creative_context={}, schema=ProposedPlan, writer_spec=None, review_spec=None)
    return provider, records, execute


def test_extra_unsupported_detail_is_repaired_before_any_plan_approval():
    original = sample()
    original.major_beats[0] = '甲坐下。'
    bad = review(original)
    bad['statements'][1]['actions'] = ['sit']
    repaired = sample()
    provider, records, execute = policy_run([bad,repaired.model_dump(),review(repaired)])
    before = original.model_dump()
    result = execute(original)
    assert result == repaired and original.model_dump() == before
    assert [c[0] for c in provider.calls] == ['production_plan_review','production_plan_repair','production_plan_review']
    assert provider.calls[-1][1]['original_proposed_plan'] == before
    assert records[0]['issue_count'] == 1 and records[-1]['issue_count'] == 0


def test_user_required_unsupported_action_stops_without_deleting_requirement():
    plan = sample()
    request = {'source':{'idea':'必须拥抱，以拥抱完成和解'}}
    audit = review(plan)
    audit['capability_conflicts'] = [{'origin':'user_required','path':'/source/idea','quote':'必须拥抱','reason':'没有拥抱动作'}]
    provider, _, execute = policy_run([audit], request)
    with pytest.raises(ProductionError, match='STORY_REQUIRED_CAPABILITY_UNAVAILABLE'):
        execute(plan)
    assert len(provider.calls) == 1


def test_model_added_conflict_is_repairable_not_misclassified_as_user_requirement():
    plan = sample()
    plan.major_beats[0] = '甲蹲下拨泥土。'
    audit = review(plan)
    audit['capability_conflicts'] = [{'origin':'plan_added','path':'/major_beats/0',
                                    'quote':'甲蹲下拨泥土。','reason':'模型新增的道具动作'}]
    good = sample()
    provider, _, execute = policy_run([audit,good.model_dump(),review(good)])
    assert execute(plan) == good and len(provider.calls) == 3


def test_invalid_conflict_reference_uses_native_bounded_schema_repair():
    pytest.importorskip('drama_engine')
    from drama_engine.llm.base import BaseLLMProvider
    from drama_engine.contracts import LLMResult
    from drama_engine.llm.usage_context import current_usage_context
    plan = sample()
    bad, good = review(plan), review(plan)
    bad['capability_conflicts'] = [{'origin':'user_required','path':'/source/idea',
                                  'quote':'用户未曾要求的动作','reason':'错误归因'}]
    class Provider(BaseLLMProvider):
        def __init__(self): self.calls = []
        def complete(self, spec, system, user):
            self.calls.append((current_usage_context()['attempt_kind'],user))
            return LLMResult(model='local-test',text=json.dumps(bad if len(self.calls)==1 else good))
    provider = Provider()
    policy = ProductionPlanPolicy(provider, {'capabilities':CAPS,'catalog':{},'catalog_sha256':'a'*64}, REQUEST)
    assert policy(plan,creative_context={},schema=ProposedPlan,writer_spec=None,review_spec=None) == plan
    assert [c[0] for c in provider.calls] == ['production_plan_review','json_schema_repair']
    assert 'USER_REQUIREMENT_REFERENCE_INVALID' in provider.calls[-1][1]


def test_bad_repair_does_not_create_unbounded_outline_retries():
    plan = sample()
    audit = review(plan)
    audit['original_requirements_preserved'] = False
    provider, _, execute = policy_run([audit,plan.model_dump(),audit])
    with pytest.raises(ProductionError, match='STORY_PLAN_READINESS_FAILED'):
        execute(plan)
    assert len(provider.calls) == 3


def test_native_plan_is_not_saved_when_production_audit_rejects(monkeypatch):
    pytest.importorskip('drama_engine')
    from drama_engine.workflow.adapters import LLMPlanGenerator
    from drama_engine.workflow.repository import InMemoryWorkflowRepository
    from drama_engine.workflow.service import StoryWorkflowService
    tests = Path(__file__).resolve().parents[2] / 'StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb/tests/unit'
    monkeypatch.syspath_prepend(str(tests))
    from test_phase10_workflow import FixedPlanGenerator, FixedStoryExecutor
    seen = []
    class Provider:
        def complete_structured(self, spec, system, user, schema):
            return schema.model_validate(content.model_dump(mode='json'))
        def production_plan_policy(self, plan, **kwargs):
            seen.append(kwargs)
            raise ProductionError('STORY_PLAN_READINESS_FAILED')
    from drama_engine.workflow.schemas import CreateStoryRequest
    request = CreateStoryRequest.model_validate({'intent':'create','request_id':'plan-readiness-test',
        'idempotency_key':'plan-readiness-test', 'user_instruction':'两人交谈',
        'creation_preferences':{'target_episodes':1,'target_duration_sec':90}})
    content = FixedPlanGenerator().generate(request,None,[])
    repo = InMemoryWorkflowRepository()
    app = StoryWorkflowService(repo,LLMPlanGenerator(Provider()),FixedStoryExecutor())
    with pytest.raises(ProductionError, match='STORY_PLAN_READINESS_FAILED'):
        app.prepare_story_plan(request.model_dump(mode='json'))
    assert not repo._plans and len(seen) == 1
    assert seen[0]['review_spec'].role == 'production_plan_auditor'
    assert seen[0]['creative_context']['user_instruction'] == '两人交谈'


def test_plan_repair_cannot_bypass_original_closed_ending_gate(monkeypatch):
    pytest.importorskip('drama_engine')
    from drama_engine.workflow.adapters import LLMPlanGenerator
    from drama_engine.workflow.schemas import CreateStoryRequest
    tests = Path(__file__).resolve().parents[2] / 'StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb/tests/unit'
    monkeypatch.syspath_prepend(str(tests))
    from test_phase10_workflow import FixedPlanGenerator
    request = CreateStoryRequest.model_validate({'intent':'create','request_id':'plan-original-gate',
        'idempotency_key':'plan-original-gate', 'user_instruction':'完整短篇',
        'creation_preferences':{'target_episodes':1,'target_duration_sec':90,'ending_preference':'closed'}})
    content = FixedPlanGenerator().generate(request,None,[])
    class Provider:
        def complete_structured(self, spec, system, user, schema):
            return schema.model_validate(content.model_dump(mode='json'))
        def production_plan_policy(self, plan, **kwargs):
            revised = plan.model_copy(deep=True)
            revised.episode_outlines[0].major_beats[-1] = '突然出现新的留言，揭开第二个秘密。'
            return revised
    with pytest.raises(ValueError, match='PLAN_CLOSED_ENDING_TAIL_REVEAL'):
        LLMPlanGenerator(Provider()).generate(request,None,[])


def test_native_annotation_retry_is_visible_to_shared_repair_guard():
    pytest.importorskip('drama_engine')
    from drama_engine.workflow.adapters import LLMPerformanceAnnotator
    from drama_engine.llm.usage_context import current_usage_context
    seen = []
    class Provider:
        def complete_structured(self, spec, system, user, schema):
            seen.append(current_usage_context()['attempt_kind'])
            return schema.model_validate({'annotations':[{'line_id':'line-1','emotion':'neutral',
                'tone_instruction':'平静', 'emphasis':[{'span_text':'错误重音' if len(seen)==1 else '你好',
                'strength':'medium'}]}]})
    lines = [{'line_id':'line-1','kind':'dialogue','speaker':'a','text':'你好。','performer_id':'a'}]
    assert LLMPerformanceAnnotator(Provider()).annotate(lines,{})[0].text == '你好。'
    assert seen == ['performance_annotation','performance_annotation_repair']
