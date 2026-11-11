"""Durable orchestration of existing immutable sound and stage jobs.

Only the coordinator advances flows. A per-flow database advisory lock serializes
advancement and cancellation. Stable child keys recover a create/link crash.
"""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from typing import Literal
from uuid import uuid4
import logging

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from .contracts import (Contract, CreativeSource, ProductionRequest, ArtifactRef,
                        DirectingRequest, RemakeAudioRequest, fingerprint)
from .repository import ProductionError
from .playback import playback_view


class FlowCasting(Contract):
    catalog_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    bindings: dict[str, str] = Field(min_length=1, max_length=3)
    initial_outfits: dict[str, str] = Field(min_length=1, max_length=3)
    casting_confirmed: Literal[True]

    @model_validator(mode='after')
    def same_roles(self):
        if set(self.bindings) != set(self.initial_outfits):
            raise ValueError('DIRECTING_CASTING_INVALID')
        return self


class FlowRequest(Contract):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    source: CreativeSource
    casting: FlowCasting | None = None
    output: Literal['script','audio','h5'] = 'h5'
    work_id: str | None = None
    draft_id: str | None = None
    expected_draft_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def draft_pair(self):
        if bool(self.draft_id) != (self.expected_draft_revision is not None):
            raise ValueError('DRAFT_REVISION_REQUIRED')
        return self


class FlowRevision(Contract):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    expected_revision: int = Field(ge=1)
    kind: Literal['revise','continue','remix','audio','audio_full','stage','stage_saved','stage_basic','restart']
    instruction: str = Field(default='', max_length=8000)
    retain_elements: list[str] = Field(default_factory=list)
    transform_elements: list[str] = Field(default_factory=list)
    casting: FlowCasting | None = None
    voices: dict[str,str] | None = None
    regenerate: list[dict] = Field(default_factory=list)
    acknowledge_unknown_source: bool = False
    preview_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


def freeze_casting(body):
    from .h5_preview import load_h5_catalog
    from .spine_preview import local_root
    from .commander_preview import commander_scene_digest
    from .scene_preview import load_scene_catalog
    from .asset_capabilities import build_capabilities, save_capabilities
    from .performance_delivery import verify_resources
    catalog = load_h5_catalog(local_root(), body.catalog_sha256)
    actors = {a['actor_id']: a for a in catalog['actors']}
    if catalog.get('actor_model') not in ('fixed_commander/1','mixed_life/1'):
        raise ProductionError('DIRECTING_COMMANDER_REQUIRED')
    for role, actor in body.bindings.items():
        if actor not in actors or body.initial_outfits[role] not in actors[actor]['outfit_ids']:
            raise ProductionError('DIRECTING_CASTING_INVALID')
    scene_hash = commander_scene_digest(local_root(), catalog)
    verify_resources(dict(catalog_sha256=body.catalog_sha256,
                          scene_track={'segments':[{'scene':{'catalog_sha256':scene_hash}}]}))
    rooms=load_scene_catalog(local_root(), scene_hash)
    caps = build_capabilities(catalog, rooms)
    return {**body.model_dump(), 'scene_catalog_sha256':scene_hash,
            'scene_choices':[dict(id=r['scene_id'],name=r['display_name']) for r in rooms['scenes'] if r.get('blocking')],
            'capability_sha256':save_capabilities(local_root(), caps), 'visual_policy':'asset_stage/1'}


def flow_fingerprint(value):
    # Preserve request hashes created before draft submission was introduced.
    if isinstance(value,FlowRequest) and not value.draft_id:
        value=value.model_dump(exclude={'draft_id','expected_draft_revision'})
    return fingerprint(value)


