from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_life_preview import fixture, compile_value
from my_legend_production.production.life_contracts import LifeValidationError, canonical_hash
from my_legend_production.production.life_preview import build_life_preview, read_life_preview
from my_legend_production.production.scene_preview import load_scene_catalog, scene_envelope
from my_legend_production.production.spine_preview import StagePreviewRequest, save_preview
from my_legend_production.production.repository import ProductionError


def setup(tmp_path):
    store, job, actors = fixture(tmp_path)
    baseline = compile_value(store, job, actors)
    image = b'fixture-png-bytes'
    h = sha256(image).hexdigest()
    ref = {'sha256': h, 'size_bytes': len(image), 'media_type': 'image/png'}
    scene = {'scene_id': 'loft', 'width': 960, 'height': 540, 'actor_scale': .72,
             'layout_kind': 'authored_performance_sample',
             'objects': [{'id': 'sofa', 'image': ref, 'layer': 'depth', 'position': [650, 420],
                          'anchor': [.5, 1], 'width': 300, 'height': 150, 'depth_y': 420, 'repeat': False}],
             'seats': [{'seat_id': 'sofa', 'object_id': 'sofa', 'actor_id': 'life', 'actor_revision': 'r1',
                        'outfit_id': None, 'facing': 1, 'approach': [.4, .825], 'position': [.7, .825],
                        'exit': [.4, .825], 'sit_offset': [0, -30]}]}
    catalog = {'schema_version': 'legend_scene_catalog/1', 'scenes': [scene], 'files': {h: ref}}
    catalog['catalog_sha256'] = canonical_hash(catalog)
    for folder in ('catalogs', 'scene-catalogs', 'content'):
        (tmp_path / folder).mkdir(exist_ok=True)
    (tmp_path / 'catalogs' / (actors['catalog_sha256'] + '.json')).write_text(json.dumps(actors))
    (tmp_path / 'life-current.json').write_text(json.dumps({'catalog_sha256': actors['catalog_sha256']}))
    (tmp_path / 'scene-catalogs' / (catalog['catalog_sha256'] + '.json')).write_text(json.dumps(catalog))
    (tmp_path / 'scene-current.json').write_text(json.dumps({'catalog_sha256': catalog['catalog_sha256']}))
    (tmp_path / 'content' / h).write_bytes(image)
    scene_ref = {'scene_id': 'loft', 'catalog_sha256': catalog['catalog_sha256']}
    stage = deepcopy(baseline['stage'])
    stage.update(usage='technical_coverage', cues=[])
    track = {'schema_version': 'legend_furniture_track/1', 'cues': [
        {'id': 'seat', 'instance_id': 'actor_1', 'seat_id': 'sofa', 'start_frame': 4000,
         'arrive_frame': 16000, 'leave_frame': 48000, 'end_frame': 72000}]}
    return store, job, actors, baseline, scene_ref, stage, track


def save_body(stage, scene, track=None):
    return StagePreviewRequest(profile='life_v1', operation='validate_and_save', script=stage,
                               scene=scene, furniture_track=track)


