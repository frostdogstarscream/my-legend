"""第一步验收：输入不依赖AI；来源保留；目标不支持时必须明确失败。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from app.visual_workflow.input_pipeline import prepare_request, compile_prepared
from app.visual_workflow.source_adapter import apply_context_answers
from app.visual_workflow.asset_catalog import load_catalog, target_capabilities, DEFAULT_ASSETS
from app.visual_workflow.planner import VisualError, ROOT
from app.visual_workflow.editing import revise

class VisualInputTests(unittest.TestCase):
    def setUp(self):
        self.request=json.loads((ROOT/'examples/visual_workflow/unified-classroom.json').read_text())

    def test_normalization_preserves_order_text_ids_and_facts(self):
        before=copy.deepcopy(self.request)
        prepared=prepare_request(self.request); plan=compile_prepared(prepared)
        self.assertEqual(self.request,before)
        original=[b for s in self.request['script']['scenes'] for b in s['blocks']]
        actual=[b for s in plan['document']['scenes'] for b in s['blocks']]
        self.assertEqual([(b['block_id'],b.get('text')) for b in original],[(b['block_id'],b.get('text')) for b in actual])
        self.assertEqual(plan['source_snapshot']['constraints'],prepared['source_snapshot']['constraints'])
        self.assertEqual(plan['manifest']['duration_ms'],37200)

    def test_missing_mapping_needs_answers_even_when_context_flag_true(self):
        self.request['context_confirmed']=True
        self.request['script']['characters'][0].pop('appearance')
        prepared=prepare_request(self.request)
        self.assertEqual(prepared['missing_requirements'][0]['code'],'MISSING_APPEARANCE')
        self.assertIsNone(prepared['normalized_script'])
        with self.assertRaises(VisualError):compile_prepared(prepared)
        with self.assertRaises(VisualError):apply_context_answers(prepared['request'],{},prepared['missing_requirements'])
        answer={'characters.teacher.appearance':{'template_type':'elder','color_key':'brown','accessory':'none'}}
        resolved=apply_context_answers(prepared['request'],answer,prepared['missing_requirements'])
        self.assertFalse(prepare_request(resolved)['missing_requirements'])
        self.assertNotIn('appearance',self.request['script']['characters'][0])

    def test_excerpt_requires_explicit_continuity_confirmation(self):
        self.request['script']['excerpt']=True
        prepared=prepare_request(self.request)
        self.assertEqual(prepared['missing_requirements'][0]['code'],'UNCONFIRMED_CONTINUITY')
        with self.assertRaises(VisualError):apply_context_answers(prepared['request'],{'continuity_confirmed':False},prepared['missing_requirements'])

    def test_unknown_fields_bad_references_duplicates_and_shape_rejected(self):
        for mode in ('code','ref','duplicate','no_text','ambiguous_face','both_sources','new_plot','empty_name'):
            bad=copy.deepcopy(self.request);script=bad['script'];block=script['scenes'][0]['blocks'][0]
            if mode=='code':block['script']='alert(1)'
            elif mode=='ref':block['role_id']='unknown'
            elif mode=='duplicate':script['characters'][1]['role_id']=script['characters'][0]['role_id']
            elif mode=='no_text':script['scenes'][0]['blocks'][1].pop('text')
            elif mode=='ambiguous_face':block.update(action='face',direction='left',target_role_id='teacher')
            elif mode=='both_sources':bad['document']={}
            elif mode=='new_plot':script['constraints']['allow_new_plot']=True
            else:script['characters'][0]['name']=''
            with self.subTest(mode=mode),self.assertRaises(VisualError) as exc:prepare_request(bad)
            self.assertEqual(exc.exception.issue['code'],'INVALID_INPUT')
            self.assertTrue(exc.exception.issue['field_path'])

    def test_unknown_action_and_multi_actor_never_silently_degraded(self):
        for mode in ('attack','multi','requirement','action_target'):
            bad=copy.deepcopy(self.request);block=bad['script']['scenes'][0]['blocks'][0]
            bad['script']['constraints']['allow_narration_fallback']=False
            if mode=='attack':block.update(action='attack',target_role_id='teacher')
            elif mode=='multi':block.pop('role_id');block['participant_role_ids']=['teacher','student']
            elif mode=='requirement':bad['script']['characters'][0]['visual_requirements']=['精确原角色面貌']
            else:block['target_role_id']='teacher'
            with self.subTest(mode=mode),self.assertRaises(VisualError) as exc:prepare_request(bad)
            self.assertIn('UNSUPPORTED',exc.exception.issue['code'])

    def test_source_timing_preserved_unverified_not_executed_and_budget_checked(self):
        block=self.request['script']['scenes'][0]['blocks'][1]
        block['timing']={'start_ms':8000,'end_ms':9000,'verified':False,'basis':'provided_media'}
        prepared=prepare_request(self.request);plan=compile_prepared(prepared)
        self.assertEqual(plan['source_snapshot']['scenes'][0]['blocks'][1]['timing'],block['timing'])
        self.assertNotIn('timing',plan['document']['scenes'][0]['blocks'][1])
        self.assertTrue(any(w['code']=='SOURCE_TIMING_UNVERIFIED' for w in plan['validation_report']['warnings']))
        block['timing']['verified']=True
        with self.assertRaises(VisualError) as exc:prepare_request(self.request)
        self.assertEqual(exc.exception.issue['code'],'UNSUPPORTED_TIMING')
        block.pop('timing');self.request['script']['target_duration_ms']=1000
        with self.assertRaises(VisualError) as exc:compile_prepared(prepare_request(self.request))
        self.assertEqual(exc.exception.issue['code'],'TIMING_BUDGET_EXCEEDED')

    def test_overlapping_or_reversed_source_timing_rejected(self):
        blocks=self.request['script']['scenes'][0]['blocks']
        blocks[0]['timing']={'start_ms':0,'end_ms':2000,'verified':False,'basis':'provided_media'}
        blocks[1]['timing']={'start_ms':1000,'end_ms':3000,'verified':False,'basis':'provided_media'}
        with self.assertRaises(VisualError):prepare_request(self.request)
        blocks[1]['timing']['start_ms']=4000
        with self.assertRaises(VisualError):prepare_request(self.request)

    def test_catalog_versions_corruption_license_and_target_profiles(self):
        self.assertNotEqual(target_capabilities('mobile_current_v2')['render_mode'],target_capabilities('web_basic_v1')['render_mode'])
        for pack,version in [('unknown','1.0.0'),('basic_pixel','2.0.0')]:
            with self.assertRaises(VisualError):load_catalog(pack_id=pack,version=version)
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(DEFAULT_ASSETS,root,dirs_exist_ok=True)
            registry=root/'basic_pixel/1.0.0/registry.json';original=registry.read_text()
            for mode in ('corrupt','wrong_version','no_license'):
                obj=json.loads(original)
                if mode=='wrong_version':obj['version']='9.0.0'
                if mode=='no_license':obj.pop('license')
                registry.write_text('bad json' if mode=='corrupt' else json.dumps(obj))
                with self.subTest(mode=mode),self.assertRaises(VisualError):load_catalog(root)

    def test_real_fogport_excerpt_is_blocked_without_changing_dialogue(self):
        raw=json.loads((ROOT/'examples/visual_workflow/fogport-unsupported.json').read_text())
        before=copy.deepcopy(raw)
        with self.assertRaises(VisualError) as exc:prepare_request(raw)
        self.assertEqual(exc.exception.issue['code'],'UNSUPPORTED_ACTION')
        self.assertEqual(raw,before)
        self.assertIn('table_touch',exc.exception.issue['message'])

    def test_unified_edit_keeps_source_fields_and_invalidates_timing(self):
        self.request['script']['scenes'][0]['blocks'][1]['timing']={'start_ms':0,'end_ms':1000,'verified':False,'basis':'provided_media'}
        edited=revise(self.request,[{'kind':'block','id':'c2','fields':{'text':'老师，我想再试一次。'}},{'kind':'character','id':'student','fields':{'color_key':'green'}}],'parent')
        self.assertEqual(edited['script']['constraints'],self.request['script']['constraints'])
        self.assertNotIn('timing',edited['script']['scenes'][0]['blocks'][1])
        self.assertEqual(edited['script']['characters'][1]['appearance']['color_key'],'green')
        self.assertEqual(edited['parent_visual_version'],'parent')
        compile_prepared(prepare_request(edited))


    def test_narration_policy_preserves_source_reestimates_duration_and_freezes_trace(self):
        from app.visual_workflow.repository import Repository
        before=copy.deepcopy(self.request)
        block=self.request['script']['scenes'][0]['blocks'][2]
        original=copy.deepcopy(block)
        block.clear();block.update(block_id=original['block_id'],type='action',role_id='student',
            action='table_touch',text='学生用扳手轻轻触碰桌面。',duration_ms=100,source_ref='source_touch')
        source=copy.deepcopy(self.request)
        prepared=prepare_request(self.request)
        converted=prepared['normalized_script']['scenes'][0]['blocks'][2]
        self.assertEqual(converted,{'block_id':original['block_id'],'type':'narration','text':block['text'],'source_ref':'source_touch'})
        self.assertEqual(self.request,source)
        self.assertEqual(prepared['normalized_script']['scenes'][0]['blocks'][0]['action'],before['script']['scenes'][0]['blocks'][0]['action'])
        plan=compile_prepared(prepared)
        self.assertEqual(plan['degradations'][0]['source_event']['duration_ms'],100)
        self.assertEqual(plan['source_snapshot'],source['script'])
        with tempfile.TemporaryDirectory() as folder:
            repo=Repository(folder);repo.preview(plan);repo.freeze(prepared['request'],plan)
            provenance=json.loads(next(Path(folder).glob('versions/*/provenance.json')).read_text())
            self.assertEqual(provenance['degradations'],plan['degradations'])
        revised=revise(self.request,[{'kind':'block','id':block['block_id'],'fields':{'text':'学生触碰桌面。'}}],'parent_version')
        self.assertNotIn('duration_ms',revised['script']['scenes'][0]['blocks'][2])
        self.assertNotEqual(compile_prepared(prepare_request(revised))['plan_hash'],plan['plan_hash'])

    def test_fogport_narration_reports_pending_context_and_multi_action_is_one_event(self):
        request=json.loads((ROOT/'examples/visual_workflow/fogport-narration.json').read_text())
        prepared=prepare_request(request)
        self.assertEqual([d['source_event']['action'] for d in prepared['degradations']],['table_touch','close_door'])
        self.assertTrue(prepared['missing_requirements'])
        self.assertIsNone(prepared['normalized_script'])
        block=self.request['script']['scenes'][0]['blocks'][2]
        key=block['block_id'];block.clear();block.update(block_id=key,type='action',
            participant_role_ids=['teacher','student'],action='lift_table',text='老师和学生一起抬起桌子。')
        prepared=prepare_request(self.request)
        self.assertEqual(prepared['normalized_script']['scenes'][0]['blocks'][2]['text'],block['text'])
        self.assertEqual(len(prepared['degradations']),1)
        block.update(action='enter')
        with self.assertRaises(VisualError):prepare_request(self.request)

    def test_automatic_defaults_and_notes_never_change_original_dialogue(self):
        raw=json.loads((ROOT/'examples/visual_workflow/fogport-auto-v2.json').read_text())
        before=copy.deepcopy(raw);prepared=prepare_request(raw);plan=compile_prepared(prepared)
        self.assertFalse(prepared['missing_requirements']);self.assertEqual(raw,before)
        blocks=plan['document']['scenes'][0]['blocks']
        self.assertEqual(blocks[1]['text'],'一次金属扳手与木桌接触')
        self.assertEqual(blocks[3]['text'],'一次正常关门')
        self.assertEqual(blocks[0]['text'],raw['script']['scenes'][0]['blocks'][0]['text'])
        self.assertEqual(len(plan['generation_issues']),6)
        self.assertFalse(plan['preview_skin']['mobile_supported'])
        raw['target_profile']='mobile_current_v2'
        with self.assertRaises(VisualError):prepare_request(raw)

    def test_refined_background_preview_preserves_motion_and_freezes_assets(self):
        import hashlib
        from unittest.mock import patch
        from app.visual_workflow.repository import Repository
        raw=json.loads((ROOT/'examples/visual_workflow/fogport-simulation-v4.json').read_text())
        plan=compile_prepared(prepare_request(raw))
        self.assertEqual(plan['preview_skin']['id'],'fogport_v2')
        poses=[next(a for a in f['actors'] if a['role_id']=='sutang') for f in plan['frames'][:20]]
        self.assertGreater(len({tuple(a['position']) for a in poses}),3)
        self.assertFalse(next(a for a in plan['frames'][0]['actors'] if a['role_id']=='laoqin')['visible'])
        with tempfile.TemporaryDirectory() as folder:
            repo=Repository(folder);preview=Path(repo.preview(plan));result=repo.freeze(raw,plan)
            frozen=Path(result['runtime_package_ref']).parent/'background.png'
            self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(),plan['preview_skin']['background_sha256'])
            self.assertEqual(frozen.read_bytes(),(preview.parent/'background.png').read_bytes())
            original=Path.read_bytes
            def changed(path):
                return b'changed' if path.name=='background.png' else original(path)
            with patch.object(Path,'read_bytes',changed),self.assertRaises(VisualError) as exc:
                repo.freeze(raw,plan)
            self.assertEqual(exc.exception.issue['code'],'ASSET_CHANGED')

    def test_painted_version_binds_new_assets_and_keeps_story_events(self):
        import hashlib
        from app.visual_workflow.repository import Repository
        raw=json.loads((ROOT/'examples/visual_workflow/fogport-painted-v5.json').read_text())
        plan=compile_prepared(prepare_request(raw))
        self.assertEqual(plan['preview_skin']['art_style'],'painted')
        self.assertEqual(plan['manifest']['duration_ms'],34800)
        folder=ROOT/'app/visual_workflow/assets/fogport_painted_v1'
        self.assertEqual(plan['preview_skin']['sha256'],hashlib.sha256((folder/'characters.png').read_bytes()).hexdigest())
        self.assertEqual(plan['preview_skin']['background_sha256'],hashlib.sha256((folder/'background.png').read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            repo=Repository(tmp);preview=Path(repo.preview(plan));result=repo.freeze(raw,plan)
            frozen=Path(result['runtime_package_ref']).parent
            for name in ('characters.png','background.png'):
                self.assertEqual((preview.parent/name).read_bytes(),(folder/name).read_bytes())
                self.assertEqual((frozen/name).read_bytes(),(folder/name).read_bytes())

    def test_script_supplied_template_keeps_final_narration_and_generic_ids(self):
        raw=json.loads((ROOT/'examples/visual_workflow/standing-dialogue-template.json').read_text())
        before=copy.deepcopy(raw)
        plan=compile_prepared(prepare_request(raw))
        self.assertEqual(raw,before)
        self.assertEqual(plan['preview_skin']['roles'],['role_a','role_b','role_c'])
        self.assertEqual(plan['target_capabilities']['actions'],['enter','exit','move','wait'])
        self.assertEqual(plan['target_capabilities']['max_characters'],3)
        self.assertEqual(plan['degradations'],[])
        source=raw['script']['scenes'][0]['blocks'];actual=plan['document']['scenes'][0]['blocks']
        self.assertEqual([(b['block_id'],b.get('text')) for b in source],[(b['block_id'],b.get('text')) for b in actual])
        raw['script']['scenes'][0]['blocks'][0]['text']='正文中的叙述尚未确认。'
        self.assertEqual(prepare_request(raw)['normalized_script']['scenes'][0]['blocks'][0]['text'],'正文中的叙述尚未确认。')

    def test_script_supplied_profile_rejects_unwritten_narration_and_overcapacity(self):
        raw=json.loads((ROOT/'examples/visual_workflow/standing-dialogue-template.json').read_text())
        for mode in ('fallback','complex','reaction','capacity'):
            bad=copy.deepcopy(raw)
            if mode=='fallback':bad['script']['constraints']['allow_narration_fallback']=True
            elif mode=='capacity':bad['script']['characters'].append({'role_id':'role_d','name':'第四人','description':'未登记的第四张立绘'})
            else:
                block=bad['script']['scenes'][0]['blocks'][4];block.pop('target_anchor')
                block['action']='close_door' if mode=='complex' else 'nod'
            with self.subTest(mode=mode),self.assertRaises(VisualError) as exc:
                compile_prepared(prepare_request(bad))
            expected={'fallback':'INVALID_INPUT','complex':'SCRIPT_NARRATION_REQUIRED','reaction':'SCRIPT_NARRATION_REQUIRED','capacity':'UNSUPPORTED_ASSET_CAPACITY'}[mode]
            self.assertEqual(exc.exception.issue['code'],expected)
            self.assertTrue(exc.exception.issue['field_path'])

if __name__=='__main__':unittest.main()
