"""Owner-scoped creation drafts and read-only work summaries. No worker calls."""
from uuid import uuid4
from typing import Literal
from datetime import datetime, timezone
from pydantic import Field
from psycopg.types.json import Jsonb
from fastapi import Request, Query
from .contracts import Contract, CreativeSource, ArtifactRef, fingerprint
from .flows import FlowCasting
from .repository import ProductionError
from .playback import playback_view


class DraftSource(CreativeSource):
    character_ids: list[str] = Field(default_factory=list,max_length=6)
    idea: str = Field(default='', max_length=8000)


class DraftPayload(Contract):
    source: DraftSource
    output: Literal['script','audio','h5'] = 'h5'
    casting: FlowCasting | None = None


class DraftCreate(DraftPayload):
    idempotency_key: str = Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_.:-]+$')
    legacy_story_id: str | None = None


class DraftUpdate(DraftPayload):
    expected_revision: int = Field(ge=1)


def destination(page, **query):
    return dict(page=page,query=query)


class CreationService:
    def __init__(self,flows,legacy=None):
        self.flows=flows;self.repo=flows.repo;self.store=flows.store
        self.legacy=legacy or (lambda owner:[])

    @staticmethod
    def public(row):
        return {k:row[k] for k in ('id','work_id','payload','revision','flow_id','legacy_story_id','created_at','updated_at')}

    def draft(self,owner,did):
        with self.repo.transaction() as db:
            row=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s AND id=%s',(owner,did)).fetchone()
        if not row:raise ProductionError('JOB_NOT_FOUND')
        return self.public(row)

    def create(self,owner,body):
        digest=fingerprint(body)
        with self.repo.transaction() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('draft-key:'+owner+':'+body.idempotency_key,))
            old=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s AND idempotency_key=%s',(owner,body.idempotency_key)).fetchone()
            if old:
                if old['request_hash']!=digest:raise ProductionError('IDEMPOTENCY_CONFLICT')
                return self.public(old)
            if body.legacy_story_id:
                db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('draft-legacy:'+owner+':'+body.legacy_story_id,))
                old=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s AND legacy_story_id=%s',(owner,body.legacy_story_id)).fetchone()
                if old:
                    if old['payload']!=body.model_dump(include={'source','output','casting'}):raise ProductionError('DRAFT_SOURCE_ALREADY_IMPORTED')
                    return self.public(old)
                if not any(s['id']==body.legacy_story_id and s['status'] in ('draft','failed') for s in self.legacy(owner)):
                    raise ProductionError('SOURCE_ARTIFACT_NOT_OWNED')
            row=db.execute('''INSERT INTO creation_drafts(id,owner_id,work_id,idempotency_key,request_hash,payload,legacy_story_id)
                 VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                 (str(uuid4()),owner,str(uuid4()),body.idempotency_key,digest,
                  Jsonb(body.model_dump(include={'source','output','casting'})),body.legacy_story_id)).fetchone()
        return self.public(row)

    def update(self,owner,did,body):
        with self.repo.transaction() as db:
            row=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s AND id=%s FOR UPDATE',(owner,did)).fetchone()
            if not row:raise ProductionError('JOB_NOT_FOUND')
            if row['flow_id']:raise ProductionError('DRAFT_ALREADY_SUBMITTED')
            if row['revision']!=body.expected_revision:raise ProductionError('DRAFT_REVISION_CONFLICT')
            payload=body.model_dump(include={'source','output','casting'})
            if payload==row['payload']:return self.public(row)
            row=db.execute('UPDATE creation_drafts SET payload=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *',(Jsonb(payload),did)).fetchone()
        return self.public(row)

    def submit(self,owner,body):
        from .flows import freeze_casting
        with self.repo.transaction() as db:
            row=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s AND id=%s FOR UPDATE',(owner,body.draft_id)).fetchone()
            if not row:raise ProductionError('JOB_NOT_FOUND')
            if row['revision']!=body.expected_draft_revision:raise ProductionError('DRAFT_REVISION_CONFLICT')
            payload=dict(source=body.source.model_dump(),output=body.output,casting=body.casting.model_dump() if body.casting else None)
            if payload!=row['payload'] or body.work_id not in (None,row['work_id']):raise ProductionError('DRAFT_SNAPSHOT_MISMATCH')
            if row['flow_id']:fid=row['flow_id']
            else:
                if body.output=='h5' and (not body.casting or not set(body.casting.bindings)<=set(body.source.character_ids)):
                    raise ProductionError('DIRECTING_CASTING_INVALID')
                casting=freeze_casting(body.casting) if body.casting and body.output=='h5' else {}
                request=dict(title=body.source.title,duration=body.source.target_duration_seconds,
                    sound=self.flows.sound_request(body.source.model_dump(),row['work_id'],casting=casting),kind='create',output=body.output,draft_id=row['id'],stage_mode='basic_stage/1')
                if body.output=='script':request['sound'].update(outputs=['script'],sound_workflow=None,audio_delivery_policy='strict')
                fid=self.flows.insert(owner,body.idempotency_key,body,request,casting,row['work_id'],db=db)
                db.execute('UPDATE creation_drafts SET flow_id=%s,submitted_revision=revision,updated_at=now() WHERE id=%s',(fid,row['id']))
        return self.flows.get(fid,owner)

    def drafts(self,owner,offset=0):
        with self.repo.transaction() as db:
            rows=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s AND flow_id IS NULL ORDER BY updated_at DESC,id DESC LIMIT 31 OFFSET %s',(owner,offset)).fetchall()
        return dict(items=[self.public(r) for r in rows[:30]],next_offset=offset+30 if len(rows)>30 else None)

    def candidates(self,owner):
        with self.repo.transaction() as db:
            drafts=db.execute('SELECT * FROM creation_drafts WHERE owner_id=%s',(owner,)).fetchall()
            flows=db.execute('SELECT * FROM production_flows WHERE owner_id=%s ORDER BY created_at DESC,id DESC',(owner,)).fetchall()
            jobs=db.execute('''SELECT j.* FROM jobs j WHERE owner_id=%s AND NOT EXISTS
                (SELECT 1 FROM production_flow_jobs l WHERE l.job_id=j.id) ORDER BY created_at DESC,id DESC''',(owner,)).fetchall()
        groups={}
        for f in flows:groups.setdefault(f['work_id'],dict(kind='flow',versions=[]))['versions'].append(f)
        for j in jobs:
            if j['work_id'] not in groups:groups[j['work_id']]=dict(kind='job',versions=[])
            if groups[j['work_id']]['kind']=='job':groups[j['work_id']]['versions'].append(j)
        result=[]
        for wid,g in groups.items():
            latest=g['versions'][0];request=latest['request'];source=request.get('source',{})
            title=self.flows.display_title(latest) if g['kind']=='flow' else request.get('title') or source.get('title')
            if not title:
                ref=source.get('delivery') or next((a for a in source.get('inputs',[]) if a['kind']=='StoryDelivery'),None)
                if ref:
                    try:title=self.store.json(ArtifactRef.model_validate(ref)).get('title')
                    except ProductionError:pass
                title=title or next((r['request'].get('source',{}).get('title') for r in g['versions'] if r['request'].get('source',{}).get('title')),None) or '故事作品'
            result.append(dict(id=wid,kind=g['kind'],title=title,status=latest['status'],updated_at=max(r['updated_at'] for r in g['versions']),versions=g['versions']))
        for d in drafts:
            if not d['flow_id']:
                result.append(dict(id=d['work_id'],kind='draft',title=d['payload']['source']['title'].strip() or '未命名故事',status='draft',updated_at=d['updated_at'],draft=d))
        hidden={d['legacy_story_id'] for d in drafts if d['legacy_story_id']}|set(groups)
        for s in self.legacy(owner):
            if s['id'] in hidden:continue
            try:updated=datetime.fromisoformat(s.get('createdAt','').replace('Z','+00:00'))
            except ValueError:updated=datetime(1970,1,1,tzinfo=timezone.utc)
            if updated.tzinfo is None:updated=updated.replace(tzinfo=timezone.utc)
            result.append(dict(id=s['id'],kind='legacy',title=s['title'],status={'ready':'succeeded','generating':'running'}.get(s['status'],s['status']),updated_at=updated,story=s))
        return sorted(result,key=lambda r:(r['updated_at'],r['id']),reverse=True)

    def summary(self,owner,item):
        result={k:item[k] for k in ('id','kind','title','status','updated_at')}
        result.update(available_outputs=[],available_actions=['open'],latest_flow_id=None,playable_flow_id=None,play=None)
        if item['kind']=='draft':
            result['open']=destination('create',draftId=item['draft']['id']);result['available_actions']=['edit_draft'];return result
        if item['kind']=='legacy':
            s=item['story'];result['open']=destination('create' if s['status'] in ('draft','failed') else 'detail',id=s['id'])
            if s.get('media'):result.update(play=destination('performance',id=s['id']),available_outputs=['visual'])
            return result
        versions=item['versions'];latest=versions[0]
        if item['kind']=='flow':
            result['open']=destination('flow',id=latest['id']);result['latest_flow_id']=latest['id']
            for index,row in enumerate(versions):
                if index and row['status']!='succeeded':continue
                try:f=self.flows.get(row['id'],owner)
                except ProductionError:continue
                if index==0:
                    result['recovery']=f['recovery']
                    result['available_outputs']=f['available_outputs'];result['kind_of_version']=f['kind']
                    result['available_actions']+=['versions']
                    sound=f['jobs'].get('sound')
                    if sound and sound['status'] in ('succeeded','failed','cancelled') and not sound['unsettled_calls'] and any(a['kind']=='StoryDelivery' for a in sound['artifacts']):
                        result['available_actions']+=['revise','continue','remix','audio_full']
                        if 'audio' in f['available_outputs'] and f['output']=='h5':result['available_actions']+=['stage']
                    if f['status'] in ('queued','running'):result['available_actions']+=['cancel']
                    result['available_actions']+=f['recovery']['actions']
                if 'h5' in f['available_outputs']:
                    result.update(playable_flow_id=f['id'],play=destination('performance',profile='life_v2',deliveryId=f['stage_job_id'],flowId=f['id'],view='play'));break
        else:
            result['open']=destination('work',id=latest['id'])
            for index,row in enumerate(versions):
                if index and row['status']!='succeeded':continue
                try:view=playback_view(self.repo.get(row['id'],owner),self.store)
                except ProductionError:continue
                if index==0:result['available_outputs']=view.get('available_outputs',[])
                if view.get('performance_manifest'):
                    result['play']=destination('performance',profile='life_v2',deliveryId=row['id'],view='play');break
        if result['play']:result['available_actions']+=['play']
        return result

    def works(self,owner,offset=0,search='',status='all'):
        with self.repo.transaction() as db:
            archived={r['work_id'] for r in db.execute('SELECT work_id FROM work_archives WHERE owner_id=%s',(owner,)).fetchall()}
        items=[x for x in self.candidates(owner) if x['id'] not in archived and search.casefold() in x['title'].casefold() and
               (status=='all' or status=='completed' and x['status']=='succeeded' or status=='drafts' and x['status']=='draft' or status=='active' and x['status'] in ('queued','running') or status=='attention' and x['status'] in ('failed','needs_input','cancelled'))]
        page=items[offset:offset+30]
        return dict(items=[self.summary(owner,x) for x in page],next_offset=offset+30 if len(items)>offset+30 else None)

    def work(self,owner,wid):
        item=next((x for x in self.candidates(owner) if x['id']==wid),None)
        if not item:raise ProductionError('JOB_NOT_FOUND')
        return self.summary(owner,item)


def register_creation_routes(router,flows,owner,translate,legacy=None):
    service=CreationService(flows,legacy)
    @router.post('/creation-drafts',status_code=201)
    def create(body:DraftCreate,request:Request):return translate(lambda:service.create(owner(request),body))
    @router.get('/creation-drafts')
    def listing(request:Request,offset:int=Query(0,ge=0,le=10000)):return translate(lambda:service.drafts(owner(request),offset))
    @router.get('/creation-drafts/{did}')
    def get(did:str,request:Request):return translate(lambda:service.draft(owner(request),did))
    @router.post('/creation-drafts/{did}')
    def update(did:str,body:DraftUpdate,request:Request):return translate(lambda:service.update(owner(request),did,body))
    @router.get('/works')
    def works(request:Request,offset:int=Query(0,ge=0,le=10000),search:str=Query('',max_length=200),status:Literal['all','completed','drafts','active','attention']='all'):
        return translate(lambda:service.works(owner(request),offset,search,status))
    @router.get('/works/{wid}')
    def work(wid:str,request:Request):return translate(lambda:service.work(owner(request),wid))
