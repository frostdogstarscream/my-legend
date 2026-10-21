"""Independent PCM expectation for the current linear-compiler fixture."""
from pathlib import Path
import json

import pytest
from my_legend_production.production.compound_audio import CompoundArrangement
from my_legend_production.production.compound_audio_render import compile_compound
from my_legend_production.production.compound_timeline import bind_compound_timeline
from test_compound_audio_render import fixture_media


def test_linear_compiler_background_fades_and_full_speech_have_expected_pcm(tmp_path,monkeypatch):
    np=pytest.importorskip('numpy');sf=pytest.importorskip('soundfile')
    data=json.loads((Path(__file__).parent/'fixtures/simple-audio-linear.json').read_bytes())
    raw=json.dumps(data['delivery'],ensure_ascii=False).encode()
    plan=CompoundArrangement.model_validate(data['plan']);plan.duck_gain_db=0
    measured={o.operation_id:48000 for e in plan.events for o in e.operations if o.mode in ('speech','one_shot','background')}
    root,assets,samples=fixture_media(tmp_path,monkeypatch,measured)
    output=tmp_path/'linear'
    timeline,report=compile_compound(root,output,raw,plan,assets)
    bind_compound_timeline(plan,timeline)
    pcm,rate=sf.read(output/'mix.wav',dtype='float64',always_2d=True)
    assert rate==48000 and len(pcm)==163344
    expected=np.zeros_like(pcm)
    for oid,start,end in [('sound_line_0001_1',0,115248),('sound_line_0003_1',57648,115296)]:
        count=end-start
        bed=np.tile(samples[oid],(4,1))[:count].copy()
        gain=np.full(count,10**(-18/20))
        gain[:4800]=np.linspace(10**(-60/20),10**(-18/20),4800)
        gain[-4800:]*=np.linspace(1,0,4800)
        expected[start:end]+=bed*gain[:,None]
    for oid,start in [('speech_0002',9600),('speech_0004',67248),('speech_0006',115344)]:
        expected[start:start+48000]+=samples[oid]
    expected[115344:163344]+=samples['sound_line_0005_1']*10**(-18/20)
    assert np.max(np.abs(pcm-expected))<2e-5
    assert report['provider_calls']==0 and report['human_listening']=='pending'
