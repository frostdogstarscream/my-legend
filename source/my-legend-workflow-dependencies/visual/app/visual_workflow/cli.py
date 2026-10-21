"""本地调试入口：start/get/resume；人工审核必须显式提供当前审核哈希。"""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .contracts import Decision
from .review import resume_graph
from .editing import revise
from .graph import build_graph
from .repository import Repository
from .planner import VisualError
from .input_pipeline import prepare_request, compile_prepared

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--store', default='output/visual-workflow')
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start'); start.add_argument('input')
    get = commands.add_parser('get'); get.add_argument('run_id')
    resume = commands.add_parser('resume'); resume.add_argument('run_id'); resume.add_argument('decision')
    edit = commands.add_parser('edit'); edit.add_argument('run_id'); edit.add_argument('changes'); edit.add_argument('output')
    validate = commands.add_parser('validate'); validate.add_argument('input')
    args = parser.parse_args()
    if args.command == 'validate':
        prepared = prepare_request(json.loads(Path(args.input).read_text()))
        if prepared['missing_requirements']:
            output = {'status':'awaiting_context', 'missing_requirements':prepared['missing_requirements'], 'warnings':prepared['source_warnings'], 'degradations':prepared['degradations']}
        else:
            plan = compile_prepared(prepared)
            output = {'status':'validated', 'plan_hash':plan['plan_hash'],
                'duration_ms':plan['manifest']['duration_ms'], 'target_capabilities':plan['target_capabilities'],
                'validation_report':plan['validation_report'], 'degradations':plan['degradations']}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    repository = Repository(args.store)
    with SqliteSaver.from_conn_string(str(repository.root / 'checkpoints.sqlite')) as saver:
        graph = build_graph(repository, saver)
        run_id = uuid4().hex if args.command == 'start' else args.run_id
        config = {'configurable': {'thread_id': run_id}}
        if args.command == 'edit':
            snapshot = graph.get_state(config)
            if snapshot.values.get('status') != 'succeeded':
                raise VisualError('VERSION_CONFLICT', '只允许基于已审核版本修改')
            values = snapshot.values
            updated = revise(values['request'], json.loads(Path(args.changes).read_text()), values['result']['visual_version_id'])
            Path(args.output).write_text(json.dumps(updated, ensure_ascii=False, indent=2))
            print(json.dumps({'request_ref': args.output, 'status': 'revision_created', 'next': 'start 新输入并重新审核'}, ensure_ascii=False))
            return
        if args.command == 'start':
            graph.invoke({'request': json.loads(Path(args.input).read_text())}, config)
        elif args.command == 'resume':
            snapshot = graph.get_state(config)
            if not snapshot.tasks or not any(t.interrupts for t in snapshot.tasks):
                raise VisualError('NOT_PAUSED', '该任务不存在或不在等待确认')
            decision = Decision.model_validate_json(Path(args.decision).read_text()).model_dump()
            pending = next(i.value for t in snapshot.tasks for i in t.interrupts)
            if any(decision[k] != pending[k] for k in ('review_id', 'expected_plan_hash')):
                raise VisualError('STALE_REVIEW', '审核版本不匹配，未恢复任务')
            resume_graph(graph, config, decision)
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise VisualError('NOT_FOUND', '任务不存在')
        pending = [i.value for t in snapshot.tasks for i in t.interrupts]
        values = snapshot.values
        output = {'run_id': run_id, 'status': pending[0]['status'] if pending else values['status'],
            'pending_review': pending, 'error': values.get('error'), 'result': values.get('result')}
        print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (VisualError, ValueError) as exc:
        print(json.dumps({'error': exc.issue if isinstance(exc, VisualError) else
                         {'code': 'INVALID_INPUT', 'message': str(exc)}}, ensure_ascii=False))
        raise SystemExit(1)
