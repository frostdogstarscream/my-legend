import importlib.util
import json
from pathlib import Path

import pytest
pytest.importorskip('drama_engine')
from drama_engine.workflow.adapters import DraftEpisode, DraftScene, DraftLine, ApprovedPlanLLMExecutor, draft_episode_contract_errors, spoken_character_budget
from drama_engine.contracts import LLMSpec
from drama_engine.workflow.adapters import bound_quality_schema
from pydantic import ValidationError

spec = importlib.util.spec_from_file_location('legend_worker_profile', Path(__file__).resolve().parents[3] / 'ops/my-legend/run_story_worker.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def draft(count, role='speaker'):
    return DraftEpisode(title='test', synopsis='test', episode_summary='test', scenes=[DraftScene(title='one',dramatic_goal='same goal',lines=[DraftLine(kind='dialogue',speaker_role_id=role,text='字'*count)])])


def repair(monkeypatch, counts, role='speaker'):
    observed = []
    def parent(self, **kwargs):
        observed.append(kwargs['observed_sec'])
        return draft(counts[len(observed)-1], role)
    monkeypatch.setattr(ApprovedPlanLLMExecutor, '_repair_duration_scene', parent)
    executor = object.__new__(profile.MomaStoryExecutor)
    minimum, ideal, maximum = spoken_character_budget(90)
    result = executor._repair_duration_scene(draft=draft(400),creative_packet=None,target_duration_sec=90,observed_sec=114,min_chars=minimum,ideal_chars=ideal,max_chars=maximum,approved_role_ids={'speaker'},tier_map=None)
    return result, observed


def test_repair_recalculates_real_text_count_and_stops_on_valid_result(monkeypatch):
    result, observed = repair(monkeypatch,[80,420,315])
    assert observed == [114,23,120]
    assert draft_episode_contract_errors(result,90,{'speaker'})[1] == []


def test_three_failed_repairs_do_not_bypass_original_gate(monkeypatch):
    result, observed = repair(monkeypatch,[400,400,400])
    assert len(observed) == 3
    assert draft_episode_contract_errors(result,90,{'speaker'})[1]


def test_wrong_cast_does_not_trigger_duration_repair_loop(monkeypatch):
    result, observed = repair(monkeypatch,[400],role='unapproved')
    assert len(observed) == 1
    assert any('unapproved dialogue roles' in e for e in draft_episode_contract_errors(result,90,{'speaker'})[1])


def test_repair_request_uses_recalculated_budget_and_preserves_approved_beats():
    request = {'repair_type':'duration_only_single_scene','replacement_scene_spoken_character_target':266,
               'scene_card':{'required_beats':['approved event'],'spoken_character_budget':118,'dialogue_character_budget':80,'narration_character_budget':38},
               'original_scene':{'lines':[{'kind':'dialogue','text':'字'*70},{'kind':'action','text':'字'*100}]},'instruction':'keep facts'}
    original = json.dumps(request,ensure_ascii=False)
    spec = LLMSpec(role='storycraft_episode_writer',model='test',extra={'provider_params':{'existing':True}})
    changed, system, user = profile.profile_request(spec,'system',original)
    body = json.loads(user)
    assert body['scene_card']['spoken_character_budget'] == 266
    assert body['scene_card']['dialogue_character_budget']+body['scene_card']['narration_character_budget'] == 266
    assert body['scene_card']['required_beats'] == ['approved event']
    assert body['original_scene'] == request['original_scene']
    assert '70字符' in body['instruction'] and '+196字符' in system
    assert changed.extra['json_mode'] and changed.extra['provider_params']['existing']
    assert changed.extra['provider_params']['thinking'] == {'type':'enabled'}
    assert changed.max_tokens >= 8192
    assert 'json_mode' not in spec.extra


def test_nonrepair_request_keeps_original_payload():
    spec = LLMSpec(role='storycraft_plan_generator',model='test')
    changed, system, user = profile.profile_request(spec,'system','{"plan":"unchanged"}')
    assert (system,user) == ('system','{"plan":"unchanged"}')
    assert changed.extra['provider_params']['thinking'] == {'type':'disabled'}


def test_judge_pass_requires_source_evidence_before_original_final_gate():
    schema = profile.evidence_quality_schema(bound_quality_schema({'timeline':'continuous events'},['line-1']))
    result = {'outline_alignment_passed':True,'continuity_passed':True,'content_rating_passed':True,
              'reasons':[],'timeline_claims':[],'operation_checks':[{'requirement_id':'timeline','requirement':'continuous events','passed':True,'evidence_line_ids':[],'evidence_quotes':[]}]}
    with pytest.raises(ValidationError, match='PASSED_CHECK_REQUIRES_SOURCE_LINE_IDS'):
        schema.model_validate(result)
    result['operation_checks'][0]['evidence_line_ids'] = ['line-1']
    assert schema.model_validate(result).operation_checks[0].evidence_line_ids == ['line-1']
    result['operation_checks'][0]['evidence_line_ids'] = ['invented-line']
    with pytest.raises(ValidationError):
        schema.model_validate(result)


def test_judge_failure_can_report_missing_evidence_without_becoming_passed():
    schema = profile.evidence_quality_schema(bound_quality_schema({'timeline':'continuous events'},['line-1']))
    result = schema.model_validate({'outline_alignment_passed':True,'continuity_passed':False,'content_rating_passed':True,
              'reasons':['not proven'],'timeline_claims':[],'operation_checks':[{'requirement_id':'timeline','requirement':'continuous events','passed':False,'evidence_line_ids':[],'evidence_quotes':[]}]})
    assert result.operation_checks[0].passed is False
