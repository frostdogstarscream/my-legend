from copy import deepcopy
import json

import pytest

from my_legend_production.production.repository import ProductionError
from my_legend_production.production.story_readiness import DraftAudit, ProductionDraftPolicy, audit_issues
from my_legend_production.production.story_readiness import bound_draft_audit_schema
from my_legend_production.production.story_readiness import bound_draft_repair_schema, draft_repair_budget


CAPS = {'actions':['enter','exit','move','face','wait'], 'max_scene_cast':4}


def test_repair_contract_counts_spoken_text_and_rejects_cosmetic_duration_fix():
    pytest.importorskip('drama_engine')
    from drama_engine.workflow.adapters import DraftEpisode
    from pydantic import ValidationError
    raw, _ = sample()
    raw['scenes'][0]['lines'][1]['text'] = '你好。' * 70
    short = DraftEpisode.model_validate(raw)
    budget = draft_repair_budget(short, 90)
    assert (budget['current'], budget['min'], budget['ideal'], budget['max']) == (210,268,315,362)
    assert budget['add_at_least'] == 58 and budget['remove_at_least'] == 0
    schema = bound_draft_repair_schema(DraftEpisode, 90, {'a'})
    # More silent action text and claimed duration cannot repair the spoken budget.
    raw['scenes'][0]['lines'][0]['text'] += '等待。' * 100
    with pytest.raises(ValidationError, match='STORY_DRAFT_REPAIR_CONTRACT_INVALID'):
        schema.model_validate(raw)
    raw['scenes'][0]['lines'][1]['text'] = '你好。' * 105
    assert draft_repair_budget(schema.model_validate(raw),90)['current'] == 315


def test_repair_contract_normalizes_unspoken_cues_and_still_checks_cast():
    pytest.importorskip('drama_engine')
    from drama_engine.workflow.adapters import DraftEpisode
    from pydantic import ValidationError
    raw, _ = sample()
    schema = bound_draft_repair_schema(DraftEpisode,90,{'a'})
    raw['scenes'][0]['lines'][1]['text'] = '（轻声）' + '你好。' * 70
    with pytest.raises(ValidationError, match='STORY_DRAFT_REPAIR_CONTRACT_INVALID'):
        schema.model_validate(raw)
    raw['scenes'][0]['lines'][1]['text'] = '你好。' * 105
    raw['scenes'][0]['lines'][1]['speaker_role_id'] = 'unknown'
    with pytest.raises(ValidationError, match='unapproved dialogue roles'):
        schema.model_validate(raw)


def test_bound_review_rejects_nonaction_actors_without_rewriting_story():
    from pydantic import ValidationError
    draft, raw = sample()
    original=deepcopy(draft)
    schema=bound_draft_audit_schema(draft,{'a'},CAPS)
    raw['scenes'][0]['lines'][1]['actor_role_ids']=['a']
    with pytest.raises(ValidationError,match='non-action rows must have empty'):
        schema.model_validate(raw)
    raw['scenes'][0]['lines'][1]['actor_role_ids']=[]
    assert schema.model_validate(raw).approved_requirements_preserved
    assert draft==original


def sample():
    draft = {'title':'院子', 'synopsis':'两人交谈', 'episode_summary':'再见', 'scenes':[
        {'title':'进入', 'location':'院子', 'dramatic_goal':'相遇', 'lines':[
            {'kind':'action','text':'甲走进院子。'},
            {'kind':'dialogue','speaker_role_id':'a','text':'你好。' * 105}]},
        {'title':'分别', 'location':'院子', 'dramatic_goal':'约定', 'lines':[
            {'kind':'action','text':'甲离开院子。'}]}]}
    audit = {'approved_requirements_preserved':True, 'required_capability_conflicts':[], 'findings':[],
             'scenes':[]}
    for i, scene in enumerate(draft['scenes'], 1):
        rows = []
        for j, line in enumerate(scene['lines'], 1):
            rows.append({'line_index':j, 'quote':line['text'],
                         'actor_role_ids':['a'] if line['kind']=='action' else [],
                         'actions':[('enter' if i==1 else 'exit')] if line['kind']=='action' else [],
                         'interpretation_faithful':True, 'missing_visible_action':False, 'reason':'与原文一致'})
        audit['scenes'].append({'scene_index':i, 'initial_present':[] if i==1 else ['a'], 'voice_only':[],
            'continuous_from_previous':i>1, 'transition_line_index':None, 'transition_quote':None,
            'continuity_reason':'同一院子连续交谈', 'lines':rows})
    return draft, audit


