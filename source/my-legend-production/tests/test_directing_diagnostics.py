from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from test_spine_director import sample, seeded, Model, run_worker
from test_production_queue import env
from my_legend_production.production.directing_compile import compile_plan
from my_legend_production.production.directing_diagnostics import diagnose_plan
from my_legend_production.production.directing_protocol import planner_input
from my_legend_production.production.life_contracts import LifeValidationError


def test_diagnostic_projection_exposes_later_failures_without_repairing_plan(tmp_path):
    store,job,_,context,plan=sample(tmp_path)
    plan['actions'][0]['target_id']='missing-object'
    plan['expressions'][0]['preset']='surprised'
    before=deepcopy(plan)
    report=diagnose_plan(plan,context,job,store,tmp_path)
    errors={d['path']:d for d in report['diagnostics']}
    assert errors['approach']['provisional'] is False
    assert errors['eyes']['provisional'] is True and errors['eyes']['excluded_before']==['approach']
    assert 'bookcase_front' in errors['approach']['allowed_targets']
    assert report['acceptance_allowed'] is False and report['stop_reason']=='projection_checked'
    assert not any(k in report for k in ('plan','preview','execution','repaired_plan'))
    assert plan==before
    with pytest.raises(LifeValidationError):compile_plan(job,store,context,plan,tmp_path)


def test_invalid_duration_does_not_hide_actor_state_error(tmp_path):
    store,job,_,context,plan=sample(tmp_path)
    plan['expressions'][0]['duration_ms']=46080
    plan['scenes'][0]['actors'][0]['visible']=False
    plan['actions'][0].update(kind='face',target_kind='none',target_id=None,facing=1)
    before=deepcopy(plan)
    report=diagnose_plan(plan,context,job,store,tmp_path)
    assert any(d['code']=='SCHEMA_INVALID' for d in report['diagnostics'])
    state=next(d for d in report['diagnostics'] if d['path']=='approach')
    assert state['state_at_anchor']['visible'] is False and state['scene_state_resets']
    assert state['prior_cues_in_this_scene']==[] and state['provisional']
    assert plan==before
    with pytest.raises(ValidationError):compile_plan(job,store,context,plan,tmp_path)


def test_diagnostics_do_not_invent_scenes_or_cross_the_probe_limit(tmp_path):
    store,job,_,context,plan=sample(tmp_path)
    plan['scenes'][0]['resource_scene_id']='missing'
    report=diagnose_plan(plan,context,job,store,tmp_path)
    assert report['passes']==1 and report['excluded']==[]
    assert report['stop_reason']=='non_cue_execution_error'
    plan['scenes'][0]['resource_scene_id']='library'
    plan['actions'][0]['target_id']='missing'
    report=diagnose_plan(plan,context,job,store,tmp_path,max_passes=1)
    assert report['passes']==1 and report['stop_reason']=='diagnostic_limit'


def test_planner_gets_exact_milliseconds_and_scene_reset_without_mutating_source(tmp_path):
    _,_,_,context,_=sample(tmp_path);before=deepcopy(context)
    data=planner_input(context)
    for line,original in zip(data['source_lines'],context['model_input']['source_lines']):
        for i,(op,old) in enumerate(zip(line['operations'],original['operations'])):
            assert op['operation_index']==i
            assert op['duration_ms']==round((old['end_frame']-old['start_frame'])*1000/context['model_input']['sample_rate'],3)
            assert op['remaining_scene_ms']>=op['duration_ms']
            assert 'start_frame' not in op and 'end_frame' not in op
    assert 'total_frames' not in data and 'sample_rate' not in data
    assert all('start_frame' not in s and 'end_frame' not in s for s in data['source_scenes'])
    assert 'not inherited' in data['scene_state_rule'] and context==before


def test_once_animation_duration_is_rounded_up_and_not_confused_with_seconds(tmp_path):
    _,_,_,context,_=sample(tmp_path)
    context['model_input']['locked_cast'][0]['actions']['victory']=1.7666666507720947
    data=planner_input(context)
    assert data['locked_cast'][0]['actions']['victory']=={'duration_ms':1767,'playback':'once'}
    assert context['model_input']['locked_cast'][0]['actions']['victory']==1.7666666507720947


def test_successful_diagnostic_projection_cannot_deliver_invalid_model_response(env,tmp_path,monkeypatch):
    repo,store,source,old,request=seeded(env,tmp_path,monkeypatch)
    class BadTargetModel(Model):
        feedback=None
        def complete(self,system,payload,schema,timeout):
            if 'repair_feedback' in payload:self.feedback=payload['repair_feedback']
            response=super().complete(system,payload,schema,timeout)
            if 'required_checks' not in payload:
                value=json.loads(response['content']);line=payload['source_lines'][-1]
                value['actions']=[dict(id='bad-target',role_id='a',kind='move',
                    anchor={'source':line['source']},target_kind='object',target_id='missing',
                    quote=line['text'],reason='invalid target retained in model response')]
                response['content']=json.dumps(value)
            return response
    model=BadTargetModel();jid=repo.create('alice',request);worker=run_worker(repo,store,model)
    assert worker.tick() and worker.tick()
    job=repo.get(jid,'alice')
    assert job['status']=='failed' and model.calls==3
    assert model.feedback['preflight_diagnostics']['stop_reason']=='projection_checked'
    assert model.feedback['preflight_diagnostics']['acceptance_allowed'] is False
    assert not any(a['kind'] in ('SpineStagePreview','PerformanceManifest','SpineDirectingPlan') for a in job['artifacts'])
    assert repo.get(source['id'],'alice')==source
