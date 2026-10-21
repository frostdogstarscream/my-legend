from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_arrangement import (
    AudioArrangement, arrangement_bytes, bind_arrangement, resolve_frames, source_key)


def fixture_plan():
    def ref(n):
        return dict(episode_id='ep', scene_id='s'+str((n+1)//2), line_id='l'+str(n))
    def anchor(n=None, point='start', offset_ms=0):
        return dict(event=ref(n) if n else None, point=point, offset_ms=offset_ms)
    rows = [('sfx','雨声持续','background'), ('dialogue','第一句话','speech'),
            ('dialogue','第二句话','speech'), ('action','雨声停止','stop_sound'),
            ('narration','最后一句旁白','speech')]
    events = [dict(source=ref(n), kind=k, text=t, identity_id='voice' if m=='speech' else None,
                   mode=m, evidence_quote=t, reason='测试显式编排', start=anchor())
              for n,(k,t,m) in enumerate(rows,1)]
    events[0]['end']=anchor(4)
    events[2]['start']=anchor(2,'end')
    events[3].update(start=anchor(3,'end'),stop_target=ref(1))
    events[4]['start']=anchor(4)
    scenes = []
    for e in events:
        scene_id=e['source']['scene_id']
        if not scenes or scenes[-1]['scene_id'] != scene_id:
            scenes.append(dict(scene_id=scene_id,utterances=[]))
        line=dict(line_id=e['source']['line_id'],kind=e['kind'],text=e['text'])
        if e['mode']=='speech':line['narrator_id' if e['kind']=='narration' else 'speaker_role_id']='voice'
        scenes[-1]['utterances'].append(line)
    raw=json.dumps(dict(story_version_id='v',episodes=[dict(episode_id='ep',scenes=scenes)]),ensure_ascii=False).encode()
    return dict(source_version_id='v',delivery_sha256=sha256(raw).hexdigest(),duck_gain_db=-12,
                duck_attack_ms=10,duck_release_ms=20,events=events),raw


def durations(plan):
    return {source_key(e.source):(24000 if e.mode=='background' else 96000)
            for e in plan.events if e.mode in ('speech','one_shot','background')}


def test_whole_story_timing_is_bound_to_unchanged_text_and_measured_frames():
    data,raw=fixture_plan();plan=AudioArrangement.model_validate(data)
    bind_arrangement(plan,raw)
    frames=resolve_frames(plan,durations(plan))
    positions=[(frames[source_key(e.source),'start'],frames[source_key(e.source),'end']) for e in plan.events]
    assert positions==[(0,192000),(0,96000),(96000,192000),(192000,192000),(192000,288000)]
    assert arrangement_bytes(plan)==arrangement_bytes(AudioArrangement.model_validate_json(arrangement_bytes(plan)))


@pytest.mark.parametrize('fault', ['unknown_anchor','cycle','end_cycle','speech_stop','duplicate_stop','duplicate_source',
    'made_up_evidence','action_sound','speech_fade','missing_end','missing_target','one_shot_end','origin_end'])
def test_reject_invalid_model_plans(fault):
    d,_=fixture_plan();e=d['events']
    if fault=='unknown_anchor':e[2]['start']['event']['line_id']='missing'
    if fault=='cycle':e[1]['start']=deepcopy(e[2]['start'])
    if fault=='end_cycle':e[2]['start']=dict(event=e[0]['source'],point='end')
    if fault=='speech_stop':e[3]['stop_target']=e[1]['source']
    if fault=='duplicate_stop':
        x=deepcopy(e[3]);x['source']=dict(episode_id='ep',scene_id='extra',line_id='extra');e.append(x)
    if fault=='duplicate_source':e.append(deepcopy(e[0]))
    if fault=='made_up_evidence':e[0]['evidence_quote']='原文没有的声音'
    if fault=='action_sound':e[3]['mode']='one_shot'
    if fault=='speech_fade':e[1]['fade_out_ms']=100
    if fault=='missing_end':del e[0]['end']
    if fault=='missing_target':del e[3]['stop_target']
    if fault=='one_shot_end':e[0]['mode']='one_shot'
    if fault=='origin_end':e[0]['start']['point']='end'
    with pytest.raises(ValidationError):AudioArrangement.model_validate(d)


@pytest.mark.parametrize('fault', ['hash','version','text','identity','missing_line','order'])
def test_source_binding_is_exact(fault):
    d,raw=fixture_plan()
    if fault=='hash':d['delivery_sha256']='a'*64
    if fault=='version':d['source_version_id']='other'
    if fault=='text':d['events'][1].update(text='被修改的第一句话',evidence_quote='第一句话')
    if fault=='identity':d['events'][1]['identity_id']='someone_else'
    if fault=='missing_line':d['events'].pop()
    if fault=='order':d['events'][1],d['events'][2]=d['events'][2],d['events'][1]
    with pytest.raises(ValueError,match='ARRANGEMENT_'):bind_arrangement(AudioArrangement.model_validate(d),raw)


@pytest.mark.parametrize('fault',['negative','overlap','stop_mismatch','fade_too_long','huge_output','missing_asset','marker_asset'])
def test_measured_execution_rejects_bad_bounds_and_false_stop(fault):
    d,_=fixture_plan()
    if fault=='negative':d['events'][0]['start']['offset_ms']=-1
    if fault=='overlap':d['events'][2]['start']['point']='start'
    if fault=='stop_mismatch':d['events'][0]['end']['offset_ms']=1
    if fault=='fade_too_long':d['events'][0]['fade_out_ms']=5000
    plan=AudioArrangement.model_validate(d);ds=durations(plan)
    if fault=='huge_output':ds[source_key(plan.events[-1].source)]=48000*1801
    if fault=='missing_asset':ds.pop(source_key(plan.events[-1].source))
    if fault=='marker_asset':ds[source_key(plan.events[3].source)]=1
    with pytest.raises(ValueError,match='ARRANGEMENT_'):resolve_frames(plan,ds)


def native_assets(tmp_path,monkeypatch,plan):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    workspace=Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(workspace/'runtime/my-legend-production/dependencies/audio/pipeline/src'))
    from lingban_pipeline.media import measure
    root=tmp_path/'assets';root.mkdir()
    assets={}
    for n,(key,count) in enumerate(durations(plan).items()):
        path=root/f'{n}.wav'
        a=.1*np.sin(2*np.pi*(120 if n==0 else 400+100*n)*np.arange(count)/48000)
        sf.write(path,a,48000,subtype='PCM_24')
        assets[key]=dict(asset_id=f'a{n}',artifact_revision='synthetic',task_id=f't{n}',
                         processed=measure(path,root),keypoints=[],processing={},generation={'synthetic':True})
    return root,assets


@pytest.mark.parametrize('background_frames,fade_ms',[(24000,0),(24000,250),(240000,250),(192000,250)])
def test_compiler_mixes_cross_scene_sound_stops_and_preserves_speech(tmp_path,monkeypatch,background_frames,fade_ms):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    from my_legend_production.production.audio_arrangement_render import compile_arrangement
    from my_legend_production.production.timeline import read_timeline
    d,raw=fixture_plan();d['events'][0]['fade_out_ms']=fade_ms
    plan=AudioArrangement.model_validate(d);root,assets=native_assets(tmp_path,monkeypatch,plan)
    from lingban_pipeline.media import measure
    key=source_key(plan.events[0].source);bed=root/assets[key]['processed']['file']
    sf.write(bed,.1*np.sin(2*np.pi*120*np.arange(background_frames)/48000),48000,subtype='PCM_24')
    assets[key]['processed']=measure(bed,root)
    original={p:sha256(p.read_bytes()).hexdigest() for p in root.glob('*.wav')}
    out=tmp_path/'compiled';timeline,report=compile_arrangement(root,out,raw,plan,assets)
    assert timeline.total_frames==288000 and report['provider_calls']==0
    assert timeline.events[3].start_frame==timeline.events[3].end_frame==192000
    assert timeline.events[0].end_frame==192000
    assert read_timeline((out/'timeline.json').read_bytes())==timeline
    assert sha256((out/'arrangement.json').read_bytes()).hexdigest()==timeline.arrangement_sha256
    assert {p:sha256(p.read_bytes()).hexdigest() for p in original}==original
    actual,rate=sf.read(out/'mix.wav',always_2d=True)
    expected=np.zeros_like(actual)
    for event in timeline.events:
        if getattr(event,'render_role',None)=='speech':
            asset=assets[event.episode_id,event.scene_id,event.line_id]
            assert event.source_audio_sha256==asset['processed']['sha256']
            a,_=sf.read(root/asset['processed']['file'],always_2d=True)
            expected[event.start_frame:event.end_frame]+=a
    bed_info=report['asset_provenance'][0]['rendered']['processed']
    b,_=sf.read(out/bed_info['file'],always_2d=True)
    expected[:192000]+=b*10**(-12/20)
    # Compare actual samples, not just the compiler's metadata claims.
    assert np.max(np.abs(actual[1000:191000]-expected[1000:191000]))<2e-5
    assert np.max(np.abs(actual[193000:]-expected[193000:]))<2e-5
    assert rate==48000 and np.max(np.abs(actual))<.95
    if fade_ms:assert abs(b[-1,0])<2e-7
    assert timeline.events[0].parent_audio_sha256==assets[key]['processed']['sha256']
    with pytest.raises(FileExistsError):compile_arrangement(root,out,raw,plan,assets)


@pytest.mark.parametrize('fault',['corrupt_hash','metadata','clock','path_escape','confirmed_crop'])
def test_compiler_rejects_untrusted_or_destructive_asset_processing(tmp_path,monkeypatch,fault):
    pytest.importorskip('soundfile')
    from my_legend_production.production.audio_arrangement_render import compile_arrangement
    d,raw=fixture_plan();plan=AudioArrangement.model_validate(d)
    root,assets=native_assets(tmp_path,monkeypatch,plan)
    first=assets[source_key(plan.events[0].source)]
    if fault=='corrupt_hash':first['processed']['sha256']='a'*64
    if fault=='metadata':first['processed']['frames']+=1
    if fault=='clock':
        import soundfile as sf
        from lingban_pipeline.media import measure
        p=root/first['processed']['file'];a,_=sf.read(p);sf.write(p,a,24000)
        first['processed']=measure(p,root)
    if fault=='path_escape':first['processed']['file']='../outside.wav'
    if fault=='confirmed_crop':
        # A known event marker beyond the crop must not silently disappear.
        import numpy as np
        import soundfile as sf
        from lingban_pipeline.media import measure
        p=root/first['processed']['file'];sf.write(p,np.zeros(240000),48000)
        first['processed']=measure(p,root)
        first['keypoints']=[dict(name='confirmed-hit',frame=220000,status='confirmed')]
    with pytest.raises(ValueError):compile_arrangement(root,tmp_path/'compiled',raw,plan,assets)