def finding(raw, severity='blocker', code='CONTINUITY_CONTRADICTION'):
    return {'severity':severity, 'code':code, 'scene_index':1, 'line_index':1,
            'quote':raw['scenes'][0]['lines'][0]['quote'], 'reason':'具体证据对应的问题'}


def test_audit_tracks_real_exit_and_cross_scene_presence_without_mutating_source():
    draft, raw = sample()
    before = deepcopy(draft)
    assert audit_issues(draft, DraftAudit.model_validate(raw), {'a'}, CAPS) == []
    assert draft == before
    raw['scenes'][1]['initial_present'] = []
    issues = audit_issues(draft, DraftAudit.model_validate(raw), {'a'}, CAPS)
    assert any('CONTINUITY_PRESENCE_CHANGED' in i for i in issues)
    assert any('ACTOR_NOT_PRESENT' in i for i in issues)


@pytest.mark.parametrize('defect', ['quote','coverage','actor','nonaction'])
def test_untrustworthy_audit_is_rejected(defect):
    draft, raw = sample()
    if defect == 'quote': raw['scenes'][0]['lines'][0]['quote'] = '伪造证据'
    if defect == 'coverage': raw['scenes'][0]['lines'].pop()
    if defect == 'actor': raw['scenes'][0]['lines'][0]['actor_role_ids'] = ['unknown']
    if defect == 'nonaction': raw['scenes'][0]['lines'][1]['actions'] = ['enter']
    with pytest.raises(ProductionError, match='STORY_READINESS_REVIEW_INVALID'):
        audit_issues(draft, DraftAudit.model_validate(raw), {'a'}, CAPS)


@pytest.mark.parametrize('defect,expected', [
    ('repeat','ALREADY_PRESENT'), ('reset','TRANSITION_EVIDENCE_MISSING'),
    ('unsupported','ACTION_UNSUPPORTED'), ('compound','COMPOUND_OR_AMBIGUOUS_ACTION'),
    ('hidden','MISSING_VISIBLE_ACTION'), ('unfaithful','UNFAITHFUL_INTERPRETATION')])
def test_readiness_catches_unrenderable_story_before_annotation(defect, expected):
    draft, raw = sample()
    if defect == 'repeat': raw['scenes'][1]['lines'][0]['actions'] = ['enter']
    if defect == 'reset': raw['scenes'][1]['continuous_from_previous'] = False
    if defect == 'unsupported': raw['scenes'][0]['lines'][0]['actions'] = ['sit']
    if defect == 'compound': raw['scenes'][0]['lines'][0]['actions'] = ['enter','face']
    if defect == 'hidden': raw['scenes'][0]['lines'][1]['missing_visible_action'] = True
    if defect == 'unfaithful': raw['scenes'][0]['lines'][0]['interpretation_faithful'] = False
    assert any(expected in i for i in audit_issues(draft, DraftAudit.model_validate(raw), {'a'}, CAPS))


def run_policy(responses, records):
    pytest.importorskip('drama_engine')
    from drama_engine.workflow.adapters import DraftEpisode
    from drama_engine.llm.usage_context import current_usage_context

    class Provider:
        calls = []
        def complete_structured(self, spec, system, user, schema):
            self.calls.append((current_usage_context()['attempt_kind'], json.loads(user)))
            return schema.model_validate(responses.pop(0))
    provider = Provider()
    policy = ProductionDraftPolicy(provider, {'capabilities':CAPS,'catalog_sha256':'a'*64},
        {'source':{'idea':'保持人物与结局'}}, records.append)
    original = DraftEpisode.model_validate(sample()[0])
    def execute():
        return policy(original, creative_context='{}', schema=DraftEpisode,
                      writer_spec=None, review_spec=None, approved_roles={'a'}, target_seconds=90)
    return provider, original, execute


