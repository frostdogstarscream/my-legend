"""Configure isolated existing workflow copies, never rewrite the source ZIPs."""
from pathlib import Path
import argparse
import hashlib
import json
import os


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--dart',required=True,type=Path)
    parser.add_argument('--packages',required=True,type=Path)
    parser.add_argument('--ffmpeg',type=Path)
    args=parser.parse_args();root=args.runtime.resolve()
    visual=root/'dependencies/visual';audio=root/'dependencies/audio/pipeline'
    planner=visual/'app/visual_workflow/planner.py'
    text=planner.read_text(encoding='utf-8')
    old="dart = MOBILE / '.tooling/flutter/bin/cache/dart-sdk/bin/dart'"
    new="dart = Path(__import__('os').environ['MY_LEGEND_DART']) if __import__('os').environ.get('MY_LEGEND_DART') else MOBILE / '.tooling/flutter/bin/cache/dart-sdk/bin/dart'"
    old2="packages = MOBILE / 'packages/story_player/.dart_tool/package_config.json'"
    new2="packages = Path(__import__('os').environ['MY_LEGEND_DART_PACKAGES']) if __import__('os').environ.get('MY_LEGEND_DART_PACKAGES') else MOBILE / 'packages/story_player/.dart_tool/package_config.json'"
    require=lambda ok: (_ for _ in ()).throw(ValueError('Dependency compiler source does not match baseline')) if not ok else None
    before=hashlib.sha256(planner.read_bytes()).hexdigest()
    if old in text:
        require(text.count(old)==text.count(old2)==1)
        planner.write_text(text.replace(old,new).replace(old2,new2),encoding='utf-8',newline='\n')
    else: require(new in text and new2 in text)
    paths={'audio_root':str(audio),'visual_root':str(visual),'dart':str(args.dart.resolve()),
           'dart_packages':str(args.packages.resolve()),'ffmpeg':str(args.ffmpeg.resolve()) if args.ffmpeg else None}
    for name in ('audio_root','visual_root','dart','dart_packages'):
        if not Path(paths[name]).exists():raise ValueError('Missing runtime component: '+name)
    (root/'workflows.json').write_text(json.dumps(paths,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for folder in (audio/'src',visual/'app',visual/'pixel-story-mobile/packages/story_player/lib')
           for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    (root/'dependency-lock.json').write_text(json.dumps({'schema_version':'my-legend.dependencies/1',
        'portability_patch':{'file':planner.relative_to(root).as_posix(),'before':before,
                             'after':hashlib.sha256(planner.read_bytes()).hexdigest(),'change':'Dart executable and package-config injection only'},
        'files':files},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'configured','workflow_paths':str(root/'workflows.json'),'locked_source_files':len(files)}))

if __name__=='__main__':main()

