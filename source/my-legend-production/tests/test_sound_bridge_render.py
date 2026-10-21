"""Offline PCM and native mapping checks for an original sfx used only as control."""
from hashlib import sha256
import json
from pathlib import Path
import pytest
from my_legend_production.production.compound_audio import CompoundArrangement
from my_legend_production.production.compound_audio_render import compile_compound
from my_legend_production.production.compound_audio_prepare import expand_compound_script
from test_compound_audio_render import fixture_media


def control_fixture():
    data=json.loads((Path(__file__).parent/'fixtures/simple-audio-linear.json').read_bytes())
    delivery=data['delivery'];plan=data['plan']
    delivery['episodes']=delivery['episodes'][:1]
    delivery['episodes'][0]['scenes']=delivery['episodes'][0]['scenes'][:1]
    delivery['episodes'][0]['scenes'][0]['utterances']=delivery['episodes'][0]['scenes'][0]['utterances'][:2]
    text='脚步声停住，短暂的安静。'
    delivery['episodes'][0]['scenes'][0]['utterances'][0]['text']=text
    raw=json.dumps(delivery,ensure_ascii=False).encode()
    plan['events']=plan['events'][:2];plan['delivery_sha256']=sha256(raw).hexdigest()
    plan['events'][0].update(text=text,sound_intent='control_only',operations=[dict(operation_id='control_marker',
        mode='action_note',cue_text=text,reason='没有活动背景，不生成新声音',start=dict(operation_id=None,point='start',offset_ms=0))])
    plan['events'][1]['operations'][0]['start']=dict(operation_id=None,point='start',offset_ms=0)
    return raw,CompoundArrangement.model_validate(plan)


def test_control_marker_produces_no_native_sound_task():
    raw,plan=control_fixture()
    script={'events':[dict(block_id='old'+str(i),kind='speech' if i else 'sfx',text=e.text,
        sound={'production_description':e.text},performance={},media={}) for i,e in enumerate(plan.events)]}
    mapping={'coverage':[dict(source=e.source.model_dump(),target_block_ids=['old'+str(i)]) for i,e in enumerate(plan.events)]}
    expanded,operations=expand_compound_script(script,mapping,plan)
    assert [e['kind'] for e in expanded['events']]==['action','speech']
    assert expanded['events'][0]['duration_ms']==0 and expanded['events'][0]['sound']=={}
    assert operations[0]['source_kind']=='sfx'


def test_no_material_control_renders_without_changing_source_or_speech(tmp_path,monkeypatch):
    np=pytest.importorskip('numpy');sf=pytest.importorskip('soundfile')
    raw,plan=control_fixture()
    root,assets,samples=fixture_media(tmp_path,monkeypatch,{'speech_0002':48000})
    timeline,report=compile_compound(root,tmp_path/'rendered',raw,plan,assets)
    pcm,rate=sf.read(tmp_path/'rendered/mix.wav',always_2d=True)
    assert rate==48000 and len(pcm)==48000
    assert np.max(np.abs(pcm-samples['speech_0002']))<2e-5
    assert timeline.events[0].kind=='sfx' and timeline.events[0].sound_intent=='control_only'
    assert timeline.events[0].operations[0].source_audio_sha256 is None
    assert report['provider_calls']==0
