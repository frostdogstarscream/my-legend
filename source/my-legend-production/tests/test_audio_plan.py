from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
import pytest

from my_legend_production.production.audio_arrangement import AudioArrangement, arrangement_bytes
from my_legend_production.production.audio_model import AudioModel
from my_legend_production.production.audio_plan import AudioPlanner
from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.repository import ProductionError
from test_audio_arrangement import fixture_plan


def simple_plan(payload):
    """Fixture choices, not a production semantic planner."""
    rows=[];previous=None
    for slot in payload['required_events']:
        mode='speech' if slot['kind'] in ('dialogue','narration') else 'action_note' if slot['kind']=='action' else 'one_shot'
        rows.append(dict(**slot,mode=mode,evidence_quote=slot['text'],reason='synthetic test choice',
                         start=dict(event=previous,point='end' if previous else 'start')))
        previous=slot['source']
    return AudioArrangement(schema_version=payload['schema_version'],source_version_id=payload['source_version_id'],delivery_sha256=payload['delivery_sha256'],events=rows).model_dump()


class Model:
    def __init__(self,fault=None):self.calls=[];self.fault=fault
    def complete(self,system,payload,schema,timeout):
        self.calls.append((system,deepcopy(payload),schema))
        if self.fault=='network':raise RuntimeError('uncertain request')
        if 'arrangement' not in payload:
            data=simple_plan(payload)
            if self.fault=='plan_schema':data['events']=[]
        else:
            data=dict(source_version_id=payload['source_version_id'],delivery_sha256=payload['delivery_sha256'],
                arrangement_sha256=payload['arrangement_sha256'],passed=True,
                checks=[dict(source=e['source'],text=e['text'],passed=True,issues=[],advisories=[]) for e in payload['required_events']])
            if self.fault=='review_schema' and len(self.calls)==2:data['checks']=[]
            if self.fault=='review_binding':data['arrangement_sha256']='0'*64
            if self.fault=='semantic':
                data.update(passed=False);data['checks'][0].update(passed=False,issues=['原文要求持续背景，但方案改成单次声音'])
        return dict(content=json.dumps(data,ensure_ascii=False),usage={'total_tokens':1},requested_model='fixture')


class Context:
    def __init__(self,tmp_path,repairs=2):
        self.store=ArtifactStore(tmp_path/'artifacts');self.effects={};self.units=0;self.active=True
        self.stage={'deadline':datetime.now(timezone.utc)+timedelta(minutes=5)}
        self.request=SimpleNamespace(budget=SimpleNamespace(max_repairs_per_stage=repairs))
    def check_active(self):
        if not self.active:raise ProductionError('CANCEL_REQUESTED')
    def effect(self,key,value,operation,units=0):
        if key not in self.effects:
            self.units+=units
            try:self.effects[key]=operation()
            except Exception:raise ProductionError('EXTERNAL_RESULT_UNKNOWN') from None
        return self.effects[key]


def run(tmp_path,fault=None):
    ctx=Context(tmp_path);_,raw=fixture_plan()
    ref=ctx.store.put('StoryDelivery','v',raw)
    model=Model(fault);planner=AudioPlanner(model)
    return ctx,ref,model,planner


def test_plan_review_artifact_and_replay_keep_precise_hashes_and_bounded_calls(tmp_path):
    ctx,ref,model,planner=run(tmp_path)
    result=planner(ctx,ref)
    assert result.status=='succeeded' and ctx.units==2
    refs={a.kind:a for a in result.artifacts}
    plan_raw=ctx.store.verify(refs['AudioArrangement']).read_bytes()
    assert sha256(plan_raw).hexdigest()==refs['AudioArrangement'].sha256
    assert arrangement_bytes(AudioArrangement.model_validate_json(plan_raw))==plan_raw
    review=ctx.store.json(refs['AudioArrangementReview'])
    assert review['arrangement_sha256']==refs['AudioArrangement'].sha256
    assert planner(ctx,ref)==result and len(model.calls)==2
    assert model.calls[0][2]['properties']['events']['minItems']==5
    assert model.calls[1][2]['properties']['checks']['maxItems']==5


@pytest.mark.parametrize('fault,calls,success',[('plan_schema',3,False),('semantic',6,False),
    ('review_schema',3,True),('review_binding',4,False)])
