"""统一命令：输入→两套子流程→音频实测对齐→纯画面 MP4→手机预览包。"""
import argparse
import fcntl
import json
import shutil
import sys
from pathlib import Path
from .alignment import align
from .bundle import assemble
from .common import IntegrationError, command, digest, file_hash, read, within, write
from .exporter import prepare, render
from .staging import apply_staging

WORKSPACE=Path(__file__).resolve().parents[2]


def package_snapshot(package):
    expected=read(package/'source_manifest.json')
    actual={name:file_hash(within(package,name)) for name in expected}
    if actual!=expected:raise IntegrationError('AUDIO_PACKAGE_CHANGED','同事开发包与其源码清单不一致')
    return actual


def local_audio_only(script, config):
    # 首轮整合只消费本地声音素材；付费模型必须由声音方在自己的流程中显式授权。
    if config.get('text_model',{}).get('enabled',True) or config.get('visual',{}).get('enabled',True):
        raise IntegrationError('LOCAL_MODE_REQUIRED','整合入口不调用文字模型或声音方视觉分支')
    if any(config.get(k,{}).get('adapter')!='local' for k in ('tts','sfx')):
        raise IntegrationError('LOCAL_MODE_REQUIRED','请提供已生成本地音频，或在同事原入口完成生成后再对接')
    budget=config.get('call_budget',{})
    if budget.get('max_calls')!=0 or budget.get('batch_max_calls')!=0:
        raise IntegrationError('LOCAL_MODE_REQUIRED','本地接入模式要求模型调用预算为零')
    for event in script['events']:
        if event['kind'] in ('speech','sfx') and not event.get('media',{}).get('file'):
            raise IntegrationError('MISSING_LOCAL_AUDIO',f"{event['block_id']} 缺少已生成声音文件")


