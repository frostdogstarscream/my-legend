from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from my_legend_production.adapters import AdapterService
from my_legend_production.core import AdapterError,PROJECT,ident,parse_json,sha,validate,weighted_length
from my_legend_production.storage import ArtifactStore


@pytest.mark.parametrize('kind',['audio','visual'])
def test_prepared_complete_coverage_and_target_schema(factory,kind):
    store,service,req=factory;result=service.prepare(req(kind))
    assert result['state']=='prepared'
    side=store.json(result['sidecar']);target=store.json(result['target'])
    assert len(side['coverage'])==5
    assert sum(x['disposition']=='mapped' for x in side['coverage'])==4
    assert len({tuple(x['source'].values()) for x in side['coverage']})==5
    validate(target,'target/'+('StandardScript' if kind=='audio' else 'VisualRequest')+'.schema.json')
    assert result['workflow_state']=='not_submitted' and result['media_state']=='not_generated'


def test_narrator_identity_is_preserved(prepared_pair):
    store,results=prepared_pair
    audio=store.json(results['audio']['target']);visual=store.json(results['visual']['target'])
    nar=audio['events'][1];block=visual['script']['scenes'][0]['blocks'][0]
    assert nar['role_id'] and nar['performance']['source_kind']=='narration'
    assert block.get('role_id') is None and block['block_id']==nar['block_id'] and block['text']==nar['text']
    assert 'description' not in audio['events'][0]['sound']
    assert {r['name'] for r in audio['roles']}=={'旁白','林岚','周野'}


@pytest.mark.parametrize('kind',['audio','visual'])
def test_old_joint_profile_still_blocks(factory,kind):
    store,service,req=factory;r=req(kind);r['joint_profile']='joint_v02'
    result=service.prepare(r)
    assert result['state']=='blocked' and result['target'] is None
    assert result['issues'][0]['scope']=='joint'


@pytest.mark.parametrize('code,change,state',[
 ('VOICE_BINDING_REQUIRED',lambda c:c['voice_bindings'].pop(),'needs_context'),
 ('PERFORMER_MISMATCH',lambda c:c['voice_bindings'][0].update(performer_id='wrong'),'blocked'),
 ('VOICE_BINDING_AMBIGUOUS',lambda c:c['voice_bindings'].append(deepcopy(c['voice_bindings'][0])),'blocked'),
 ('VOICE_VERIFICATION_REQUIRED',lambda c:c['voice_bindings'][0].update(verification='verified'),'needs_context'),
 ('EXACT_CONTROL_UNSUPPORTED',lambda c:c.update(required_exact_controls=['character_pause']),'blocked'),
 ('ENRICH_LOCK_VALIDATOR_REQUIRED',lambda c:c.update(performance_mode='enrich'),'blocked'),
 ('CUE_ROUTE_REQUIRED',lambda c:c['cue_routes'].pop(0),'needs_context'),
 ('CUE_ROUTE_INVALID',lambda c:c['cue_routes'][0].update(route='visual_only'),'blocked'),
 ('INSTRUCTION_POLICY_EXCEEDED',lambda c:c.update(instruction_max_weight=1),'blocked'),
 ('CONTEXT_SCHEMA_INVALID',lambda c:c.update(api_key='not-allowed'),'blocked'),
])
def test_audio_failures_are_explicit(factory,code,change,state):
    store,service,req=factory;r=service.prepare(req(edit_context=change))
    assert r['state']==state and r['target'] is None and r['issues'][0]['code']==code
    validate(store.json(r['sidecar']),'adapter.schema.json','Sidecar')


