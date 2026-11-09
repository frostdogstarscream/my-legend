from pathlib import Path
from copy import deepcopy
import pytest
from my_legend_production.production.contracts import ArtifactRef
from my_legend_production.production.compound_audio import CompoundArrangement
from my_legend_production.production.sound_bridge import SoundBridgeStage, validate_package
from my_legend_production.production.repository import ProductionError
from test_sound_bridge import setup_context, finish_bridge, BridgeModel


def music_context(tmp_path):
    ctx=setup_context(tmp_path)
    refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    delivery=ctx.store.json(refs['StoryDelivery']);manifest=ctx.store.json(refs['RenderManifest'])
    delivery['episodes'][0]['scenes'][0]['utterances'][0].update(kind='music',text='舒缓的钢琴音乐')
    manifest['tasks'][0].update(source_kind='music',text='舒缓的钢琴音乐')
    ctx.stage['artifacts']=[ctx.store.put_json(k,delivery['story_version_id'],v).model_dump() for k,v in [('StoryDelivery',delivery),('RenderManifest',manifest)]]
    return ctx,delivery,manifest


def test_missing_music_is_recorded_without_dropping_speech_or_calling_music(tmp_path):
    ctx,delivery,manifest=music_context(tmp_path)
    ctx.request.audio_delivery_policy='audio_first';ctx.request.review_policy='listen_first'
    model=BridgeModel();bridge,result=finish_bridge(ctx,model)
    assert result.status=='succeeded'
    refs={a['kind']:ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind:a for a in result.artifacts})
    package=validate_package(ctx,refs)
    assert len(package.speech_tasks)==sum(u['kind'] in ('dialogue','narration') for e in delivery['episodes'] for s in e['scenes'] for u in s['utterances'])
    assert all(t.cue_text!='舒缓的钢琴音乐' for t in package.sound_tasks)
    evidence=ctx.store.json(refs['AudioArrangementEvidence'])
    assert evidence['omissions'][0]['reason']=='MUSIC_MEDIA_REQUIRED'
    assert ctx.store.json(refs['StoryDelivery'])==delivery
    before=len(model.calls);assert bridge(ctx)==result and len(model.calls)==before
    # Real native preparation exercises adapter mapping and paid-call budgets,
    # while stopping before every provider invocation.
    pytest.importorskip('soundfile')
    from my_legend_production.performance_production import prepare_audio,read
    workspace=Path(__file__).resolve().parents[3]
    paths=read(workspace/'runtime/my-legend-production/workflows.json')
    plan=CompoundArrangement.model_validate(ctx.store.json(refs['AudioArrangement']))
    _,_,batches=prepare_audio(tmp_path/'native',delivery,manifest,package.voices,paths,arrangement=plan)
    native=[e for b in batches for e in b['audio_script']['events']]
    music=next(e for e in native if e['text']=='舒缓的钢琴音乐')
    assert music['kind']=='action' and music['duration_ms']==0 and not music['media']


def test_strict_policy_still_blocks_missing_music_before_model(tmp_path):
    ctx,_,_=music_context(tmp_path);model=BridgeModel()
    with pytest.raises(ProductionError,match='AUDIO_INPUT_CAPABILITY_MISSING'):SoundBridgeStage(model)(ctx)
    assert model.calls==[]
