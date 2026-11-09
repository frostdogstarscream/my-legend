import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from drama_engine.workflow.adapters import LLMPlanGenerator
from my_legend_production.production.contracts import CreativeSource
from my_legend_production.production.story import plan_body
from test_production_queue import env


def fixture_module():
    path=Path(__file__).resolve().parents[2]/'StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb/tests/unit/test_phase10_workflow.py'
    spec=importlib.util.spec_from_file_location('_duration_workflow_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('duration',[None,90,180])
def test_bridge_auto_has_no_hidden_fixed_target(duration):
    source=CreativeSource(title='信',idea='归还一封信',genre='生活',character_ids=['one'],target_duration_seconds=duration)
    ctx=SimpleNamespace(request=SimpleNamespace(source=source),stage={'job_id':'job','request_hash':'hash'})
    prefs=plan_body(ctx)['creation_preferences']
    if duration is None:
        assert prefs['duration_mode']=='auto' and 'target_duration_sec' not in prefs
    else:
        assert prefs['target_duration_sec']==duration and 'duration_mode' not in prefs
    assert CreativeSource(title='旧请求',idea='旧内容',genre='生活',character_ids=['one']).target_duration_seconds==90


@pytest.mark.parametrize('chosen',[120,300])
def test_model_selects_duration_within_existing_planning_call(chosen):
    f=fixture_module();request=f.make_request(duration_mode='auto',story_mode='general')
    class Provider:
        calls=[]
        def complete_structured(self,spec,system,user,schema):
            payload=json.loads(user[user.index('{'):]);self.calls.append(payload)
            assert 'target_duration_sec' not in payload['preferences']
            assert 'beat_sheet' not in payload
            plan=f.FixedPlanGenerator().generate(request,None,[]).model_dump()
            plan['episode_outlines'][0]['target_duration_sec']=chosen
            return schema.model_validate(plan)
    provider=Provider();plan=LLMPlanGenerator(provider).generate(request,None,[])
    assert plan.episode_outlines[0].target_duration_sec==chosen
    assert len(provider.calls)==1


def test_selected_duration_frozen_for_execution_and_plan_revision():
    f=fixture_module();app=f.make_service()
    class Planner(f.FixedPlanGenerator):
        def generate(self,*args,**kwargs):
            plan=super().generate(*args,**kwargs)
            plan.episode_outlines[0].target_duration_sec=300 if kwargs.get('feedback') else 120
            return plan
    app.plan_generator=Planner()
    plan=app.prepare_story_plan(f.make_request(duration_mode='auto'))
    assert plan.resolved_preferences.target_duration_sec==120
    assert 'target_duration_sec' in plan.auto_filled_fields
    revised=app.revise_story_plan(plan.plan_id,plan.plan_revision,'多写一些相遇的细节')
    assert revised.resolved_preferences.target_duration_sec==300
    job=app.approve_story_plan(revised.plan_id,revised.plan_revision,revised.plan_fingerprint,'duration-approval')
    assert job.input_snapshot.preview.resolved_preferences.target_duration_sec==300


def test_auto_duration_draft_persists_and_submits_idempotently(env,monkeypatch):
    from test_creation_works import setup,submission
    service,body=setup(env,monkeypatch)
    body.source.target_duration_seconds=None
    draft=service.create('alice',body)
    assert draft['payload']['source']['target_duration_seconds'] is None
    one=service.submit('alice',submission(draft));two=service.submit('alice',submission(draft))
    assert one['id']==two['id']
    row=service.flows.row(one['id'],'alice')
    assert row['request']['duration'] is None
    assert row['request']['sound']['source']['target_duration_seconds'] is None

