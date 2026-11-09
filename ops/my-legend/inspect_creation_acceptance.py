"""Read-only evidence for explicitly named local acceptance flows; no provider calls."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import httpx
from my_legend_production.production.repository import ProductionRepository
from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.contracts import ArtifactRef

parser=argparse.ArgumentParser()
parser.add_argument('--flow',action='append',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
repo=ProductionRepository(os.environ['MY_LEGEND_PRODUCTION_DSN'])
store=ArtifactStore(root/'runtime/my-legend-production/independent/artifacts')
report={'checked_at':datetime.now(timezone.utc).isoformat(),'flows':[]}
with httpx.Client(base_url='http://127.0.0.1:8897',headers={'Authorization':'Bearer '+os.environ['MY_LEGEND_SERVICE_TOKEN']},timeout=15,trust_env=False) as engine:
    for fid in args.flow:
        with repo.transaction() as db:
            db.execute('SET TRANSACTION READ ONLY')
            flow=db.execute('SELECT * FROM production_flows WHERE id=%s',(fid,)).fetchone()
            if not flow:raise ValueError('Unknown acceptance flow')
            links=db.execute('SELECT phase,job_id FROM production_flow_jobs WHERE flow_id=%s',(fid,)).fetchall()
            entry={k:flow[k] for k in ('id','work_id','status','request_hash','request','auto_title','error_code','created_at')}
            entry.update({k:flow.get(k) for k in ('parent_id','revision','recovery_query')})
            entry['jobs']=[]
            for link in links:
                job=repo.get(link['job_id'],flow['owner_id'])
                item={k:job[k] for k in ('id','status','source_version_id','actual_calls','unsettled_calls','unknown_calls','error_code','artifacts')}
                item['phase']=link['phase']
                stages=db.execute('SELECT name,status,error_code,checkpoint FROM stages WHERE job_id=%s',(job['id'],)).fetchall()
                item['stages']=[{k:s[k] for k in ('name','status','error_code')} for s in stages]
                for stage in stages:
                    if stage['name']!='story':continue
                    state=stage['checkpoint'];decision=state.get('decision',{});pid=decision.get('plan_id')
                    if pid:
                        r=engine.get('/v1/plans/'+pid);r.raise_for_status();plan=r.json()
                        item['plan']={k:plan.get(k) for k in ('plan_id','plan_revision','plan_fingerprint','plan')}
                    if state.get('upstream_id'):
                        r=engine.get('/v1/jobs/'+state['upstream_id']);r.raise_for_status();native=r.json()
                        item['native']={k:native.get(k) for k in ('job_id','status','stage','error_code','error_message','result_story_version_id')}
                        item['native']['calls']=[{k:c.get(k) for k in ('role','status','error_code','requested_model','actual_model','latency_ms','input_tokens','output_tokens')} for c in (native.get('usage') or {}).get('call_records',[])]
                item['artifact_summaries']={}
                for a in job['artifacts']:
                    if a['kind'] not in ('StoryDelivery','MeasuredTimeline','ArrangedTimeline','ControlledTimeline','PerformanceManifest','PlaybackManifest','StoryPlanDecision'):continue
                    value=store.json(ArtifactRef.model_validate(a))
                    item['artifact_summaries'][a['kind']]={k:v for k,v in value.items() if k in ('title','story_id','story_version_id','source_version_id','schema_version','sample_rate','duration_samples','duration_frames','duration_seconds','total_frames','audio','source','source_job_id','performance','preview','scene_track')}
                entry['jobs'].append(item)
            report['flows'].append(entry)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps([{'id':f['id'],'status':f['status'],'title':f['request'].get('title') or f['auto_title'],'error':f['error_code'],'jobs':[{'phase':j['phase'],'status':j['status'],'calls':j['actual_calls'],'native':j.get('native'),'plan_id':j.get('plan',{}).get('plan_id')} for j in f['jobs']]} for f in report['flows']],ensure_ascii=False,indent=2))
