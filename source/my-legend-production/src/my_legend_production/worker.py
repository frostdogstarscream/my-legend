"""Child-process hosts for the pinned existing workflows; credentials stay in environment."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .core import json_bytes, parse_json, require, sha


def load(path):return parse_json(Path(path).read_bytes())


def save(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(json_bytes(data));temp.replace(path)


def audio_run(args, paths):
    sys.path.insert(0,str(Path(paths['audio_root'])/'src'))
    from lingban_pipeline.graph import Pipeline
    script=load(args.input);config=load(args.config)
    if args.local_only:
        require(not config.get('text_model',{}).get('enabled'),'LOCAL_MODE_REJECTED')
        require(not config.get('visual',{}).get('enabled'),'LOCAL_MODE_REJECTED')
        require(config['tts']['adapter']==config['sfx']['adapter']=='local','LOCAL_MODE_REJECTED')
        require(all(e.get('media',{}).get('file') and e.get('media',{}).get('strategy','local') in ('local','library')
                    for e in script['events'] if e['kind'] in ('speech','sfx','ambience','music')),'LOCAL_MODE_REJECTED')
    pipe=Pipeline(Path(args.root))
    try:
        if pipe.status(args.thread)['status']=='not_started':
            pipe.run(args.thread,script,config,Path(args.input).parent,{'file_sha256':sha(Path(args.input).read_bytes()),'file_name':Path(args.input).name})
        state=pipe.graph.get_state(pipe.invocation(args.thread)).values
        status=pipe.status(args.thread)
        if status.get('preview'):
            from lingban_pipeline.delivery import verify_bundle
            verify_bundle(pipe.root,status['preview']['bundle'])
        save(Path(args.root)/'host-result.json',{'status':status['status'],'next':status['next'],
            'technical_passed':(status.get('preview') or {}).get('checks',{}).get('technical_passed'),
            'problems':status.get('problems'),
            'call_budget':status['call_budget'],'timeline':state.get('timeline'),
            'preview_directory':(status.get('preview') or {}).get('directory'),
            'human_listening':'pending','publication':'not_approved','synthetic_media':True if script.get('source',{}).get('media_origin')=='synthetic_test_signals' else None})
    finally:pipe.close()


def visual_run(args,paths):
    import os
    os.environ['MY_LEGEND_DART']=paths['dart'];os.environ['MY_LEGEND_DART_PACKAGES']=paths['dart_packages']
    os.environ['DART_SUPPRESS_ANALYTICS']='true'
    sys.path.insert(0,paths['visual_root'])
    from app.visual_workflow.graph import build_graph
    from app.visual_workflow.repository import Repository
    from langgraph.checkpoint.sqlite import SqliteSaver
    repository=Repository(args.root);request=load(args.input)
    with SqliteSaver.from_conn_string(str(Path(args.root)/'checkpoints.sqlite')) as saver:
        graph=build_graph(repository,saver);config={'configurable':{'thread_id':args.thread}}
        if not graph.get_state(config).values:graph.invoke({'request':request},config)
        snapshot=graph.get_state(config);state=snapshot.values
        pending=[i.value for t in snapshot.tasks for i in t.interrupts]
        plan=state.get('plan')
        if plan:
            save(Path(args.root)/'compiled-plan.json',plan)
            # Inspection preview: retain pending context/preview approval; never resume as a human.
            diagnostic=repository.preview(plan)
        else:diagnostic=None
        review_status=(state.get('result') or {}).get('review_status','pending')
        save(Path(args.root)/'host-result.json',{'status':pending[0].get('status') if pending else state.get('status'),
            'pending_review':pending,'error':state.get('error'),'diagnostic_preview':diagnostic,
            'plan_hash':plan['plan_hash'] if plan else None,'workflow_review_status':review_status,
            'human_visual_approval':'approved' if review_status=='approved' else 'pending','publication':'not_approved'})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('kind',choices=['audio','visual'])
    parser.add_argument('--paths',required=True);parser.add_argument('--input',required=True)
    parser.add_argument('--config');parser.add_argument('--root',required=True);parser.add_argument('--thread',required=True)
    parser.add_argument('--local-only',action='store_true')
    args=parser.parse_args();paths=load(args.paths)
    try:
        root=Path(args.root);root.mkdir(parents=True,exist_ok=True)
        binding={'kind':args.kind,'thread':args.thread,'input_sha256':sha(Path(args.input).read_bytes()),
                 'config_sha256':sha(Path(args.config).read_bytes()) if args.config else None,'local_only':args.local_only}
        frozen=root/'host-input.json'
        if frozen.exists():require(load(frozen)==binding,'RUN_INPUT_CHANGED')
        else:save(frozen,binding)
        if args.local_only:
            import socket
            def denied(*_args,**_kwargs):raise OSError('Network is disabled for local integration')
            socket.socket.connect=denied
            socket.socket.connect_ex=denied
        (audio_run if args.kind=='audio' else visual_run)(args,paths)
    except Exception as exc:
        issue=getattr(exc,'issue',None)
        save(Path(args.root)/'host-error.json',{'code':issue.get('code','WORKFLOW_FAILED') if isinstance(issue,dict) else 'WORKFLOW_FAILED',
             'error_type':type(exc).__name__})
        raise SystemExit(2) from None

if __name__=='__main__':main()
