"""Offline export of compiled basic-pixel frames. No new animation or model inference."""
from __future__ import annotations

import math
from pathlib import Path
import subprocess

from .core import require,sha


def caption_pages(text, font, width=1190, lines_per_page=3):
    """Wrap every source character; long speech is paged, never cut off-screen."""
    lines, line = [], ''
    for character in text:
        if line and font.getlength(line+character)>width:
            lines.append(line)
            line=''
        line+=character
    if line:
        lines.append(line)
    return [lines[i:i+lines_per_page] for i in range(0,len(lines),lines_per_page)]


def render_video(plan, audio, output, assets, ffmpeg, fps=10, synthetic_audio=True, captions=None, watermark=None, production_style=False):
    from PIL import Image,ImageDraw,ImageFont
    output,assets=Path(output),Path(assets)
    require(fps==10,'RENDER_FPS_UNSUPPORTED')
    require(not plan.get('preview_skin'),'RENDER_SKIN_UNSUPPORTED')
    atlas=Image.open(assets/'basic_pixel/1.0.0/actors.png').convert('RGBA')
    actors={a['role_id']:a for a in plan['document']['characters']}
    duration=plan['manifest']['duration_ms']/1000
    frames=math.ceil(duration*fps)
    font_path = str(assets/'fonts/StorySansSC.ttf') if production_style else r'C:\Windows\Fonts\msyh.ttc'
    font=ImageFont.truetype(font_path,48 if production_style else 30) if captions is not None else None
    small=ImageFont.truetype(font_path,22) if captions is not None else None
    reaction_font=ImageFont.truetype(font_path,9) if production_style else None
    if production_style:
        for item in (font,small,reaction_font):
            if item is not None:
                item.set_variation_by_axes([500])
    prepared_captions = []
    for caption in captions or []:
        label={'dialogue':'对白','narration':'旁白','action':'动作分镜','sfx':'音效'}.get(caption['kind'],caption['kind'])
        prepared_captions.append({**caption,'pages':caption_pages(label+'：'+caption['text'],font)})
    temp=output.with_name(output.stem+'.rendering.mp4')
    args=[str(ffmpeg),'-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1280x720','-r',str(fps),'-i','pipe:0',
          '-i',str(audio),'-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p',
          '-c:a','aac','-b:a','128k','-t',str(frames/fps),'-movflags','+faststart',str(temp)]
    with (output.parent/'render.log').open('wb') as log:
        proc=subprocess.Popen(args,stdin=subprocess.PIPE,stderr=log,stdout=subprocess.DEVNULL)
        try:
            for index in range(frames):
                frame=plan['frames'][min(index,len(plan['frames'])-1)]
                image=Image.new('RGB',(320,180),'#c4aa77' if frame['background']=='indoor' else '#8aaa68')
                draw=ImageDraw.Draw(image)
                if frame['background']=='indoor':
                    draw.rectangle((0,0,320,57),fill='#75644e');draw.rectangle((0,52,320,59),fill='#514635')
                    draw.rectangle((42,10,88,42),fill='#314a47');draw.rectangle((46,14,84,38),fill='#a5c1b0')
                    draw.rectangle((158,26,254,36),fill='#9d754d')
                else:
                    draw.rectangle((0,94,320,126),fill='#c9b58b')
                    if production_style and frame['background']=='courtyard':
                        draw.rectangle((0,0,320,68),fill='#c4b59a')
                        draw.rectangle((0,0,320,7),fill='#514635')
                        draw.rectangle((130,12,190,68),fill='#75644e')
                for pose in frame['actors']:
                    if not pose['visible']:continue
                    a=actors[pose['role_id']]
                    template=['adult','elder','child','animal'].index(a['template_type'])
                    color=['blue','green','red','brown'].index(a['color_key'])
                    accessory=['none','hat','beard'].index(a['accessory'])
                    n=((template*4+color)*3+accessory)*8+['down','left','right','up'].index(pose['direction'])*2+pose['walk_frame']
                    sprite=atlas.crop((n%16*32,n//16*32,n%16*32+32,n//16*32+32))
                    x,y=pose['position'];x+=pose['offset_x'];y+=pose['offset_y']
                    image.paste(sprite,(round(x-16),round(y-30)),sprite)
                    if production_style and pose.get('reaction'):
                        label={'neutral':'静','happy':'喜','sad':'悲','angry':'怒','surprised':'惊','serious':'肃'}[pose['reaction']]
                        draw.ellipse((round(x-7),round(y-45),round(x+7),round(y-31)),fill='#fff4dc',outline='#6d3a20')
                        draw.text((round(x-4),round(y-44)),label,font=reaction_font,fill='#6d3a20')
                if frame.get('fade'):
                    overlay=Image.new('RGB',(320,180),'#1c251b');image=Image.blend(image,overlay,min(1,max(0,frame['fade'])))
                raw=image.resize((1280,720),Image.Resampling.NEAREST)
                if captions is not None:
                    overlay=ImageDraw.Draw(raw)
                    if watermark:
                        overlay.rounded_rectangle((20,18,760,62),radius=10,fill='#fff4dc')
                        overlay.text((34,24),watermark,font=small,fill='#6d3a20')
                    active=next((c for c in prepared_captions if c['start']<=index/fps<c['end']),None)
                    if active:
                        progress=(index/fps-active['start'])/(active['end']-active['start'])
                        lines=active['pages'][min(len(active['pages'])-1,int(progress*len(active['pages'])))]
                        line_height=58 if production_style else 46
                        top=690-line_height*len(lines)
                        overlay.rounded_rectangle((24,top-16,1256,708),radius=14,fill='#fff4dc')
                        for number,line in enumerate(lines):overlay.text((44,top+number*line_height),line,font=font,fill='#553222')
                if index==0:raw.save(output.parent/'poster.png')
                proc.stdin.write(raw.tobytes())
            proc.stdin.close();return_code=proc.wait(timeout=120)
            require(return_code==0 and temp.is_file() and temp.stat().st_size>0,'VIDEO_EXPORT_FAILED')
        except BaseException:
            if proc.poll() is None:proc.kill();proc.wait(timeout=10)
            raise
    temp.replace(output)
    # Decode both streams with the same local executable, not only inspect file existence.
    verified=subprocess.run([str(ffmpeg),'-v','error','-i',str(output),'-map','0:v:0','-map','0:a:0','-f','null','-'],capture_output=True,timeout=60)
    require(verified.returncode==0,'VIDEO_DECODE_FAILED')
    return {'file':output.name,'sha256':sha(output.read_bytes()),'fps':fps,'width':1280,'height':720,
            'duration_ms':plan['manifest']['duration_ms'],'frames':frames,'video_grid_duration_ms':frames*1000/fps,'audio_track':True,
            'decode_validation':'passed','subtitle_mode':'burnt_speech' if captions is not None else 'external_ui',
            'subtitle_pagination':'uniform_within_utterance_not_word_alignment' if captions is not None else None,
            'subtitle_font_size':48 if production_style else 30,'subtitle_weight':500 if production_style else None,
            'synthetic_audio':synthetic_audio,'renderer':'basic-pixel frame export; native compiled poses'}
