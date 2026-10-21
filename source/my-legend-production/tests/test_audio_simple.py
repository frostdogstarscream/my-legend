from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_simple import SimpleAudioPlanner, compile_intents, intent_schema, SIMPLE_POLICY
from my_legend_production.production.audio_choices import slots_for
from my_legend_production.production.compound_audio import resolve_compound_frames, bind_compound
from my_legend_production.production.audio_model import AudioModel
from my_legend_production.production.repository import ProductionError
from test_audio_plan import Context
from test_audio_choices import passing_review
from test_compound_audio import compound_fixture


def fixture():
    _, raw, _ = compound_fixture()
    slots = slots_for(json.loads(raw))
    def sound(text, kind):
        return dict(cue_text=text, kind=kind, placement='under_next_speech', level='ambient', reason='保留主要声音')
    def control(slot, action, text):
        return dict(background_slot=slot, action=action, cue_text=text, reason='按原文控制已有背景')
    choices = {
        'line_0001': dict(sounds=[sound('雨声响起','background')], controls=[], simplifications=[]),
        'line_0003': dict(sounds=[sound('檐下滴水声响起','background')],
            controls=[control('line_0001','continue','雨声渐弱')], simplifications=['省略雨声精细渐弱曲线']),
        'line_0005': dict(sounds=[sound('鸟鸣响起','one_shot')],
            controls=[control('line_0001','stop','雨声和滴水声停止'),control('line_0003','stop','雨声和滴水声停止')],
            simplifications=[]),
    }
    return slots, choices, raw


@pytest.mark.parametrize('frames', [1, 480, 48000, 480000])
def test_compiler_timeline_is_valid_for_tiny_and_large_measured_materials(frames):
    slots, choices, raw = fixture()
    plan, notes = compile_intents(slots, choices, 'fixture', sha256(raw).hexdigest())
    bind_compound(plan, raw)
    durations = {o.operation_id:frames for e in plan.events for o in e.operations if o.mode in ('speech','one_shot','background')}
    positions = resolve_compound_frames(plan, durations)
    speeches = [o for e in plan.events for o in e.operations if o.mode=='speech']
    assert all(positions[b.operation_id,'start'] >= positions[a.operation_id,'end'] for a,b in zip(speeches,speeches[1:]))
    assert [o.cue_text for o in speeches] == [s['text'] for s in slots if s['kind'] in ('dialogue','narration')]
    assert all(positions[o.operation_id,'end']-positions[o.operation_id,'start'] >= 4800
               for e in plan.events for o in e.operations if o.mode=='background')
    assert notes == [dict(slot_id='line_0003', detail='省略雨声精细渐弱曲线')]
    assert len([o for e in plan.events for o in e.operations if o.mode=='change_background']) == 2


@pytest.mark.parametrize('extra', [{'operation_id':'invented'}, {'start':{}}, {'transition_end':{}}, {'gain_db':10}])
def test_model_cannot_supply_execution_parameters(extra):
    slots, choices, _ = fixture()
    choices['line_0001']['sounds'][0].update(extra)
    with pytest.raises(ValidationError):
        intent_schema(slots).model_validate({'choices':choices})


@pytest.mark.parametrize('fault', ['silent_sfx','not_background','duplicate_stop','forged_cue','two_backgrounds'])
def test_main_sound_source_and_control_gates_remain(fault):
    slots, choices, raw = fixture()
    if fault=='silent_sfx': choices['line_0001']['sounds']=[]
    if fault=='not_background': choices['line_0001']['sounds'][0]['kind']='one_shot'
    if fault=='duplicate_stop': choices['line_0005']['controls'].append(deepcopy(choices['line_0005']['controls'][0]))
    if fault=='forged_cue': choices['line_0001']['sounds'][0]['cue_text']='原文没有的爆炸'
    if fault=='two_backgrounds': choices['line_0001']['sounds']*=2
    with pytest.raises(ValueError): compile_intents(slots,choices,'fixture',sha256(raw).hexdigest())


def test_background_at_end_without_speech_is_bounded_and_faded():
    slots, choices, _ = fixture()
    plan,_=compile_intents(slots[:1],{'line_0001':choices['line_0001']},'fixture','a'*64)
    points=resolve_compound_frames(plan,{'sound_line_0001_1':1})
    assert points['sound_line_0001_1','end']==14400
    assert plan.events[0].operations[0].fade_out_ms==100


def test_between_lines_waits_for_effect_and_overlay_does_not():
    slots, choices, raw = fixture()
    durations=None
    plans=[]
    for placement in ('under_next_speech','between_lines'):
        choices['line_0005']['sounds'][0]['placement']=placement
        plan,_=compile_intents(slots,choices,'fixture',sha256(raw).hexdigest())
        durations={o.operation_id:48000 for e in plan.events for o in e.operations if o.mode in ('speech','one_shot','background')}
        plans.append(resolve_compound_frames(plan,durations))
    assert plans[1]['speech_0006','start']-plans[0]['speech_0006','start']==48000


def test_media_fixture_is_current_compiler_output():
    slots, choices, raw = fixture()
    plan,_ = compile_intents(slots,choices,'fixture',sha256(raw).hexdigest())
    saved=json.loads((Path(__file__).parent/'fixtures/simple-audio-linear.json').read_bytes())
    assert saved['plan']==plan.model_dump() and saved['delivery']==json.loads(raw)


def test_independent_review_failure_is_repaired_and_calls_replay(tmp_path):
    slots, choices, raw = fixture()
    ctx=Context(tmp_path)
    ref=ctx.store.put('StoryDelivery','fixture',raw)
    class Model:
        calls=0
        def complete(self,system,payload,schema,timeout):
            self.calls+=1
            if 'arrangement' not in payload:
                return {'content':json.dumps({'choices':choices})}
            answer=passing_review(payload)
            if self.calls==2:
                data=json.loads(answer['content'])
                data['checks']['line_0005']['issues']=[dict(rule='missing_sound_component',evidence='鸟鸣响起',
                    reason='fixture semantic failure must trigger repair',operation_ids=[])]
                answer['content']=json.dumps(data)
            return answer
    model=Model();planner=SimpleAudioPlanner(model)
    result=planner(ctx,ref)
    assert result.status=='succeeded' and model.calls==4
    evidence=ctx.store.json(next(a for a in result.artifacts if a.kind=='AudioArrangementEvidence'))
    assert evidence['execution_policy']==SIMPLE_POLICY and evidence['repairs']==1
    assert planner(ctx,ref)==result and model.calls==4


def test_http_failure_logs_only_sanitized_category_and_status(caplog):
    def handler(request):
        return httpx.Response(429,json={'error':'private prompt and credential secret-value'},request=request)
    model=AudioModel('https://example.invalid/v1','secret-value','fixture',transport=httpx.MockTransport(handler))
    with pytest.raises(ProductionError,match='UNCERTAIN'):
        model.complete('private prompt',{}, {},5)
    assert 'HTTPStatusError' in caplog.text and '429' in caplog.text
    assert 'secret-value' not in caplog.text and 'private prompt' not in caplog.text
