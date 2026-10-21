"""Local production jobs, scoped expiring media URLs and explicit preview review."""
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
import time
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .performance_production import VOICES, DEFAULT_VOICES, validate_voices
from .worker import save


class ProductionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    voices: dict[str,str]
    acknowledgeStoryboard: bool
    newRevision: bool=False


class PreviewReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['audio','visual']
    decision: Literal['approve','changes']
    storyVersionId: str
    videoSha256: str=Field(pattern='^[a-f0-9]{64}$')
    notes: str=Field(default='',max_length=1000)


def register_routes(app,repo,ok,launcher=None,reviewer=None):
    workspace=Path(__file__).resolve().parents[4]
    runs=repo.root/'productions';runs.mkdir(exist_ok=True)
    signing_file=repo.root/'media-signing-key'
    if not signing_file.exists():
        try:
            with signing_file.open('xb') as f:f.write(secrets.token_bytes(32))
        except FileExistsError:pass
    signing=signing_file.read_bytes()
    files={'scene.mp4':'video/mp4','mix.wav':'audio/wav','poster.png':'image/png'}

    def signed(job_id,name):
        expires=int(time.time())+3600
        token=hmac.new(signing,f'{job_id}:{name}:{expires}'.encode(),hashlib.sha256).hexdigest()
        return f'http://127.0.0.1:8895/media/{job_id}/{name}?expires={expires}&signature={token}'

    def media(job_id,data):
        result=deepcopy(data)
        for key,name in [('videoUrl','scene.mp4'),('posterUrl','poster.png'),('audioUrl','mix.wav')]:
            result[key]=signed(job_id,name)
        return result

    def state(job):
        path=Path(job['root'])/'status.json'
        current=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status':'queued','progress':0,'message':'制作任务已排队'}
        # A saved state belongs only to its immutable job/source binding.
        if current.get('id') and current['id']!=job['id']:raise HTTPException(503,'制作任务记录不一致')
        if current.get('storyVersionId') and current['storyVersionId']!=job['storyVersionId']:raise HTTPException(503,'制作任务来源版本不一致')
        if current.get('media') and current['media'].get('sourceVersionId')!=job['storyVersionId']:raise HTTPException(503,'媒体来源版本不一致')
        current.update(id=job['id'],storyId=job['storyId'],storyVersionId=job['storyVersionId'],voices=job['voices'])
        if current.get('media'):current['media']=media(job['id'],current['media'])
        return current

    def refresh(story):
        if not story.get('productionTaskId'):return story
        job=repo.get('production_job',story['productionTaskId']);current=state(job)
        story['production']={'story':'ready','audio':'app_preview_approved' if current.get('audioReview')=='approved' else 'awaiting_review' if current['status'] in ('awaiting_review','reviewed') else current['status'],
            'visual':'app_preview_approved' if current.get('visualReview')=='approved' else 'awaiting_review' if current.get('media') else 'not_started','message':current['message']}
        if current.get('media') and current['storyVersionId']==story.get('storyVersionId'):
            story['media']=current['media']
        repo.put('story',story)
        return story

    def start_process(job):
        root=Path(job['root']);root.mkdir(parents=True,exist_ok=True)
        with (root/'worker.log').open('ab') as log:
            subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(workspace/'ops/my-legend/Start-Performance.ps1'),
                '-RequestPath',str(root/'request.json'),'-Root',str(root)],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    launch=launcher or start_process

    def native_review(root,record_file):
        try:
            result=subprocess.run([str(workspace/'runtime/my-legend-production/.venv/Scripts/python.exe'),'-X','utf8','-m','my_legend_production.performance_review',
                '--root',str(root),'--record',str(record_file),'--paths',str(workspace/'runtime/my-legend-production/workflows.json')],
                capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.TimeoutExpired):raise HTTPException(503,'审核意见已保存，原工作流回写未完成，请稍后重试') from None
        if result.returncode:raise HTTPException(503,'审核意见已保存，原工作流回写未完成，请稍后重试')
    sync_review=reviewer or native_review

    @app.get('/api/v1/production-options')
    def options():
        return ok({'voices':VOICES,'defaults':DEFAULT_VOICES,'presentation':'basic_storyboard_action_cards',
                   'description':'基础像素分镜，动作以原文卡片呈现；真实配音与音效；制作完成后需试听和视觉审核。',
                   'credentialConfigured':(workspace/'runtime/my-legend-production/audio-provider/profile.json').exists()})

    @app.post('/api/v1/stories/{story_id}/production-tasks')
    def create(story_id:str,body:ProductionInput):
        with repo.lock:
            if launcher is None and not (workspace/'runtime/my-legend-production/audio-provider/profile.json').exists():raise HTTPException(503,'请先配置后台音频平台凭据')
            story=repo.get('story',story_id)
            if story['status']!='ready' or not story.get('handoffArtifacts'):raise HTTPException(409,'请先完成真实剧本生成')
            if not body.acknowledgeStoryboard:raise HTTPException(422,'请确认基础分镜方案和动作卡呈现方式')
            artifacts={a['kind']:a for a in story['handoffArtifacts']};paths={}
            for kind in ('StoryDelivery','RenderManifest'):
                item=artifacts[kind];file=repo.root/'artifacts'/(item['sha256']+'.json')
                if hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:raise HTTPException(409,'来源制品已变化')
                paths[kind]=file
            delivery=json.loads(paths['StoryDelivery'].read_text(encoding='utf-8'))
            try:validate_voices(delivery,body.voices)
            except Exception:raise HTTPException(422,'音色绑定未覆盖实际出场角色和旁白') from None
            if story.get('productionTaskId'):
                old=repo.get('production_job',story['productionTaskId'])
                if old['voices']==body.voices:return ok(state(old))
                if state(old)['status'] not in ('failed','awaiting_review','reviewed'):raise HTTPException(409,'当前制作尚未结束')
                if not body.newRevision:raise HTTPException(409,'换音色需要明确新建制作版本，旧结果会保留')
            job_id=str(uuid4());root=runs/job_id
            job={'id':job_id,'storyId':story_id,'storyVersionId':story['storyVersionId'],'voices':body.voices,'root':str(root)}
            root.mkdir(parents=True)
            save(root/'request.json',{'id':job_id,'story_id':story_id,'voices':body.voices,'delivery_path':str(paths['StoryDelivery']),'manifest_path':str(paths['RenderManifest'])})
            repo.put('production_job',job)
            if story.get('productionTaskId'):story.setdefault('previousProductionTaskIds',[]).append(story['productionTaskId'])
            story['productionTaskId']=job_id;story.pop('media',None);repo.put('story',story)
            try:launch(job)
            except OSError:save(root/'status.json',{'id':job_id,'status':'failed','progress':0,'message':'制作进程未启动，可重试','errorCode':'WORKER_START_FAILED'})
            return ok(state(job))

    @app.get('/api/v1/stories/{story_id}/production-context')
    def context(story_id:str):
        with repo.lock:
            story=repo.get('story',story_id)
            if story['status']!='ready' or not story.get('handoffArtifacts'):raise HTTPException(409,'剧本尚未就绪')
            artifact=next(a for a in story['handoffArtifacts'] if a['kind']=='StoryDelivery')
            raw=(repo.root/'artifacts'/(artifact['sha256']+'.json')).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise HTTPException(409,'来源制品校验失败')
            delivery=json.loads(raw);names={c['role_id']:c['canon']['name'] for c in delivery['character_snapshot']};roles={}
            for ep in delivery['episodes']:
                for sc in ep['scenes']:
                    for u in sc['utterances']:
                        if u['kind'] not in ('dialogue','narration'):continue
                        identity=u['speaker_role_id'] if u['kind']=='dialogue' else u['narrator_id']
                        roles.setdefault(identity,{'id':identity,'name':names.get(identity,'旁白'),'voice':DEFAULT_VOICES.get(identity,VOICES[len(roles)%len(VOICES)]['id'])})
            return ok({'storyVersionId':story['storyVersionId'],'roles':list(roles.values()),
                'scenes':[{'id':s['scene_id'],'title':s['title'],'events':len(s['utterances'])} for ep in delivery['episodes'] for s in ep['scenes']],
                'presentation':'基础像素分镜；精细动作以原文动作卡呈现；画面效果待审核。'})

    @app.get('/api/v1/production-tasks/{job_id}')
    def get(job_id:str):
        with repo.lock:return ok(state(repo.get('production_job',job_id)))

    @app.post('/api/v1/production-tasks/{job_id}/retry')
    def retry(job_id:str):
        with repo.lock:
            job=repo.get('production_job',job_id)
            if state(job)['status']!='failed':raise HTTPException(409,'只有失败任务可以恢复')
            save(Path(job['root'])/'status.json',{'id':job_id,'status':'queued','progress':1,'message':'恢复制作，复用已生成素材；未知外部结果不会自动重发'})
            launch(job);return ok(state(job))

    @app.post('/api/v1/production-tasks/{job_id}/reviews')
    def review(job_id:str,body:PreviewReview):
        with repo.lock:
            job=repo.get('production_job',job_id);root=Path(job['root']);current=json.loads((root/'status.json').read_text(encoding='utf-8'))
            if current['status'] not in ('awaiting_review','reviewed') or not current.get('media'):raise HTTPException(409,'预览尚未就绪')
            if body.storyVersionId!=job['storyVersionId'] or body.videoSha256!=current['media']['videoSha256']:raise HTTPException(409,'审核对象已更新，请重新加载')
            if hashlib.sha256((root/'preview/scene.mp4').read_bytes()).hexdigest()!=body.videoSha256:raise HTTPException(409,'视频制品校验失败')
            record={**body.model_dump(),'jobId':job_id,'reviewer':'local-developer','reviewScope':'app_and_original_workflows','externalPublication':'not_requested'}
            digest=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            record_file=root/'reviews'/(digest+'.json');save(record_file,record)
            if body.decision=='approve':sync_review(root,record_file)
            current[body.kind+'Review']='approved' if body.decision=='approve' else 'changes_requested'
            current['status']='reviewed' if current.get('audioReview')==current.get('visualReview')=='approved' else 'awaiting_review'
            current['message']='审阅通过并已回写原工作流，本地制品已冻结；未对外发布' if current['status']=='reviewed' else '已记录并回写审阅意见；修改不会自动重新调用模型'
            current['reviewScope']='app_and_original_workflows';current['externalPublication']='not_requested'
            current['media']['humanApproval']='approved' if current['status']=='reviewed' else 'pending'
            save(root/'status.json',current)
            return ok(state(job))

    @app.api_route('/media/{job_id}/{name}',methods=['GET','HEAD'])
    def serve_media(job_id:str,name:str,expires:int,signature:str):
        if name not in files or expires<int(time.time()) or expires>int(time.time())+3700:raise HTTPException(403,'媒体链接已过期')
        expected=hmac.new(signing,f'{job_id}:{name}:{expires}'.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature):raise HTTPException(403,'媒体访问校验失败')
        with repo.lock:job=repo.get('production_job',job_id)
        file=Path(job['root'])/'preview'/name
        if not file.is_file():raise HTTPException(404,'媒体尚未生成')
        current=state(job);field={'scene.mp4':'videoSha256','mix.wav':'audioSha256','poster.png':'posterSha256'}[name]
        expected_hash=(current.get('media') or {}).get(field)
        if not expected_hash or hashlib.sha256(file.read_bytes()).hexdigest()!=expected_hash:raise HTTPException(409,'媒体制品校验失败')
        return FileResponse(file,media_type=files[name],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    return refresh