class Job:
    def __init__(self, request_file, root):
        self.root=Path(root).resolve();self.request_file=Path(request_file).resolve()
        self.request=read(self.request_file)
        if self.request.get('schema_version')!='joint_preview_request/1.0':
            raise IntegrationError('INVALID_INPUT','不支持的整合输入版本')
        self.paths={k:self.path(self.request[k]) for k in ('visual_request','audio_script','audio_config','audio_package','audio_python')}
        if self.request.get('visual_staging'):
            self.paths['visual_staging']=self.path(self.request['visual_staging'])
        self.renderer=dict(self.request['renderer'])
        for key in ('cli_file','gsap_file','browser_file'):
            if self.renderer.get(key):self.renderer[key]=str(self.path(self.renderer[key]))
        self.visual=read(self.paths['visual_request']);self.script=read(self.paths['audio_script']);self.config=read(self.paths['audio_config'])
        local_audio_only(self.script,self.config)
        self.staging=read(self.paths['visual_staging']) if 'visual_staging' in self.paths else None
        self.source_hashes={k:file_hash(p) for k,p in self.paths.items() if k in ('visual_request','audio_script','audio_config','visual_staging')}
        for event in self.script['events']:
            if event.get('media',{}).get('file'):
                name=event['media']['file'];path=within(self.paths['audio_script'].parent,name)
                self.source_hashes['audio_media/'+name]=file_hash(path)
        code_root=Path(__file__).parent
        code_hashes={p.relative_to(code_root).as_posix():file_hash(p) for p in code_root.rglob('*')
                     if p.is_file() and (p.suffix in ('.py','.js','.css','.html','.png'))}
        self.identity=digest({'request':self.request,'inputs':self.source_hashes,'integration_code':code_hashes})
        self.root.mkdir(parents=True,exist_ok=True)
        self.state_file=self.root/'state.json'
        self.state=read(self.state_file) if self.state_file.exists() else {'request_hash':self.identity,'status':'created','stages':{}}
        if self.state['request_hash']!=self.identity:
            raise IntegrationError('INPUT_VERSION_CHANGED','输入或媒体已修改，请使用新的运行目录，旧版本保留')

    def path(self,name):
        p=Path(name)
        # 解释器路径不能跟随最后的符号链接，否则会绕过声音方虚拟环境。
        return p.absolute() if p.is_absolute() else (self.request_file.parent/p).absolute()

    def stage(self,name,fn,artifact):
        record=self.state['stages'].get(name,{})
        if record.get('status')=='done':
            if not Path(artifact).is_file() or file_hash(artifact)!=record['sha256']:
                raise IntegrationError('ARTIFACT_CHANGED',f'{name} 阶段产物已变化，不沿用缓存')
            return
        attempt=record.get('attempts',0)+1
        if attempt>2:raise IntegrationError('RETRY_LIMIT',f'{name} 已达到两次执行上限，请定位错误后使用新目录')
        self.state['status']='running';self.state['stage']=name
        self.state['stages'][name]={'status':'running','attempts':attempt};write(self.state_file,self.state)
        try:
            fn()
        except Exception:
            self.state['stages'][name]['status']='failed'
            write(self.state_file,self.state)
            raise
        self.state['stages'][name].update(status='done',sha256=file_hash(artifact));write(self.state_file,self.state)

    def visual_stage(self):
        write(self.root/'visual-request.json',self.visual)
        stdout=command([sys.executable,'-m','app.visual_workflow.cli','--store',self.root/'visual','start',self.root/'visual-request.json'],cwd=WORKSPACE,log=self.root/'visual.log')
        result=json.loads(stdout);write(self.root/'visual-result.json',result)
        if result.get('status')!='succeeded':raise IntegrationError('VISUAL_NOT_READY',f"视觉子图状态为 {result.get('status')}，未标记整合成功")

    def audio_stage(self):
        inputs=self.root/'audio-inputs';inputs.mkdir(exist_ok=True)
        write(inputs/'script.json',self.script);write(inputs/'config.json',self.config)
        for event in self.script['events']:
            name=event.get('media',{}).get('file')
            if name:
                dest=within(inputs,name);dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(within(self.paths['audio_script'].parent,name),dest)
                if file_hash(dest)!=self.source_hashes['audio_media/'+name]:
                    raise IntegrationError('INPUT_VERSION_CHANGED','复制时声音素材已变化，请创建新版本')
        env={'PYTHONPATH':str(self.paths['audio_package']/'pipeline/src')}
        command([self.paths['audio_python'],Path(__file__).with_name('audio_worker.py'),inputs/'script.json',inputs/'config.json',self.root/'audio',self.root/'audio-result.json'],cwd=WORKSPACE,log=self.root/'audio.log',env=env)
        result=read(self.root/'audio-result.json')
        if not result.get('technical_passed') or not result.get('mix_file'):
            raise IntegrationError('AUDIO_NOT_READY',f"声音子图状态为 {result['status']}，原流程状态保留")
        if result['provider_calls']!=0:raise IntegrationError('UNEXPECTED_MODEL_CALL','本地整合检测到模型调用')
        result['audio_kind']=self.request['audio_kind'];write(self.root/'audio-result.json',result)

    def aligned_stage(self):
        result=read(self.root/'visual-result.json');draft=Path(result['result']['preview_ref']).parent
        self.draft=draft
        plan=align(read(draft/'preview-data.json'),read(self.root/'audio-result.json'))
        write(self.root/'aligned-plan.json',plan)

    def staging_stage(self):
        # 原对齐结果独立保存；只增加我们的视觉状态，不改声音事件及原剧本。
        if self.staging is not None:write(self.root/'staging-input.json',self.staging)
        plan=apply_staging(read(self.root/'aligned-plan.json'),self.staging)
        write(self.root/'joint-plan.json',plan)

    def export_stage(self):
        plan=read(self.root/'joint-plan.json');audio=read(self.root/'audio-result.json')
        draft=Path(read(self.root/'visual-result.json')['result']['preview_ref']).parent
        target=prepare(plan,draft,audio['mix_file'],self.root/'render',self.renderer['gsap_file'])
        video=render(target,self.renderer,self.root/'render.log')
        stdout=command(['ffprobe','-v','error','-show_streams','-show_format','-of','json',video],cwd=self.root,log=self.root/'ffprobe.log')
        write(self.root/'video-info.json',json.loads(stdout))
        write(self.root/'video-result.json',{'file':str(video),'sha256':file_hash(video)})

    def bundle_stage(self):
        video=read(self.root/'video-result.json')
        if file_hash(video['file'])!=video['sha256']:raise IntegrationError('ARTIFACT_CHANGED','导出视频已变化')
        result=assemble(read(self.root/'joint-plan.json'),video['file'],read(self.root/'audio-result.json'),self.root/'preview')
        # 文件清单不能包含自己的哈希；外层结果补记交付清单字节，恢复时一并核对。
        result['files']['delivery.json']=file_hash(self.root/'preview/delivery.json')
        write(self.root/'result.json',result|{'preview_ref':str(self.root/'preview/index.html')})

    def run(self):
        # 只锁本次整合目录，不修改任何同事目录；重复运行复用哈希相同的完成阶段。
        with (self.root/'execution.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise IntegrationError('JOB_RUNNING','同一目录已有整合任务运行') from exc
            if self.state_file.exists():
                self.state=read(self.state_file)
                if self.state['request_hash']!=self.identity:
                    raise IntegrationError('INPUT_VERSION_CHANGED','运行目录已属于另一输入版本')
            write(self.root/'request.json',self.request);write(self.root/'input-hashes.json',self.source_hashes)
            before=package_snapshot(self.paths['audio_package']);write(self.root/'audio-package-hashes.json',before)
            try:
                for name,fn,artifact in [('visual',self.visual_stage,'visual-result.json'),('audio',self.audio_stage,'audio-result.json'),('alignment',self.aligned_stage,'aligned-plan.json'),('staging',self.staging_stage,'joint-plan.json'),('export',self.export_stage,'video-result.json'),('bundle',self.bundle_stage,'result.json')]:
                    self.stage(name,fn,self.root/artifact)
                # 恢复时也检查全部交付文件，不能仅凭阶段完成标记复用被改动的媒体。
                result=read(self.root/'result.json')
                for name,sha in result['files'].items():
                    if file_hash(within(self.root/'preview',name))!=sha:
                        raise IntegrationError('ARTIFACT_CHANGED',f'手机交付文件 {name} 已变化')
                self.state.update(status='preview_ready',stage='complete',error=None)
            except Exception as exc:
                self.state.update(status='failed',error={'code':getattr(exc,'code','INTEGRATION_FAILED'),'message':str(exc)})
                raise
            finally:
                try:
                    if package_snapshot(self.paths['audio_package'])!=before:
                        raise IntegrationError('AUDIO_PACKAGE_CHANGED','运行后同事包源码哈希变化')
                    self.state['package_source_unchanged']=True
                except Exception:
                    self.state.update(status='failed',error={'code':'AUDIO_PACKAGE_CHANGED','message':'同事包源码校验失败'})
                    write(self.state_file,self.state);raise
                write(self.state_file,self.state)
        return read(self.root/'result.json')


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');run.add_argument('request');run.add_argument('--root',required=True)
    status=sub.add_parser('status');status.add_argument('--root',required=True)
    args=parser.parse_args()
    try:
        result=Job(args.request,args.root).run() if args.command=='run' else read(Path(args.root)/'state.json')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (IntegrationError,OSError,KeyError,ValueError) as exc:
        print(json.dumps({'status':'failed','error':{'code':getattr(exc,'code','INVALID_INPUT'),'message':str(exc)}},ensure_ascii=False));raise SystemExit(1)

if __name__=='__main__':main()
