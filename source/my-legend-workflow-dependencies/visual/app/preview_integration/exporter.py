"""复用视觉播放器求值代码导出纯画面；台词独立留给手机 UI。"""
import json
import re
import shutil
from pathlib import Path
from .common import IntegrationError, command, read, write


def prepare(plan, draft, audio_file, target, gsap_file):
    target=Path(target);target.mkdir(parents=True,exist_ok=True);draft=Path(draft)
    for name in ['assets','characters.png','background.png']:
        source=draft/name
        if source.is_dir():shutil.copytree(source,target/name,dirs_exist_ok=True)
        elif source.exists():shutil.copy2(source,target/name)
    vendor=target/'assets/vendor';vendor.mkdir(parents=True,exist_ok=True)
    shutil.copy2(gsap_file,vendor/'gsap.min.js');shutil.copy2(audio_file,target/'assets/mix.wav')
    source=(draft/'index.html').read_text()
    style=re.search(r'<style>(.*?)</style>',source,re.S).group(1).replace('"PingFang SC","Microsoft YaHei",sans-serif','story,sans-serif')
    script=re.search(r'<script>(.*?)</script>',source,re.S).group(1)
    old="const data=await (await fetch('preview-data.json')).json(), doc=data.document;"
    if script.count(old)!=1:raise IntegrationError('RENDERER_VERSION_UNSUPPORTED','源播放器数据入口变化，请更新导出适配')
    # 剧本文本不能闭合 script 标签；数据始终由原播放器以 textContent 展示。
    encoded=json.dumps(plan,ensure_ascii=False).replace('</','<\\/')
    script=script.replace(old,'const data='+encoded+', doc=data.document;')
    script=script.replace('let position=0,playing=false,last=0;','let position=0,playing=false;')
    # 声音源场景边界保留；同一视觉组跨片段也可插值入场坐标，避免 100ms 跳动。
    script=script.replace('next.scene_id===frame.scene_id',
                          '(next.visual_group_id||next.scene_id)===(frame.visual_group_id||frame.scene_id)')
    left=script.index('function tick(now)');right=script.index('}catch(e)',left)
    script=script[:left]+"window.storyDraw=ms=>{position=Math.min(+seek.max,Math.max(0,ms));draw()};window.storyDraw(clock.innerText);"+script[right:]
    script=script.replace('if(playing&&frame.fade)','if(frame.fade)')
    for text in ["ctx.fillText(a.name,x,y-(data.preview_skin?layout.sprite_height+4:35));",
                 "ctx.fillText('▼',point(pose)[0],point(pose)[1]-(data.preview_skin?layout.sprite_height+14:44));"]:
        if script.count(text)!=1:raise IntegrationError('RENDERER_VERSION_UNSUPPORTED','纯画面文字绘制接口变化')
        script=script.replace(text,'/* 姓名及台词由视频外手机 UI 显示。 */')
    body=source[source.index('</style>')+8:source.index('<script>')]
    duration=plan['manifest']['duration_ms']/1000
    css='html,body,#root{width:1280px;height:720px;max-width:none;margin:0;padding:0;overflow:hidden}#root{position:relative}#scene{position:absolute;inset:0;background:#102b28}#stage{display:block;width:1280px;height:720px}#title,#mode,details,#dialogue,#play,#pause,#replay,#seek,#time{display:none}'
    clock=f"const clock={{innerText:0}};const tl=gsap.timeline({{paused:true}});tl.to(clock,{{innerText:{duration*1000},duration:{duration},ease:'none',onUpdate:()=>window.storyDraw?.(Number(clock.innerText))}},0);window.__timelines=window.__timelines||{{}};window.__timelines['joint-scene']=tl;"
    html=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><script src="assets/vendor/gsap.min.js"></script><style>{style}{css}</style></head><body><div id="root" data-composition-id="joint-scene" data-width="1280" data-height="720" data-duration="{duration}"><section id="scene" class="clip" data-start="0" data-duration="{duration}" data-track-index="0">{body}</section><audio id="mix" src="assets/mix.wav" data-start="0" data-duration="{duration}" data-track-index="1"></audio></div><script>{clock}{script}</script></body></html>'''
    (target/'index.html').write_text(html)
    (target/'design.md').write_text('纯画面版本：复用已编排背景及立绘，1280×720，原时间线；对白和旁白在手机 UI 显示。无新增剧情、无生成配音。')
    write(target/'package.json',{'scripts':{'check':'npx --yes hyperframes@0.8.155 check'}})
    return target

def render(target, renderer, log):
    target=Path(target)
    args=[renderer.get('node','node'),renderer['cli_file']]
    extra={'HYPERFRAMES_BROWSER_PATH':renderer['browser_file']} if renderer.get('browser_file') else {}
    output=command(args+['check','--json'],cwd=target,log=target/'check.log',env=extra)
    check=json.loads(output);write(target/'check-report.json',check)
    if not check.get('ok'):raise IntegrationError('RENDER_CHECK_FAILED','导出检查未通过')
    command(args+['render','--quality','high','--fps','30','--workers','1','--strict','--low-memory-mode','--quiet','--output','scene.mp4'],cwd=target,log=log,env=extra,timeout=900)
    path=target/'scene.mp4'
    if not path.is_file() or not path.stat().st_size:raise IntegrationError('EMPTY_RENDER','没有生成视频文件')
    return path
