from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS))
from prepare_workflow_scenes import build


def source():
    recipe = json.loads((OPS / 'workflow-scene-presets.json').read_text(encoding='utf-8'))
    base = {'catalog_sha256': 'a' * 64, 'scenes': [], 'files': {'image': {'size_bytes': 1}}}
    for spec in recipe['scenes']:
        names = [*spec['objects'], *spec.get('omit_objects', [])]
        base['scenes'].append({'scene_id': spec['scene_id'], 'objects': [
            {'id': name, 'position': [100, 530], 'width': 200, 'height': 100, 'repeat': False,
             'layer': 'front', 'depth_y': 530, 'image': {'sha256': 'image'}} for name in names],
            'seats': [{'seat_id': 'old_calibration'}]})
    return base, recipe


def test_layout_rebuild_keeps_source_and_images_immutable_and_retires_stale_seats():
    base, recipe = source()
    before = deepcopy(base)
    result = build(base, recipe, 'b' * 64)
    assert base == before and result['files'] == base['files']
    assert result == build(base, recipe, 'b' * 64)
    assert len(result['scenes']) == 15
    for scene, old in zip(result['scenes'], base['scenes']):
        assert not scene['seats']
        assert all(o['position'][1] <= 420 and o['layer'] in ('back', 'depth') for o in scene['objects'])
        for obj in scene['objects']:
            original = next(o for o in old['objects'] if o['id'] == obj['id'])
            assert obj['image'] == original['image']
            assert abs(obj['height']/obj['width'] - original['height']/original['width']) < .00001
        assert {t['object_id'] for t in scene['blocking']['targets']} <= {o['id'] for o in scene['objects']}


@pytest.mark.parametrize('change', ['duplicate', 'missing', 'object', 'foreground', 'target'])
def test_invalid_layout_cannot_be_published(change):
    base, recipe = source()
    if change == 'duplicate': recipe['scenes'].append(deepcopy(recipe['scenes'][0]))
    if change == 'missing': recipe['scenes'].pop()
    if change == 'object': recipe['scenes'][0]['objects']['invented'] = [1, 2, 3]
    if change == 'foreground': recipe['scenes'][0]['objects']['sofa'][1] = 500
    if change == 'target': recipe['scenes'][0]['targets'][0][0] = 'invented'
    with pytest.raises(ValueError):
        build(base, recipe, 'b' * 64)