def test_scene_and_furniture_versions_are_immutable_and_roundtrip(tmp_path):
    store, job, actors, baseline, scene, stage, track = setup(tmp_path)
    old = save_preview(tmp_path, baseline)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    for script, furniture in ((baseline['stage'], None), (stage, track)):
        value = build_life_preview(job, save_body(script, scene, furniture), store, actors, scene_root=tmp_path)
        saved = save_preview(tmp_path, value)
        assert saved['preview_sha256'] != old['preview_sha256']
        assert read_life_preview(tmp_path, job, saved['preview_sha256'], store) == saved
        assert save_preview(tmp_path, value) == saved
        for key in ('script', 'audio', 'timeline'):
            assert saved[key] == baseline[key]
        assert saved['stage'] == script
        assert saved.get('furniture_track') == furniture
    assert read_life_preview(tmp_path, job, old['preview_sha256'], store) == old
    from pathlib import Path
    assert all(Path(p).read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('mutation', ['story', 'missing_scene', 'scene_id', 'seat', 'actor', 'revision',
    'outfit', 'action', 'start_bool', 'end_float', 'negative', 'reversed', 'outside', 'short_sit',
    'short_stand', 'duplicate', 'body_conflict', 'unknown_field', 'schema', 'path'])
def test_rejects_unapproved_combinations_and_invalid_times(tmp_path, mutation):
    _, _, actors, _, scene, stage, track = setup(tmp_path)
    cue = track['cues'][0]
    if mutation == 'story': stage['usage'] = 'story_preview'
    if mutation == 'missing_scene': scene = None
    if mutation == 'scene_id': scene['scene_id'] = 'absent'
    if mutation == 'seat': cue['seat_id'] = 'absent'
    if mutation == 'actor': cue['instance_id'] = 'absent'
    if mutation == 'revision': stage['actors'][0]['actor_revision'] = 'different'
    if mutation == 'outfit': stage['actors'][0]['initial_outfit_id'] = 'summer'
    if mutation == 'action': stage['actors'][0]['actions'].pop('sit')
    if mutation == 'start_bool': cue['start_frame'] = True
    if mutation == 'end_float': cue['end_frame'] = 72000.5
    if mutation == 'negative': cue['start_frame'] = -1
    if mutation == 'reversed': cue['arrive_frame'] = cue['leave_frame']
    if mutation == 'outside': cue['end_frame'] = stage['total_frames'] + 1
    if mutation == 'short_sit': cue['leave_frame'] = cue['arrive_frame'] + 1
    if mutation == 'short_stand': cue['end_frame'] = cue['leave_frame'] + 1
    if mutation == 'duplicate': track['cues'].append(deepcopy(cue))
    if mutation == 'body_conflict': stage['cues'] = [{'type': 'actor.move_to', 'enabled': True, 'instance_id': 'actor_1'}]
    if mutation == 'unknown_field': cue['guess'] = 'chair'
    if mutation == 'schema': track['schema_version'] = 'guess/1'
    if mutation == 'path': scene['catalog_sha256'] = '../escape'
    with pytest.raises((LifeValidationError, ProductionError)):
        scene_envelope(tmp_path, scene, track, stage, actors)


def test_scene_alone_preserves_story_and_rejects_direction_interaction(tmp_path):
    store, job, actors, baseline, scene, stage, track = setup(tmp_path)
    value = build_life_preview(job, save_body(baseline['stage'], scene), store, actors, scene_root=tmp_path)
    assert value['stage'] == baseline['stage'] and 'furniture_track' not in value
    body = save_body(stage, scene, track)
    body.dialogue_enabled = True
    with pytest.raises(LifeValidationError):
        build_life_preview(job, body, store, actors, scene_root=tmp_path)
    # Disabling all scene fields restores the exact original preview content.
    assert build_life_preview(job, save_body(baseline['stage'], None), store, actors, scene_root=tmp_path) == baseline


def test_static_room_without_seats_roundtrips_but_rejects_furniture(tmp_path):
    store, job, actors, baseline, old_ref, stage, track = setup(tmp_path)
    catalog = load_scene_catalog(tmp_path)
    catalog.pop('catalog_sha256')
    catalog['scenes'][0]['seats'] = []
    catalog['scenes'][0]['scene_id'] = 'library'
    catalog['catalog_sha256'] = canonical_hash(catalog)
    (tmp_path / 'scene-catalogs' / (catalog['catalog_sha256'] + '.json')).write_text(json.dumps(catalog))
    ref = {'catalog_sha256': catalog['catalog_sha256'], 'scene_id': 'library'}
    value = build_life_preview(job, save_body(baseline['stage'], ref), store, actors, scene_root=tmp_path)
    saved = save_preview(tmp_path, value)
    assert read_life_preview(tmp_path, job, saved['preview_sha256'], store) == saved
    assert value['stage'] == baseline['stage'] and 'furniture_track' not in value
    with pytest.raises(LifeValidationError):
        scene_envelope(tmp_path, ref, track, stage, actors)
    assert load_scene_catalog(tmp_path, old_ref['catalog_sha256'])['scenes'][0]['seats']


def test_duplicate_actor_and_shared_seat_are_rejected(tmp_path):
    _, _, actors, _, scene, stage, track = setup(tmp_path)
    second = deepcopy(track['cues'][0]);second['id'] = 'second'
    track['cues'].append(second)
    with pytest.raises(LifeValidationError): scene_envelope(tmp_path, scene, track, stage, actors)
    clone = deepcopy(stage['actors'][0]);clone['instance_id'] = 'clone'
    stage['actors'].append(clone);second['instance_id'] = 'clone'
    with pytest.raises(LifeValidationError): scene_envelope(tmp_path, scene, track, stage, actors)


def test_unknown_and_tampered_catalog_cannot_be_read(tmp_path):
    setup(tmp_path)
    c = load_scene_catalog(tmp_path)
    with pytest.raises(ProductionError): load_scene_catalog(tmp_path, '../catalog')
    with pytest.raises(ProductionError): load_scene_catalog(tmp_path, 'f' * 64)
    (tmp_path / 'scene-catalogs' / (c['catalog_sha256'] + '.json')).write_text('{}')
    with pytest.raises(ProductionError): load_scene_catalog(tmp_path)


def test_authenticated_api_catalog_assets_and_saved_scene(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from my_legend_production.production.api import register_production_routes
    store, job, actors, baseline, scene, stage, track = setup(tmp_path)
    class Repository:
        def get(self, jid, uid):
            if jid != job['id'] or uid != 'alice': raise ProductionError('JOB_NOT_FOUND')
            return deepcopy(job)
    def owner(request):
        if not request.headers.get('authorization'): raise HTTPException(401, 'UNAUTHORIZED')
        return request.headers['authorization']
    monkeypatch.setenv('MY_LEGEND_SPINE_ROOT', str(tmp_path))
    app = FastAPI();register_production_routes(app, Repository(), store, owner, lambda: {})
    client = TestClient(app);h = {'authorization': 'alice'}
    assert client.get('/api/v1/stage-preview/scenes').status_code == 401
    response = client.get('/api/v1/stage-preview/scenes', headers=h)
    assert response.status_code == 200 and 'files' not in response.json()['data']
    catalog = load_scene_catalog(tmp_path);image_hash = next(iter(catalog['files']))
    path = '/api/v1/stage-preview/assets/' + image_hash
    assert client.get(path).status_code == 401
    assert client.get(path, headers=h).content == (tmp_path / 'content' / image_hash).read_bytes()
    assert client.get('/api/v1/stage-preview/assets/' + 'f'*64, headers=h).status_code == 404
    (tmp_path / 'content' / image_hash).write_bytes(b'tampered')
    assert client.get(path, headers=h).status_code == 404
    endpoint = f"/api/v1/production-jobs/{job['id']}/stage-preview"
    body = save_body(stage, scene, track).model_dump()
    assert client.post(endpoint, json=body).status_code == 401
    assert client.post(endpoint, json=body, headers={'authorization': 'bob'}).status_code == 404
    saved = client.post(endpoint, json=body, headers=h)
    assert saved.status_code == 200, saved.text
    value = saved.json()['data']
    assert client.get(endpoint + '/' + value['preview_sha256'], headers=h).json()['data'] == value
    body['profile'] = 'battle'
    assert client.post(endpoint, json=body, headers=h).status_code == 422
