"""One bounded real short-phrase request. Do not print raw HTTP responses or credentials."""
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import soundfile as sf

root = Path(__file__).resolve().parents[2]
paths = json.loads((root / 'runtime/my-legend-production/workflows.json').read_text(encoding='utf-8'))
sys.path.insert(0, str(Path(paths['audio_root']) / 'src'))
from lingban_pipeline.adapters import QianwenAudioTTS

out = root / 'runtime/my-legend-production/audio-provider/probes' / uuid4().hex
out.mkdir(parents=True)
calls = []
def budget():
    if calls:
        raise RuntimeError('PROBE_BUDGET_EXCEEDED')
    calls.append(time.time())
config = {'model':'qwen-audio-3.1-tts-flash','http_url':'https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer',
          'credential_env':'DASHSCOPE_API_KEY','sample_rate':24000,'controls':['instructions'],'download_attempts':2,'timeout_seconds':90}
report = {'provider':'qianwen_ai_audio','model':config['model'],'voice':'longanyang_v3.1','model_calls':0,'human_listening':'pending'}
try:
    result = QianwenAudioTTS(config,budget).generate({'voice':report['voice'],'text':'我来按住接口，你拧。','instructions':'语气平静，主动提出帮忙，清楚自然。','required_controls':['instructions']},out/'audition.wav')
    info = sf.info(out/'audition.wav')
    assert info.frames > 0 and info.samplerate == 24000
    report.update(status='generated',request_id=result.get('request_id'),usage=result.get('usage'),duration_seconds=info.duration,
                  sample_rate=info.samplerate,channels=info.channels,file=str(out/'audition.wav'))
except Exception as error:
    report.update(status='failed',error_type=type(error).__name__)
finally:
    report['model_calls'] = len(calls)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if report['status']=='generated' else 2)
