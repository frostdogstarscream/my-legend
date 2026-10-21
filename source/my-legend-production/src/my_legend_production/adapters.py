from __future__ import annotations

from copy import deepcopy

from .core import AdapterError, encoded, ident, json_bytes, key, locator, require, sha, source_check, unique, validate, weighted_length

EMOTIONS = {'neutral':'中性','happy':'愉快','sad':'悲伤','worried':'担忧','angry':'愤怒','surprised':'惊讶',
            'fearful':'害怕','reassuring':'安抚','serious':'严肃','playful':'俏皮','mysterious':'神秘','tender':'温柔',
            'calm':'平静','excited':'兴奋'}


def identity(line):
    return ('narrator', line['narrator_id']) if line['kind'] == 'narration' else ('role', line['speaker_role_id'])


def compact_instruction(line, default=''):
    """Compact labels only; preserve every supplied performance value and emphasis."""
    parts = []
    if line.get('emotion'): parts.append(EMOTIONS.get(line['emotion'], line['emotion']))
    if line.get('tone_instruction'): parts.append(line['tone_instruction'])
    if line.get('emphasis'):
        parts.append('重音：'+'、'.join(s['span_text']+(f"（第{s['occurrence']}次）" if s.get('occurrence',1)!=1 else '') for s in line['emphasis']))
    if line.get('pauses'): parts.append('衔接处短停')
    if line.get('speech_rate',1.0)!=1.0: parts.append(f"语速参考{line['speech_rate']}倍")
    for field,label in [('pitch','音高'),('volume','音量')]:
        if line.get(field): parts.append(label+'：'+line[field])
    if line.get('delivery_note'): parts.append(line['delivery_note'])
    if default: parts.append(default)
    return '；'.join(parts)


class Mapping:
    def __init__(self, request, delivery, manifest, context):
        self.request, self.delivery, self.manifest, self.context = request, delivery, manifest, context
        self.rows = source_check(request, delivery, manifest)
        source = request['source']; sid, vid = source['story_id'], source['story_version_id']
        parts = [request['adapter'],'1.0',sid,vid,source['delivery']['sha256'],
                 source['render_manifest']['sha256'] if source['render_manifest'] else '-']
        parts += [x for sc in request['scope']['scenes'] for x in (sc['episode_id'],sc['scene_id'])]
        parts += [request['context']['sha256'],request['joint_profile']]
        self.fingerprint = sha(encoded(parts))
        self.mapping_id = 'map_' + self.fingerprint[:32]
        self.version = ident('v_', ['version',sid,vid])
        self.roles = {}
        self.alias_sources = {}
        self.coverage, self.handling, self.issues = [], [], []
        for _, _, line in self.rows:
            if line['kind'] in ('dialogue','narration'):
                who = identity(line)
                if who in self.roles:
                    require(self.roles[who]['performer_id'] == line['performer_id'], 'PERFORMER_MISMATCH')
                alias = self.alias('r_', ['role',sid,*who])
                self.roles[who] = {'identity_kind':who[0], 'source_identity_id':who[1],
                                   'target_role_id':alias, 'performer_id':line['performer_id']}
        self.tasks = {key(t):t for t in manifest['tasks']} if manifest else {}

    def alias(self, prefix, parts):
        value = ident(prefix, parts)
        require(value not in self.alias_sources or self.alias_sources[value] == parts, 'ID_COLLISION')
        self.alias_sources[value] = parts
        return value

    def scene(self, ep, sc):
        return self.alias('s_', ['scene',self.delivery['story_id'],ep['episode_id'],sc['scene_id']])

    def block(self, ep, sc, line):
        return self.alias('b_', ['block',self.delivery['story_id'],ep['episode_id'],sc['scene_id'],line['line_id']])

    def role(self, source_role):
        return self.alias('r_', ['role',self.delivery['story_id'],'role',source_role])

    def cover(self, ep, sc, line, mapped, reason):
        loc = locator(ep['episode_id'],sc['scene_id'],line)
        task = self.tasks.get(key(loc))
        self.coverage.append({'source':loc, 'source_task_id':task['task_id'] if task else None,
            'source_kind':line['kind'], 'text_sha256':sha(line['text'].encode('utf-8')),
            'target_block_ids':[self.block(ep,sc,line)] if mapped else [],
            'disposition':'mapped' if mapped else 'routed_other', 'reason':reason})

    def field(self, source_path, target_path, handling, reason):
        self.handling.append({'source_path':source_path,'target_path':target_path,'handling':handling,'reason':reason})

    def sidecar(self, reasons=None, error=None):
        reasons = sorted(set(reasons or []))
        joint = self.request['joint_profile']
        return {'contract_version':'my-legend.adapter-map/1.0','adapter':self.request['adapter'],
            'mapping_id':self.mapping_id,'request_fingerprint':self.fingerprint,'source':self.request['source'],
            'source_version_id':self.version,'coverage':self.coverage,'roles':list(self.roles.values()),
            'field_handling':self.handling,'compatibility':{
                'workflow_input':'blocked' if error and error.issue['scope'] != 'joint' else 'structural_valid',
                'joint_profile':joint,'joint_status':'not_requested' if joint=='independent' else ('blocked' if reasons or error else 'requires_runtime_check'),
                'reason_codes':reasons}, 'issues':[error.issue] if error else self.issues}


