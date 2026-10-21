"""Decode the actual MP4: fractional frame durations must retain the final frame."""
import json
import math
from pathlib import Path
import subprocess
import wave

import pytest

pytest.importorskip('PIL')
from my_legend_production.render import render_video


@pytest.mark.parametrize('duration_ms',[600,640,1060])
def test_final_partial_frame_survives_encoding(tmp_path,duration_ms):
    workspace=Path(__file__).resolve().parents[3]
    config=workspace/'runtime/my-legend-production/workflows.json'
    if not config.exists():
        pytest.skip('local media tools/assets required')
    paths=json.loads(config.read_text(encoding='utf-8'))
    ffmpeg=Path(paths['ffmpeg'])
    assets=Path(paths['visual_root'])/'pixel-story-mobile/packages/story_player/assets'
    if not ffmpeg.is_file() or not (assets/'basic_pixel/1.0.0/actors.png').is_file():
        pytest.skip('local media tools/assets required')
    audio=tmp_path/'source.wav'
    with wave.open(str(audio),'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
        stream.writeframes(b'\x00\x00'*(duration_ms*8))
    count=math.ceil(duration_ms/100)
    plan={'document':{'characters':[]},'manifest':{'duration_ms':duration_ms},
          'frames':[{'background':'courtyard','actors':[]} for _ in range(count)]}
    video=tmp_path/'video.mp4'
    report=render_video(plan,audio,video,assets,ffmpeg)
    result=subprocess.run([str(ffmpeg),'-nostdin','-v','error','-i',str(video),'-map','0:v:0',
        '-an','-fps_mode','passthrough','-f','framemd5','pipe:1'],capture_output=True,check=True,timeout=30)
    frames=[line for line in result.stdout.decode().splitlines() if line and not line.startswith('#')]
    assert len(frames)==report['frames']==count
