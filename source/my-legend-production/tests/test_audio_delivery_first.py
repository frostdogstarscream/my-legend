"""Delivery-first must preserve every spoken line and expose degraded sound evidence."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from my_legend_production.production.audio_delivery_policy import omit_sounds, speech_only_plan
from my_legend_production.production.compound_audio import CompoundArrangement, bind_compound
from my_legend_production.production.contracts import ArtifactRef
from test_compound_audio import compound_fixture
from test_compound_audio_render import fixture_media


def test_semantic_sound_failure_delivers_warned_plan_without_changing_speech(tmp_path):
    pytest.importorskip('psycopg')
    from test_sound_bridge import opening_context, finish_bridge, MisclassifiedOpeningModel
    from my_legend_production.production.sound_bridge import validate_package
    ctx=opening_context(tmp_path)
    ctx.request.audio_delivery_policy='audio_first'
    model=MisclassifiedOpeningModel(refuse_reclassification=True)
    bridge,result=finish_bridge(ctx,model)
    assert result.status=='succeeded'
    refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind:a for a in result.artifacts})
    package=validate_package(ctx,refs)
    assert package.speech_tasks
    evidence=ctx.store.json(refs['AudioArrangementEvidence'])
    assert evidence['semantic_review']=='not_passed' and evidence['omissions']
    before=len(model.calls)
    assert bridge(ctx)==result and len(model.calls)==before
    ctx.request.audio_delivery_policy='strict'
    with pytest.raises(Exception,match='SOUND_BRIDGE_PACKAGE_INVALID'):
        validate_package(ctx,refs)


def test_omitting_sound_preserves_speech_and_stops_its_controls():
    data,raw,_=compound_fixture()
    original=CompoundArrangement.model_validate(data)
    plan,omissions=omit_sounds(original,{'rain'},'fixture missing sound')
    assert original.events[0].operations[0].mode=='background'
    assert plan.events[0].operations[0].mode=='action_note'
    assert any(o['original_mode']=='stop_sound' for o in omissions)
    assert [e for e in plan.events if e.kind in ('dialogue','narration')]==[
        e for e in original.events if e.kind in ('dialogue','narration')]
    bind_compound(plan,raw)
    with pytest.raises(ValueError,match='DELIVERY_OMISSION_MUST_BE_SOUND'):
        omit_sounds(original,{'speech1'},'must not omit speech')


def test_speech_only_fallback_compiles_real_pcm_and_keeps_all_source_rows(tmp_path,monkeypatch):
    from my_legend_production.production.compound_audio_render import compile_compound
    data,raw,_=compound_fixture()
    plan,omissions=speech_only_plan(json.loads(raw),sha256(raw).hexdigest(),'fixture no valid sound plan')
    assert omissions
    measured={o.operation_id:48000 for e in plan.events for o in e.operations if o.mode=='speech'}
    root,assets,_=fixture_media(tmp_path,monkeypatch,measured)
    timeline,_=compile_compound(root,tmp_path/'compiled',raw,plan,assets)
    assert timeline.total_frames==48000*len(measured)
    assert [(e.source,e.text,e.kind) for e in timeline.events]==[(e.source,e.text,e.kind) for e in plan.events]


def test_native_sound_failure_can_be_partial_but_missing_speech_cannot(tmp_path,monkeypatch):
    from my_legend_production.production import audio_job
    directory=tmp_path/'scene';preview=directory/'audio-workflow/preview'
    preview.mkdir(parents=True)
    (directory/'audio-workflow/host-result.json').write_text(json.dumps(dict(
        status='awaiting_review',technical_passed=False,preview_directory='preview')))
    (preview/'assets.json').write_text(json.dumps({'voice':{'processed':{}}}))
    batch=dict(path=str(directory),audio_script=dict(events=[
        dict(block_id='voice',kind='speech'),dict(block_id='effect',kind='sfx')]))
    def fail(*args):raise ValueError('AUDIO_REQUIRES_MANUAL_RECOVERY')
    monkeypatch.setattr(audio_job,'run_audio',fail)
    result=audio_job.run_delivery_batch(batch,None,audio_first=True)
    assert result['technical_passed'] is False and result['omitted_block_ids']==['effect']
    (preview/'assets.json').write_text('{}')
    with pytest.raises(Exception,match='AUDIO_SPEECH_INCOMPLETE'):
        audio_job.run_delivery_batch(batch,None,audio_first=True)


def test_partial_native_assets_compile_without_creating_silent_sound_file(tmp_path,monkeypatch):
    from my_legend_production.production.audio_job import combine_arranged
    from test_sound_bridge_render import control_fixture
    raw,plan=control_fixture()
    data=plan.model_dump()
    data['events'][0].pop('sound_intent')
    data['events'][0]['operations'][0]['mode']='one_shot'
    original=CompoundArrangement.model_validate(data)
    effective,omissions=omit_sounds(original,{'control_marker'},'fixture')
    root,assets,_=fixture_media(tmp_path,monkeypatch,{'speech_0002':48000})
    directory=root/'scene/audio-workflow';preview=directory/'preview'
    preview.mkdir(parents=True)
    source=root/assets['speech_0002']['processed']['file']
    (directory/source.name).write_bytes(source.read_bytes())
    (preview/'assets.json').write_text(json.dumps(assets))
    batch=dict(path=str(root/'scene'),audio_script=dict(events=[
        dict(block_id='control_marker',kind='sfx'),dict(block_id='speech_0002',kind='speech')]),
        operation_map=[dict(source=e.source.model_dump(),operation_id=o.operation_id,block_id=o.operation_id)
                       for e in original.events for o in e.operations])
    report=combine_arranged(root,[batch],[dict(technical_passed=False,delivery_partial=True,preview_directory='preview')],
        raw,effective,omitted_operations=['control_marker'])
    assert report['total_frames']==48000
    timeline=json.loads((root/'output/timeline.json').read_bytes())
    assert timeline['events'][0]['sound_intent']=='omitted_for_delivery'
    assert timeline['events'][0]['operations'][0]['source_audio_sha256'] is None
    assert timeline['events'][1]['operations'][0]['source_audio_sha256']==assets['speech_0002']['processed']['sha256']