def context_check(context, adapter, store):
    require(isinstance(context,dict),'CONTEXT_SCHEMA_INVALID',scope=adapter)
    if adapter=='visual' and context.get('scene_bindings')==[]:
        raise AdapterError('SCENE_CONTEXT_REQUIRED',state='needs_context',scope='visual')
    if adapter=='audio' and context.get('voice_bindings')==[]:
        raise AdapterError('VOICE_BINDING_REQUIRED',state='needs_context',scope='audio')
    validate(context, 'adapter.schema.json', 'AudioContext' if adapter=='audio' else 'VisualContext', 'CONTEXT_SCHEMA_INVALID')
    caps = store.json(context['capability_profile'])
    validate(caps, 'adapter.schema.json', 'CapabilityProfile', 'CAPABILITY_PROFILE_INVALID')
    require(not caps['fixture_only'] or store.fixture_root is not None, 'FIXTURE_REFERENCE_FORBIDDEN')
    if caps['provider_account_verified'] or caps['locked_performance_enrichment_validation'] or caps['exact_controls']:
        # No optional exact-control/enrichment execution is enabled in this release.
        require(caps['verification_ref'] is not None, 'CAPABILITY_EVIDENCE_REQUIRED')
        store.read(caps['verification_ref'])
    return caps


def audio_target(m, store, caps):
    ctx = m.context
    require(ctx['performance_mode']=='preserve', 'ENRICH_LOCK_VALIDATOR_REQUIRED', scope='audio')
    require(not ctx['required_exact_controls'], 'EXACT_CONTROL_UNSUPPORTED', scope='audio')
    bindings = {}
    for row in ctx['voice_bindings']:
        k = row['identity_kind'],row['source_identity_id']
        require(k not in bindings, 'VOICE_BINDING_AMBIGUOUS', scope='audio')
        require(row['verification']!='fixture' or store.fixture_root is not None, 'FIXTURE_REFERENCE_FORBIDDEN')
        require(row['verification']!='verified' or caps['provider_account_verified'], 'VOICE_VERIFICATION_REQUIRED', state='needs_context',scope='audio')
        require(set(row['required_controls']) <= set(caps['controls']), 'EXACT_CONTROL_UNSUPPORTED', scope='audio')
        bindings[k] = row
    cues = {}
    selected = {key(locator(ep['episode_id'],sc['scene_id'],line)) for ep,sc,line in m.rows}
    for row in ctx['cue_routes']:
        k = key(row['source'])
        require(k in selected and k not in cues, 'CUE_ROUTE_INVALID', scope='audio')
        cues[k] = row
    roles, scenes, events, reasons = [], {}, [], []
    names={(c.get('role_id') or c['character_id']):c.get('canon',{}).get('name') for c in m.delivery.get('character_snapshot',[])}
    names.update({c['role_id']:c.get('story_name') for c in m.delivery.get('character_portrayals',[])+m.delivery.get('fictional_voice_roles',[]) if c.get('role_id') and c.get('story_name')})
    for who, row in m.roles.items():
        require(who in bindings, 'VOICE_BINDING_REQUIRED', state='needs_context', scope='audio')
        require(bindings[who]['performer_id']==row['performer_id'], 'PERFORMER_MISMATCH', scope='audio')
        roles.append({'role_id':row['target_role_id'],'name':(names.get(who[1]) or who[1]) if who[0]=='role' else '旁白','description':'来源身份由 sidecar 保存。'})
    for ep,sc,line in m.rows:
        sid, bid = m.scene(ep,sc),m.block(ep,sc,line)
        scenes.setdefault(sid, {'scene_id':sid,'description':sc.get('location','')+'；'+sc['title']})
        path = f"episodes.{ep['episode_id']}.scenes.{sc['scene_id']}.utterances.{line['line_id']}"
        event = {'block_id':bid,'scene_id':sid,'role_id':None,'text':line['text'], 'performance':{},'sound':{},'media':{},
                 'timing':[], 'source_ref':locator(ep['episode_id'],sc['scene_id'],line)|{'story_version_id':m.delivery['story_version_id']}}
        if line['kind'] in ('dialogue','narration'):
            event['kind']='speech';event['role_id']=m.roles[identity(line)]['target_role_id']
            missing = not line.get('emotion') or not (line.get('tone_instruction') or '').strip() or not line.get('emphasis')
            require(not missing or ctx['missing_performance_policy']=='profile_default' and ctx['default_instruction'].strip(), 'PERFORMANCE_REQUIRED', state='needs_context', scope='audio')
            parts = []
            if line.get('emotion'): parts.append('情绪：'+EMOTIONS.get(line['emotion'],line['emotion'])+'。')
            if line.get('tone_instruction'): parts.append(line['tone_instruction'])
            if line.get('emphasis'):
                parts.append('重音：'+'、'.join(s['span_text']+(f"（第{s['occurrence']}次）" if s.get('occurrence',1)!=1 else '') for s in line['emphasis'])+'。')
            if line.get('pauses'): parts.append('语句衔接处适当短暂停顿。')
            if line.get('speech_rate',1.0)!=1.0: parts.append(f"语速参考 {line['speech_rate']} 倍，仅作表演提示。")
            for field, label in [('pitch','音高'),('volume','音量'),('delivery_note','补充表演')]:
                if line.get(field): parts.append(label+'：'+line[field]+'。')
            if missing: parts.append(ctx['default_instruction'])
            instruction=''.join(parts)
            if weighted_length(instruction)>ctx['instruction_max_weight']:
                instruction=compact_instruction(line,ctx['default_instruction'] if missing else '')
                m.field(path,'events.'+bid+'.performance.instructions','hint','超过指令预算时仅精简固定标签与连接文字；原表演字段和重音完整保留，不截断。')
            require(weighted_length(instruction)<=ctx['instruction_max_weight'], 'INSTRUCTION_POLICY_EXCEEDED', path, scope='audio')
            event['performance']={'instructions':instruction,'source_kind':line['kind'],'read_aloud':True,'display':True,
                                  'required_controls':bindings[identity(line)]['required_controls']}
            if line['kind']=='narration': reasons.append('JOINT_NARRATION_IDENTITY_UNSUPPORTED')
            for field in ('emotion','tone_instruction','emphasis','pauses','speech_rate','pitch','volume','delivery_note'):
                m.field(path+'.'+field,'events.'+bid+'.performance.instructions','hint' if line.get(field) else 'not_applicable','原标注保留来源；instructions 不承诺精确控制。')
        else:
            loc = locator(ep['episode_id'],sc['scene_id'],line)
            route=cues.get(key(loc))
            require(route is not None, 'CUE_ROUTE_REQUIRED', path, state='needs_context', scope='audio')
            if route['route']=='visual_only':
                require(line['kind']=='action', 'CUE_ROUTE_INVALID', path, scope='audio')
                m.cover(ep,sc,line,False,'纯视觉动作交给视觉支线。');reasons.append('JOINT_EVENT_SET_MISMATCH');continue
            require(line['kind']!='music' or route['route'] in ('music','sound_control'), 'CUE_ROUTE_INVALID', path, scope='audio')
            require(line['kind']!='sfx' or route['route'] in ('sfx','ambience','sound_control'), 'CUE_ROUTE_INVALID', path, scope='audio')
            if route['route'] in ('audio_marker','sound_control'):
                require((line['kind']=='action' if route['route']=='audio_marker' else line['kind'] in ('sfx','music','action'))
                        and route['media_ref'] is None and not route['timing_relations'],
                        'AUDIO_MARKER_ROUTE_INVALID', path, scope='audio')
                event['kind']='action';event['duration_ms']=0
            else:
                event['kind']=route['route']
            # Existing sound_prompt supplies description=text itself; avoid its duplicate keyword path.
            event['sound']={'production_description':route['description'] or line['text'],'constraints':deepcopy(route['sound_requirements'])}
            if route['media_ref']:
                store.read(route['media_ref'])
                # Staged only by the host; an artifact URI is never a provider file path.
                event['media']={}
            require(event['kind']!='music' or route['media_ref'] is not None, 'MUSIC_MEDIA_REQUIRED', path, state='needs_context', scope='audio')
            for relation in route['timing_relations']:
                require(set(relation)<={'anchor','anchor_point','self_point','offset_ms','mode','provenance'}, 'TIMING_RELATION_INVALID', path)
                rel=deepcopy(relation)
                if rel.get('anchor'):
                    found=[r for r in m.rows if r[2]['line_id']==rel['anchor']]
                    require(len(found)==1, 'TIMING_ANCHOR_INVALID', path)
                    rel['anchor']=m.block(*found[0])
                event['timing'].append(rel)
        m.cover(ep,sc,line,True,'原文与身份映射到目标事件。');events.append(event)
        m.field(path+'.text','events.'+bid+'.text','copied','逐字保留。')
    require(bool(events), 'EMPTY_TARGET', scope='audio')
    present = {e['block_id'] for e in events}
    graph = {e['block_id']:[r['anchor'] for r in e['timing'] if r.get('anchor')] for e in events}
    visited, active = set(),set()
    def visit(bid):
        require(bid in present, 'TIMING_ANCHOR_INVALID', scope='audio')
        require(bid not in active, 'TIMING_CYCLE', scope='audio')
        if bid in visited:return
        active.add(bid)
        for anchor in graph[bid]:visit(anchor)
        active.remove(bid);visited.add(bid)
    for bid in graph:visit(bid)
    target={'schema_version':'lingban.script/1','source_version_id':m.version,'artifact_revision':'r1','title':m.delivery['title'],
            'roles':roles,'scenes':list(scenes.values()),'events':events,'source':{'story_id':m.delivery['story_id'],'story_version_id':m.delivery['story_version_id']},
            'boundaries':{'preserve_dialogue':True,'allow_new_plot':False},'issues':[],'id_mapping':{}}
    validate(target,'target/StandardScript.schema.json',code='TARGET_SCHEMA_INVALID')
    return target,reasons