def test_one_bounded_repair_then_fresh_audit_preserves_original_and_evidence():
    draft, raw = sample()
    bad = deepcopy(raw)
    bad['findings'] = [finding(raw)]
    records = []
    provider, original, execute = run_policy([bad, draft, raw], records)
    before = original.model_dump(mode='json')
    result = execute()
    assert result.model_dump(mode='json') == before == original.model_dump(mode='json')
    assert [c[0] for c in provider.calls] == ['production_draft_review','production_draft_repair','production_draft_review']
    assert provider.calls[-1][1]['original_draft'] == before
    assert len(records) == 2 and records[0]['issue_count'] == 1 and records[1]['issue_count'] == 0


def test_required_unsupported_fact_fails_without_repair_or_erasing_story():
    _, raw = sample()
    raw['required_capability_conflicts'] = ['明确要求两人拥抱，但素材不支持']
    provider, _, execute = run_policy([raw], [])
    with pytest.raises(ProductionError, match='STORY_REQUIRED_CAPABILITY_UNAVAILABLE'):
        execute()
    assert len(provider.calls) == 1


def test_repaired_draft_still_bad_stops_after_one_repair():
    draft, raw = sample()
    raw['findings'] = [finding(raw)]
    provider, _, execute = run_policy([raw,draft,raw], [])
    with pytest.raises(ProductionError, match='STORY_PRODUCTION_READINESS_FAILED'):
        execute()
    assert len(provider.calls) == 3


def test_editorial_advice_does_not_spend_repair_or_block_valid_draft():
    _, raw = sample()
    raw['findings'] = [finding(raw, severity='advisory', code='EDITORIAL_SUGGESTION')]
    provider, original, execute = run_policy([raw], [])
    assert execute() == original and len(provider.calls) == 1


@pytest.mark.parametrize('defect', ['quote','editorial_blocker'])
def test_blocking_findings_require_source_evidence_and_execution_reason(defect):
    draft, raw = sample()
    issue = finding(raw)
    if defect == 'quote': issue['quote'] = '不是原文'
    if defect == 'editorial_blocker': issue['code'] = 'EDITORIAL_SUGGESTION'
    raw['findings'] = [issue]
    with pytest.raises(ProductionError, match='STORY_READINESS_REVIEW_INVALID'):
        audit_issues(draft, DraftAudit.model_validate(raw), {'a'}, CAPS)


def test_native_executor_runs_policy_before_any_performance_annotation(monkeypatch):
    pytest.importorskip('drama_engine')
    import importlib.util
    from pathlib import Path
    from drama_engine.workflow.adapters import ApprovedPlanLLMExecutor
    tests = Path(__file__).resolve().parents[2] / 'StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb/tests/unit'
    monkeypatch.syspath_prepend(str(tests))
    spec = importlib.util.spec_from_file_location('_native_operations_fixture', tests / 'test_story_operations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app, provider = module.setup()
    plan = app.prepare_story_plan(module.req(creation_preferences={'target_episodes':1,'target_duration_sec':90}))
    job = app.approve_story_plan(plan.plan_id, plan.plan_revision, plan.plan_fingerprint, 'readiness-hook-test')
    seen = []
    def policy(draft, **kwargs):
        seen.append(kwargs)
        raise ProductionError('STORY_PRODUCTION_READINESS_FAILED')
    provider.production_draft_policy = policy
    class NoAnnotation:
        def annotate(self, *args):
            raise AssertionError('annotation ran before readiness')
    with pytest.raises(ProductionError, match='STORY_PRODUCTION_READINESS_FAILED'):
        ApprovedPlanLLMExecutor(provider, NoAnnotation()).execute(job.input_snapshot)
    assert len(seen) == 1 and seen[0]['review_spec'].role == 'production_readiness_auditor'
    assert seen[0]['target_seconds'] == 90 and 'hero' in seen[0]['approved_roles']
