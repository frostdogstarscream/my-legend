from copy import deepcopy
import io
import json
import wave

import pytest
from pydantic import ValidationError

from my_legend_production.production.timeline import ArrangedTimeline, MeasuredTimeline, read_timeline


def arranged():
    def audio(n, kind, role, start, end):
        return dict(sequence=n, episode_id='ep', scene_id='s'+str(n), line_id='l'+str(n), kind=kind,
            text='原文'+str(n), start_frame=start, end_frame=end, duration_basis='measured_audio',
            source_audio_sha256='a'*64, render_role=role)
    marker = dict(sequence=3, episode_id='ep', scene_id='s3', line_id='l3', kind='action', text='雨声停止',
        start_frame=48000, end_frame=48000, duration_basis='non_audio_marker', marker_type='stop_sound',
        target={'episode_id':'ep','scene_id':'s1','line_id':'l1'})
    return dict(schema_version='measured_timeline/1.1', source_version_id='v', arrangement_sha256='b'*64,
        sample_rate=48000, channels=1, total_frames=96000, audio_sha256='c'*64,
        events=[audio(1,'sfx','background',0,48000), audio(2,'dialogue','speech',0,48000),
                marker, audio(4,'narration','speech',48000,96000)])


def test_new_timeline_preserves_source_order_but_allows_background_overlap_and_instant_stop():
    data = arranged()
    parsed = read_timeline(json.dumps(data).encode())
    assert isinstance(parsed, ArrangedTimeline)
    assert parsed.events[2].source_audio_sha256 is None
    assert parsed.events[2].start_frame == parsed.events[2].end_frame
    assert [e.line_id for e in parsed.events] == ['l1','l2','l3','l4']
    with pytest.raises(ValidationError):
        MeasuredTimeline.model_validate(data)


@pytest.mark.parametrize('fault', ['wrong_stop_time','unknown_target','speech_target','fake_marker_audio',
    'marker_span','fake_measured_action','empty_audio','speech_overlap','reversed_speech','bounds',
    'missing_parent','cropped_speech','duplicate_source','unbound_arrangement','unknown_version'])
def test_reject_invalid_execution_claims(fault):
    data = arranged()
    marker=data['events'][2]
    if fault=='wrong_stop_time':marker.update(start_frame=47000,end_frame=47000)
    if fault=='unknown_target':marker['target']['line_id']='missing'
    if fault=='speech_target':marker['target'].update(scene_id='s2',line_id='l2')
    if fault=='fake_marker_audio':marker['source_audio_sha256']='d'*64
    if fault=='marker_span':marker['end_frame']+=1
    if fault=='fake_measured_action':marker.update(duration_basis='measured_audio',source_audio_sha256='d'*64)
    if fault=='empty_audio':data['events'][1]['end_frame']=0
    if fault=='speech_overlap':data['events'][3]['start_frame']=47000
    if fault=='reversed_speech':
        data['events'][1].update(start_frame=48000,end_frame=96000)
        data['events'][3].update(start_frame=0,end_frame=48000)
    if fault=='bounds':marker.update(start_frame=96001,end_frame=96001)
    if fault=='missing_parent':data['events'][0]['processing']='looped_background'
    if fault=='cropped_speech':data['events'][1].update(processing='cropped_background',parent_audio_sha256='e'*64)
    if fault=='duplicate_source':data['events'][3].update(scene_id='s2',line_id='l2')
    if fault=='unbound_arrangement':del data['arrangement_sha256']
    if fault=='unknown_version':data['schema_version']='measured_timeline/99'
    with pytest.raises(ValidationError):read_timeline(data)


def test_old_timeline_keeps_original_silent_hold_semantics():
    data=arranged()
    data.pop('arrangement_sha256');data['schema_version']='measured_timeline/1.0'
    data['events']=[dict(sequence=1,episode_id='ep',scene_id='sc',line_id='l',kind='action',text='旧动作',
        start_frame=0,end_frame=100,duration_basis='planned_silent_hold',source_audio_sha256='a'*64)]
    assert isinstance(read_timeline(data), MeasuredTimeline)
    data['events'][0]['end_frame']=0
    with pytest.raises(ValidationError):read_timeline(data)


def test_playback_and_download_audit_bind_new_timeline_to_registered_arrangement(tmp_path):
    from my_legend_production.production.artifacts import ArtifactStore
    from my_legend_production.production.playback import playback_view
    from my_legend_production.production.acceptance_media import audit_downloads
    from my_legend_production.production.repository import ProductionError
    store=ArtifactStore(tmp_path)
    data=arranged()
    plan=store.put_json('AudioArrangement','v',{'synthetic_fixture_only':True})
    pcm=io.BytesIO()
    with wave.open(pcm,'wb') as stream:
        stream.setparams((1,2,48000,0,'NONE','not compressed'));stream.writeframes(b'\0\0'*96000)
    audio=store.put('AudioMix','v',pcm.getvalue(),'audio/wav')
    data.update(arrangement_sha256=plan.sha256,audio_sha256=audio.sha256)
    timing=store.put_json('MeasuredTimeline','v',data)
    script=store.put_json('StoryDelivery','v',{'story_version_id':'v','episodes':[{'episode_id':'ep','scenes':[
        {'scene_id':e['scene_id'],'utterances':[{'line_id':e['line_id'],'kind':e['kind'],'text':e['text']}]}
        for e in data['events']]}]})
    job=dict(id='job',work_id='work',source_version_id='v',status='succeeded',
        artifacts=[r.model_dump() for r in (plan,audio,timing,script)])
    view=playback_view(job,store)
    assert view['manifest']['duration_seconds']==2
    report=audit_downloads(job,view,tmp_path,ffmpeg=None,report_directory=tmp_path)
    assert report['source_events']==4 and report['audio_frames']==96000
    missing=deepcopy(job);missing['artifacts']=missing['artifacts'][1:]
    with pytest.raises(ProductionError,match='ARRANGEMENT_BINDING_MISMATCH'):playback_view(missing,store)
    with pytest.raises(ValueError,match='ARRANGEMENT_BINDING_MISMATCH'):
        audit_downloads(missing,view,tmp_path,ffmpeg=None,report_directory=tmp_path)
    wrong=deepcopy(job);wrong['artifacts'][0]['source_version_id']='different'
    with pytest.raises(ProductionError,match='ARRANGEMENT_BINDING_MISMATCH'):playback_view(wrong,store)