def visual_target(m, store, caps):
    ctx=m.context
    if ctx['context_confirmed']:
        require(ctx['context_approval_ref'] is not None, 'CONTEXT_APPROVAL_INVALID', scope='visual')
        approval=store.json(ctx['context_approval_ref'])
        # Approval covers source and presentation content, avoiding a self-referential approval hash.
        approved={k:v for k,v in ctx.items() if k not in ('context_confirmed','context_approval_ref')}
        require(approval.get('story_version_id')==m.delivery['story_version_id'] and approval.get('context_content_sha256')==sha(encoded(approved))
                and approval.get('scope')==m.request['scope'] and bool(approval.get('reviewer')), 'CONTEXT_APPROVAL_INVALID', scope='visual')
    require(ctx['preview_skin']=='basic', 'ASSET_BINDING_REQUIRED', state='needs_context', scope='visual', remedy='本适配器首版使用基础素材；指定皮肤需要已验证的素材绑定扩展。')
    selected=m.request['scope']['scenes']
    all_scenes=[(ep['episode_id'],sc['scene_id']) for ep in m.delivery['episodes'] for sc in ep['scenes']]
    excerpt=len(selected)!=len(all_scenes)
    require(not excerpt or ctx['context_confirmed'], 'CONTINUITY_CONFIRMATION_REQUIRED', state='needs_context', scope='visual')
    limits=caps['visual']
    require(len(selected)<=min(2,limits['max_scenes']), 'VISUAL_CAPACITY_EXCEEDED', scope='visual')
    require(len(m.delivery['title'])<=80, 'SOURCE_FIELD_LIMIT_UNSUPPORTED', 'title', scope='visual')
    characters={}
    known_roles={(c.get('role_id') or c['character_id']) for c in m.delivery.get('character_snapshot',[])}
    known_roles.update(c['role_id'] for c in m.delivery.get('character_portrayals',[])+m.delivery.get('fictional_voice_roles',[]) if c.get('role_id'))
    for row in ctx['character_bindings']:
        require(row['source_role_id'] in known_roles,'ACTOR_REFERENCE_INVALID',scope='visual')
        require(row['source_role_id'] not in characters,'DUPLICATE_CHARACTER_BINDING',scope='visual')
        require(row['appearance'] is not None or row['presence']=='voice_only','APPEARANCE_REQUIRED',state='needs_context',scope='visual')
        require(row['asset_revision']=='basic_pixel/1.0.0','ASSET_BINDING_REQUIRED',state='needs_context',scope='visual')
        characters[row['source_role_id']]=row
    scene_ctx={}
    for row in ctx['scene_bindings']:
        k=(row['source']['episode_id'],row['source']['scene_id'])
        require(k not in scene_ctx,'DUPLICATE_SCENE_BINDING',scope='visual');scene_ctx[k]=row
    require(set(scene_ctx)=={(s['episode_id'],s['scene_id']) for s in selected},'SCENE_CONTEXT_REQUIRED',state='needs_context',scope='visual')
    actions={}
    for row in ctx['event_bindings']:
        k=key(row['source']);require(k not in actions,'DUPLICATE_ACTION_BINDING',scope='visual');actions[k]=row
    caption={key(x) for x in ctx['caption_sources']};require(len(caption)==len(ctx['caption_sources']),'CAPTION_SOURCE_INVALID',scope='visual')
    valid_captions={key(locator(ep['episode_id'],sc['scene_id'],line)) for ep,sc,line in m.rows if line['kind'] in ('sfx','music')}
    require(caption<=valid_captions and (not caption or ctx['sound_caption_policy']=='explicit_caption'),'CAPTION_SOURCE_INVALID',scope='visual')
    valid_actions={key(locator(ep['episode_id'],sc['scene_id'],line)) for ep,sc,line in m.rows if line['kind']=='action'}
    require(set(actions)<=valid_actions,'ACTION_BINDING_INVALID',scope='visual')
    scenes={};used=set();facts=[];reasons=[]
    for ep,sc,line in m.rows:
        k=(ep['episode_id'],sc['scene_id']);context=scene_ctx[k]
        if k not in scenes:
            cast=context['cast_source_role_ids'];initial=context['initial_cast_source_role_ids']
            require(len(cast)<=min(4,limits['max_scene_cast_basic']) and set(initial)<=set(cast),'VISUAL_CAPACITY_EXCEEDED',scope='visual')
            require(all(r in characters and characters[r]['presence']=='onstage' for r in cast),'ACTOR_REFERENCE_INVALID',scope='visual')
            require(len(sc['title'])<=40,'SOURCE_FIELD_LIMIT_UNSUPPORTED',scope='visual')
            scenes[k]={'scene_id':m.scene(ep,sc),'name':sc['title'],'description':sc.get('location') or sc['title'],
                       'background_type':context['background_type'],'cast':[m.role(r) for r in cast],
                       'initial_cast':[m.role(r) for r in initial],'blocks':[], '_present':set(initial)}
            used.update(cast);facts.extend(context['locked_facts'])
        scene=scenes[k];bid=m.block(ep,sc,line);loc=locator(ep['episode_id'],sc['scene_id'],line)
        block={'block_id':bid,'text':line['text'],'source_ref':f'source-map://{m.mapping_id}/{bid}'}
        require(0<len(line['text'])<=500,'SOURCE_FIELD_LIMIT_UNSUPPORTED',line['line_id'],scope='visual')
        if line['kind']=='dialogue':
            rid=line['speaker_role_id'];binding=characters.get(rid)
            require(binding is not None,'ACTOR_REFERENCE_INVALID',scope='visual')
            if binding['presence']=='voice_only':
                require(ctx['voice_only_policy']=='explicit_caption' and ctx['context_confirmed'],'VOICE_ONLY_CONTEXT_REQUIRED',state='needs_context',scope='visual')
                block['type']='narration';reasons.append('JOINT_NARRATION_IDENTITY_UNSUPPORTED')
            else:
                require(rid in scene['_present'],'ACTOR_NOT_PRESENT',line['line_id'],scope='visual')
                block.update(type='dialogue',role_id=m.role(rid))
                if line.get('emotion'):
                    require(line['emotion'] in ctx['emotion_map'],'EMOTION_MAPPING_REQUIRED',state='needs_context',scope='visual')
                    block['emotion']=ctx['emotion_map'][line['emotion']]
        elif line['kind']=='narration':
            block['type']='narration';reasons.append('JOINT_NARRATION_IDENTITY_UNSUPPORTED')
        elif line['kind'] in ('sfx','music'):
            if key(loc) not in caption:
                m.cover(ep,sc,line,False,'独立声音事件由声音支线消费。');reasons.append('JOINT_EVENT_SET_MISMATCH');continue
            block['type']='narration'
        else:
            row=actions.get(key(loc))
            require(row is not None,'ACTION_BINDING_REQUIRED',line['line_id'],state='needs_context',scope='visual')
            require(row['provenance']!='model_candidate' or ctx['context_confirmed'],'ACTION_CONFIRMATION_REQUIRED',state='needs_context',scope='visual')
            actor,action=row['actor_source_role_id'],row['action'];present=scene['_present'];cast=context['cast_source_role_ids']
            allowed={'enter','exit','move','wait'} if ctx['presentation_profile']=='standing_dialogue_v1' else {'enter','exit','move','face','wait','nod','shake_head','react'}
            require(action in allowed,'ACTION_UNSUPPORTED',line['line_id'],scope='visual')
            require(actor in cast,'ACTOR_REFERENCE_INVALID',scope='visual')
            require((actor not in present) if action=='enter' else (actor in present),'ACTOR_NOT_PRESENT',line['line_id'],scope='visual')
            if row['target_source_role_id']: require(row['target_source_role_id'] in present and action=='face','ACTOR_REFERENCE_INVALID',scope='visual')
            require((row['target_anchor'] is not None)==(action=='move'),'ACTION_BINDING_INVALID',scope='visual')
            require(row['direction'] is None or action=='face','ACTION_BINDING_INVALID',scope='visual')
            if action=='face':require((row['direction'] is None)!=(row['target_source_role_id'] is None),'ACTION_BINDING_INVALID',scope='visual')
            require(action=='react' or row['emotion'] is None and row['intensity'] is None,'ACTION_BINDING_INVALID',scope='visual')
            block.update(type='action',role_id=m.role(actor),action=action,duration_ms=int(row['duration_ms']))
            for f in ('target_anchor','direction','emotion','intensity'):
                if row[f] is not None:block[f]=row[f]
            if row['target_source_role_id']:block['target_role_id']=m.role(row['target_source_role_id'])
            if action=='enter':present.add(actor)
            elif action=='exit':present.remove(actor)
            reasons.append('JOINT_EVENT_SET_MISMATCH')
        m.cover(ep,sc,line,True,'原文保留；结构化视觉参数来自明确上下文。');scene['blocks'].append(block)
        m.field(line['line_id']+'.text','blocks.'+bid+'.text','copied','逐字保留；声音表演控制留在来源快照。')
    require(len(used)<=min(6,limits['max_characters_basic']),'VISUAL_CAPACITY_EXCEEDED',scope='visual')
    require(sum(len(sc['blocks']) for sc in scenes.values())<=min(100,limits['max_events']),'VISUAL_CAPACITY_EXCEEDED',scope='visual')
    require(all(sc['blocks'] for sc in scenes.values()),'EMPTY_VISUAL_SCENE',scope='visual')
    for sc in scenes.values():sc.pop('_present')
    script={'schema_version':'visual_script/1.0','document_id':m.alias('d_',['document',m.delivery['story_id'],m.delivery['story_version_id'],*[x for sc in selected for x in (sc['episode_id'],sc['scene_id'])]]),
            'source_version_id':m.version,'revision':1,'title':m.delivery['title'],'excerpt':excerpt,'continuity_confirmed':excerpt and ctx['context_confirmed'],
            'constraints':{'preserve_dialogue':True,'allow_new_plot':False,'locked_facts':list(dict.fromkeys(facts)),
                           'allow_narration_fallback':False,'allow_visual_simplification':False},
            'characters':[{'role_id':m.role(r),'name':characters[r]['name'],'description':characters[r]['description'],
                           'appearance':characters[r]['appearance'],'visual_requirements':[]} for r in characters if r in used], 'scenes':list(scenes.values())}
    target={'schema_version':'visual_request/1.0','request_id':m.request['request_id'],'script':script,
            **{f:ctx[f] for f in ('target_profile','presentation_profile','preview_skin','generation_mode','deliverable','context_confirmed')},
            'asset_pack_id':'basic_pixel','asset_pack_version':'1.0.0'}
    validate(target,'target/VisualRequest.schema.json',code='TARGET_SCHEMA_INVALID')
    return target,reasons


