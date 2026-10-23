from copy import deepcopy
import pytest
from test_life_preview import fixture, compile_value
from my_legend_production.production.face_preview import face_envelope
from my_legend_production.production.life_contracts import LifeValidationError, canonical_hash
from my_legend_production.production.spine_preview import save_preview
from my_legend_production.production.life_preview import read_life_preview
import json


def test_opt_in_pins_profile_and_preserves_old_preview(tmp_path):
    store, job, catalog = fixture(tmp_path)
    original = deepcopy(job)
    old = compile_value(store, job, catalog)
    assert 'face' not in old
    enabled = compile_value(store, job, catalog, face_enabled=True)
    assert enabled['stage'] == old['stage']
    assert canonical_hash(enabled['face']['profile_set']) == enabled['face']['profile_sha256']
    assert compile_value(store, job, catalog, face_enabled=True, face_profile_sha256=enabled['face']['profile_sha256']) == enabled
    assert all(d['code'] == 'FACE_PROFILE_UNAVAILABLE' for d in enabled['face']['diagnostics'])
    assert job == original
    (tmp_path/'catalogs').mkdir()
    (tmp_path/'catalogs'/f"{catalog['catalog_sha256']}.json").write_text(json.dumps(catalog), encoding='utf-8')
    for value in [old, enabled]:
        saved = save_preview(tmp_path, value)
        assert read_life_preview(tmp_path, job, saved['preview_sha256'], store) == saved
    with pytest.raises(LifeValidationError):
        compile_value(store, job, catalog, face_enabled=True, face_profile_sha256='0'*64)


def test_matching_profile_checks_original_converted_and_actor_revision():
    script={'actors': [], **{k:'test' for k in ['job_id','source_version_id','catalog_sha256','audio_sha256','timeline_sha256']}}
    p=face_envelope(script, {'actors':[]})['profile_set']['profiles'][0]
    script['actors']=[{'instance_id':'hk','actor_id':p['actor_id'],'actor_revision':p['actor_revision']}]
    asset={'asset_id':p['asset_id'],'source_filename':p['source_filename'],'original':{'sha256':p['original_sha256']},'skeleton':{'sha256':p['skeleton_sha256']}}
    catalog={'actors':[{'actor_id':p['actor_id'],'asset_id':p['asset_id']}],'skeletons':[asset]}
    assert face_envelope(script,catalog)['diagnostics'][0]['code']=='FACE_PROFILE_READY'
    for key in ['original','skeleton']:
        changed=deepcopy(catalog);changed['skeletons'][0][key]['sha256']='bad'
        assert face_envelope(script,changed)['diagnostics'][0]['code']=='FACE_PROFILE_UNAVAILABLE'
    script['actors'][0]['actor_revision']='new'
    assert face_envelope(script,catalog)['diagnostics'][0]['code']=='FACE_PROFILE_UNAVAILABLE'
