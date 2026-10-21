from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest
from pydantic import ValidationError

from my_legend_production.production.compound_audio import CompoundArrangement
from my_legend_production.production.compound_audio_render import compile_compound
from my_legend_production.production.compound_timeline import CompoundTimeline, bind_compound_timeline
from test_compound_audio import compound_fixture


def fixture_media(tmp_path,monkeypatch,measured):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    workspace=Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(workspace/'runtime/my-legend-production/dependencies/audio/pipeline/src'))
    from lingban_pipeline.media import measure
    def reject_network(*args,**kwargs):raise AssertionError('offline compiler attempted network')
    monkeypatch.setattr(socket.socket,'connect',reject_network)
    root=tmp_path/'inputs';root.mkdir()
    assets={};samples={}
    for i,(key,frames) in enumerate(measured.items()):
        wave=.05*np.sin(2*np.pi*(100+70*i)*np.arange(frames)/48000)
        path=root/(key+'.wav');sf.write(path,wave,48000,subtype='PCM_24')
        samples[key]=sf.read(path,dtype='float64',always_2d=True)[0]
        assets[key]=dict(asset_id=key,artifact_revision='fixture',task_id=key,processed=measure(path,root),
                         keypoints=[],processing={},generation={'synthetic':True})
    return root,assets,samples


def test_compound_real_pcm_preserves_source_and_executes_two_simultaneous_sound_changes(tmp_path,monkeypatch):
    sf=pytest.importorskip('soundfile');np=pytest.importorskip('numpy')
    data,raw,measured=compound_fixture();data['duck_gain_db']=0.0
    plan=CompoundArrangement.model_validate(data)
    root,assets,samples=fixture_media(tmp_path,monkeypatch,measured)
    output=tmp_path/'compiled'
    timeline,report=compile_compound(root,output,raw,plan,assets)
    assert timeline.schema_version=='measured_timeline/2.0' and len(timeline.events)==6
    assert sum(len(e.operations) for e in timeline.events)==9 and len(report['asset_provenance'])==6
    assert report['provider_calls']==0 and report['human_listening']=='pending'
    assert len(report['operation_sources'])==9
    assert CompoundTimeline.model_validate_json((output/'timeline.json').read_bytes())==timeline
    bind_compound_timeline(plan,timeline)
    assert timeline.events[2].source==plan.events[2].source
    assert timeline.events[2].text=='雨声渐弱，檐下滴水声响起。'
    assert timeline.events[2].operations[0].target_operation_id=='rain'
    assert timeline.events[2].operations[1].mode=='background'
    assert [x.operation_id for x in timeline.events[4].operations]==['stop_rain','stop_drips','bird']
    pcm,rate=sf.read(output/'mix.wav',dtype='float64',always_2d=True)
    assert rate==48000 and len(pcm)==288000
    expected=np.zeros_like(pcm)
    # Independent arithmetic, without calling the production position or gain resolver.
    rain=np.tile(samples['rain'],(8,1))
    gain=np.ones(192000);gain[96000:]=np.linspace(1,10**(-18/20),96000)
    expected[:192000]+=rain*gain[:,None]
    expected[96000:192000]+=np.tile(samples['drips'],(4,1))
    for key,start,end in [('speech1',0,96000),('speech2',96000,192000),('bird',192000,240000),('narration',240000,288000)]:
        expected[start:end]+=samples[key]
    assert np.max(np.abs(pcm-expected))<2e-5
    assert sha256((output/'mix.wav').read_bytes()).hexdigest()==timeline.audio_sha256
    for source in timeline.events:
        if source.kind in ('dialogue','narration'):
            op=source.operations[0]
            assert op.source_audio_sha256==assets[op.operation_id]['processed']['sha256']
    with pytest.raises(FileExistsError):compile_compound(root,output,raw,plan,assets)
    assert (output/'arrangement.json').read_bytes()!= (output/'operations/arrangement.json').read_bytes()
    assert json.loads((output/'arrangement.json').read_bytes())['schema_version']=='audio_arrangement/2.0'
    assert report['internal_report_sha256']==sha256((output/'operations/compile-report.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('fault',['duplicate_source','duplicate_operation','unknown_target','false_stop','nonzero_marker',
    'missing_audio','marker_audio','unexecuted_gain','wrong_plan_hash','changed_text','changed_cue','changed_gain','changed_target'])
def test_compound_timeline_rejects_false_execution_and_binding(tmp_path,monkeypatch,fault):
    data,raw,measured=compound_fixture();plan=CompoundArrangement.model_validate(data)
    root,assets,_=fixture_media(tmp_path,monkeypatch,measured)
    timeline,_=compile_compound(root,tmp_path/'compiled',raw,plan,assets)
    bad=timeline.model_dump();events=bad['events'];fade=events[2]['operations'][0]
    if fault=='duplicate_source':events.append(deepcopy(events[0]))
    if fault=='duplicate_operation':events[2]['operations'][1]['operation_id']='rain'
    if fault=='unknown_target':fade['target_operation_id']='unknown'
    if fault=='false_stop':events[4]['operations'][0]['start_frame']-=1;events[4]['operations'][0]['end_frame']-=1
    if fault=='nonzero_marker':fade['end_frame']+=1
    if fault=='missing_audio':events[1]['operations'][0]['source_audio_sha256']=None
    if fault=='marker_audio':fade['source_audio_sha256']='0'*64
    if fault=='unexecuted_gain':events[0]['operations'][0]['processing']='looped_background'
    if fault=='wrong_plan_hash':bad['arrangement_sha256']='0'*64
    if fault=='changed_text':events[2]['text']+='修改。'
    if fault=='changed_cue':events[4]['operations'][2]['cue_text']='鸟鸣'
    if fault=='changed_gain':fade['to_gain_db']=-6.0
    if fault=='changed_target':events[4]['operations'][1]['target_operation_id']='rain'
    with pytest.raises(ValueError):bind_compound_timeline(plan,CompoundTimeline.model_validate(bad))


def test_compound_missing_or_marker_assets_fail_before_output(tmp_path,monkeypatch):
    data,raw,measured=compound_fixture();plan=CompoundArrangement.model_validate(data)
    root,assets,_=fixture_media(tmp_path,monkeypatch,measured)
    for bad in ({k:v for k,v in assets.items() if k!='drips'},dict(assets,fade_rain=assets['rain'])):
        with pytest.raises(ValueError,match='COMPOUND_MEASURED_ASSETS_MISMATCH'):
            compile_compound(root,tmp_path/'compiled',raw,plan,bad)
        assert not (tmp_path/'compiled').exists()
