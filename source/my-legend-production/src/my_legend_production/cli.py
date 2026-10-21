from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from .adapters import AdapterService
from .core import AdapterError, json_bytes, parse_json, require
from .storage import ArtifactStore


def main():
    parser=argparse.ArgumentParser(description='我的传说：故事到声音/视觉适配与本地工作流联调')
    commands=parser.add_subparsers(dest='command',required=True)
    register=commands.add_parser('register');register.add_argument('--store',required=True);register.add_argument('--file',required=True)
    prepare=commands.add_parser('prepare');prepare.add_argument('--store',required=True);prepare.add_argument('--request',required=True)
    prepare.add_argument('--fixture-root');prepare.add_argument('--export-dir')
    demo=commands.add_parser('run-local');demo.add_argument('--root',required=True);demo.add_argument('--paths',required=True);demo.add_argument('--video',action='store_true')
    local=commands.add_parser('run-media');local.add_argument('--root',required=True);local.add_argument('--paths',required=True)
    for option in ('store','audio-request','visual-request','media'):local.add_argument('--'+option,required=True)
    local.add_argument('--video',action='store_true')
    status=commands.add_parser('status');status.add_argument('--root',required=True)
    serve=commands.add_parser('serve');serve.add_argument('--root',required=True);serve.add_argument('--port',type=int,default=8893)
    args=parser.parse_args()
    try:
        if args.command=='register':
            result=ArtifactStore(args.store).register(args.file)
        elif args.command=='prepare':
            store=ArtifactStore(args.store,args.fixture_root)
            result=AdapterService(store).prepare(parse_json(Path(args.request).read_bytes()))
            if args.export_dir:
                directory=Path(args.export_dir);directory.mkdir(parents=True,exist_ok=True)
                for name,data in [('result.json',result),('sidecar.json',store.json(result['sidecar']))]+([('target.json',store.json(result['target']))] if result['target'] else []):
                    target=directory/name
                    require(not target.exists() or target.read_bytes()==json_bytes(data),'EXPORT_ALREADY_EXISTS')
                    target.write_bytes(json_bytes(data))
        elif args.command in ('run-local','run-media'):
            from .local_demo import run
            inputs={k:getattr(args,k) for k in ('store','audio_request','visual_request','media')} if args.command=='run-media' else None
            result=run(args.root,args.paths,args.video,inputs)
        elif args.command=='status':
            file=Path(args.root)/'integration-report.json'
            result=parse_json(file.read_bytes()) if file.exists() else {'status':'not_completed'}
        else:
            directory=Path(args.root).resolve()
            require((directory/'index.html').is_file(),'PREVIEW_NOT_FOUND')
            from .server import make_server
            server=make_server(directory,args.port)
            print(f'http://127.0.0.1:{args.port}/',flush=True)
            server.serve_forever();return
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command=='prepare' and result['state']!='prepared':raise SystemExit(2)
    except AdapterError as exc:
        print(json.dumps({'error':exc.issue,'state':exc.state},ensure_ascii=False,indent=2));raise SystemExit(2) from None
    except (OSError,KeyError,ValueError) as exc:
        # No unfiltered provider errors, source prompts or credentials in CLI output.
        print(json.dumps({'error':{'code':'LOCAL_OPERATION_FAILED','error_type':type(exc).__name__}},ensure_ascii=False));raise SystemExit(2) from None

if __name__=='__main__':main()
