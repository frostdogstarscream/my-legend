"""Extract pinned user-provided workflow archives into a NEW isolated runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--audio-zip',required=True,type=Path)
    parser.add_argument('--visual-zip',required=True,type=Path)
    parser.add_argument('--runtime',required=True,type=Path)
    args=parser.parse_args()
    baseline=json.loads((Path(__file__).resolve().parents[1]/'source-baseline.json').read_text(encoding='utf-8'))
    root=args.runtime.resolve()/'dependencies'
    if root.exists():raise SystemExit('依赖目录已存在，请复用已有配置或选择新的 runtime；不会覆盖。')
    # Verify both archives completely before creating an extraction destination.
    for label,path in [('audio',args.audio_zip),('visual',args.visual_zip)]:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=baseline[label+'_zip_sha256']:
            raise SystemExit('归档与固定基线不符：'+label)
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:raise SystemExit('归档 CRC 无效：'+label)
    for label,path in [('audio',args.audio_zip),('visual',args.visual_zip)]:
        folder=root/label
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                name=item.filename
                if label=='visual':
                    if '/project/' not in name:continue
                    name=name.split('/project/',1)[1]
                if not name or name.endswith('/'):continue
                target=(folder/name).resolve()
                if not target.is_relative_to(folder.resolve()):raise SystemExit('归档成员越界')
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(item))
    print(json.dumps({'status':'extracted','runtime':str(args.runtime.resolve()),
                      'next':'配置 Dart SDK、纯 Dart 的 characters/crypto 依赖后运行 configure_runtime.py'},ensure_ascii=False))

if __name__=='__main__':main()