class FlowService:
    def __init__(self, repository, store, reconciler=None):
        self.repo, self.store, self.reconciler = repository, store, reconciler

    @contextmanager
    def lock(self, flow_id):
        with self.repo.transaction() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('production-flow:'+flow_id,))
            yield

    def row(self, flow_id, owner):
        with self.repo.transaction() as db:
            row = db.execute('SELECT * FROM production_flows WHERE id=%s AND owner_id=%s', (flow_id,owner)).fetchone()
        if not row:
            raise ProductionError('JOB_NOT_FOUND')
        return row

    def insert(self, owner, key, original, request, casting, work_id, parent=None, db=None):
        digest = flow_fingerprint(original)
        with (self.repo.transaction() if db is None else nullcontext(db)) as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('flow-key:'+owner+':'+key,))
            old = db.execute('SELECT id,request_hash FROM production_flows WHERE owner_id=%s AND idempotency_key=%s',(owner,key)).fetchone()
            if old:
                if old['request_hash'] != digest:
                    raise ProductionError('IDEMPOTENCY_CONFLICT')
                return old['id']
            fid = str(uuid4())
            db.execute('''INSERT INTO production_flows(id,owner_id,work_id,parent_id,idempotency_key,request_hash,request,casting)
                          VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                       (fid,owner,work_id,parent,key,digest,Jsonb(request),Jsonb(casting)))
            if parent:
                db.execute('UPDATE production_flows f SET auto_title=p.auto_title FROM production_flows p WHERE f.id=%s AND p.id=%s AND p.owner_id=%s',(fid,parent,owner))
            return fid

    def existing(self, owner, key, original):
        with self.repo.transaction() as db:
            row=db.execute('SELECT id,request_hash FROM production_flows WHERE owner_id=%s AND idempotency_key=%s',(owner,key)).fetchone()
        if row and row['request_hash'] != flow_fingerprint(original):
            raise ProductionError('IDEMPOTENCY_CONFLICT')
        return row['id'] if row else None

    def create(self, owner, body):
        if body.draft_id:
            from .works import CreationService
            return CreationService(self).submit(owner,body)
        old=self.existing(owner,body.idempotency_key,body)
        if old:return self.get(old,owner)
        if body.output=='h5' and (not body.casting or not set(body.casting.bindings) <= set(body.source.character_ids)):
            raise ProductionError('DIRECTING_CASTING_INVALID')
        casting=freeze_casting(body.casting) if body.casting else {}
        work_id=body.work_id or str(uuid4())
        # Existing work identifiers must belong to this principal.
        if body.work_id:
            with self.repo.transaction() as db:
                if not db.execute('SELECT 1 FROM jobs WHERE work_id=%s AND owner_id=%s LIMIT 1',(work_id,owner)).fetchone():
                    raise ProductionError('SOURCE_ARTIFACT_NOT_OWNED')
        request=dict(title=body.source.title, duration=body.source.target_duration_seconds,
                     sound=self.sound_request(body.source.model_dump(),work_id,casting=casting), kind='create',output=body.output,stage_mode='basic_stage/1')
        if body.output=='script':request['sound'].update(outputs=['script'],sound_workflow=None,audio_delivery_policy='strict')
        fid=self.insert(owner,body.idempotency_key,body,request,casting,work_id)
        return self.get(fid,owner)

    @staticmethod
    def sound_request(source, work_id, voices=None, casting=None):
        from .creation_library import with_scene_choices
        source=with_scene_choices(source,casting)
        value=dict(idempotency_key='placeholder-key',work_id=work_id,source=source,outputs=['audio'],
                   mode='automatic',review_policy='listen_first',sound_workflow='sound_bridge/1',audio_delivery_policy='audio_first')
        if voices:value['voices']=dict(mode='configured',bindings=voices)
        return ProductionRequest.model_validate(value).model_dump()

    def jobs(self, row):
        with self.repo.transaction() as db:
            links=db.execute('SELECT phase,job_id FROM production_flow_jobs WHERE flow_id=%s',(row['id'],)).fetchall()
        return {link['phase']:self.repo.get(link['job_id'],row['owner_id']) for link in links}

    @staticmethod
    def display_title(row):
        return row['request'].get('title','').strip() or row.get('auto_title') or '故事制作中'

    def capture_title(self, row, sound):
        if row['request'].get('title','').strip() or row.get('auto_title'):return
        ref=next((a for a in sound['artifacts'] if a['kind']=='StoryDelivery'),None)
        if not ref:return
        title=self.store.json(ArtifactRef.model_validate(ref)).get('title','').strip()
        if title:
            with self.repo.transaction() as db:
                db.execute('UPDATE production_flows SET auto_title=%s WHERE id=%s AND auto_title IS NULL',(title,row['id']))

    def get(self, fid, owner):
        row=self.row(fid,owner);jobs=self.jobs(row)
        result={k:row[k] for k in ('id','work_id','parent_id','status','revision','error_code','created_at','updated_at')}
        result['kind']=row['request'].get('kind','create')
        result['stage_mode']=row['request'].get('stage_mode','asset_stage/1')
        result.update(title=self.display_title(row),output=row['request'].get('output','h5'),casting=row['casting'] or None,jobs=jobs,
                      actual_calls=sum(j['actual_calls'] for phase,j in jobs.items() if not (phase=='sound' and row['request'].get('reuse_sound'))),
                      human_review='pending',publication='not_requested')
        sound=jobs.get('sound');stage=jobs.get('stage')
        result['sound_job_id']=sound['id'] if sound else None
        result['stage_job_id']=stage['id'] if stage else None
        result['available_outputs']=[];result['offstage_roles']=[]
        if sound:
            view=playback_view(sound,self.store)
            result['available_outputs']=view['available_outputs']
            refs={a['kind']:a for a in sound['artifacts']}
            if 'AudioMix' in refs:self.store.verify(ArtifactRef.model_validate(refs['AudioMix']))
            if 'StoryDelivery' in refs:
                delivery=self.store.json(ArtifactRef.model_validate(refs['StoryDelivery']))
                roles={r['role_id']:r.get('canon',{}).get('name',r['role_id']) for r in delivery.get('character_snapshot',[])}
                for role in delivery.get('character_portrayals',[]):
                    if role.get('story_name'):roles[role['role_id']]=role['story_name']
                for role in delivery.get('fictional_voice_roles',[]):roles[role['role_id']]=role.get('story_name',role.get('name',role['role_id']))
                for ep in delivery.get('episodes',[]):
                    for sc in ep['scenes']:
                        for line in sc['utterances']:
                            if line.get('speaker_role_id'):roles.setdefault(line['speaker_role_id'],line['speaker_role_id'])
                result['offstage_roles']=[dict(role_id=k,name=v) for k,v in roles.items() if k not in row['casting'].get('bindings',{})] if row['request'].get('output','h5')=='h5' else []
        if stage and stage['status']=='succeeded':
            view=playback_view(stage,self.store)
            if view.get('performance_manifest'):result['available_outputs']=list(dict.fromkeys(result['available_outputs']+['h5']))
        result['error_detail']=next((j.get('error_detail') for j in jobs.values() if j.get('error_detail')), {})
        result['recovery']=self.recovery(row,jobs)
        return result

    def recovery(self,row,jobs):
        from .recovery import flow_recovery
        ids=[j['id'] for j in jobs.values()]
        with self.repo.transaction() as db:
            calls=db.execute('''SELECT c.call_key,c.stage_name,c.state,c.result,s.checkpoint,s.status AS stage_status
                FROM calls c JOIN stages s ON s.job_id=c.job_id AND s.name=c.stage_name
                WHERE c.job_id=ANY(%s)''',(ids,)).fetchall() if ids else []
        return flow_recovery(row,jobs,calls,row.get('recovery_query'))

    def reconcile(self,fid,owner,revision):
        from .reconciliation import ProductionReconciler
        with self.lock(fid):
            row=self.row(fid,owner)
            if row['revision']!=revision:raise ProductionError('REVISION_CONFLICT')
            if row['status'] not in ('failed','needs_input','cancelled'):raise ProductionError('RECONCILIATION_CONFLICT')
            report=dict(settled=[],unresolved=[],terminal=list((row.get('recovery_query') or {}).get('terminal',[])),generation_submissions=0)
            for job in self.jobs(row).values():
                if not job['unsettled_calls']:continue
                result=(self.reconciler or ProductionReconciler(self.repo)).run(job['id'],owner,job['revision'])
                for key in ('settled','unresolved','terminal'):report[key].extend(result.get(key,[]))
            with self.repo.transaction() as db:
                db.execute('UPDATE production_flows SET recovery_query=%s,revision=revision+1,updated_at=now() WHERE id=%s',
                           (Jsonb(report),fid))
        return self.get(fid,owner)

    def listing(self, owner, offset=0, work_id=None):
        with self.repo.transaction() as db:
            rows=db.execute('''SELECT id FROM production_flows f WHERE owner_id=%s
                 AND (%s::text IS NULL OR work_id=%s)
                 AND (%s::text IS NOT NULL OR NOT EXISTS(SELECT 1 FROM production_flows newer
                     WHERE newer.owner_id=f.owner_id AND newer.work_id=f.work_id
                     AND (newer.created_at,newer.id)>(f.created_at,f.id)))
                 ORDER BY created_at DESC,id DESC LIMIT 31 OFFSET %s''',(owner,work_id,work_id,work_id,offset)).fetchall()
        return dict(items=[self.get(r['id'],owner) for r in rows[:30]],next_offset=offset+30 if len(rows)>30 else None)

    def update(self, fid, status, error=None):
        with self.repo.transaction() as db:
            db.execute('''UPDATE production_flows SET status=%s,error_code=%s,updated_at=now()
                          WHERE id=%s AND (status IS DISTINCT FROM %s OR error_code IS DISTINCT FROM %s)''',(status,error,fid,status,error))

    def link(self, fid, phase, jid):
        with self.repo.transaction() as db:
            db.execute('INSERT INTO production_flow_jobs(flow_id,phase,job_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(fid,phase,jid))

    def ensure_child(self, row, phase, request):
        request=deepcopy(request);request['idempotency_key']='flow:'+row['id']+':'+phase
        jid=self.repo.create(row['owner_id'],ProductionRequest.model_validate(request))
        self.link(row['id'],phase,jid)
        return self.repo.get(jid,row['owner_id'])

    def advance(self, fid, owner):
        with self.lock(fid):
            row=self.row(fid,owner)
            if row['cancel_requested'] or row['status'] in ('succeeded','failed','needs_input','cancelled'):return
            jobs=self.jobs(row)
            if 'sound' not in jobs:
                if row['request'].get('reuse_sound'):
                    self.link(fid,'sound',row['request']['reuse_sound'])
                    sound=self.repo.get(row['request']['reuse_sound'],owner)
                else:sound=self.ensure_child(row,'sound',row['request']['sound'])
            else:sound=jobs['sound']
            self.capture_title(row,sound)
            if sound['status']!='succeeded':
                self.update(fid,sound['status'],sound['error_code']);return
            if row['request'].get('output','h5')!='h5':
                self.update(fid,'succeeded');return
            if 'stage' not in jobs:
                if row['request'].get('stage_request'):
                    request=row['request']['stage_request']
                elif row['request'].get('stage_mode')=='basic_stage/1':
                    from .basic_stage import prepare_basic_request
                    request=prepare_basic_request(self.repo,self.store,owner,sound['id'],row['casting'],'flow:'+fid+':stage').model_dump()
                else:
                    from .spine_director import prepare_directing_request
                    c=row['casting']
                    body=DirectingRequest(idempotency_key='flow:'+fid+':stage',**{k:c[k] for k in ('catalog_sha256','bindings','initial_outfits','casting_confirmed')})
                    request=prepare_directing_request(self.repo,self.store,owner,sound['id'],body,frozen_casting=c).model_dump()
                    # Freeze at submission, not at the time audio happens to finish.
                    request['source'].update({k:c[k] for k in ('scene_catalog_sha256','capability_sha256','visual_policy')})
                stage=self.ensure_child(row,'stage',request)
            else:stage=jobs['stage']
            self.update(fid,stage['status'] if stage['status']!='queued' else 'running',stage['error_code'])

    def tick(self):
        with self.repo.transaction() as db:
            rows=db.execute("SELECT id,owner_id FROM production_flows WHERE status IN ('queued','running') AND NOT cancel_requested ORDER BY created_at").fetchall()
        for row in rows:
            try:self.advance(row['id'],row['owner_id'])
            except ProductionError as exc:
                with self.lock(row['id']):
                    if not self.row(row['id'],row['owner_id'])['cancel_requested']:self.update(row['id'],'needs_input' if exc.code=='EXTERNAL_RESULT_UNKNOWN' else 'failed',exc.code)
            except Exception as exc:
                logging.error('FLOW_ADVANCE_EXCEPTION type=%s',type(exc).__name__)
                with self.lock(row['id']):
                    if not self.row(row['id'],row['owner_id'])['cancel_requested']:self.update(row['id'],'failed','FLOW_INTERNAL_ERROR')
        return bool(rows)

    def control(self, fid, owner, action, revision):
        with self.lock(fid):
            row=self.row(fid,owner)
            if row['revision']!=revision:raise ProductionError('REVISION_CONFLICT')
            if action=='cancel':
                if row['status']=='succeeded':raise ProductionError('FLOW_ALREADY_COMPLETE')
                with self.repo.transaction() as db:
                    db.execute("UPDATE production_flows SET cancel_requested=true,status='cancelled',revision=revision+1,updated_at=now() WHERE id=%s",(fid,))
                    # Also discover children committed immediately before a coordinator crash.
                    children=db.execute('SELECT id FROM jobs WHERE owner_id=%s AND idempotency_key IN (%s,%s)',(owner,'flow:'+fid+':sound','flow:'+fid+':stage')).fetchall()
                for child in children:
                    self.repo.cancel(child['id'],owner)
                    path=self.store.root.parent/'jobs'/child['id'];path.mkdir(parents=True,exist_ok=True)
                    (path/'cancel.requested').touch()
            else:
                if row['status'] not in ('failed','needs_input'):raise ProductionError('RESUME_CONFLICT')
                if 'resume' not in self.recovery(row,self.jobs(row))['actions']:raise ProductionError('RESUME_CONFLICT')
                for job in self.jobs(row).values():
                    if job['status'] in ('failed','needs_input'):self.repo.resume(job['id'],owner,job['revision'],{})
                with self.repo.transaction() as db:
                    db.execute("UPDATE production_flows SET status='queued',error_code=NULL,revision=revision+1,updated_at=now() WHERE id=%s",(fid,))
        return self.get(fid,owner)

    def revise(self,fid,owner,body):
        original={'parent_id':fid,**body.model_dump()}
        old=self.existing(owner,body.idempotency_key,original)
        if old:return self.get(old,owner)
        with self.lock(fid):
            row=self.row(fid,owner)
            if row['revision']!=body.expected_revision:raise ProductionError('REVISION_CONFLICT')
            jobs=self.jobs(row);sound=jobs.get('sound')
            if body.kind=='restart':
                if row['status'] not in ('cancelled','failed','needs_input'):raise ProductionError('RESUME_CONFLICT')
                if self.recovery(row,jobs)['remake_kind']!='restart':raise ProductionError('RESUME_CONFLICT')
                if any(j['status'] in ('queued','running') for j in jobs.values()):raise ProductionError('CANCEL_PENDING')
                request=deepcopy(row['request']);request['kind']='restart'
                if not request.get('stage_request'):request['stage_mode']='basic_stage/1'
                next_id=self.insert(owner,body.idempotency_key,original,request,row['casting'],row['work_id'],fid)
                return self.get(next_id,owner)
            acknowledged_new_audio = bool(sound and body.kind=='audio_full' and
                body.acknowledge_unknown_source and sound['status'] in ('needs_input','failed','cancelled') and
                sound['unsettled_calls'])
            if not sound or (not acknowledged_new_audio and
                    (sound['status'] not in ('succeeded','failed','cancelled') or sound['unsettled_calls'])):
                raise ProductionError('AUDIO_REMAKE_SOURCE_NOT_READY')
            refs={a['kind']:a for a in sound['artifacts']}
            if not {'StoryDelivery','RenderManifest'}<=refs.keys():raise ProductionError('SOURCE_ARTIFACT_NOT_OWNED')
            source=dict(kind='delivery',source_version_id=sound['source_version_id'],delivery=refs['StoryDelivery'],manifest=refs['RenderManifest'])
            work_id=str(uuid4()) if body.kind=='remix' else row['work_id']
            request=dict(title=row['request']['title'],duration=row['request']['duration'],kind=body.kind,output=row['request'].get('output','h5'),stage_mode='basic_stage/1')
            if body.kind=='stage_saved':request['stage_mode']=row['request'].get('stage_mode','saved_preview/1')
            if acknowledged_new_audio:
                request['unknown_source_acknowledgement'] = dict(job_id=sound['id'],
                    unsettled_calls=sound['unsettled_calls'],new_paid_version=True)
            casting=freeze_casting(body.casting) if body.casting else row['casting']
            if body.kind in ('revise','continue','remix'):
                source.update(kind='operation',intent=body.kind,instruction=body.instruction,target_duration_seconds=request['duration'])
                if body.kind=='revise':source['edit']=dict(scope='whole_story',target={},downstream_policy='replan_affected')
                if body.kind=='remix':source['remix']=dict(retain_elements=body.retain_elements,transform_elements=body.transform_elements,relation_to_source='adaptation')
                request['sound']=self.sound_request(source,work_id,casting=casting)
            elif body.kind=='audio_full':request['sound']=self.sound_request(source,work_id,body.voices)
            elif body.kind=='audio':
                from .audio_remake import remake_request
                request['sound']=remake_request(sound,RemakeAudioRequest(expected_revision=sound['revision'],idempotency_key='placeholder-key',regenerate=body.regenerate,voices=body.voices),self.store).model_dump()
            else:
                if request['output']!='h5':raise ProductionError('SPINE_AUDIO_NOT_READY')
                if sound['status']!='succeeded':raise ProductionError('SPINE_AUDIO_NOT_READY')
                request['reuse_sound']=sound['id']
                if body.kind=='stage_saved':
                    from .performance_delivery import prepare_request
                    from .contracts import H5DeliveryRequest
                    if not body.preview_sha256:raise ProductionError('H5_PREVIEW_REQUIRED')
                    request['stage_request']=prepare_request(self.repo,self.store,owner,sound['id'],H5DeliveryRequest(
                        idempotency_key='placeholder-key',preview_sha256=body.preview_sha256,
                        parent_job_id=jobs.get('stage',{}).get('id'))).model_dump()
            if request['output']=='script' and body.kind in ('revise','continue','remix'):request['sound'].update(outputs=['script'],sound_workflow=None,audio_delivery_policy='strict')
            elif request['output']=='script' and body.kind in ('audio','audio_full'):request['output']='audio'
            next_id=self.insert(owner,body.idempotency_key,original,request,casting,work_id,fid)
        return self.get(next_id,owner)


def register_flow_routes(router,repo,store,owner,translate,capabilities,reconciler=None):
    from fastapi import Request, Query
    service=FlowService(repo,store,reconciler)

    def ready(output):
        caps=capabilities()
        if output=='h5' and not caps.get('h5_flow',{}).get('configured'):raise ProductionError('H5_FLOW_NOT_READY')
        if output!='h5' and output not in caps.get('outputs',[]):raise ProductionError('H5_FLOW_NOT_READY')

    @router.post('/production-flows',status_code=202)
    def create_flow(body:FlowRequest,request:Request):
        principal=owner(request)
        def op():
            existing=service.existing(principal,body.idempotency_key,body)
            if existing:return service.get(existing,principal)
            if body.draft_id:
                from .works import CreationService
                draft=CreationService(service).draft(principal,body.draft_id)
                if draft['flow_id']:return service.create(principal,body)
            ready(body.output);return service.create(principal,body)
        return translate(op)

    @router.get('/production-flows')
    def list_flows(request:Request,offset:int=Query(0,ge=0,le=10000),work_id:str|None=None):
        return translate(lambda:service.listing(owner(request),offset,work_id))

    @router.get('/production-flows/{fid}')
    def get_flow(fid:str,request:Request):return translate(lambda:service.get(fid,owner(request)))

    class Control(Contract):
        expected_revision:int=Field(ge=1)

    @router.post('/production-flows/{fid}/cancel')
    def cancel_flow(fid:str,body:Control,request:Request):return translate(lambda:service.control(fid,owner(request),'cancel',body.expected_revision))

    @router.post('/production-flows/{fid}/resume')
    def resume_flow(fid:str,body:Control,request:Request):return translate(lambda:service.control(fid,owner(request),'resume',body.expected_revision))

    @router.post('/production-flows/{fid}/reconcile')
    def reconcile_flow(fid:str,body:Control,request:Request):return translate(lambda:service.reconcile(fid,owner(request),body.expected_revision))

    @router.post('/production-flows/{fid}/revisions',status_code=202)
    def revise_flow(fid:str,body:FlowRevision,request:Request):return translate(lambda:service.revise(fid,owner(request),body))