def test_repairs_are_bounded_and_invalid_review_only_repairs_review(tmp_path,fault,calls,success):
    ctx,ref,model,planner=run(tmp_path,fault)
    result=planner(ctx,ref)
    assert (result.status=='succeeded')==success
    assert len(model.calls)==ctx.units==calls
    if fault.startswith('review'):
        assert all('arrangement' in p for _,p,_ in model.calls[1:])
    if not success:
        assert result.artifacts[0].kind=='AudioArrangementIssueReport'


def test_unknown_submission_and_cancel_are_never_model_repairs(tmp_path):
    ctx,ref,model,planner=run(tmp_path,'network')
    with pytest.raises(ProductionError,match='EXTERNAL_RESULT_UNKNOWN'):planner(ctx,ref)
    assert len(model.calls)==1
    ctx.active=False
    with pytest.raises(ProductionError,match='CANCEL_REQUESTED'):planner(ctx,ref)
    assert len(model.calls)==1


def test_audio_model_uses_configured_endpoint_schema_and_no_hidden_retry():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'model':'configured-model','choices':[{'message':{'content':'{}'}}]})
    model=AudioModel('https://model.example/v1','fake-test-key','configured-model',transport=httpx.MockTransport(handler))
    assert model.complete('system',{'text':'原文'},{'type':'object'},10)['content']=='{}'
    assert str(calls[0].url)=='https://model.example/v1/chat/completions'
    body=json.loads(calls[0].content);assert body['model']=='configured-model' and body['stream'] is False
    def failed(request):calls.append(request);return httpx.Response(503)
    bad=AudioModel('https://model.example/v1','fake-test-key','m',transport=httpx.MockTransport(failed))
    with pytest.raises(ProductionError,match='AUDIO_MODEL_RESPONSE_UNCERTAIN'):bad.complete('s',{}, {},10)
    assert len(calls)==2


def test_exported_model_schema_explicitly_forbids_sfx_as_action_note(tmp_path):
    from jsonschema import Draft202012Validator
    ctx,ref,model,planner=run(tmp_path)
    assert planner(ctx,ref).status=='succeeded'
    _,payload,schema=model.calls[0]
    data=simple_plan(payload);data['events'][0]['mode']='action_note'
    assert list(Draft202012Validator(schema).iter_errors(data))


@pytest.mark.parametrize('mode,fields,valid', [
    ('background', {}, False), ('background', {'end': None}, False),
    ('background', {'end': {'event': None, 'point': 'start'}}, True),
    ('one_shot', {'end': {'event': None, 'point': 'start'}}, False),
    ('one_shot', {'end': None}, True),
    ('continue_background', {}, False),
    ('continue_background', {'control_target': {'episode_id': 'e', 'scene_id': 's', 'line_id': 'l'}}, True),
    ('change_background', {'control_target': {'episode_id': 'e', 'scene_id': 's', 'line_id': 'l'},
                           'transition_end': {'event': None}, 'target_gain_db': -12}, True),
    ('change_background', {'control_target': {'episode_id': 'e', 'scene_id': 's', 'line_id': 'l'},
                           'transition_end': {'event': None}, 'target_gain_db': None}, False),
    ('stop_sound', {'stop_target': {'episode_id': 'e', 'scene_id': 's', 'line_id': 'l'}}, True),
    ('stop_sound', {'stop_target': {'episode_id': 'e', 'scene_id': 's', 'line_id': 'l'}, 'gain_db': -3}, False),
    ('one_shot', {'fade_out_ms': 100}, False),
    ('one_shot', {'target_gain_db': -12}, False),
])
def test_model_schema_and_runtime_agree_on_mode_fields(mode, fields, valid):
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from my_legend_production.production.audio_arrangement import SoundDecision
    data = dict(source={'episode_id': 'e', 'scene_id': 's', 'line_id': 'self'},
                kind='sfx', text='声音', evidence_quote='声音', reason='fixture',
                mode=mode, start={'event': None}, **fields)
    assert Draft202012Validator(SoundDecision.model_json_schema()).is_valid(data) == valid
    if valid:
        SoundDecision.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            SoundDecision.model_validate(data)
