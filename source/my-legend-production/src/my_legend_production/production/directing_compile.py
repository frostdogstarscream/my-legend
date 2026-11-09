"""Deterministic source-relative compiler for freshly directed performances."""
from copy import deepcopy
import math

from .contracts import ArtifactRef
from .directing_contracts import DirectingPlan, DirectingReview, CoverageReview
from .repository import ProductionError
from .h5_preview import (source_context, measured_scenes, make_script, bind_scenes,
    position_at, validate_cues_in_scenes, load_h5_catalog)
from .life_contracts import validate_script, canonical_hash, fail, LifeValidationError
from .scene_preview import load_scene_catalog
from .commander_preview import validate_faces, validate_furniture
from .stage_policy import asset_first, parse_plan, StagePlan, StageReview


def key(source):
    return tuple(source[k] for k in ('episode_id','scene_id','line_id'))


def source_lines(delivery, timeline):
    events = {key(e.get('source',e)): e for e in timeline['events']}
    result=[]
    for ep in delivery['episodes']:
        for scene in ep['scenes']:
            for line in scene['utterances']:
                source=dict(episode_id=ep['episode_id'],scene_id=scene['scene_id'],line_id=line['line_id'])
                event=events.get(key(source))
                if event is None:raise ProductionError('DIRECTING_SOURCE_COVERAGE_INVALID')
                operations=[dict(start_frame=o['start_frame'],end_frame=o['end_frame'],
                    cue_text=o.get('cue_text',line['text']),
                    mode=o.get('mode',o.get('render_role','action' if line['kind']=='action' else 'speech')))
                    for o in event.get('operations',[event])]
                result.append(dict(source=source,text=line['text'],kind=line['kind'],
                    speaker_role_id=line.get('speaker_role_id'),operations=operations))
    return result


def create_context(job, source, store, root):
    catalog=load_h5_catalog(root,source.catalog_sha256)
    rooms=load_scene_catalog(root,source.scene_catalog_sha256)
    if rooms.get('commander_catalog_sha256')!=source.catalog_sha256:raise ProductionError('DIRECTING_SCENE_CATALOG_MISMATCH')
    if catalog.get('actor_model') not in ('fixed_commander/1','mixed_life/1'):raise ProductionError('DIRECTING_COMMANDER_REQUIRED')
    base,delivery,timeline,lines,expected,roles=source_context(job,store,catalog)
    expected['roles']=[r['role_id'] for r in roles]
    script=make_script(base,expected,roles,catalog,source.bindings,source.initial_outfits)
    validate_script(script,catalog,expected,set(lines))
    if set(source.initial_outfits)!=set(source.bindings):raise ProductionError('DIRECTING_CASTING_INVALID')
    scenes,problems=measured_scenes(delivery,timeline)
    if problems:raise ProductionError('DIRECTING_SCENE_TIME_INVALID')
    casts=[]
    for a in script['actors']:
        actor=next(x for x in catalog['actors'] if x['actor_id']==a['actor_id'])
        casts.append({**a,'eyes':list(actor.get('face_profile',{}).get('eyes',{})),'mouth':['native','open','closed'] if actor.get('face_profile') else ['native']})
    resources=[]
    for room in rooms['scenes']:
        if not room.get('blocking'):continue
        b=room['blocking']
        points=b['ensemble_slots'] if len(casts)==3 else b['dialogue_slots'] if len(casts)==2 else [[.5,b['foot_y']]]
        resources.append(dict(scene_id=room['scene_id'],name=room['display_name'],walk_lane=b['walk_lane'],foot_y=b['foot_y'],
            slots=[dict(id='slot_'+str(i),position=p) for i,p in enumerate(points)],targets=b['targets'],entrances=b['entrances'],
            seats=[{k:s[k] for k in ('seat_id','object_id','position','approach','exit','facing')} for s in room.get('commander_seats',[])],
            objects=[dict(id=o['id']) for o in room['objects']]))
    value=dict(schema_version='spine_directing_context/1.0',source_job_id=job['id'],source_version_id=source.source_version_id,
        catalog_sha256=source.catalog_sha256,scene_catalog_sha256=source.scene_catalog_sha256,
        bindings=source.bindings,initial_outfits=source.initial_outfits,casting_confirmed=True,
        model_input=dict(title=delivery.get('title',''),sample_rate=expected['sample_rate'],total_frames=expected['total_frames'],
            characters=delivery.get('character_snapshot',[]),character_portrayals=delivery.get('character_portrayals',[]),
            locked_cast=casts,source_scenes=scenes,source_lines=source_lines(delivery,timeline),scene_resources=resources))
    if source.visual_policy:
        from .asset_capabilities import load_capabilities, enrich_context
        value['schema_version']='spine_directing_context/1.1'
        enrich_context(value,load_capabilities(root,source.capability_sha256,catalog,rooms))
    return value


