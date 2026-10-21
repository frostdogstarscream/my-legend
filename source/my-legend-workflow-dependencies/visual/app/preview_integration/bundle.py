"""将冻结的媒体与字幕组为同源手机预览包；旧版本不覆盖。"""
import html
import shutil
from pathlib import Path
from .common import IntegrationError, command, digest, file_hash, read, write

TEMPLATE=Path(__file__).with_name('mobile_template')

def assemble(plan, video, audio, target):
    target=Path(target);target.mkdir(parents=True,exist_ok=True)
    if plan.get('preview_skin',{}).get('art_style')!='painted':
        raise IntegrationError('MOBILE_TEMPLATE_UNSUPPORTED','当前手机主题适配手绘三格立绘，其他素材目标需另配适配器')
    for filename in ['index.html','app.js','style.css','video-preview.css']:
        shutil.copy2(TEMPLATE/filename,target/filename)
    assets=target/'assets';assets.mkdir(exist_ok=True)
    shutil.copy2(TEMPLATE/'assets/autumn-background.png',assets/'autumn-background.png')
    shutil.copy2(video,assets/'preview.mp4')
    info=read(target.parent/'video-info.json')
    stream=next(s for s in info['streams'] if s['codec_type']=='video')
    duration=float(info['format']['duration'])*1000
    if abs(duration-plan['manifest']['duration_ms'])>1000/30+1:
        raise IntegrationError('VIDEO_DURATION_MISMATCH','成片与统一时间轴不一致')
    command(['ffmpeg','-v','error','-i',assets/'preview.mp4','-ss','0.3','-frames:v','1','-y',assets/'poster.png'],cwd=target,log=target.parent/'poster.log')
    shutil.copy2(target.parent/'render/characters.png',assets/'fogport-characters.png')
    payload={'schema_version':'mobile_video_preview/0.1','title':plan['document']['title'],
             'source_version_id':plan['manifest']['source_version_id'],'plan_hash':plan['plan_hash'],
             'video_file':'assets/preview.mp4','video_sha256':file_hash(video),
             'video_width':stream['width'],'video_height':stream['height'],
             'duration_ms':plan['manifest']['duration_ms'],'audio_kind':audio['audio_kind'],
             'subtitle_mode':'external_ui','roles':plan['document']['characters'],
             'portrait_atlas':{'file':'assets/fogport-characters.png','role_ids':plan['preview_skin']['roles'],'columns':3,'crop_height_fraction':.43},
             'events':[{'block_id':b['block']['block_id'],'scene_id':s['scene_id'],'type':b['block']['type'],'role_id':b['block'].get('role_id'),'text':b['block'].get('text',''),'start_ms':b['start_ms'],'end_ms':b['end_ms']} for s in plan['manifest']['scenes'] for b in s['blocks']],
             'frames':[{k:f.get(k) for k in ('time_ms','scene_id','block_id','speaker_id','subtitle')} for f in plan['frames']]}
    if plan.get('visual_staging'):
        payload['visual_staging']={k:plan['visual_staging'][k] for k in ('staging_id','config_sha256','policy','audio_events_unchanged')}
    write(target/'preview.json',payload)
    content=(target/'index.html').read_text().replace('assets/fogport-scene-only-v2.mp4','assets/preview.mp4').replace('assets/preview-scene-only-v2.png','assets/poster.png')
    content=content.replace('雾港第三声',html.escape(payload['title'])).replace('电台节选','当前版本')
    (target/'index.html').write_text(content)
    version=digest({'plan_hash':plan['plan_hash'],'video_sha256':payload['video_sha256']})
    files={p.relative_to(target).as_posix():file_hash(p) for p in target.rglob('*') if p.is_file()}
    result={'status':'preview_ready','delivery_ready':False,'publication':'not_approved',
            'joint_version_id':version,'source_version_id':payload['source_version_id'],
            'audio_status':audio['status'],'audio_review_preserved':True,
            'timing_mode':plan['timing_mode'],'subtitle_mode':'external_ui','files':files}
    if plan.get('visual_staging'):
        result['visual_staging']={k:plan['visual_staging'][k] for k in ('staging_id','config_sha256','policy','audio_events_unchanged')}
    write(target/'delivery.json',result)
    return result
