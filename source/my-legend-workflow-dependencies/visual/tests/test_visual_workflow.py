"""用真实 Dart 编排器和磁盘检查点验证，不用假成功替身。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
except ImportError:
    raise unittest.SkipTest("视觉子图测试需使用 .visual-venv，原业务环境不新增依赖")
from app.visual_workflow.graph import build_graph
from app.visual_workflow.repository import Repository
from app.visual_workflow.planner import compile_document, VisualError, ROOT
from app.visual_workflow.editing import revise
from app.visual_workflow.review import resume_graph

class VisualWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.request = json.loads((ROOT / 'examples/visual_workflow/classroom.json').read_text())

    def test_determinism_and_coverage(self):
        a = compile_document(self.request['document']); b = compile_document(self.request['document'])
        self.assertEqual(a, b)
        self.assertEqual(len(a['coverage']), 12)
        self.assertEqual(a['manifest']['duration_ms'], 37200)
        self.assertEqual([b.get('text') for s in a['document']['scenes'] for b in s['blocks']], [b.get('text') for s in self.request['document']['scenes'] for b in s['blocks']])

    def test_bad_actions_states_references_timing_and_fields(self):
        cases = [({'action':'attack'},'action'), ({'role_id':'missing'},'role_id'),
                 ({'action':'exit'},None), ({'duration_ms':100},None), ({'code':'alert(1)'},None)]
        for fields, _ in cases:
            with self.subTest(fields=fields):
                d = copy.deepcopy(self.request['document']); d['scenes'][0]['blocks'][0].update(fields)
                with self.assertRaises(VisualError): compile_document(d)
        d = copy.deepcopy(self.request['document']); d['scenes'][0]['blocks'][1]['duration_ms']=100
        with self.assertRaises(VisualError): compile_document(d)

    def test_pause_reopen_review_and_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository=Repository(tmp); db=str(Path(tmp)/'checkpoints.sqlite'); config={'configurable':{'thread_id':'run'}}
            with SqliteSaver.from_conn_string(db) as saver:
                graph=build_graph(repository,saver)
                result=graph.invoke({'request':self.request},config)
                pending=result['__interrupt__'][0].value
                self.assertEqual(pending['status'],'awaiting_context')
                self.assertNotIn('result',result)
            # 新连接和新图模拟进程重启，恢复相同 thread_id。
            with SqliteSaver.from_conn_string(db) as saver:
                graph=build_graph(repository,saver)
                result=graph.invoke(Command(resume={**{k:pending[k] for k in ('review_id','expected_plan_hash')},'decision':'approve'}),config)
                pending=result['__interrupt__'][0].value
                self.assertEqual(pending['status'],'awaiting_review')
                self.assertTrue(Path(pending['preview_ref']).is_file())
                self.assertFalse((Path(tmp)/'versions').exists())
                with self.assertRaises(VisualError):
                    resume_graph(graph,config,{'review_id':'old','expected_plan_hash':'old','decision':'approve'})
            with SqliteSaver.from_conn_string(db) as saver:
                graph=build_graph(repository,saver)
                result=graph.invoke(Command(resume={**{k:pending[k] for k in ('review_id','expected_plan_hash')},'decision':'approve'}),config)
                self.assertEqual(result['status'],'succeeded')
                runtime=json.loads(Path(result['result']['runtime_package_ref']).read_text())
                self.assertEqual(runtime,compile_document(self.request['document'])['document'])

    def test_rejection_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp, SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_graph(Repository(tmp),saver); config={'configurable':{'thread_id':'reject'}}
            result=graph.invoke({'request':self.request},config); p=result['__interrupt__'][0].value
            result=graph.invoke(Command(resume={'review_id':p['review_id'],'expected_plan_hash':p['expected_plan_hash'],'decision':'reject'}),config)
            self.assertEqual(result['status'],'rejected')
            bad=copy.deepcopy(self.request);bad['context_confirmed']=True;bad['document']['scenes'][0]['blocks'][0]['action']='attack'
            result=graph.invoke({'request':bad},{'configurable':{'thread_id':'bad'}})
            self.assertEqual(result['status'],'failed'); self.assertIn('field_path',result['error'])
            self.assertNotIn('result',result)

    def test_different_topic_uses_same_rules(self):
        request = json.loads((ROOT / 'examples/visual_workflow/three_visits.json').read_text())
        plan = compile_document(request['document'])
        self.assertGreater(plan['manifest']['duration_ms'], 0)
        self.assertLessEqual(plan['manifest']['duration_ms'], 90000)
        self.assertEqual(set(plan['coverage']), {b['block_id'] for s in request['document']['scenes'] for b in s['blocks']})
        self.assertEqual(plan, compile_document(request['document']))

    def test_duplicate_ids_occupied_anchor_and_target_profiles(self):
        from app.visual_workflow.contracts import Request
        from pydantic import ValidationError
        bad = copy.deepcopy(self.request); bad['target_profile'] = 'web_preview_v1'
        with self.assertRaises(ValidationError): Request.model_validate(bad)
        for mode in ('duplicate', 'occupied', 'audio'):
            d = copy.deepcopy(self.request['document'])
            if mode == 'duplicate': d['scenes'][0]['blocks'][1]['block_id'] = 'c1'
            if mode == 'occupied': d['scenes'][0]['blocks'].insert(1, {'block_id':'occupied','type':'action','role_id':'student','action':'move','target_anchor':'slot_1'})
            if mode == 'audio': d['timing_mode'] = 'audio_locked'
            with self.subTest(mode=mode), self.assertRaises(VisualError): compile_document(d)

    def test_missing_and_changed_assets_block_delivery(self):
        from unittest.mock import patch
        from app.visual_workflow.planner import ASSETS
        plan = compile_document(self.request['document'])
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing'; missing.mkdir()
            with patch('app.visual_workflow.planner.ASSETS', missing), self.assertRaises(VisualError):
                compile_document(self.request['document'])
            plan['asset_bindings']['file_hashes']['basic_pixel/1.0.0/actors.png'] = 'old'
            with self.assertRaises(VisualError): Repository(tmp).freeze(self.request, plan)
            self.assertFalse((Path(tmp)/'versions').exists())

    def test_unified_context_answers_resume_then_source_trace_is_frozen(self):
        request=json.loads((ROOT/'examples/visual_workflow/unified-needs-context.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'checkpoints.sqlite');config={'configurable':{'thread_id':'unified'}}
            with SqliteSaver.from_conn_string(db) as saver:
                graph=build_graph(Repository(tmp),saver)
                result=graph.invoke({'request':request},config)
                pending=result['__interrupt__'][0].value
                self.assertEqual(len(pending['missing_requirements']),2)
                decision={k:pending[k] for k in ('review_id','expected_plan_hash')}
                decision['decision']='approve'
                with self.assertRaises(VisualError):resume_graph(graph,config,decision)
                self.assertIsNone(graph.get_state(config).values['normalized_script'])
                decision['answers']={'characters.student.appearance':{'template_type':'child','color_key':'blue','accessory':'none'},'continuity_confirmed':True}
                # 未知外观字段在写入检查点前拒绝，不污染待恢复答案。
                bad=copy.deepcopy(decision);bad['answers']['characters.student.appearance']['url']='https://invalid.local/image.png'
                with self.assertRaises(ValueError):resume_graph(graph,config,bad)
            with SqliteSaver.from_conn_string(db) as saver:
                graph=build_graph(Repository(tmp),saver);result=resume_graph(graph,config,decision)
                pending=result['__interrupt__'][0].value
                self.assertEqual(pending['status'],'awaiting_review')
                self.assertNotIn('appearance',result['plan']['source_snapshot']['characters'][1])
                self.assertIn('appearance',result['plan']['resolved_source_snapshot']['characters'][1])
                result=resume_graph(graph,config,{**{k:pending[k] for k in ('review_id','expected_plan_hash')},'decision':'approve'})
                self.assertEqual(result['status'],'succeeded')
                provenance=json.loads((Path(result['result']['runtime_package_ref']).parent/'provenance.json').read_text())
                self.assertEqual(provenance['source_snapshot']['constraints']['locked_facts'],request['script']['constraints']['locked_facts'])
                self.assertEqual(provenance['target_capabilities']['render_mode'],'flame_realtime')

    def test_unified_unsupported_input_fails_before_context_and_preview(self):
        request=json.loads((ROOT/'examples/visual_workflow/fogport-unsupported.json').read_text())
        with tempfile.TemporaryDirectory() as tmp, SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_graph(Repository(tmp),saver)
            result=graph.invoke({'request':request},{'configurable':{'thread_id':'unsupported'}})
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['error']['code'],'UNSUPPORTED_ACTION')
            self.assertNotIn('__interrupt__',result)
            self.assertFalse((Path(tmp)/'drafts').exists())

    def test_edits_recompile_and_preserve_old_version(self):
        original=copy.deepcopy(self.request)
        edited=revise(self.request,[{'kind':'block','id':'c2','fields':{'text':'老师，我想再试一次。'}}],'old_version')
        self.assertEqual(self.request,original)
        self.assertNotIn('duration_ms',edited['document']['scenes'][0]['blocks'][1])
        self.assertEqual(edited['document']['revision'],2)
        self.assertNotEqual(compile_document(original['document'])['plan_hash'],compile_document(edited['document'])['plan_hash'])
        with self.assertRaises(VisualError): revise(self.request,[{'kind':'block','id':'c1','fields':{'action':'attack'}}],'old')

    def test_automatic_graph_delivers_without_interrupt_and_returns_issues(self):
        from app.visual_workflow.graph import build_graph
        from app.visual_workflow.repository import Repository
        import tempfile
        raw=json.loads((ROOT/'examples/visual_workflow/fogport-auto-v2.json').read_text())
        with tempfile.TemporaryDirectory() as folder:
            result=build_graph(Repository(folder)).invoke({'request':raw})
            self.assertEqual(result['status'],'succeeded')
            self.assertNotIn('__interrupt__',result)
            self.assertEqual(result['result']['review_status'],'not_required')
            self.assertEqual(len(result['result']['generation_issues']),6)

if __name__=='__main__': unittest.main()
