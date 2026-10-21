"""Create a non-overwriting source + local-preview handoff with SHA256 manifest."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile


def sha(data):return hashlib.sha256(data).hexdigest()


def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();runtime=args.runtime.resolve();source=Path(__file__).resolve().parents[1]
    evidence=source/'evidence';evidence.mkdir(exist_ok=True)
    for name in ['adapter-tests-final.xml','audio-regression-final.xml','visual-regression.xml','replay-verification.json','dependency-lock.json']:
        shutil.copy2(runtime/name,evidence/name)
    shutil.copy2(runtime/'runs/local-004/integration-report.json',evidence/'local-integration.json')
    shutil.copy2(runtime/'runs/registered-artifacts-001/integration-report.json',evidence/'registered-input-integration.json')
    shutil.copy2(runtime/'runs/registered-automatic-001/integration-report.json',evidence/'automatic-mode-integration.json')
    suites={}
    for name in ['adapter-tests-final.xml','audio-regression-final.xml','visual-regression.xml']:
        nodes=list(ET.parse(evidence/name).getroot().iter('testsuite'))
        record={k:sum(int(n.get(k,0)) for n in nodes) for k in ('tests','failures','errors','skipped')}
        assert record['failures']==record['errors']==record['skipped']==0
        suites[name]=record
    summary=json.loads((evidence/'verification-summary.json').read_text(encoding='utf-8'))
    summary.update(created_at=datetime.now(timezone.utc).isoformat(),test_suites=suites)
    summary['reported_top_level_cases']['adapters_and_media']=suites['adapter-tests-final.xml']['tests']
    summary['runtime_modules_sha256']={p.relative_to(source).as_posix():sha(p.read_bytes()) for p in (source/'src').rglob('*.py')}
    write(evidence/'verification-summary.json',summary)
    integration=json.loads((evidence/'local-integration.json').read_text(encoding='utf-8'))
    assert integration['model_calls']==0 and integration['synthetic_media'] is True
    assert integration['status']=='local_preview_ready' and integration['publication']=='not_approved'
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    archive=output/'我的传说_适配器与本地联调_v0.1.0_20261015.zip'
    assert not archive.exists(),'Existing deliveries must not be overwritten'
    entries=[]
    for p in sorted(source.rglob('*')):
        if not p.is_file() or any(x in {'.pytest_cache','__pycache__','.git','.venv'} or x.endswith('.egg-info') for x in p.relative_to(source).parts):continue
        assert p.suffix not in ('.sqlite','.db','.pyc','.wav','.mp4')
        entries.append(('source/'+p.relative_to(source).as_posix(),p.read_bytes()))
    preview=runtime/'runs/local-004/preview'
    entries.extend(('preview/'+p.relative_to(preview).as_posix(),p.read_bytes()) for p in sorted(preview.rglob('*')) if p.is_file())
    instructions='''# 我的传说：适配器与本地联调 0.1.0

source/ 为完整接入源码、Schema、示例、测试与证据。先阅读 source/README.md 和 source/docs/本地联调验收记录_20261015.md。

preview/ 为可播放的本地样例，音轨是合成测试信号；声音听审、视觉确认和发布均待完成。请通过支持 HTTP Range 的本机服务打开，避免 file:// 下的 JSON 加载限制。

原声音/视觉开发包、Python/Dart/FFmpeg 工具和虚拟环境不重复打入此包。当前本机环境已在 runtime/my-legend-production，另一台机器按源码 README 的固定依赖与 bootstrap 步骤配置。
'''
    entries.insert(0,('README.md',instructions.encode('utf-8')))
    manifest={'package':'my-legend-production','version':'0.1.0','model_calls':0,'files':[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in entries]}
    entries.append(('MANIFEST.json',(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')))
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in entries:z.writestr('my-legend-production-0.1.0/'+name,data)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for entry in manifest['files']:
            assert sha(z.read('my-legend-production-0.1.0/'+entry['path']))==entry['sha256']
    checksum=sha(archive.read_bytes())
    with (output/'SHA256SUMS.txt').open('x',encoding='utf-8') as f:f.write(checksum+'  '+archive.name+'\n')
    write(output/'交付清单.json',{'archive':archive.name,'sha256':checksum,'bytes':archive.stat().st_size,'file_count':len(entries),'crc':'passed','manifest_verification':'passed'})
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(entries),'sha256':checksum,'crc':'passed'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
