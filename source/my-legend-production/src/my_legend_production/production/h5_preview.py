"""Versioned H5 story choreography. Deterministic, no model or production mutations."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import re
from types import SimpleNamespace
from typing import Literal

from pydantic import Field, ValidationError
from .contracts import Contract, ArtifactRef
from .life_contracts import canonical_hash, validate_catalog, validate_script, action_durations, fail, LifeValidationError
from .life_preview import context, SOURCE_FIELDS, NON_EXECUTION
from .scene_preview import SceneRef, load_scene_catalog
from .repository import ProductionError


class SceneActor(Contract):
    instance_id: str
    position: list[float] = Field(min_length=2, max_length=2)
    facing: Literal[-1, 1] = 1
    visible: bool = True


class SceneBinding(Contract):
    episode_id: str
    scene_id: str
    scene: SceneRef
    actors: list[SceneActor] = Field(max_length=3)
    confirmed: bool = False
    note: str = Field(default='', max_length=500)


class CoverageDecision(Contract):
    coverage_id: str
    resolution: Literal['manual', 'omitted']
    cue_ids: list[str] = Field(default_factory=list, max_length=30)
    note: str = Field(min_length=1, max_length=500)


class ExpressionCue(Contract):
    id: str
    instance_id: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    preset: Literal['native', 'open', 'closed', 'talk']
    enabled: bool = True


def parse(model, value, path):
    try: return model.model_validate(value).model_dump()
    except ValidationError as exc:
        fail(path, '；'.join('.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors()))


def load_h5_catalog(root, digest=None):
    try:
        digest = digest or json.loads((root/'life-v2-current.json').read_text('utf-8'))['catalog_sha256']
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest): raise ValueError()
        value=json.loads((root/'catalogs'/(digest+'.json')).read_text('utf-8'))
        if value.get('profile')!='life_v2' or value.get('catalog_sha256')!=digest: raise ValueError()
        return validate_catalog(value)
    except (OSError,ValueError,KeyError): raise ProductionError('H5_CATALOG_INVALID_OR_NOT_CONFIGURED') from None


def source_context(job, store, catalog):
    base,delivery,timeline,lines,expected,roles=context(job,SimpleNamespace(bindings={}),store,catalog)
    by_role={r['role_id']:r for r in roles}
    for c in delivery.get('character_snapshot',[]):
        by_role.setdefault(c['role_id'],dict(role_id=c['role_id'],name=c.get('canon',{}).get('name',c['role_id'])))
    return base,delivery,timeline,lines,expected,list(by_role.values())


def measured_scenes(delivery, timeline):
    """Scene starts follow source order; background tails cannot extend the old scene."""
    total=timeline['total_frames'];events=timeline['events'];result=[]
    for ep in delivery['episodes']:
        for scene in ep['scenes']:
            matching=[e for e in events if (e.get('source',e)['episode_id'],e.get('source',e)['scene_id'])==(ep['episode_id'],scene['scene_id'])]
            ops=[o for e in matching for o in e.get('operations',[e]) if o.get('mode',o.get('render_role')) not in ('continue_background','change_background','stop_sound')]
            starts=[o['start_frame'] for o in ops]
            result.append(dict(episode_id=ep['episode_id'],scene_id=scene['scene_id'],title=scene.get('title',scene['scene_id']),
                location=scene.get('location',''),start_frame=min(starts) if starts else None,
                source_role_ids=list(dict.fromkeys(l['speaker_role_id'] for l in scene['utterances'] if l.get('speaker_role_id')))))
    problems=[]
    for i,s in enumerate(result):
        start=s['start_frame'];end=result[i+1]['start_frame'] if i+1<len(result) else total
        s['end_frame']=end
        if start is None or end is None or not 0<=start<end<=total:
            problems.append(dict(code='SCENE_TIME_UNRESOLVED',message='分场缺少可靠时间锚点或时间顺序冲突',scene_id=s['scene_id']))
    if result and result[0]['start_frame'] is not None: result[0]['start_frame']=0
    # Speech may not cross a scene change; background/SFX tails may continue.
    for s in result:
        if s['start_frame'] is None or s['end_frame'] is None: continue
        for e in events:
            ref=e.get('source',e)
            if (ref['episode_id'],ref['scene_id'])!=(s['episode_id'],s['scene_id']):continue
            for op in e.get('operations',[e]):
                if e['kind'] in ('dialogue','narration') and op.get('mode',op.get('render_role','speech'))=='speech' and op['end_frame']>s['end_frame']:
                    problems.append(dict(code='SCENE_SPEECH_OVERLAP',message='对白或旁白跨越分场边界，需先核对实测时间轴',scene_id=s['scene_id']))
    return result,problems


def prepare_context(job, store, catalog):
    base,delivery,timeline,lines,expected,roles=source_context(job,store,catalog)
    scenes,problems=measured_scenes(delivery,timeline)
    return dict(schema_version='h5_story_context/1',profile='life_v2',job_id=job['id'],title=base['title'],
        source_version_id=base['source_version_id'],catalog_sha256=catalog['catalog_sha256'],audio=base['audio'],timeline=base['timeline'],
        script=base['script'],sample_rate=expected['sample_rate'],total_frames=expected['total_frames'],roles=roles,
        scenes=scenes,diagnostics=problems,max_visual_actors=3)


def make_script(base, expected, roles, catalog, bindings, outfits):
    available={a['actor_id']:a for a in catalog['actors']};names={r['role_id']:r['name'] for r in roles}
    if not bindings or len(bindings)>3 or any(r not in names or aid not in available for r,aid in bindings.items()):
        fail('bindings','请选择 1 到 3 个有效的剧情角色和素材演员')
    actors=[]
    for role in names:
        if role not in bindings:continue
        asset=available[bindings[role]];oid=outfits.get(role) or asset.get('default_outfit_id',asset['outfit_ids'][0])
        if oid not in asset['outfit_ids']:fail('initial_outfits.'+role,'外观预设不属于所选角色')
        actors.append(dict(instance_id='actor_'+str(len(actors)+1),role_id=role,name=names[role],actor_id=asset['actor_id'],actor_revision=asset['revision'],
            initial_outfit_id=oid,position=[.5,.825],facing=1,visible=True,actions=action_durations(asset,catalog,oid)))
    script={k:deepcopy(v) for k,v in expected.items() if k!='roles'}
    script.update(schema_version='legend_stage_script/0.3',rule_version='zh_life_keywords/1',actors=actors,cues=[],usage='story_preview')
    return script


def bind_scenes(source_scenes, values, script, root, *, model_plan=False):
    parsed=[parse(SceneBinding,v,'scene_bindings') for v in values]
    keys=[(s['episode_id'],s['scene_id']) for s in source_scenes]
    lookup={(v['episode_id'],v['scene_id']):v for v in parsed}
    if len(lookup)!=len(parsed) or set(lookup)!=set(keys):fail('scene_bindings','必须逐一绑定原文全部分场，不能重复或新增分场')
    instances={a['instance_id'] for a in script['actors']};track=[];catalogs={}
    for s in source_scenes:
        binding=lookup[(s['episode_id'],s['scene_id'])]
        if not binding['confirmed'] and not model_plan:fail('scene_bindings.'+s['scene_id'],'请确认场景及其与原文地点的差异')
        ref=binding['scene'];h=ref['catalog_sha256']
        if h not in catalogs:catalogs[h]=load_scene_catalog(root,h)
        room=next((r for r in catalogs[h]['scenes'] if r['scene_id']==ref['scene_id']),None)
        if not room or not room.get('blocking'):fail('scene_bindings.'+s['scene_id'],'请选择具有走位空间的场景')
        if {a['instance_id'] for a in binding['actors']}!=instances or len(binding['actors'])!=len(instances):
            fail('scene_bindings.'+s['scene_id'],'各分场须明确全部已绑定演员的在场状态')
        lane=room['blocking']['walk_lane'];foot=room['blocking']['foot_y']
        for a in binding['actors']:
            x,y=a['position']
            if not all(math.isfinite(v) for v in (x,y)) or not lane[0]<=x<=lane[2] or abs(y-foot)>1e-6:
                fail('scene_bindings.'+s['scene_id'],'初始站位须在场景脚部通道内')
        visible=[a for a in binding['actors'] if a['visible']]
        if any(abs(a['position'][0]-b['position'][0])<.12 for i,a in enumerate(visible) for b in visible[i+1:]):
            fail('scene_bindings.'+s['scene_id'],'初始演员站位过近，请调整位置')
        track.append({**deepcopy(s),**binding})
    return track,catalogs


def position_at(script, segment, iid, frame):
    state=deepcopy(next(a for a in segment['actors'] if a['instance_id']==iid))
    order=lambda c:(c['start_frame'],0 if c['type']=='actor.place' else 1,c['id'])
    for c in sorted((c for c in script['cues'] if c['enabled'] and c['instance_id']==iid and segment['start_frame']<=c['start_frame']<=frame),key=order):
        if c['type']=='actor.place':state.update(position=c['params']['position'][:],visible=c['params'].get('visible',True),facing=c['params'].get('facing',1))
        elif c['type']=='actor.move_to':
            t=min(1,(frame-c['start_frame'])/(c['end_frame']-c['start_frame']));to=c['params']['to'];before=state['position'][:]
            state['position']=[x+(y-x)*t for x,y in zip(before,to)]
            if abs(to[0]-before[0])>1e-8:state['facing']=-1 if to[0]<before[0] else 1
            state['visible']=True if t<1 else c['params'].get('visible_after',True)
    return state


def movement_issue(script, segment, iid, start, end, destination, room):
    lane=room['blocking']['walk_lane'];foot=room['blocking']['foot_y'];x,y=destination
    if not lane[0]<=x<=lane[2] or abs(y-foot)>1e-6:return '移动目标不在当前场景通道内'
    initial=position_at(script,segment,iid,start)
    if not initial['visible']:return '演员尚未入场，请先添加入场指令'
    # Each trajectory is piecewise linear. Include every cue boundary so a short
    # crossing between samples cannot be missed; endpoints bound relative distance.
    boundaries={start,end}
    for cue in script['cues']:
        if cue['enabled']:
            boundaries.update(f for f in (cue['start_frame'],cue['end_frame']) if start<f<end)
    ordered=sorted(boundaries)
    for other in script['actors']:
        if other['instance_id']==iid:continue
        for a,b in zip(ordered,ordered[1:]):
            states=[position_at(script,segment,other['instance_id'],f) for f in (a,b-1e-6)]
            if not any(state['visible'] for state in states):continue
            distances=[initial['position'][0]+(x-initial['position'][0])*(f-start)/(end-start)-state['position'][0] for f,state in zip((a,b-1e-6),states)]
            if min(distances)<.10 and max(distances)>-.10:return '行走路径或目标被其他演员占用'
    return None


ACTION_PATTERN=re.compile(r'(?P<enter>走进来|走进|走入|进场|入场|进入房间)|(?P<exit>走出去|走出|离场|退场|离开房间)|(?P<approach>走近|靠近|走向|走到)|(?P<idle>停下来|停下|等待|待机|站立)|(?P<walk>向左走|向右走|往左走|往右走|走动|行走|走过去)|(?P<face>转过身|转身|面向|面对|转向|朝左|朝右)|(?P<unsupported>翻找|翻书|拿起|拾起|捡起|坐下|落座|躺下|跳舞|舞蹈|拥抱|挥手|打开|关上)')


def compile_choreography(script, delivery, timeline, lines, scene_track, scene_catalogs):
    coverage=[];actors=script['actors'];aliases={a['instance_id']:{a['name']} for a in actors}
    for c in delivery.get('character_snapshot',[]):
        a=next((a for a in actors if a['role_id']==c['role_id']),None)
        if a:aliases[a['instance_id']].update(n for n in [c.get('canon',{}).get('name'),*c.get('canon',{}).get('aliases',[])] if isinstance(n,str) and n)
    for event in timeline['events']:
        ref=event.get('source',event);key=tuple(ref[k] for k in SOURCE_FIELDS);line=lines.get(key)
        if not line or line['kind'] not in ('action','narration','dialogue'):continue
        segment=next(s for s in scene_track if (s['episode_id'],s['scene_id'])==key[:2])
        room=next(r for r in scene_catalogs[segment['scene']['catalog_sha256']]['scenes'] if r['scene_id']==segment['scene']['scene_id'])
        for op_index,op in enumerate(event.get('operations',[event])):
            if op.get('mode',op.get('render_role','speech')) not in ('speech','action_note'):continue
            text=op.get('cue_text') or line['text'];prior='';cursor=op['start_frame']
            # Clause splitting also exposes unrecognized compound actions instead of silently dropping them.
            clauses=list(re.finditer(r'[^，,。；;！!？?\n]+',text))
            for ci,clause_match in enumerate(clauses):
                clause=clause_match.group();chunks=re.split(r'然后|接着|并且|并',clause)
                for chunk_i,chunk in enumerate(chunks):
                    matches=list(ACTION_PATTERN.finditer(chunk))
                    if not matches and line['kind']!='action':continue
                    for mi,match in enumerate(matches or [None]):
                        kind=match.lastgroup if match else 'unsupported';word=match.group() if match else chunk
                        cid='coverage_'+canonical_hash([key,op_index,ci,chunk_i,mi])[:20]
                        item=dict(id=cid,source=dict(zip(SOURCE_FIELDS,key)),text=chunk,action=kind,word=word,
                            start_frame=op['start_frame'],end_frame=segment['end_frame'],status='needs_correction',cue_ids=[],message='',instance_id=None,target=None)
                        coverage.append(item)
                        def reject(message,status='needs_correction'):
                            item.update(message=message,status=status)
                        if NON_EXECUTION.search(chunk) or re.search(r'[？?]|(?:吗|么)[。！!\s]*$',text):
                            reject('否定、意图、转述或疑问不自动执行','not_executed');continue
                        if kind=='unsupported':reject('当前能力不支持该动作；可人工说明本版不演出','unsupported');continue
                        before=chunk[:match.start()];hits={iid for iid,names in aliases.items() if any(n in before for n in names)}
                        if not hits and prior and not re.search('你|他|她|两人|二人|大家',before):hits={prior}
                        if not hits and line['kind']=='dialogue' and not re.search('你|他|她|两人|二人|大家',before):
                            hits={a['instance_id'] for a in actors if a['role_id']==line.get('speaker_role_id')}
                        if len(hits)!=1:reject('动作主体不明确或未绑定，请人工指定');continue
                        iid=hits.pop();prior=iid;item['instance_id']=iid
                        start=max(cursor,segment['start_frame']);end=min(start+round((.3 if kind=='idle' else 1.4)*script['sample_rate']),segment['end_frame'])
                        if end<=start:reject('分场内没有足够的动作时间');continue
                        current=position_at(script,segment,iid,start);pos=current['position'];params={};ctype='body.play';target=None
                        suffix=chunk[match.end():];lane=room['blocking']['walk_lane'];foot=room['blocking']['foot_y']
                        if kind=='idle':params=dict(action='idle',mode='loop')
                        elif kind in ('approach','walk','enter','exit'):
                            ctype='actor.move_to'
                            if kind=='approach':
                                targets=[]
                                for other,names in aliases.items():
                                    if other!=iid and any(n in suffix for n in names):targets.append(dict(kind='actor',id=other))
                                for p in room['blocking']['targets']:
                                    names={p['label'],re.sub(r'(?:前|旁|停靠点|站位)$','',p['label'])}
                                    if any(n and n in suffix for n in names):targets.append(dict(kind='object',id=p['id']))
                                if len(targets)!=1:reject('当前场景中没有唯一的人物或物件目标，请人工选择');continue
                                target=targets[0];item['target']=target
                                if target['kind']=='object':to=next(p['position'][:] for p in room['blocking']['targets'] if p['id']==target['id'])
                                else:
                                    other=position_at(script,segment,target['id'],start)
                                    if not other['visible']:reject('目标人物不在场');continue
                                    to=[other['position'][0]+(-.18 if pos[0]<=other['position'][0] else .18),foot]
                            elif kind=='walk':
                                if not re.search('左|右',word+suffix):reject('行走方向不明确，请人工指定');continue
                                to=[pos[0]+(-.16 if '左' in word+suffix else .16),foot]
                            else:
                                side='left' if '左' in chunk else 'right' if '右' in chunk else ('left' if pos[0]<=.5 else 'right')
                                entrance=min(room['blocking']['entrances'],key=lambda p:p['position'][0]) if side=='left' else max(room['blocking']['entrances'],key=lambda p:p['position'][0])
                                if kind=='enter':
                                    if current['visible']:reject('演员已在场；请先确认该场初始在场状态');continue
                                    to=pos[:]
                                    script['cues'].append(dict(id=cid+'_enter',type='actor.place',instance_id=iid,start_frame=start,end_frame=start,enabled=True,
                                        params=dict(position=entrance['position'][:],visible=True,facing=1 if side=='left' else -1),source=item['source'],source_text=text,rule_id='h5_rules/1',reason='入场起点'))
                                else:to=entrance['position'][:]
                            problem=movement_issue(script,segment,iid,start,end,to,room)
                            if problem:
                                script['cues']=[c for c in script['cues'] if c['id']!=cid+'_enter'];reject(problem);continue
                            params=dict(to=to,visible_after=kind!='exit')
                            if target:params['target']=target
                        else:
                            ctype='actor.place';end=start
                            if '左' in word+suffix:facing=-1
                            elif '右' in word+suffix:facing=1
                            elif word in ('转身','转过身'):facing=-current['facing']
                            else:
                                targets={other for other,names in aliases.items() if other!=iid and any(n in suffix for n in names)}
                                if len(targets)!=1:reject('朝向目标不明确，请人工指定');continue
                                other=position_at(script,segment,targets.pop(),start);facing=-1 if other['position'][0]<pos[0] else 1
                            params=dict(position=pos[:],visible=current['visible'],facing=facing)
                        cue=dict(id=cid+'_cue',type=ctype,instance_id=iid,start_frame=start,end_frame=end,enabled=True,params=params,
                            source=item['source'],source_text=text,rule_id='h5_rules/1',reason='规则编排：'+word)
                        occupied=any(c['enabled'] and c['instance_id']==iid and c['type'] in ('body.play','actor.move_to') and max(start,c['start_frame'])<min(end,c['end_frame']) for c in script['cues'])
                        if occupied:reject('动作时间与已有指令重叠，请人工校正');continue
                        script['cues'].append(cue);item.update(status='compiled',cue_ids=[c['id'] for c in script['cues'] if c['id'].startswith(cid+'_')],message='已生成基础动作')
                        cursor=max(cursor,end)
    return coverage


def validate_cues_in_scenes(script, track, catalogs):
    errors=[]
    for c in script['cues']:
        try:_validate_cue_in_scene(c,script,track,catalogs)
        except LifeValidationError as exc:
            segment=next((s for s in track if s['start_frame']<=c['start_frame']<s['end_frame']),None)
            for diagnostic in exc.diagnostics:
                if segment and c['type']=='actor.move_to':
                    diagnostic={**diagnostic,'instance_id':c['instance_id'],'to':c['params']['to'],
                        'start_frame':c['start_frame'],'end_frame':c['end_frame'],
                        'positions_at_start':{a['instance_id']:position_at(script,segment,a['instance_id'],c['start_frame']) for a in script['actors']},
                        'positions_at_end':{a['instance_id']:position_at(script,segment,a['instance_id'],c['end_frame']) for a in script['actors']}}
                errors.append(diagnostic)
    if errors:raise LifeValidationError(errors)


def _validate_cue_in_scene(c, script, track, catalogs):
    if not c['enabled']:return
    segment=next((s for s in track if s['start_frame']<=c['start_frame']<s['end_frame']),None)
    if not segment or c['end_frame']>segment['end_frame']:fail(c['id'],'动作不可跨越分场边界')
    if c['source'] and (c['source']['episode_id'],c['source']['scene_id'])!=(segment['episode_id'],segment['scene_id']):fail(c['id'],'动作来源与执行分场不一致')
    room=next(r for r in catalogs[segment['scene']['catalog_sha256']]['scenes'] if r['scene_id']==segment['scene']['scene_id'])
    if c['type']=='actor.move_to':
        # Evaluate the trajectory without the cue itself when checking its initial visibility.
        probe={**script,'cues':[x for x in script['cues'] if x['id']!=c['id']]}
        target=c['params'].get('target')
        if target:
            if target['kind']=='object':
                point=next((p for p in room['blocking']['targets'] if p['id']==target['id']),None)
                if not point or c['params']['to']!=point['position']:fail(c['id'],'物件目标不属于当前场景或目标坐标已变化')
            else:
                if target['id']==c['instance_id'] or not any(a['instance_id']==target['id'] for a in script['actors']):fail(c['id'],'人物目标无效')
                other=position_at(probe,segment,target['id'],c['start_frame'])
                if not other['visible'] or abs(abs(c['params']['to'][0]-other['position'][0])-.18)>1e-6:fail(c['id'],'人物目标不在场或停靠位置已变化，请重新选择目标')
        problem=movement_issue(probe,segment,c['instance_id'],c['start_frame'],c['end_frame'],c['params']['to'],room)
        if problem:fail(c['id'],problem)
    elif c['type']=='actor.place':
        x,y=c['params']['position'];lane=room['blocking']['walk_lane']
        # position_at interpolates prior moves: an exact .06 endpoint can become
        # .05999999999999994. A subsequent face cue preserves that derived
        # position. Absorb floating-point roundoff without changing stored cues
        # or permitting a materially out-of-lane placement.
        if not lane[0]-1e-9<=x<=lane[2]+1e-9 or abs(y-room['blocking']['foot_y'])>1e-6:fail(c['id'],'位置必须在该场景通道内')
        if c['params'].get('visible',True):
            for other in script['actors']:
                if other['instance_id']==c['instance_id']:continue
                state=position_at(script,segment,other['instance_id'],c['start_frame'])
                if state['visible'] and abs(x-state['position'][0])<.10:fail(c['id'],'位置被其他演员占用')



def coverage_status(items, decisions, script):
    by_id={i['id']:deepcopy(i) for i in items};parsed=[parse(CoverageDecision,d,'coverage_decisions') for d in decisions]
    if len({d['coverage_id'] for d in parsed})!=len(parsed):fail('coverage_decisions','动作处理记录重复')
    cues={c['id']:c for c in script['cues'] if c['enabled']}
    for item in by_id.values():
        if item['status']=='compiled' and any(cid not in cues for cid in item['cue_ids']):item.update(status='needs_correction',message='原编排指令被删除或禁用，需说明处理方式')
    for decision in parsed:
        if decision['coverage_id'] not in by_id:fail('coverage_decisions','未知原文动作')
        item=by_id[decision['coverage_id']]
        if decision['resolution']=='manual':
            if item['status']=='unsupported':fail('coverage_decisions','缺少的动作能力不能用基础指令标记为完整演出；请选择本版不演出')
            if not decision['cue_ids'] or any(cid not in cues or cues[cid]['source']!=item['source'] for cid in decision['cue_ids']):fail('coverage_decisions','人工校正须关联同一原文的有效指令')
            item.update(status='manual',cue_ids=decision['cue_ids'],message=decision['note'])
        else:item.update(status='omitted',message=decision['note'])
    return list(by_id.values()),parsed


def validate_expressions(values, script, catalog, track):
    parsed=[parse(ExpressionCue,v,'expression_track') for v in values];ids=set();actors={a['instance_id']:a for a in script['actors']}
    for c in parsed:
        if c['id'] in ids or c['instance_id'] not in actors or not c['start_frame']<c['end_frame']<=script['total_frames']:fail('expression_track','表情 ID、演员或时间范围无效')
        ids.add(c['id'])
        outfit=next(o for o in catalog['outfits'] if o['outfit_id']==actors[c['instance_id']]['initial_outfit_id'])
        if c['preset'] not in outfit.get('face_presets',['native']):fail('expression_track','该头部没有所选表情')
        if c['enabled'] and any(o is not c and o['enabled'] and o['instance_id']==c['instance_id'] and max(c['start_frame'],o['start_frame'])<min(c['end_frame'],o['end_frame']) for o in parsed):fail('expression_track','同一演员表情区间重叠')
    return parsed


def build_h5_preview(job, body, store, catalog, root):
    base,delivery,timeline,lines,expected,roles=source_context(job,store,catalog)
    source_scenes,problems=measured_scenes(delivery,timeline)
    if problems:fail('scene_track',problems[0]['message'])
    expected['roles']=[r['role_id'] for r in roles]
    if body.operation=='compile':script=make_script(base,expected,roles,catalog,body.bindings,body.initial_outfits)
    else:
        if body.script is None:fail('script','请提供编排草稿')
        script=deepcopy(body.script)
    script=validate_script(script,catalog,expected,set(lines))
    track,catalogs=bind_scenes(source_scenes,body.scene_bindings,script,root)
    if body.operation=='compile':coverage=compile_choreography(script,delivery,timeline,lines,track,catalogs)
    else:
        probe=deepcopy(script);probe['cues']=[];coverage=compile_choreography(probe,delivery,timeline,lines,track,catalogs)
    script=validate_script(script,catalog,expected,set(lines));validate_cues_in_scenes(script,track,catalogs)
    coverage,decisions=coverage_status(coverage,body.coverage_decisions,script)
    expressions=validate_expressions(body.expression_track,script,catalog,track)
    pending=[c for c in coverage if c['status'] in ('needs_correction','unsupported')]
    omitted=[c for c in coverage if c['status']=='omitted']
    unbound=[r for r in roles if r['role_id'] not in {a['role_id'] for a in script['actors']}]
    if body.review_confirmed and pending:fail('review_confirmed','仍有未处理的原文动作，不能确认演绎')
    status='needs_attention' if pending else 'confirmed_with_omissions' if body.review_confirmed and (omitted or unbound) else 'confirmed' if body.review_confirmed else 'preview_ready'
    diagnostics=[dict(code='ACTION_'+c['status'].upper(),message=c['message'],source=c['source'],text=c['text']) for c in coverage if c['status'] not in ('compiled','manual')]
    diagnostics.extend(dict(code='UNBOUND_ROLE',message=r['name']+'仅保留声音和字幕，未绑定舞台演员') for r in unbound)
    return dict(schema_version='spine_stage_preview/3.0',profile='life_v2',job_id=job['id'],title=base['title'],source_version_id=base['source_version_id'],
        catalog_sha256=catalog['catalog_sha256'],audio=base['audio'],timeline=base['timeline'],script=base['script'],stage=script,roles=roles,
        scene_track=dict(schema_version='legend_scene_track/1',segments=track),scene_bindings=[{k:s[k] for k in ('episode_id','scene_id','scene','actors','confirmed','note')} for s in track],
        coverage=coverage,coverage_decisions=decisions,expression_track=expressions,dialogue_enabled=body.dialogue_enabled,
        review_confirmed=body.review_confirmed,status=status,diagnostics=diagnostics,mouth_control=False,usage='local_preview_only',rule_version='h5_rules/1')


def read_h5_preview(root,job,digest,store):
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',job['id']):raise ProductionError('SPINE_PREVIEW_NOT_FOUND')
    try:
        raw=(root/'previews'/job['id']/(digest+'.json')).read_bytes()
        if sha256(raw).hexdigest()!=digest:raise ValueError()
        value=json.loads(raw)
        if value['job_id']!=job['id'] or value['profile']!='life_v2' or value['schema_version']!='spine_stage_preview/3.0':raise ValueError()
    except (OSError,ValueError,KeyError):raise ProductionError('SPINE_PREVIEW_NOT_FOUND') from None
    validate_h5_value(root,job,value,store)
    return {**value,'preview_sha256':digest}


def validate_h5_value(root,job,value,store):
    from .spine_preview import StagePreviewRequest
    body=StagePreviewRequest(profile='life_v2',operation='validate_and_save',script=value['stage'],scene_bindings=value['scene_bindings'],
        coverage_decisions=value['coverage_decisions'],expression_track=value['expression_track'],dialogue_enabled=value['dialogue_enabled'],review_confirmed=value['review_confirmed'])
    rebuilt=build_h5_preview(job,body,store,load_h5_catalog(root,value['catalog_sha256']),root)
    if rebuilt!=value:raise ProductionError('H5_PREVIEW_REVALIDATION_MISMATCH')