@pytest.mark.parametrize('code,change,state',[
 ('APPEARANCE_REQUIRED',lambda c:c['character_bindings'][0].update(appearance=None),'needs_context'),
 ('SCENE_CONTEXT_REQUIRED',lambda c:c['scene_bindings'].clear(),'needs_context'),
 ('ACTOR_NOT_PRESENT',lambda c:c['scene_bindings'][0].update(initial_cast_source_role_ids=[]),'blocked'),
 ('ACTION_BINDING_REQUIRED',lambda c:c['event_bindings'].clear(),'needs_context'),
 ('ACTION_UNSUPPORTED',lambda c:c['event_bindings'][0].update(action='face',direction='left'),'blocked'),
 ('ACTION_BINDING_INVALID',lambda c:c['event_bindings'][0].update(target_anchor='slot_1'),'blocked'),
 ('EMOTION_MAPPING_REQUIRED',lambda c:c['emotion_map'].pop('calm'),'needs_context'),
 ('CONTEXT_APPROVAL_INVALID',lambda c:c.update(context_confirmed=True),'blocked'),
 ('ASSET_BINDING_REQUIRED',lambda c:c.update(preview_skin='fogport_painted_v1'),'needs_context'),
 ('ACTION_CONFIRMATION_REQUIRED',lambda c:c['event_bindings'][0].update(provenance='model_candidate'),'needs_context'),
 ('CAPTION_SOURCE_INVALID',lambda c:c.update(caption_sources=[{'episode_id':'episode-1','scene_id':'scene-1','line_id':'line-3'}]),'blocked'),
])
def test_visual_failures_are_explicit(factory,code,change,state):
    _,service,req=factory;r=service.prepare(req('visual',edit_context=change))
    assert r['state']==state and r['target'] is None and r['issues'][0]['code']==code


def test_idempotent_replay_and_conflict(factory):
    _,service,req=factory;r=req();first=service.prepare(r)
    assert service.prepare(r)==first
    r2=req(edit_context=lambda c:c.update(profile_revision='2'))
    with pytest.raises(AdapterError,match='IDEMPOTENCY_CONFLICT'):service.prepare(r2)


def test_concurrent_prepare_one_result(factory):
    store,service,req=factory;r=req()
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:service.prepare(r),range(8)))
    assert all(x==results[0] for x in results)
    with store.database() as db:assert db.execute('select count(*) from prepared').fetchone()[0]==1


def test_caller_namespace(factory):
    store,service,req=factory;service.prepare(req())
    other=AdapterService(store,caller='different-local-caller')
    result=other.prepare(req(edit_context=lambda c:c.update(profile_revision='2')))
    assert result['state']=='prepared'


def test_corrupt_cached_target_does_not_silently_rebuild(factory):
    store,service,req=factory;r=req();result=service.prepare(r)
    (store.objects/result['target']['sha256']).write_bytes(b'corrupted')
    with pytest.raises(AdapterError,match='SOURCE_HASH_MISMATCH'):service.prepare(r)


@pytest.mark.parametrize('field,value,code',[
 ('text','先别急，我们一起检查。X','SOURCE_MANIFEST_MISMATCH'),('performer_id','other','SOURCE_MANIFEST_MISMATCH'),
 ('line_id','line-1','DUPLICATE_LINE_ID')])
def test_source_drift_rejected_before_targets(factory,field,value,code):
    _,service,req=factory;r=req(edit_source=lambda s:s['episodes'][0]['scenes'][0]['utterances'][2].update({field:value}))
    with pytest.raises(AdapterError,match=code):service.prepare(r)


def test_wrong_emphasis_occurrence(factory):
    _,service,req=factory
    r=req(edit_source=lambda s:s['episodes'][0]['scenes'][0]['utterances'][2]['emphasis'][0].update(occurrence=2))
    with pytest.raises(AdapterError,match='SOURCE_EMPHASIS_INVALID'):service.prepare(r)


def test_manifest_declared_delivery_format_must_match_source(factory):
    store,service,req=factory;r=req();manifest=store.json(r['source']['render_manifest'])
    manifest['delivery_format_version']='9.0';r['source']['render_manifest']=store.put_json(manifest)
    with pytest.raises(AdapterError,match='SOURCE_VERSION_MISMATCH'):service.prepare(r)


def test_timing_cycle_is_blocked(factory):
    _,service,req=factory
    r=req(edit_context=lambda c:c['cue_routes'][0]['timing_relations'].append({'anchor':'line-1'}))
    result=service.prepare(r)
    assert result['issues'][0]['code']=='TIMING_CYCLE' and result['target'] is None