class AdapterService:
    def __init__(self, store, caller='local-operator'):
        self.store,self.caller=store,caller

    def prepare(self, request):
        request=deepcopy(request)
        validate(request,'adapter.schema.json','Request','REQUEST_SCHEMA_INVALID')
        store=self.store
        delivery=store.json(request['source']['delivery'])
        manifest=store.json(request['source']['render_manifest']) if request['source']['render_manifest'] else None
        context=store.json(request['context'])
        # Source/preflight errors deliberately do not fabricate a valid-source sidecar.
        m=Mapping(request,delivery,manifest,context)
        db=store.database()
        try:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT fingerprint,result FROM prepared WHERE caller=? AND adapter=? AND idem=?',
                (self.caller,request['adapter'],request['idempotency_key'])).fetchone()
            if old:
                require(old[0]==m.fingerprint,'IDEMPOTENCY_CONFLICT')
                from .core import parse_json
                result=parse_json(old[1]);store.verify_tree(result)
                store.verify_tree(store.json(result['sidecar']));store.verify_tree(request)
                db.commit();return result
            error=None;target=None;reasons=[]
            try:
                caps=context_check(context,request['adapter'],store)
                target,reasons=(audio_target if request['adapter']=='audio' else visual_target)(m,store,caps)
                if request['joint_profile']=='joint_v02' and reasons:
                    raise AdapterError(sorted(set(reasons))[0],scope='joint',remedy='使用独立结果，或采用显式的新联合协议。')
            except AdapterError as exc:
                error=exc;target=None
            if error:
                m.coverage=[]
                for ep,sc,line in m.rows:
                    m.cover(ep,sc,line,False,'准备被阻断，未向下游提交。')
                    m.coverage[-1]['disposition']='blocked'
            sidecar=m.sidecar(reasons,error)
            validate(sidecar,'adapter.schema.json','Sidecar','INTERNAL_CONTRACT_ERROR')
            result={'contract_version':'my-legend.adapter-result/1.0','adapter':request['adapter'],'request_id':request['request_id'],
                'request_fingerprint':m.fingerprint,'state':error.state if error else 'prepared',
                'target':store.put_json(target) if target else None,'sidecar':store.put_json(sidecar),
                'issues':[error.issue] if error else [],'workflow_state':'not_submitted','media_state':'not_generated','publication_state':'not_approved'}
            validate(result,'adapter.schema.json','Result','INTERNAL_CONTRACT_ERROR')
            store.verify_tree(request)
            db.execute('INSERT INTO prepared VALUES(?,?,?,?,?,?)',(self.caller,request['adapter'],request['idempotency_key'],m.fingerprint,json_bytes(result),json_bytes(request)))
            db.commit();return result
        finally:
            db.close()
