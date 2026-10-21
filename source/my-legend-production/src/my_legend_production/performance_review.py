"""Apply an explicit, hash-bound App review to the original local workflow gates."""
import argparse
from pathlib import Path
import os
import sys

from .core import require,sha
from .performance_production import read
from .worker import save


def resume_visual_review(graph,config,plan_hash,notes):
    from langgraph.types import Command
    for _ in range(3):
        snapshot=graph.get_state(config);state=snapshot.values
        require(state.get('plan',{}).get('plan_hash')==plan_hash,'STALE_VISUAL_REVIEW')
        if state.get('status')=='succeeded':return state['result']
        pending=[i.value for task in snapshot.tasks for i in task.interrupts]
        require(len(pending)==1,'VISUAL_REVIEW_NOT_PENDING')
        item=pending[0]
        require(not item.get('missing_requirements'),'VISUAL_CONTEXT_ANSWERS_REQUIRED')
        require(item.get('expected_plan_hash')==plan_hash,'STALE_VISUAL_REVIEW')
        graph.invoke(Command(resume={'review_id':item['review_id'],'expected_plan_hash':plan_hash,'decision':'approve','notes':notes}),config)
    raise ValueError('VISUAL_REVIEW_DID_NOT_FINISH')


def apply(root,record_file,paths_file):
    root=Path(root);record=read(record_file);state=read(root/'status.json');paths=read(paths_file)
    require(record['decision']=='approve' and record['jobId']==state['id'],'REVIEW_SCOPE_INVALID')
    require(record['storyVersionId']==state['storyVersionId'] and record['videoSha256']==state['media']['videoSha256'],'STALE_MEDIA_REVIEW')
    require(sha((root/'preview/scene.mp4').read_bytes())==record['videoSha256'],'MEDIA_HASH_CHANGED')
    require(read(root/'production-report.json')['source_order_and_coverage_verified'],'SOURCE_COVERAGE_MISMATCH')
    result={'kind':record['kind'],'story_version_id':record['storyVersionId'],'video_sha256':record['videoSha256'],
            'reviewer':record['reviewer'],'external_publication':'not_requested','workflow_results':[]}
    if record['kind']=='audio':
        sys.path.insert(0,str(Path(paths['audio_root'])/'src'))
        from lingban_pipeline.graph import Pipeline
        for directory in sorted((root/'scenes').iterdir()):
            pipe=Pipeline(directory/'audio-workflow')
            try:
                snapshot=pipe.graph.get_state(pipe.invocation('production')).values
                if snapshot.get('status')!='delivered':
                    bundle=snapshot['preview']['bundle']
                    require(bundle['technical_passed'] and not bundle['required_point_confirmations'],'AUDIO_POINT_REVIEW_REQUIRED')
                    pipe.resume('production',{'decision':'approve','bundle_sha256':bundle['bundle_sha256'],'reviewer':record['reviewer'],
                        'scope':['dialogue','performance','sfx','mix','synchronization'],
                        'confirmed_points':[],'notes':[{'kind':'app_bound_preview_review','video_sha256':record['videoSha256'],'notes':record['notes']}]})
                status=pipe.status('production');require(status['status']=='delivered','AUDIO_REVIEW_SYNC_FAILED')
                result['workflow_results'].append({'scene':directory.name,'status':'delivered','release_id':status['delivery']['release_id']})
            finally:pipe.close()
    else:
        os.environ.update(MY_LEGEND_DART=paths['dart'],MY_LEGEND_DART_PACKAGES=paths['dart_packages'],DART_SUPPRESS_ANALYTICS='true')
        sys.path.insert(0,paths['visual_root'])
        from app.visual_workflow.graph import build_graph
        from app.visual_workflow.repository import Repository
        from langgraph.checkpoint.sqlite import SqliteSaver
        for directory in sorted((root/'segments').iterdir()):
            workflow=directory/'visual-workflow';plan=read(workflow/'compiled-plan.json')
            with SqliteSaver.from_conn_string(str(workflow/'checkpoints.sqlite')) as saver:
                graph=build_graph(Repository(str(workflow)),saver)
                response=resume_visual_review(graph,{'configurable':{'thread_id':'production'}},plan['plan_hash'],
                    'App真实声音分镜预览审核；基础外观、初始站位和动作卡纳入审核。原编排为估算时间；App派生制品已按实测音轨重投影。'+record['notes'])
                result['workflow_results'].append({'segment':directory.name,'status':'succeeded','plan_hash':plan['plan_hash'],'review_status':response['review_status']})
    save(Path(record_file).with_suffix('.workflow-result.json'),result)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--record',required=True);parser.add_argument('--paths',required=True)
    args=parser.parse_args()
    try:apply(args.root,args.record,args.paths)
    except Exception as error:
        print('REVIEW_SYNC_FAILED:'+type(error).__name__)
        raise SystemExit(2) from None


if __name__=='__main__':main()
