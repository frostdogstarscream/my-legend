from copy import deepcopy
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from my_legend_production.production.compound_audio import (
    CompoundArrangement, bind_compound, compound_bytes, lower_operations, resolve_compound_frames)


def compound_fixture():
    def a(operation_id=None, point='start', offset_ms=0):
        return dict(operation_id=operation_id, point=point, offset_ms=offset_ms)
    def op(operation_id, mode, cue_text, start=None, **kwargs):
        return dict(operation_id=operation_id, mode=mode, cue_text=cue_text, reason='synthetic fixture',
                    start=start or a(), **kwargs)
    def event(n, kind, text, operations):
        return dict(source=dict(episode_id='ep', scene_id='scene1' if n<3 else 'scene2', line_id=f'line{n}'),
                    kind=kind, text=text, identity_id='actor' if kind=='dialogue' else 'narrator' if kind=='narration' else None,
                    operations=operations)
    events = [
        event(0, 'sfx', '雨声响起。', [op('rain','background','雨声响起',end=a('stop_rain'))]),
        event(1, 'dialogue', '你听，雨还在下。', [op('speech1','speech','你听，雨还在下。')]),
        event(2, 'sfx', '雨声渐弱，檐下滴水声响起。', [
            op('fade_rain','change_background','雨声渐弱',a('speech1','end'),control_target='rain',
               transition_end=a('speech2','end'),target_gain_db=-18.0),
            op('drips','background','檐下滴水声响起',a('speech1','end'),end=a('stop_drips'))]),
        event(3, 'dialogue', '我们就在这里等一会。', [op('speech2','speech','我们就在这里等一会。',a('speech1','end'))]),
        event(4, 'sfx', '雨声和滴水声停止，鸟鸣响起。', [
            op('stop_rain','stop_sound','雨声和滴水声停止',a('speech2','end'),stop_target='rain'),
            op('stop_drips','stop_sound','雨声和滴水声停止',a('speech2','end'),stop_target='drips'),
            op('bird','one_shot','鸟鸣响起',a('stop_drips'))]),
        event(5, 'narration', '他们听见了雨后的声音。', [op('narration','speech','他们听见了雨后的声音。',a('bird','end'))]),
    ]
    scenes=[]
    for e in events:
        if not scenes or scenes[-1]['scene_id']!=e['source']['scene_id']:
            scenes.append(dict(scene_id=e['source']['scene_id'],utterances=[]))
        line=dict(line_id=e['source']['line_id'],kind=e['kind'],text=e['text'])
        if e['identity_id']:line['speaker_role_id' if e['kind']=='dialogue' else 'narrator_id']=e['identity_id']
        scenes[-1]['utterances'].append(line)
    raw=json.dumps(dict(story_version_id='fixture',episodes=[dict(episode_id='ep',scenes=scenes)]),ensure_ascii=False).encode()
    plan=dict(source_version_id='fixture',delivery_sha256=sha256(raw).hexdigest(),events=events)
    durations=dict(rain=24000,drips=24000,speech1=96000,speech2=96000,bird=48000,narration=48000)
    return plan,raw,durations


def test_compound_source_is_unique_and_speech_complete_while_operations_are_multiple():
    data,raw,measured=compound_fixture();plan=CompoundArrangement.model_validate(data)
    bind_compound(plan,raw)
    atomic,mapping=lower_operations(plan)
    assert len(plan.events)==6 and len(atomic.events)==9 and len(mapping)==9
    assert mapping['fade_rain']==mapping['drips']==plan.events[2].source.model_dump()
    assert mapping['stop_rain']==mapping['stop_drips']==mapping['bird']
    assert len({tuple(e.source.model_dump().values()) for e in plan.events})==6
    assert [e.text for e in atomic.events if e.mode=='speech']==[e.text for e in plan.events if e.kind in ('dialogue','narration')]
    assert [e.text for e in atomic.events if e.mode=='background']==['雨声响起','檐下滴水声响起']
    assert compound_bytes(CompoundArrangement.model_validate_json(compound_bytes(plan)))==compound_bytes(plan)
    positions=resolve_compound_frames(plan,measured)
    assert positions['rain','end']==positions['drips','end']==192000
    assert positions['fade_rain','start']==96000 and positions['fade_rain','transition_end']==192000
    assert positions['fade_rain','end']==positions['fade_rain','start']
    assert positions['bird','start']==192000 and positions['narration','end']==288000
    # A single measured speech remake shifts dependent controls/backgrounds, without rewriting source.
    remake=resolve_compound_frames(plan,dict(measured,speech1=144000))
    assert remake['rain','end']==240000 and remake['drips','start']==144000
    assert remake['narration','end']==336000 and sha256(raw).hexdigest()==plan.delivery_sha256