def test_cross_module_ids_and_text_coverage(prepared_pair):
    store,results=prepared_pair;sides={k:store.json(r['sidecar']) for k,r in results.items()}
    assert sides['audio']['source_version_id']==sides['visual']['source_version_id']
    assert sides['audio']['roles']==sides['visual']['roles']
    assert [x['text_sha256'] for x in sides['audio']['coverage']]==[x['text_sha256'] for x in sides['visual']['coverage']]
    for vector in parse_json((PROJECT/'examples/identity-vectors.json').read_bytes())['vectors']:
        assert ident(vector['prefix'],vector['parts'])==vector['expected']


@pytest.mark.parametrize('uri',['fixture://../escape.json','fixture://C:/secret','fixture://..\\secret','fixture://%2e%2e/secret'])
def test_fixture_path_escape(factory,uri):
    store,_,_=factory
    with pytest.raises(AdapterError,match='PATH_ESCAPE'):store.read({'ref':uri,'sha256':'0'*64})


def test_fixture_rejected_by_default(tmp_path):
    with pytest.raises(AdapterError,match='FIXTURE_REFERENCE_FORBIDDEN'):
        ArtifactStore(tmp_path/'production').read({'ref':'fixture://source/story_delivery.json','sha256':'0'*64})


@pytest.mark.parametrize('bad',[b'{"a":1,"a":2}',b'{"a":NaN}',b'not json'])
def test_strict_json_parser(bad):
    with pytest.raises(AdapterError):parse_json(bad)


def test_weighted_unicode_policy():
    assert weighted_length('中文A😀𠀀')==8


def test_prepare_performs_no_network(factory,monkeypatch):
    import socket
    calls=[]
    def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('unexpected network access')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    _,service,req=factory
    assert service.prepare(req())['state']=='prepared'
    assert service.prepare(req('visual'))['state']=='prepared'
    assert calls==[]


def test_nonobject_context_returns_blocked_not_an_unhandled_exception(factory):
    store,service,req=factory;r=req();r['context']=store.put_json([])
    result=service.prepare(r)
    assert result['state']=='blocked' and result['target'] is None
    assert result['issues'][0]['code']=='CONTEXT_SCHEMA_INVALID'


def test_new_unmentioned_actor_cannot_enter_cast(factory):
    def edit(c):
        actor=deepcopy(c['character_bindings'][0]);actor['source_role_id']='invented-person'
        c['character_bindings'].append(actor)
    _,service,req=factory
    result=service.prepare(req('visual',edit_context=edit))
    assert result['issues'][0]['code']=='ACTOR_REFERENCE_INVALID'


@pytest.mark.parametrize('occurrence',[None,1.0])
def test_missing_utterance_default_occurrence_is_resolved_without_rewrite(factory,occurrence):
    store,service,req=factory;r=req()
    source=store.json(r['source']['delivery'])
    if occurrence is None:source['episodes'][0]['scenes'][0]['utterances'][2]['emphasis'][0].pop('occurrence')
    else:source['episodes'][0]['scenes'][0]['utterances'][2]['emphasis'][0]['occurrence']=occurrence
    manifest=store.json(r['source']['render_manifest'])
    if occurrence is None:manifest['tasks'][2]['performance']['emphasis'][0].pop('occurrence')
    else:manifest['tasks'][2]['performance']['emphasis'][0]['occurrence']=occurrence
    r['source']['delivery']=store.put_json(source);r['source']['render_manifest']=store.put_json(manifest)
    result=service.prepare(r)
    assert result['state']=='prepared'


def test_integral_json_number_is_normalized_for_strict_visual_model(factory):
    store,service,req=factory
    result=service.prepare(req('visual',edit_context=lambda c:c['event_bindings'][0].update(duration_ms=500.0)))
    assert result['state']=='prepared'
    target=store.json(result['target'])
    assert type(target['script']['scenes'][0]['blocks'][-1]['duration_ms']) is int