def required_checks(context):
    return ['locked','execution']+['scene:'+s['episode_id']+':'+s['scene_id'] for s in context['model_input']['source_scenes']]+[
        'line:'+':'.join(key(l['source'])) for l in context['model_input']['source_lines']]


def planning_schema(context):
    """Constrain generated values to this frozen cast/catalog, not global options."""
    schema=DirectingPlan.model_json_schema()
    data=context['model_input'];roles=[a['role_id'] for a in data['locked_cast']]
    for name in ('DirectedActor','DirectedAction','DirectedExpression','DirectedFurniture'):
        schema['$defs'][name]['properties']['role_id']['enum']=roles
    schema['$defs']['DirectedScene']['properties']['resource_scene_id']['enum']=[r['scene_id'] for r in data['scene_resources']]
    schema['$defs']['AudioEventRef']['enum']=[l['source'] for l in data['source_lines']]
    schema['$defs']['DirectedExpression']['properties']['preset']['enum']=sorted(
        {'native'}|{p for a in data['locked_cast'] for channel in ('eyes','mouth') for p in a[channel]})
    return schema


def validate_review(review, context, plan=None):
    if asset_first(context) != isinstance(review,StageReview):
        raise ProductionError('DIRECTING_POLICY_MISMATCH')
    if asset_first(context) and (plan is None or not isinstance(parse_plan(plan),StagePlan)):
        raise ProductionError('DIRECTING_POLICY_MISMATCH')
    def invalid_evidence(line,field,evidence):
        error=ProductionError('DIRECTING_REVIEW_EVIDENCE_INVALID')
        error.review_evidence=dict(source=line['source'],source_text=line['text'],field=field,evidence=evidence,
            requirement='审查证据必须逐字取自 source_text，不得替换同义词或拼接；例如原文举灯不能引用成提灯。')
        raise error
    checks={c.key:c for c in review.checks}
    if len(checks)!=len(review.checks) or set(checks)!=set(required_checks(context)):
        raise ProductionError('DIRECTING_REVIEW_COVERAGE_INVALID')
    for line in context['model_input']['source_lines']:
        c=checks['line:'+':'.join(key(line['source']))]
        if not c.quote or c.quote not in line['text']:invalid_evidence(line,'quote',c.quote)
    if isinstance(review,CoverageReview):
        if plan is None:raise ProductionError('DIRECTING_REVIEW_PLAN_REQUIRED')
        plan=parse_plan(plan)
        assessments={key(a.source.model_dump()):a for a in plan.assessments};errors=[]
        if len(assessments)!=len(plan.assessments) or set(assessments)!={key(l['source']) for l in context['model_input']['source_lines']}:
            raise ProductionError('DIRECTING_REVIEW_COVERAGE_INVALID')
        for line in context['model_input']['source_lines']:
            k=key(line['source']);c=checks['line:'+':'.join(k)];a=assessments[k]
            if c.coverage_complete is None or any(not text or text not in line['text'] for text in c.unperformed_actions):
                invalid_evidence(line,'unperformed_actions',c.unperformed_actions)
            if c.coverage_complete != (not c.unperformed_actions):
                raise ProductionError('DIRECTING_REVIEW_COVERAGE_INVALID')
            details=getattr(c,'unrendered_details',[])
            if any(not text or text not in line['text'] for text in details):
                invalid_evidence(line,'unrendered_details',details)
            if ((not c.coverage_complete) or bool(details))!=(a.classification=='gap'):
                errors.append(dict(path='review.'+c.key,code='COVERAGE_CONTRADICTION',
                    message='独立审查的原文动作缺口与导演覆盖标注不一致；不能用部分演出声称完整覆盖',
                    source=line['source'],text=line['text'],classification=a.classification,
                    coverage_complete=c.coverage_complete,unperformed_actions=c.unperformed_actions,unrendered_details=details))
        if any(c.coverage_complete is not None or c.unperformed_actions or getattr(c,'unrendered_details',[]) for c in review.checks if not c.key.startswith('line:')):
            raise ProductionError('DIRECTING_REVIEW_COVERAGE_INVALID')
        if errors:raise LifeValidationError(errors)
    if not review.passed or not all(c.passed for c in review.checks):raise ProductionError('DIRECTING_REVIEW_REJECTED')