@pytest.mark.parametrize('fault', ['duplicate_source','duplicate_operation','unknown_anchor','unknown_control',
    'future_control','speech_control','cycle','origin_end','fabricated_cue','truncated_speech','duplicate_speech',
    'silent_sfx','action_as_audio','marker_gain','missing_background_end','oneshot_end','missing_gain_end',
    'irrelevant_control','non_speech_identity','empty_operations'])
def test_compound_invalid_choices_fail_before_media_generation(fault):
    data,_,_=compound_fixture();e=data['events'];fade=e[2]['operations'][0]
    if fault=='duplicate_source':e.append(deepcopy(e[0]))
    if fault=='duplicate_operation':e[2]['operations'][1]['operation_id']='rain'
    if fault=='unknown_anchor':fade['start']['operation_id']='missing'
    if fault=='unknown_control':fade['control_target']='missing'
    if fault=='future_control':fade['control_target']='drips'
    if fault=='speech_control':fade['control_target']='speech1'
    if fault=='cycle':e[1]['operations'][0]['start']['operation_id']='speech2'
    if fault=='origin_end':e[0]['operations'][0]['start']['point']='end'
    if fault=='fabricated_cue':fade['cue_text']='枪声响起'
    if fault=='truncated_speech':e[1]['operations'][0]['cue_text']='雨还在下'
    if fault=='duplicate_speech':e[1]['operations'].append(deepcopy(e[1]['operations'][0]))
    if fault=='silent_sfx':fade.update(mode='action_note',control_target=None,transition_end=None,target_gain_db=None)
    if fault=='action_as_audio':e[0]['kind']='action'
    if fault=='marker_gain':fade['gain_db']=-3.0
    if fault=='missing_background_end':e[0]['operations'][0]['end']=None
    if fault=='oneshot_end':e[4]['operations'][2]['end']=dict(operation_id='narration',point='end')
    if fault=='missing_gain_end':fade['transition_end']=None
    if fault=='irrelevant_control':e[1]['operations'][0]['stop_target']='rain'
    if fault=='non_speech_identity':e[0]['identity_id']='actor'
    if fault=='empty_operations':e[2]['operations']=[]
    with pytest.raises(ValidationError):CompoundArrangement.model_validate(data)


@pytest.mark.parametrize('fault',['hash','version','text','identity','missing_source','order'])
def test_compound_binds_unchanged_story_delivery(fault):
    data,raw,_=compound_fixture()
    if fault=='hash':data['delivery_sha256']='0'*64
    if fault=='version':data['source_version_id']='other'
    if fault=='text':data['events'][2]['text']+='这句话不是原文。'
    if fault=='identity':data['events'][1]['identity_id']='other'
    if fault=='missing_source':data['events'].pop()
    if fault=='order':data['events'][3],data['events'][5]=data['events'][5],data['events'][3]
    with pytest.raises(ValueError):bind_compound(CompoundArrangement.model_validate(data),raw)


@pytest.mark.parametrize('fault',['missing_asset','control_asset','zero_frames','late_control','overlap','long_output'])
def test_compound_measured_positions_reject_unexecutable_claims(fault):
    data,_,measured=compound_fixture()
    if fault=='missing_asset':measured.pop('drips')
    if fault=='control_asset':measured['fade_rain']=1
    if fault=='zero_frames':measured['bird']=0
    if fault=='late_control':data['events'][2]['operations'][0]['transition_end']['offset_ms']=1
    if fault=='overlap':data['events'][3]['operations'][0]['start']['point']='start'
    if fault=='long_output':measured['narration']=48000*1801
    with pytest.raises(ValueError):resolve_compound_frames(CompoundArrangement.model_validate(data),measured)


def test_compound_schema_exposes_conditional_fields_to_model():
    from jsonschema import Draft202012Validator
    data,_,_=compound_fixture();validator=Draft202012Validator(CompoundArrangement.model_json_schema())
    assert validator.is_valid(data)
    data['events'][0]['operations'][0]['end']=None
    assert not validator.is_valid(data)
    data,_,_=compound_fixture();data['events'][4]['operations'][2]['end']=dict(operation_id='narration',point='end')
    assert not validator.is_valid(data)
    data,_,_=compound_fixture();data['events'][1]['operations']*=2
    assert not validator.is_valid(data)
