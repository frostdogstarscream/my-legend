"""仅由声音独立解释器执行：调用原包公开 Pipeline，不复制或修改同事实现。"""
import json
import shutil
import sys
from pathlib import Path
from lingban_pipeline.graph import Pipeline
from lingban_pipeline.io import file_hash

script_file, config_file, output, result_file = map(Path, sys.argv[1:5])
script=json.loads(script_file.read_text());config=json.loads(config_file.read_text())
p=Pipeline(output)
try:
    # 恢复同一线程，避免外层恢复时再次提交相同声音任务。
    thread='joint_preview'
    snapshot=p.graph.get_state(p.invocation(thread))
    if not snapshot.values:
        sha=file_hash(script_file);copy=p.root/'sources'/f'input_{sha}.json'
        copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(script_file,copy)
        p.run(thread,script,config,script_file.parent,{'file_sha256':sha,'snapshot_file':copy.relative_to(p.root).as_posix()})
    state=p.graph.get_state(p.invocation(thread)).values
    status=p.status(thread);preview=status.get('preview') or {}
    checks=preview.get('checks') or {}
    result={'status':status['status'],'source_version_id':script['source_version_id'],
            'technical_passed':checks.get('technical_passed',False),
            'timeline':state.get('timeline'),'provider_calls':status['call_budget']['production_calls'],
            'preview_ref':preview.get('preview'),'publication':'not_approved'}
    # 原包约定的试听混音与 preview.html 同目录；只在原包技术检查通过后交给外层。
    if preview.get('preview'):
        result['mix_file']=str((p.root/preview['preview']).with_name('draft.wav').resolve())
    result_file.write_text(json.dumps(result,ensure_ascii=False,indent=2))
finally:
    p.close()
