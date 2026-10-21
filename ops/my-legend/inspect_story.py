"""Read-only diagnostics. Print no credentials or raw model prompts/responses."""
import json
import os
from pathlib import Path
import sqlite3
import time
import httpx

root = Path(__file__).resolve().parents[2] / 'runtime/my-legend-story'
db = sqlite3.connect(root / 'app/app.sqlite')
report = {'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'tasks': []}
with httpx.Client(base_url='http://127.0.0.1:8897', headers={'Authorization': 'Bearer ' + os.environ['MY_LEGEND_SERVICE_TOKEN']}, trust_env=False, timeout=15) as client:
    for kind, value in db.execute('SELECT kind,value FROM objects'):
        if kind != 'task':
            continue
        task = json.loads(value)
        if not task.get('upstreamId'):
            continue
        path = '/v1/jobs/' if task['phase'] == 'writing' else '/v1/tasks/'
        result = client.get(path + task['upstreamId'])
        if result.status_code != 200:
            raise RuntimeError('Unable to read upstream task')
        data = result.json()
        usage = data.get('usage') or {}
        entry = {k: data.get(k) for k in ('task_id','job_id','status','stage','error_code','error_message','result_story_version_id','approved_plan_id','approved_plan_revision')}
        entry['app_task_id'] = task['id']
        entry['usage'] = {k: usage.get(k) for k in ('calls','input_tokens','output_tokens','model_request_seconds')}
        entry['attempt_history'] = [{k:a.get(k) for k in ('status','error_code','error_message','wall_seconds')} for a in usage.get('attempt_history', [])]
        entry['calls'] = [{k: c.get(k) for k in ('role','requested_model','actual_model','status','error_code','input_tokens','output_tokens','latency_ms')} for c in usage.get('call_records', [])]
        if task.get('plan'):
            planning = client.get('/v1/plans/' + task['plan']['id']).json()
            entry['plan_revision'] = planning.get('plan_revision')
            trace = planning.get('model_trace') or []
            entry['planning_usage'] = {'calls':len(trace), 'input_tokens':sum(c.get('input_tokens',0) for c in trace), 'output_tokens':sum(c.get('output_tokens',0) for c in trace)}
        report['tasks'].append(entry)
text = json.dumps(report, ensure_ascii=False, indent=2).replace(os.environ['MY_LEGEND_SERVICE_TOKEN'], '[REDACTED]')
(root / 'diagnostics.json').write_text(text, encoding='utf-8')
print(text)
