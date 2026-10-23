from copy import deepcopy
import json
import pytest
from test_life_preview import fixture, compile_value
from my_legend_production.production.life_contracts import LifeValidationError
from my_legend_production.production.performance_tracks import track_bindings, validate_direction_track, validate_face_track
from my_legend_production.production.face_preview import face_envelope, EXPRESSION_PROFILE_SHA256
from my_legend_production.production.spine_preview import save_preview
from my_legend_production.production.life_preview import read_life_preview


def test_compile_direction_negation_turn_and_exit(tmp_path):
    store, job, catalog = fixture(tmp_path, '甲背对乙。', 'action')
    result = compile_value(store, job, catalog, dialogue_enabled=True)
    c = result['dialogue']['cues'][0]
    assert c['mode'] == 'away' and c['instance_id'] == 'actor_1' and c['target_instance_id'] == 'actor_2'
    assert c['start_frame'] == 32000 and c['end_frame'] == 80000
    store, job, catalog = fixture(tmp_path/'subject', '甲背对乙，转身。', 'action')
    sequence = compile_value(store, job, catalog, dialogue_enabled=True)
    assert all(c['instance_id'] == 'actor_1' for c in sequence['dialogue']['cues'])
    assert any(d['code'] == 'FACING_SHARED_ANCHOR' for d in sequence['diagnostics'])
    for text in ['甲不要背对乙。', '甲准备转身。', '甲面向乙吗？', '甲和乙转身。', '甲面向书架。']:
        store, job, catalog = fixture(tmp_path / str(len(text)), text, 'action')
        result = compile_value(store, job, catalog, dialogue_enabled=True)
        assert not result['dialogue']['cues'], text
        assert any(d['code'].startswith('FACING_') for d in result['diagnostics'])
    store, job, catalog = fixture(tmp_path/'turn', '甲转身。', 'action')
    assert compile_value(store, job, catalog, dialogue_enabled=True)['dialogue']['cues'][0]['mode'] == 'turn'
    store, job, catalog = fixture(tmp_path/'exit', '甲离场。', 'action')
    value = compile_value(store, job, catalog, dialogue_enabled=True)
    exit_cue = next(c for c in value['stage']['cues'] if c['type'] == 'actor.move_to')
    assert exit_cue['params']['to'][0] < value['stage']['actors'][0]['position'][0]
    assert exit_cue['params']['visible_after'] is False
    saved = compile_value(store, job, catalog, operation='validate_and_save', script=value['stage'], dialogue_enabled=True, dialogue_track=value['dialogue'])
    assert not any(d['code'] == 'MOVEMENT_BLOCKED' for d in saved['diagnostics'])


def test_direction_validation_and_immutable_roundtrip(tmp_path):
    store, job, catalog = fixture(tmp_path, '甲背对乙。', 'action')
    value = compile_value(store, job, catalog, dialogue_enabled=True)
    track = value['dialogue']
    keys = {tuple(c['source'][k] for k in ('episode_id','scene_id','line_id')) for c in track['cues']}
    for mutate in [lambda t:t['cues'].append(deepcopy(t['cues'][0])), lambda t:t['bindings'].__setitem__('audio_sha256','wrong'),
                   lambda t:t['cues'][0].__setitem__('mode','unknown'),lambda t:t['cues'][0].__setitem__('enabled','false'),
                   lambda t:t['cues'][0].__setitem__('target_instance_id','actor_1')]:
        bad = deepcopy(track);mutate(bad)
        with pytest.raises(LifeValidationError): validate_direction_track(bad, value['stage'], keys)
    manual = deepcopy(track['cues'][0]);manual.update(id='manual',rule_id='manual',mode='left',target_instance_id=None)
    track['cues'].append(manual)
    saved_value = compile_value(store, job, catalog, operation='validate_and_save', script=value['stage'],dialogue_enabled=True,dialogue_track=track)
    (tmp_path/'catalogs').mkdir();(tmp_path/'catalogs'/f"{catalog['catalog_sha256']}.json").write_text(json.dumps(catalog),encoding='utf-8')
    first=save_preview(tmp_path,saved_value)
    changed=deepcopy(saved_value);changed['dialogue']['cues'][1]['mode']='right';second=save_preview(tmp_path,changed)
    assert first['preview_sha256'] != second['preview_sha256']
    assert read_life_preview(tmp_path,job,first['preview_sha256'],store)==first


def expression_fixture(tmp_path):
    store, job, catalog=fixture(tmp_path)
    stage=compile_value(store,job,catalog)['stage']
    profile=face_envelope(stage,catalog,mode='expressions')['profile_set']['profiles'][0]
    stage['actors'][0].update(actor_id=profile['actor_id'],actor_revision=profile['actor_revision'])
    catalog={'actors':[{'actor_id':profile['actor_id'],'asset_id':profile['asset_id']},catalog['actors'][1]],'skeletons':[
        {'asset_id':profile['asset_id'],'source_filename':profile['source_filename'],'original':{'sha256':profile['original_sha256']},'skeleton':{'sha256':profile['skeleton_sha256']}}]}
    envelope=face_envelope(stage,catalog,mode='expressions')
    track=deepcopy(envelope['track']);track['cues']=[{'id':'e','instance_id':'actor_1','type':'emotion','start_frame':0,'end_frame':8000,'enabled':True,'emotion':'surprised'}]
    return stage,envelope,track


def test_expression_profile_is_separate_from_basic_and_track_channels(tmp_path):
    stage,envelope,track=expression_fixture(tmp_path)
    assert envelope['profile_sha256']==EXPRESSION_PROFILE_SHA256
    assert envelope['schema_version']=='legend_expression_face/1'
    assert validate_face_track(track,stage,envelope)==track
    track['cues'].append({**track['cues'][0],'id':'blink','type':'blink','emotion':None})
    assert validate_face_track(track,stage,envelope)==track
    track['cues'].append({**track['cues'][0],'id':'overlap'})
    with pytest.raises(LifeValidationError):validate_face_track(track,stage,envelope)


@pytest.mark.parametrize('field,value',[('id',''),('type','unknown'),('enabled','false'),('enabled',1),('start_frame',0.2),('start_frame',True),('end_frame',90000),('emotion','happy'),('instance_id','actor_2')])
def test_face_track_rejects_invalid_or_unreviewed_cues(tmp_path,field,value):
    stage,envelope,track=expression_fixture(tmp_path);track['cues'][0][field]=value
    with pytest.raises(LifeValidationError):validate_face_track(track,stage,envelope)


def test_face_track_rejects_stale_profile_binding_and_duplicate_id(tmp_path):
    stage,envelope,track=expression_fixture(tmp_path)
    for mutate in [lambda t:t.__setitem__('profile_sha256','0'*64),lambda t:t['bindings']['actors'][0].__setitem__('actor_revision','changed'),lambda t:t['cues'].append(deepcopy(t['cues'][0]))]:
        bad=deepcopy(track);mutate(bad)
        with pytest.raises(LifeValidationError):validate_face_track(bad,stage,envelope)
