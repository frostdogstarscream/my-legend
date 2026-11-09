from copy import deepcopy

import pytest

from test_spine_director import sample
from my_legend_production.production.directing_compile import compile_plan
from my_legend_production.production.h5_preview import validate_cues_in_scenes
from my_legend_production.production.life_contracts import LifeValidationError
from my_legend_production.production.scene_preview import load_scene_catalog


def boundary_plan(tmp_path):
    store,job,_,context,plan=sample(tmp_path)
    plan['scenes'][0]['actors'][0]['x']=.666
    plan['scenes'][0]['actors'][1]['x']=.8
    move=plan['actions'][0]
    move.update(target_kind='entrance',target_id='left',duration_ms=1000)
    face=deepcopy(move)
    face.update(id='face-after-arrival',kind='face',target_kind='none',target_id=None,facing=1)
    face['anchor']['offset_ms']=1000
    plan['actions'].append(face);plan['assessments'][-1]['cue_ids'].append(face['id'])
    return store,job,context,plan


def test_face_at_interpolated_lane_boundary_preserves_original_coordinates(tmp_path):
    store,job,context,plan=boundary_plan(tmp_path)
    preview,_=compile_plan(job,store,context,plan,tmp_path)
    face=next(c for c in preview['stage']['cues'] if c['id']=='face-after-arrival')
    lane=load_scene_catalog(tmp_path,context['scene_catalog_sha256'])['scenes'][0]['blocking']['walk_lane']
    assert face['params']['position'][0]<lane[0]
    assert lane[0]-face['params']['position'][0]<1e-12
    # This is numerical tolerance in validation, not snapping or rewriting cues.
    assert face['params']['position'][0]==.666+(.06-.666)


@pytest.mark.parametrize('side',['left','right'])
def test_actual_out_of_lane_placements_are_still_rejected(tmp_path,side):
    store,job,context,plan=boundary_plan(tmp_path)
    preview,_=compile_plan(job,store,context,plan,tmp_path)
    rooms=load_scene_catalog(tmp_path,context['scene_catalog_sha256'])
    lane=rooms['scenes'][0]['blocking']['walk_lane']
    face=next(c for c in preview['stage']['cues'] if c['id']=='face-after-arrival')
    face['params']['position'][0]=lane[0]-.00001 if side=='left' else lane[2]+.00001
    with pytest.raises(LifeValidationError) as exc:
        validate_cues_in_scenes(preview['stage'],preview['scene_track']['segments'],{context['scene_catalog_sha256']:rooms})
    assert any(d['path']=='face-after-arrival' and '通道' in d['message'] for d in exc.value.diagnostics)
