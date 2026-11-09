"""Deterministic, audio-led stage delivery using the existing saved-preview pipeline."""
from .contracts import H5DeliveryRequest
from .h5_preview import load_h5_catalog, source_context, measured_scenes, make_script
from .scene_preview import load_scene_catalog
from .commander_preview import build_commander_preview
from .spine_preview import local_root, StagePreviewRequest, save_preview
from .performance_delivery import prepare_request
from .repository import ProductionError

POLICY='basic_stage/1'
NOTE='基础舞台：系统按基础策略自动选定布景与固定站位，保留原配音、对白口型和字幕；布景不代表精确还原原文地点，复杂动作本版不执行。'

def choose_room(scene,candidates,model_locations=False):
    location=scene.get('location','').strip()
    if model_locations:
        matches=[r for r in candidates if location in (r['scene_id'],r['display_name'])]
        if len(matches)==1:return matches[0],True
    return next((r for r in candidates if r['scene_id']=='library'),candidates[0]),False

def build_basic_preview(job,store,casting,root):
    catalog=load_h5_catalog(root,casting['catalog_sha256'])
    rooms=load_scene_catalog(root,casting['scene_catalog_sha256'])
    base,delivery,timeline,lines,expected,roles=source_context(job,store,catalog)
    scenes,problems=measured_scenes(delivery,timeline)
    if problems:raise ProductionError('SCENE_TIME_UNRESOLVED')
    expected['roles']=[r['role_id'] for r in roles]
    script=make_script(base,expected,roles,catalog,casting['bindings'],casting['initial_outfits'])
    # A declared presentation setting, never a claim to reproduce every story location.
    candidates=sorted((r for r in rooms['scenes'] if r.get('blocking') and
        r['blocking']['walk_lane'][2]-r['blocking']['walk_lane'][0]>=.12*(len(script['actors'])+1)),key=lambda r:r['scene_id'])
    if not candidates:raise ProductionError('BASIC_STAGE_SCENE_UNAVAILABLE')
    bindings=[]
    for s in scenes:
        room,chosen=choose_room(s,candidates,bool(casting.get('scene_choices')))
        left,_,right,*_=room['blocking']['walk_lane']
        actors=[dict(instance_id=a['instance_id'],position=[left+(right-left)*(i+1)/(len(script['actors'])+1),room['blocking']['foot_y']],
            facing=1 if i<len(script['actors'])/2 else -1,visible=True) for i,a in enumerate(script['actors'])]
        note=('采用故事模型选择的场景：'+room['display_name']+'。人物采用基础站位，家具与装饰随场景展示，复杂动作不执行。') if chosen else NOTE
        bindings.append(dict(episode_id=s['episode_id'],scene_id=s['scene_id'],
            scene=dict(catalog_sha256=rooms['catalog_sha256'],scene_id=room['scene_id']),actors=actors,confirmed=True,note=note))
    body=StagePreviewRequest(profile='life_v2',operation='validate_and_save',script=script,
        scene_bindings=bindings,dialogue_enabled=True,review_confirmed=False,
        furniture_track=dict(schema_version='commander_furniture/1',cues=[]))
    preview=build_commander_preview(job,body,store,catalog,root)
    body.coverage_decisions=[dict(coverage_id=c['id'],resolution='omitted',note=NOTE,cue_ids=[])
        for c in preview['coverage'] if c['status']!='not_executed']
    return build_commander_preview(job,body,store,catalog,root)

def prepare_basic_request(repo,store,owner,sound_id,casting,key):
    job=repo.get(sound_id,owner)
    value=build_basic_preview(job,store,casting,local_root())
    saved=save_preview(local_root(),value)
    return prepare_request(repo,store,owner,sound_id,H5DeliveryRequest(
        idempotency_key=key,preview_sha256=saved['preview_sha256']))
