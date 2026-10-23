from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path

import pytest

from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.life_contracts import LifeValidationError, canonical_hash, validate_catalog, validate_script
from my_legend_production.production.life_preview import build_life_preview, compile_keywords, context, read_life_preview
from my_legend_production.production.spine_preview import StagePreviewRequest, save_preview


def fixture(tmp_path, text='甲坐下，甲捡起书本。', kind='dialogue'):
    store = ArtifactStore(tmp_path / 'artifacts')
    delivery = {'story_version_id': 'v1', 'title': 'life fixture', 'character_snapshot': [
        {'role_id': 'a', 'canon': {'name': '甲', 'aliases': ['小甲']}}, {'role_id': 'b', 'canon': {'name': '乙'}}],
        'episodes': [{'episode_id': 'ep', 'scenes': [{'scene_id': 'scene', 'utterances': [
            {'line_id': 'first', 'kind': 'dialogue', 'speaker_role_id': 'a', 'text': '开始。'},
            {'line_id': 'second', 'kind': 'dialogue', 'speaker_role_id': 'b', 'text': '你好。'},
            {'line_id': 'action', 'kind': kind, 'speaker_role_id': 'a', 'text': text}]}]}]}
    audio = store.put('AudioMix', 'v1', b'fixture-audio', 'audio/wav')
    script = store.put_json('StoryDelivery', 'v1', delivery)
    timeline = {'schema_version': 'measured_timeline/1.0', 'source_version_id': 'v1', 'sample_rate': 8000,
                'channels': 1, 'total_frames': 80000, 'audio_sha256': audio.sha256, 'events': []}
    for i, line in enumerate(delivery['episodes'][0]['scenes'][0]['utterances']):
        timeline['events'].append({'sequence': i+1, 'episode_id': 'ep', 'scene_id': 'scene', 'line_id': line['line_id'],
                                   'kind': line['kind'], 'text': line['text'], 'start_frame': i*16000, 'end_frame': (i+1)*16000,
                                   'duration_basis': 'planned_silent_hold' if line['kind'] == 'action' else 'measured_audio', 'source_audio_sha256': audio.sha256})
    timeline_ref = store.put_json('MeasuredTimeline', 'v1', timeline)
    job = {'id': 'job-life', 'work_id': 'w', 'source_version_id': 'v1', 'status': 'succeeded',
           'artifacts': [a.model_dump() for a in (script, audio, timeline_ref)]}
    ref = {'sha256': 'a'*64, 'size_bytes': 1, 'media_type': 'application/json'}
    skeleton = {'asset_id': 's', 'skeleton': ref, 'original': ref, 'atlas': ref, 'pages': {'a.png': ref},
                'qa': 'pass', 'animations': {a: {'duration': 1.0} for a in ('wait', 'move', 'sit', 'pick', 'lying')}}
    amap = {'idle': 'wait', 'walk': 'move', 'sit': 'sit', 'pick': 'pick', 'lie': 'lying'}
    actors = [{'actor_id': 'life', 'revision': 'r1', 'asset_mode': 'single_skeleton', 'asset_id': 's',
               'outfit_ids': [], 'action_map': amap, 'lifecycle': 'ready', 'asset_qa': 'pass'},
              {'actor_id': 'commander', 'revision': 'r2', 'asset_mode': 'commander_tripartite', 'asset_id': None,
               'outfit_ids': ['uniform', 'summer'], 'action_map': {'idle': 'wait', 'walk': 'move', 'sit': 'sit'}, 'lifecycle': 'ready', 'asset_qa': 'pass'}]
    calibration = {s: {'x': 0, 'y': 0, 'scale': 1, 'rotation': 0, 'flip': False, 'layer': 0, 'actions': {}} for s in ('head', 'body', 'foot')}
    outfits = [{'outfit_id': name, 'actor_id': 'commander', 'parts': {s: 's' for s in calibration},
                'allowed_actions': ['idle', 'walk', 'sit'], 'calibration': calibration,
                'calibration_revision': canonical_hash(calibration), 'qa': 'pass'} for name in ('uniform', 'summer')]
    catalog = {'schema_version': 'spine_life_catalog/2.0', 'actors': actors, 'outfits': outfits, 'skeletons': [skeleton], 'files': {'a'*64: ref}, 'runtime': ref}
    catalog['catalog_sha256'] = canonical_hash(catalog)
    return store, job, catalog


