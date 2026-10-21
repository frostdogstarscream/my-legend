from copy import deepcopy
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_arrangement import AudioArrangement,resolve_frames,source_key
from my_legend_production.production.timeline import read_timeline,ControlledTimeline,ArrangedTimeline
from test_audio_arrangement import fixture_plan,durations,native_assets


def controlled_plan(base_gain=0.0,target_gain=-12.0):
    d,_=fixture_plan();d['schema_version']='audio_arrangement/1.1';e=d['events']
    background=e[0]['source'];speech=e[1]['source'];second=e[2]['source']
    continuation=dict(source=dict(episode_id='ep',scene_id='s1',line_id='continue'),kind='sfx',text='雨声持续。',
        mode='continue_background',evidence_quote='雨声持续。',reason='同一环境声继续',control_target=background,
        start=dict(event=speech,point='start',offset_ms=1000))
    change=dict(source=dict(episode_id='ep',scene_id='s2',line_id='change'),kind='sfx',text='雨声逐渐变小。',
        mode='change_background',evidence_quote='雨声逐渐变小。',reason='改变已有雨声的音量',control_target=background,
        start=dict(event=speech,point='end'),transition_end=dict(event=second,point='start',offset_ms=500),target_gain_db=target_gain)
    e[0]['gain_db']=base_gain;e[3]['kind']='sfx'
    d['events']=e[:2]+[continuation,change]+e[2:]
    scenes=[]
    for event in d['events']:
        loc=event['source']
        if not scenes or scenes[-1]['scene_id']!=loc['scene_id']:
            scenes.append(dict(scene_id=loc['scene_id'],utterances=[]))
        line=dict(line_id=loc['line_id'],kind=event['kind'],text=event['text'])
        if event.get('identity_id'):line['narrator_id' if event['kind']=='narration' else 'speaker_role_id']=event['identity_id']
        scenes[-1]['utterances'].append(line)
    raw=json.dumps(dict(story_version_id='v',episodes=[dict(episode_id='ep',scenes=scenes)]),ensure_ascii=False).encode()
    d['delivery_sha256']=sha256(raw).hexdigest()
    return d,raw


@pytest.mark.parametrize('fault',['old_version','missing_target','speech_target','late_continue','late_ramp','early_ramp',
    'no_change','unknown_end','overlap','cycle','sfx_note'])
def test_controls_cannot_hide_missing_sound_or_unexecutable_changes(fault):
    d,_=controlled_plan();e=d['events'];change=e[3]
    if fault=='old_version':d['schema_version']='audio_arrangement/1.0'
    if fault=='missing_target':del change['control_target']
    if fault=='speech_target':change['control_target']=e[1]['source']
    if fault=='late_continue':e[2]['start']=dict(event=e[5]['source'],point='start')
    if fault=='late_ramp':change['transition_end']['offset_ms']=3000
    if fault=='early_ramp':change['transition_end']=dict(event=None,point='start')
    if fault=='no_change':change['target_gain_db']=0.0
    if fault=='unknown_end':change['transition_end']['event']['line_id']='missing'
    if fault=='overlap':
        another=deepcopy(change);another['source']['line_id']='other';another['target_gain_db']=-18.0
        d['events'].insert(4,another)
    if fault=='cycle':e[1]['start']=dict(event=e[4]['source'],point='end')
    if fault=='sfx_note':e[2]['mode']='action_note';e[2].pop('control_target')
    with pytest.raises(ValueError,match='ARRANGEMENT_'):
        plan=AudioArrangement.model_validate(d);resolve_frames(plan,durations(plan))


@pytest.mark.parametrize('base_gain,target_gain',[(0.0,-12.0),(-18.0,-6.0)])
def test_actual_pcm_executes_background_gain_and_stop_without_new_sound(tmp_path,monkeypatch,base_gain,target_gain):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    from my_legend_production.production.audio_arrangement_render import compile_arrangement
    d,raw=controlled_plan(base_gain,target_gain);plan=AudioArrangement.model_validate(d)
    root,assets=native_assets(tmp_path,monkeypatch,plan)
    assert len(assets)==4 # Three speech assets plus a single rain bed; controls have none.
    timeline,report=compile_arrangement(root,tmp_path/'compiled',raw,plan,assets)
    assert isinstance(timeline,ControlledTimeline) and timeline.schema_version=='measured_timeline/1.2'
    assert read_timeline(timeline.model_dump())==timeline
    with pytest.raises(ValidationError):ArrangedTimeline.model_validate(timeline.model_dump())
    controls=[e for e in timeline.events if e.duration_basis=='sound_control']
    assert [e.marker_type for e in controls]==['continue_background','change_background','stop_sound']
    assert controls[1].start_frame==96000 and controls[1].transition_end_frame==120000
    assert controls[1].from_gain_db==base_gain and controls[1].to_gain_db==target_gain
    assert all(e.start_frame==e.end_frame and e.source_audio_sha256 is None and e.kind=='sfx' for e in controls)
    assert timeline.events[0].processing=='automated_background' and timeline.events[0].end_frame==192000
    pcm,rate=sf.read(tmp_path/'compiled/mix.wav',dtype='float64',always_2d=True)
    expected=np.zeros_like(pcm)
    for e in timeline.events:
        if e.kind in ('dialogue','narration'):
            asset=assets[source_key(e)]
            samples,_=sf.read(root/asset['processed']['file'],always_2d=True)
            expected[e.start_frame:e.end_frame]+=samples
            assert e.source_audio_sha256==asset['processed']['sha256']
    bed,_=sf.read(root/assets[source_key(plan.events[0].source)]['processed']['file'],always_2d=True)
    repeated=np.tile(bed,(8,1))
    envelope=np.full(192000,10**(base_gain/20))
    envelope[96000:120000]=np.linspace(10**(base_gain/20),10**(target_gain/20),24000)
    envelope[120000:]=10**(target_gain/20)
    expected[:192000]+=repeated*envelope[:,None]*10**(-12/20)
    assert np.max(np.abs(pcm[1000:191000]-expected[1000:191000]))<2e-5
    assert np.max(np.abs(pcm[193000:]-expected[193000:]))<2e-5
    assert timeline.total_frames==288000 and len(report['asset_provenance'])==4
    bad=timeline.model_dump();bad['events'][3]['transition_end_frame']=200000
    with pytest.raises(ValidationError,match='TIMELINE_GAIN_NOT_EXECUTED'):read_timeline(bad)
    bad=timeline.model_dump();bad['events'][0]['processing']='looped_background'
    with pytest.raises(ValidationError,match='TIMELINE_GAIN_NOT_EXECUTED'):read_timeline(bad)