def compile_plan(job, store, context, plan, root):
    plan=parse_plan(plan)
    staged=asset_first(context)
    if staged != isinstance(plan,StagePlan):raise ProductionError('DIRECTING_POLICY_MISMATCH')
    catalog=load_h5_catalog(root,context['catalog_sha256'])
    rooms=load_scene_catalog(root,context['scene_catalog_sha256'])
    if staged:
        from .asset_capabilities import load_capabilities
        if context.get('capabilities')!=load_capabilities(root,context['capability_sha256'],catalog,rooms):
            raise ProductionError('CAPABILITY_HASH_MISMATCH')
    base,delivery,timeline,lines,expected,roles=source_context(job,store,catalog)
    expected['roles']=[r['role_id'] for r in roles]
    script=make_script(base,expected,roles,catalog,context['bindings'],context['initial_outfits'])
    by_role={a['role_id']:a for a in script['actors']}
    resources={r['scene_id']:r for r in rooms['scenes']}
    bindings=[]
    for s in plan.scenes:
        room=resources.get(s.resource_scene_id)
        if not room or not room.get('blocking'):fail('scene','模型引用了不存在的可演出场景')
        if {a.role_id for a in s.actors}!=set(by_role) or len(s.actors)!=len(by_role):fail('actors','模型改变了用户确认的演员')
        bindings.append(dict(episode_id=s.episode_id,scene_id=s.scene_id,scene=dict(catalog_sha256=context['scene_catalog_sha256'],scene_id=s.resource_scene_id),
            actors=[dict(instance_id=by_role[a.role_id]['instance_id'],position=[a.x,room['blocking']['foot_y']],facing=a.facing,visible=a.visible) for a in s.actors],
            confirmed=False,note=s.reason+('；选景差异：'+s.difference if s.difference else '')))
    scenes,problems=measured_scenes(delivery,timeline)
    if problems:fail('scene_time',problems[0]['message'])
    track,catalogs=bind_scenes(scenes,bindings,script,root,model_plan=True)
    for segment in track:segment['confirmation_source']='model'
    source_by_key={key(l['source']):l for l in context['model_input']['source_lines']}
    segments={(s['episode_id'],s['scene_id']):s for s in track}
    ids=set();evidence={};execution=[];issues=[];preflight_errors=[];derived_parents={};rate=script['sample_rate']
    def anchor(v):
        k=key(v.source.model_dump());line=source_by_key.get(k)
        if not line or v.operation_index>=len(line['operations']):
            raise LifeValidationError([dict(path='anchor',code='INVALID',message='无效的原文或声音操作索引',
                source=v.source.model_dump(),operation_index=v.operation_index,
                matching_source_lines=[l['source'] for l in source_by_key.values() if l['source']['line_id']==v.source.line_id])])
        op=line['operations'][v.operation_index]
        return op[v.edge+'_frame']+round(v.offset_ms*rate/1000),segments[k[:2]],line
    def start(item):
        if item.id in ids:fail('id','模型指令 ID 重复')
        ids.add(item.id)
        if item.role_id not in by_role:fail(item.id,'模型改变了用户确认的演员')
        frame,segment,line=anchor(item.anchor)
        if item.quote not in line['text']:fail(item.id,'指令证据不是对应源行的原文')
        evidence[item.id]=dict(source=item.anchor.source.model_dump(),quote=item.quote,reason=item.reason)
        return frame,segment,line,by_role[item.role_id]['instance_id']
    def interval(item,frame,end,segment):
        if not segment['start_frame']<=frame<end<=segment['end_frame']:
            issues.append(dict(code='DIRECTING_TIME_CONFLICT',message='可用时间不足或动作跨场，未执行该指令',source=item.anchor.source.model_dump(),text=item.quote,cue_id=item.id));return False
        return True
    def cue(item,iid,kind,begin,end,params):
        return dict(id=item.id,type=kind,instance_id=iid,start_frame=begin,end_frame=end,enabled=True,params=params,
            source=item.anchor.source.model_dump(),source_text=item.quote,rule_id='spine_director/1',reason=item.reason)
    # Invalid references cannot be executed reliably. Report every bad anchor
    # before sorting or deriving positions; never guess or silently remap it.
    for item in [*plan.actions,*plan.expressions,*plan.furniture]:
        anchors=[item.anchor]
        if getattr(item,'leave_anchor',None):anchors.append(item.leave_anchor)
        for reference in anchors:
            try:anchor(reference)
            except LifeValidationError as exc:
                preflight_errors.extend({**d,'path':item.id+':anchor'} for d in exc.diagnostics)
    if preflight_errors:raise LifeValidationError(preflight_errors)
    for item in sorted(plan.actions,key=lambda c:(anchor(c.anchor)[0],c.id)):
        frame,segment,line,iid=start(item)
        duration_ms=item.duration_ms if item.duration_ms is not None else 300 if item.kind=='idle' else 1400
        end=frame+round(duration_ms*rate/1000)
        room=resources[segment['scene']['scene_id']];pos=position_at(script,segment,iid,frame)
        params={};target=None
        if item.kind not in ('move_group','exit_group') and item.followers:
            fail(item.id,'followers 只适用于协同行走或退场，不能附加到单人动作')
        if item.target_kind!='position' and item.target_x is not None:
            fail(item.id,'target_x 只能与 position 目标一起使用')
        if item.target_kind=='position':
            if item.kind not in ('move','move_group') or item.target_x is None or item.target_id is not None:
                fail(item.id,'明确终点仅用于 move，须提供 target_x 且不能混用 target_id')
            to=[item.target_x,room['blocking']['foot_y']]
        elif item.target_kind=='object':
            point=next((p for p in room['blocking']['targets'] if p['id']==item.target_id),None)
            if not point:
                raise LifeValidationError([dict(path=item.id,code='INVALID',message='当前场景没有模型指定的物件目标',
                    source=item.anchor.source.model_dump(),resource_scene_id=room['scene_id'],requested_target=item.target_id,
                    allowed_targets=[p['id'] for p in room['blocking']['targets']])])
            to=point['position'];target=dict(kind='object',id=item.target_id)
        elif item.target_kind=='actor':
            other=by_role.get(item.target_id)
            if not other or other['instance_id']==iid:fail(item.id,'目标人物无效')
            state=position_at(script,segment,other['instance_id'],frame)
            if not state['visible']:fail(item.id,'目标人物不在场')
            to=[state['position'][0]+(-.18 if pos['position'][0]<=state['position'][0] else .18),room['blocking']['foot_y']]
            target=dict(kind='actor',id=other['instance_id'])
        elif item.target_kind in ('slot','entrance'):
            r=next(r for r in context['model_input']['scene_resources'] if r['scene_id']==room['scene_id'])
            point=next((p for p in r['slots' if item.target_kind=='slot' else 'entrances'] if p['id']==item.target_id),None)
            if not point:fail(item.id,'目标站位或入口不存在')
            to=point['position']
        else:to=None
        if item.kind in ('idle','sit','pick','lie','victory','victoryloop') and item.kind not in by_role[item.role_id]['actions']:
            fail(item.id,'已确认服装不支持该身体动作')
        if not interval(item,frame,end if item.kind!='face' else frame+1,segment):continue
        if item.kind=='victory' and end-frame<math.ceil(by_role[item.role_id]['actions']['victory']*rate):
            issues.append(dict(code='DIRECTING_TIME_CONFLICT',message='计划区间短于完整动作时长，未截断播放',source=item.anchor.source.model_dump(),text=item.quote,cue_id=item.id))
            continue
        if item.kind in ('move_group','exit_group'):
            from .coordinated_move import group_trajectories,exit_trajectories
            if to is None or item.target_kind=='actor':fail(item.id,'协同行走须指定领走者的实际地点目标')
            if item.kind=='exit_group' and item.target_kind!='entrance':fail(item.id,'协同退场必须指定真实入口')
            group_roles=[item.role_id,*item.followers]
            if any(role not in by_role for role in group_roles):fail(item.id,'协同行走引用了未确认的演员')
            states=[position_at(script,segment,by_role[role]['instance_id'],frame) for role in group_roles]
            trajectories=(exit_trajectories(item.id,states,to,room['blocking']['walk_lane'],end-frame)
                if item.kind=='exit_group' else group_trajectories(item.id,states,to,room['blocking']['walk_lane']))
            members=[]
            for i,trajectory in enumerate(trajectories):
                cid=item.id if i==0 else 'group_'+canonical_hash([item.id,trajectory['instance_id']])[:40]
                if i:
                    if cid in ids:fail(cid,'模型指令 ID 与协同行走派生指令冲突')
                    ids.add(cid);evidence[cid]=deepcopy(evidence[item.id]);derived_parents[cid]=item.id
                params=dict(to=trajectory['to'],visible_after=item.kind!='exit_group')
                if i==0 and target:params['target']=target
                member_end=frame+trajectory.get('duration_frames',end-frame)
                c=cue(item,trajectory['instance_id'],'actor.move_to',frame,member_end,params);c['id']=cid
                script['cues'].append(c)
                members.append(dict(cue_id=cid,role_id=group_roles[i],before=trajectory['before'],params=params))
                if item.kind=='exit_group':members[-1].update(start_frame=frame,end_frame=member_end)
            execution.append(dict(id=item.id,source=item.anchor.source.model_dump(),kind=item.kind,
                start_frame=frame,end_frame=end,members=members))
            continue
        if item.kind in ('move','enter','exit'):
            if to is None:fail(item.id,'移动必须指定实际目标')
            if item.kind=='enter':
                if pos['visible']:fail(item.id,'入场演员当前已经可见；仅隐藏演员可执行 enter')
                if item.target_kind!='slot':fail(item.id,'enter 的终点必须是 slot 站位；entrance 是起点，不能作为入场终点')
                entrance=min(room['blocking']['entrances'],key=lambda p:abs(p['position'][0]-to[0]))
                place=cue(item,iid,'actor.place',frame,frame,dict(position=entrance['position'],visible=True,facing=1 if to[0]>entrance['position'][0] else -1));place['id']=item.id+'_entry'
                script['cues'].append(place);pos['position']=entrance['position']
            if item.kind=='exit' and item.target_kind!='entrance':fail(item.id,'退场目标必须是实际入口')
            if abs(to[0]-pos['position'][0])<1e-6:
                preflight_errors.append(dict(path=item.id,code='INVALID',message='不能用原地行走冒充移动',
                    role_id=item.role_id,position=pos['position'],target=to,source=item.anchor.source.model_dump()))
            params=dict(to=to,visible_after=item.kind!='exit')
            if target:params['target']=target
            c=cue(item,iid,'actor.move_to',frame,end,params)
        elif item.kind=='face':
            facing=item.facing
            if item.target_kind=='actor':facing=1 if state['position'][0]>pos['position'][0] else -1
            if facing is None or not pos['visible']:
                raise LifeValidationError([dict(path=item.id,code='INVALID',message='转向缺少方向或演员不在场',
                    source=item.anchor.source.model_dump(),instance_id=iid,state_at_anchor=pos,
                    scene_initial_state=next(a for a in segment['actors'] if a['instance_id']==iid),
                    scene_state_resets=True,prior_cues_in_this_scene=[c['id'] for c in script['cues']
                        if c['instance_id']==iid and segment['start_frame']<=c['start_frame']<=frame])])
            c=cue(item,iid,'actor.place',frame,frame,dict(position=pos['position'],visible=True,facing=facing))
        else:
            if not pos['visible']:fail(item.id,'离场演员不能执行身体动作')
            c=cue(item,iid,'body.play',frame,end,dict(action=item.kind,mode='loop' if item.kind in ('idle','sit','pick','lie','victoryloop') else 'once'))
        script['cues'].append(c)
        execution.append(dict(id=item.id,source=c['source'],kind=item.kind,start_frame=frame,end_frame=c['end_frame'],before=pos,params=c['params']))
    expressions=[]
    for item in plan.expressions:
        frame,segment,line,iid=start(item);end=frame+round(item.duration_ms*rate/1000)
        actor=next(a for a in catalog['actors'] if a['actor_id']==by_role[item.role_id]['actor_id'])
        allowed=(('native','open','closed') if item.channel=='mouth' else ('native',*actor['face_profile']['eyes'])) if actor.get('face_profile') else ('native',)
        if item.preset not in allowed:
            preflight_errors.append(dict(path=item.id,code='INVALID',message='锁定素材版本不支持该表情预设',
                role_id=item.role_id,channel=item.channel,requested=item.preset,allowed=list(allowed)))
            continue
        if not interval(item,frame,end,segment):continue
        if not position_at(script,segment,iid,frame)['visible']:fail(item.id,'离场演员不能执行表情')
        expressions.append(dict(id=item.id,instance_id=iid,start_frame=frame,end_frame=end,enabled=True,channel=item.channel,preset=item.preset))
        cuts={frame,end-1}|{c['start_frame'] for c in script['cues'] if c['instance_id']==iid and frame<=c['start_frame']<end}|{c['end_frame'] for c in script['cues'] if c['instance_id']==iid and frame<=c['end_frame']<end}
        if any(not position_at(script,segment,iid,t)['visible'] for t in cuts):fail(item.id,'表情不可持续到演员离场之后')
        if item.channel=='mouth' and item.preset=='open':
            speech=sorted((s['start_frame'],s['end_frame']) for s in script['subtitles'] if s['role_id']==item.role_id)
            cursor=frame
            for a,b in speech:
                if a<=cursor<b:cursor=max(cursor,b)
            if cursor<end:fail(item.id,'开嘴覆盖必须位于该演员的实际对白区间内')
        execution.append(dict(id=item.id,source=item.anchor.source.model_dump(),kind=item.channel,start_frame=frame,end_frame=end,preset=item.preset))
    expression_normalizations=trim_redundant_expressions(expressions,execution)
    furniture=[]
    for item in plan.furniture:
        frame,segment,line,iid=start(item);arrive=frame+round(item.approach_ms*rate/1000)
        leave=None
        if item.leave_anchor:
            leave,ls,_=anchor(item.leave_anchor)
            if ls is not segment:fail(item.id,'起身不能跨场')
        end=segment['end_frame'] if leave is None else leave+round(item.depart_ms*rate/1000)
        seat=next((s for s in resources[segment['scene']['scene_id']].get('commander_seats',[]) if s['seat_id']==item.seat_id),None)
        if not seat:fail(item.id,'当前场景没有可用的指定座位')
        if not interval(item,frame,end,segment) or arrive>=end:
            if arrive>=end:issues.append(dict(code='DIRECTING_TIME_CONFLICT',message='坐姿没有可用时间',source=item.anchor.source.model_dump(),text=item.quote,cue_id=item.id))
            continue
        furniture.append(dict(id=item.id,instance_id=iid,episode_id=segment['episode_id'],scene_id=segment['scene_id'],seat_id=item.seat_id,
            calibration_revision=seat['calibration_revision'],outfit_id=by_role[item.role_id]['initial_outfit_id'],
            start_frame=frame,arrive_frame=arrive,leave_frame=leave,end_frame=end,enabled=True,source=item.anchor.source.model_dump(),source_text=item.quote,reason=item.reason))
        execution.append(dict(id=item.id,source=item.anchor.source.model_dump(),kind='furniture',start_frame=frame,arrive_frame=arrive,leave_frame=leave,end_frame=end,seat_id=item.seat_id))
    if staged and issues:
        # Asset simplifications are permitted; invalid execution intervals are not.
        preflight_errors.extend(dict(path=i['cue_id'],code=i['code'],message=i['message'],
                                     source=i['source']) for i in issues)
    try:
        script=validate_script(script,catalog,expected,set(lines))
    except LifeValidationError as exc:
        raise LifeValidationError(preflight_errors+exc.diagnostics) from None
    # These checks are independent on a structurally valid script. Collect their
    # failures for the single repair, but never emit a partially validated preview.
    checked=[]
    for check in (lambda:validate_cues_in_scenes(script,track,catalogs),
                  lambda:validate_faces(expressions,script,catalog,track),
                  lambda:validate_furniture(dict(schema_version='commander_furniture/1',cues=furniture),script,track,catalogs,catalog)):
        try:checked.append(check())
        except LifeValidationError as exc:
            preflight_errors.extend(dict(d,path=derived_parents[d['path']],derived_cue_id=d['path'])
                if d['path'] in derived_parents else d for d in exc.diagnostics)
    if preflight_errors:
        unique={}
        for diagnostic in preflight_errors:
            unique.setdefault((diagnostic['path'],diagnostic['code'],diagnostic['message']),diagnostic)
        raise LifeValidationError(list(unique.values()))
    expressions,furniture_track=checked[1:]
    actual_ids={e['id'] for e in execution};all_ids=set(evidence)
    assessments={key(a.source.model_dump()):a for a in plan.assessments}
    if len(assessments)!=len(plan.assessments) or set(assessments)!=set(source_by_key):fail('assessments','必须逐一评估所有源行，不能遗漏或新增')
    coverage=[];coverage_errors=[]
    for k,a in assessments.items():
        line=source_by_key[k]
        if a.quote not in line['text']:
            coverage_errors.append(dict(path='assessments',code='INVALID',message='覆盖评估的原文证据无效',
                source=a.source.model_dump(),quote=a.quote,source_text=line['text']))
        invalid_refs=[cid for cid in a.cue_ids if cid not in all_ids or key(evidence[cid]['source'])!=k]
        if invalid_refs:
            coverage_errors.append(dict(path='assessments',code='INVALID',message='覆盖指令必须属于对应源行，不能引用其他场次或其他源行的指令',
                source=a.source.model_dump(),quote=a.quote,
                invalid_cues=[dict(cue_id=cid,actual_source=evidence.get(cid,{}).get('source')) for cid in invalid_refs]))
        dropped=[i for i in issues if key(i['source'])==k]
        if staged and a.classification=='gap':fail('assessments','新策略用演绎说明记录简化，不使用旧 gap 分类')
        if a.classification=='performed' and not a.cue_ids:
            coverage_errors.append(dict(path='assessments',code='INVALID',message='声称已演出必须有本源行的新指令；普通对白或延续既有表情不是本行新增动作',
                source=a.source.model_dump(),quote=a.quote,classification=a.classification))
        if line['kind']=='action' and a.classification=='no_visible_action' and not a.reason:fail('assessments','动作不能被无说明忽略')
        status='needs_correction' if dropped or a.classification=='gap' else 'compiled' if a.classification=='performed' else 'not_executed'
        coverage.append(dict(id='ai_coverage_'+canonical_hash(list(k))[:20],source=a.source.model_dump(),text=line['text'],word='',action='model_assessment',
            start_frame=min(o['start_frame'] for o in line['operations']),end_frame=segments[k[:2]]['end_frame'],status=status,
            cue_ids=[c for c in a.cue_ids if c in actual_ids],message=a.reason+('；'+ '；'.join(i['message'] for i in dropped) if dropped else ''),
            instance_id=None,target=None,method='model'))
        if staged:coverage[-1].update(presentation=a.presentation,intent=a.intent,simplification=a.simplification)
    if coverage_errors:raise LifeValidationError(coverage_errors)
    diagnostics=[dict(code='ACTION_NEEDS_CORRECTION',message=c['message'],source=c['source'],text=c['text']) for c in coverage if c['status']=='needs_correction']
    diagnostics.extend(dict(code='UNBOUND_ROLE',message=r['name']+'仅保留声音和字幕，用户未绑定视觉演员') for r in roles if r['role_id'] not in by_role)
    if not staged:diagnostics.extend(dict(code='SCENE_APPROXIMATION',message=s.difference,scene_id=s.scene_id) for s in plan.scenes if s.difference)
    for s in track:s['confirmation_source']='model'
    profiles={a['instance_id']:deepcopy(next(x for x in catalog['actors'] if x['actor_id']==a['actor_id']).get('face_profile')) for a in script['actors']}
    from .life_contracts import action_durations
    value=dict(schema_version='spine_stage_preview/3.2',profile='life_v2',job_id=job['id'],title=base['title'],source_version_id=base['source_version_id'],
        catalog_sha256=catalog['catalog_sha256'],audio=base['audio'],timeline=base['timeline'],script=base['script'],stage=script,roles=roles,
        scene_track=dict(schema_version='legend_scene_track/1',segments=track),scene_bindings=[{k:s[k] for k in ('episode_id','scene_id','scene','actors','confirmed','note')} for s in track],
        coverage=coverage,coverage_decisions=[],expression_track=expressions,dialogue_enabled=False,review_confirmed=False,
        status='needs_attention' if diagnostics else 'preview_ready',diagnostics=diagnostics,mouth_control=True,usage='local_preview_only',rule_version='spine_director/1',compiler_version='spine_director_compiler/1',
        face_profiles=profiles,mouth_enabled={a['instance_id']:bool(profiles[a['instance_id']]) for a in script['actors']},furniture_track=furniture_track,upgraded_from=None,
        outfit_actions={o['outfit_id']:action_durations(next(a for a in catalog['actors'] if a['actor_id']==o['actor_id']),catalog,o['outfit_id']) for o in catalog['outfits']},
        cue_evidence=evidence)
    if staged:
        value.update(visual_policy=context['visual_policy'],capability_sha256=context['capability_sha256'],
            interpretation_notes=[dict(code='STAGE_ADAPTATION',source=a.source.model_dump(),message=a.simplification,
                presentation=a.presentation) for a in plan.assessments if a.simplification]+[
                dict(code='SCENE_APPROXIMATION',scene_id=s.scene_id,message=s.difference) for s in plan.scenes if s.difference])
    summary=dict(locked_cast=script['actors'],scenes=track,execution=execution,coverage=coverage,issues=issues,diagnostics=diagnostics)
    if expression_normalizations:summary['expression_normalizations']=expression_normalizations
    if staged:summary.update(visual_policy=value['visual_policy'],interpretation_notes=value['interpretation_notes'])
    return value,summary


def trim_redundant_expressions(expressions,execution):
    """Split identical overlapping holds without changing the visible state at any frame.

    Keep both source IDs. Conflicting presets, equal starts and nested intervals
    are deliberately left for the existing validator to reject.
    """
    changes=[]
    ordered=sorted((e for e in expressions if e['enabled']),key=lambda e:e['start_frame'])
    for previous in ordered:
        for following in ordered:
            if (previous['instance_id'],previous['channel'],previous['preset'])!=(following['instance_id'],following['channel'],following['preset']):continue
            if previous['start_frame']<following['start_frame']<previous['end_frame']<=following['end_frame']:
                old=previous['end_frame'];previous['end_frame']=following['start_frame']
                for row in execution:
                    if row['id']==previous['id']:row['end_frame']=previous['end_frame']
                changes.append(dict(id=previous['id'],continued_by=following['id'],old_end_frame=old,
                    end_frame=previous['end_frame'],reason='identical_preset_continues_without_visible_change'))
    return changes