def compile_value(store, job, catalog, **kwargs):
    return build_life_preview(job, StagePreviewRequest(profile='life_v1', **kwargs), store, catalog)


def test_compile_is_deterministic_and_preserves_delivery(tmp_path):
    store, job, catalog = fixture(tmp_path)
    before = deepcopy(job)
    first = compile_value(store, job, catalog)
    assert first == compile_value(store, job, catalog)
    assert [c['params']['action'] for c in first['stage']['cues']] == ['sit', 'pick']
    assert all(c['source']['line_id'] == 'action' for c in first['stage']['cues'])
    assert job == before
    assert save_preview(tmp_path, first) == save_preview(tmp_path, first)


@pytest.mark.parametrize('text', ['甲不要坐下。', '甲没有坐下。', '如果甲坐下。', '甲准备坐下。', '甲坐下吗？', '听说甲坐下。', '甲说：“乙坐下。”', '我想坐下。', '你坐下。', '他坐下。', '甲和乙坐下。'])
def test_non_execution_or_ambiguous_text_is_not_acted(tmp_path, text):
    store, job, catalog = fixture(tmp_path, text)
    result = compile_value(store, job, catalog)
    assert not result['stage']['cues']
    assert result['diagnostics']


@pytest.mark.parametrize('kind', ['action', 'dialogue', 'narration'])
def test_three_source_kinds_and_aliases(tmp_path, kind):
    store, job, catalog = fixture(tmp_path, '小甲躺下。', kind)
    assert compile_value(store, job, catalog)['stage']['cues'][0]['params']['action'] == 'lie'


def test_unsupported_commander_and_missing_movement_target(tmp_path):
    store, job, catalog = fixture(tmp_path, '乙捡起书，甲靠近他。')
    result = compile_value(store, job, catalog)
    assert not result['stage']['cues']
    assert {d['code'] for d in result['diagnostics']} == {'UNSUPPORTED_ACTION', 'AMBIGUOUS_TARGET'}


def test_four_review_regressions_and_wrong_provenance(tmp_path):
    store, job, catalog = fixture(tmp_path)
    bad = deepcopy(catalog)
    bad['actors'][0]['asset_id'] = None
    bad['catalog_sha256'] = canonical_hash({k: v for k, v in bad.items() if k != 'catalog_sha256'})
    with pytest.raises(LifeValidationError): validate_catalog(bad)
    value = compile_value(store, job, catalog)['stage']
    _, _, _, lines, expected, _ = context(job, StagePreviewRequest(), store, catalog)
    for mutation in ('mode', 'overlap', 'hash', 'frames', 'missing_actor'):
        bad = deepcopy(value)
        if mutation == 'mode': bad['cues'][0]['params']['mode'] = 'loop_or_hold'
        if mutation == 'overlap': bad['cues'].append({**deepcopy(bad['cues'][0]), 'id': 'overlap'})
        if mutation == 'hash': bad['audio_sha256'] = 'f'*64
        if mutation == 'frames': bad['cues'][0]['end_frame'] = bad['total_frames']+1
        if mutation == 'missing_actor': bad['cues'][0]['instance_id'] = 'missing'
        with pytest.raises(LifeValidationError): validate_script(bad, catalog, expected, set(lines))
    bad_catalog = deepcopy(catalog)
    bad_catalog['outfits'][1]['allowed_actions'].remove('walk')
    script = deepcopy(value)
    script['cues'] = [dict(id='outfit', type='actor.change_outfit', instance_id='actor_2', start_frame=1000, end_frame=1000, params={'outfit_id': 'summer'}),
                      dict(id='walk', type='actor.move_to', instance_id='actor_2', start_frame=2000, end_frame=6000, params={'to': [.8, .825]})]
    with pytest.raises(LifeValidationError, match='LIFE_VALIDATION_FAILED'):
        validate_script(script, bad_catalog, expected, set(lines))


def test_snapshot_reopen_and_tamper_rejection(tmp_path):
    store, job, catalog = fixture(tmp_path)
    value = compile_value(store, job, catalog)
    saved = save_preview(tmp_path, value)
    folder = tmp_path / 'catalogs'
    folder.mkdir()
    import json
    (folder / (catalog['catalog_sha256']+'.json')).write_text(json.dumps(catalog), encoding='utf-8')
    assert read_life_preview(tmp_path, job, saved['preview_sha256'], store) == saved
    (tmp_path / 'previews' / job['id'] / (saved['preview_sha256']+'.json')).write_text('{}')
    with pytest.raises(Exception, match='SPINE_PREVIEW_NOT_FOUND'):
        read_life_preview(tmp_path, job, saved['preview_sha256'], store)


