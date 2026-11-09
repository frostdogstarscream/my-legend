"""Manual revisions of a directed version invalidate the current model approval."""
from copy import deepcopy
from .contracts import ArtifactRef
from .h5_preview import source_context, measured_scenes, bind_scenes, validate_cues_in_scenes, coverage_status, load_h5_catalog
from .commander_preview import validate_faces, validate_furniture
from .life_contracts import validate_script, fail


def rebuild_edit(root,job,base,store,draft):
    catalog=load_h5_catalog(root,base['catalog_sha256'])
    _,_,timeline,lines,expected,roles=source_context(job,store,catalog);expected['roles']=[r['role_id'] for r in roles]
    script=validate_script(draft['stage'],catalog,expected,set(lines))
    if script['actors']!=base['stage']['actors']:fail('actors','人物服装变化后请重新确认并提交 AI 编排')
    for scene in draft['scene_bindings']:
        old=next((s for s in base['scene_bindings'] if (s['episode_id'],s['scene_id'])==(scene['episode_id'],scene['scene_id'])),None)
        if scene!=old and not scene['confirmed']:fail('scene','人工改变场景后须确认该场配置')
    scenes,problems=measured_scenes(store.json(ArtifactRef.model_validate(base['script']['artifact'])),timeline)
    if problems:fail('scene_time','分场时间无效')
    track,rooms=bind_scenes(scenes,draft['scene_bindings'],script,root,model_plan=True)
    for s in track:s['confirmation_source']='human' if s['confirmed'] else 'model'
    validate_cues_in_scenes(script,track,rooms)
    faces=validate_faces(draft['expression_track'],script,catalog,track)
    furniture=validate_furniture(draft['furniture_track'],script,track,rooms,catalog)
    proxy={**script,'cues':script['cues']+[{**c,'source':base['cue_evidence'].get(c['id'],{}).get('source')} for c in faces]+furniture['cues']}
    coverage,decisions=coverage_status(base['coverage'],draft['coverage_decisions'],proxy)
    pending=[c for c in coverage if c['status'] in ('needs_correction','unsupported')]
    if draft['review_confirmed'] and pending:fail('review_confirmed','仍有未处理动作')
    if set(draft['mouth_enabled'])!={a['instance_id'] for a in script['actors']}:fail('mouth_enabled','口型演员无效')
    result=deepcopy(base);result.pop('preview_sha256',None)
    result.update(stage=script,scene_track=dict(schema_version='legend_scene_track/1',segments=track),scene_bindings=draft['scene_bindings'],
        expression_track=faces,furniture_track=furniture,coverage=coverage,coverage_decisions=decisions,
        mouth_enabled=draft['mouth_enabled'],dialogue_enabled=draft['dialogue_enabled'],review_confirmed=draft['review_confirmed'],
        status='needs_attention' if pending else 'preview_ready')
    result['diagnostics']=[d for d in base['diagnostics'] if d['code']!='ACTION_NEEDS_CORRECTION']+[
        dict(code='ACTION_NEEDS_CORRECTION',message=c['message'],source=c['source'],text=c['text']) for c in pending]
    result['directing']={**base['directing'],'edited_from':base['preview_sha256'],
        'review_status':'edited_pending','human_acceptance':'pending'}
    return result


def edit_directed(root,job,body,store):
    from .spine_director import read_directed_preview
    base=read_directed_preview(root,job,body.directed_from,store)
    if base['directing'].get('edited_from'):base=read_directed_preview(root,job,base['directing']['edited_from'],store)
    return rebuild_edit(root,job,base,store,dict(stage=body.script,scene_bindings=body.scene_bindings,
        expression_track=body.expression_track,furniture_track=body.furniture_track,coverage_decisions=body.coverage_decisions,
        mouth_enabled=body.mouth_enabled,dialogue_enabled=body.dialogue_enabled,review_confirmed=body.review_confirmed))