def test_atlas_metadata_requires_dimensions():
    path = Path(__file__).resolve().parents[3] / 'ops/my-legend/prepare_life_preview.py'
    import sys
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('life_import_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pages, regions = module.atlas_metadata('actor.png\nsize: 512, 256\nformat: RGBA8888\nhead\n  rotate: false\n')
    assert pages == {'actor.png': (512, 256)} and regions == {'head'}
    with pytest.raises(ValueError): module.atlas_metadata('actor.png\nformat: RGBA8888\nhead\n')


@pytest.mark.parametrize('text,action', [('我坐下。', 'sit'), ('甲站起来。', 'idle'), ('甲行走。', 'walk'), ('甲走近乙。', 'walk')])
def test_supported_rules_and_first_person(tmp_path, text, action):
    store, job, catalog = fixture(tmp_path, text)
    cues = compile_value(store, job, catalog)['stage']['cues']
    assert cues
    assert (cues[0]['params'].get('action') or 'walk') == action


def test_unbound_alias_and_duplicate_instances(tmp_path):
    store, job, catalog = fixture(tmp_path, '乙坐下。')
    result = compile_value(store, job, catalog, bindings={'a': 'life', 'b': 'life'})
    assert len({a['instance_id'] for a in result['stage']['actors']}) == 2
    assert result['stage']['cues'][0]['instance_id'] == 'actor_2'
    with pytest.raises(LifeValidationError): compile_value(store, job, catalog, bindings={'missing': 'life'})


def test_action_priority_and_instant_marker_bounds(tmp_path):
    store, job, catalog = fixture(tmp_path, '甲坐下。')
    result = compile_value(store, job, catalog)
    _, delivery, timeline, lines, _, _ = context(job, StagePreviewRequest(), store, catalog)
    action = {'line_id': 'priority', 'kind': 'action', 'text': '甲躺下。'}
    lines[('ep', 'scene', 'priority')] = action
    timeline['events'].append({**timeline['events'][-1], 'line_id': 'priority', 'kind': 'action', 'text': action['text'], 'end_frame': timeline['events'][-1]['start_frame']})
    diagnostics = compile_keywords(result['stage'], delivery, timeline, lines)
    assert [c['params']['action'] for c in result['stage']['cues']] == ['lie']
    assert any(d['code'] == 'CONFLICT_SKIPPED' for d in diagnostics)
    assert result['stage']['cues'][0]['end_frame'] <= result['stage']['total_frames']


def test_missing_anchor_and_destination_are_reported(tmp_path):
    store, job, catalog = fixture(tmp_path, '甲停下，走到书架前。')
    result = compile_value(store, job, catalog)
    assert len(result['stage']['cues']) == 1
    assert any(d['code'] == 'AMBIGUOUS_TARGET' for d in result['diagnostics'])
    _, delivery, timeline, lines, _, _ = context(job, StagePreviewRequest(), store, catalog)
    timeline['events'] = timeline['events'][:2]
    diagnostics = compile_keywords(result['stage'], delivery, timeline, lines)
    assert not result['stage']['cues']
    assert any(d['code'] == 'NO_TIMELINE' for d in diagnostics)


def test_place_inside_movement_and_outfit_at_action_start(tmp_path):
    store, job, catalog = fixture(tmp_path)
    value = compile_value(store, job, catalog)['stage']
    _, _, _, lines, expected, _ = context(job, StagePreviewRequest(), store, catalog)
    value['cues'] = [dict(id='move', type='actor.move_to', instance_id='actor_1', start_frame=1000, end_frame=5000, params={'to': [.5, .825]}),
                     dict(id='place', type='actor.place', instance_id='actor_1', start_frame=2000, end_frame=2000, params={'position': [.3, .825]})]
    with pytest.raises(LifeValidationError): validate_script(value, catalog, expected, set(lines))
    value['cues'] = [dict(id='outfit', type='actor.change_outfit', instance_id='actor_2', start_frame=2000, end_frame=2000, params={'outfit_id': 'summer'}),
                     dict(id='body', type='body.play', instance_id='actor_2', start_frame=2000, end_frame=5000, params={'action': 'sit', 'mode': 'hold'})]
    assert validate_script(value, catalog, expected, set(lines))['cues'][0]['params']['outfit_id'] == 'summer'
