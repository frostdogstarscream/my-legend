"""跨模块接口测试：版本、人物、原文、时序和文件缓存不能悄悄变更。"""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from app.preview_integration.alignment import align
from app.preview_integration.common import IntegrationError,within
from app.preview_integration.cli import local_audio_only
from app.preview_integration.cli import Job
from app.preview_integration.exporter import prepare


def fixtures():
    blocks=[{'block':{'block_id':'b1','type':'dialogue','role_id':'a','text':'原台词'},'start_ms':100,'end_ms':400},
            {'block':{'block_id':'b2','type':'narration','text':'原旁白'},'start_ms':400,'end_ms':700}]
    frames=[{'time_ms':t,'scene_id':'s','block_id':'b1' if t<400 else 'b2','speaker_id':'a' if t<400 else None,'subtitle':'原台词' if t<400 else '原旁白','actors':[{'speaking':t<400}]} for t in range(0,801,100)]
    plan={'plan_hash':'origin','manifest':{'source_version_id':'v1','duration_ms':800,'scenes':[{'scene_id':'s','start_ms':0,'end_ms':800,'blocks':blocks}]},'frames':frames}
    audio={'source_version_id':'v1','timeline':{'sample_rate':1000,'total_frames':1300,'placements':[{'block_id':'b1','kind':'speech','scene_id':'s','role_id':'a','text':'原台词','start_frame':100,'end_frame':700},{'block_id':'b2','kind':'sfx','scene_id':'s','role_id':None,'text':'原旁白','start_frame':800,'end_frame':1200}]}}
    return plan,audio

class IntegrationTests(unittest.TestCase):
    def test_measured_duration_and_gap(self):
        plan,audio=fixtures()
        for f in plan['frames']:f['actors'][0]['future_entry']=f['time_ms']>=400
        result=align(plan,audio)
        self.assertEqual(result['manifest']['duration_ms'],1300)
        self.assertEqual(result['frames'][6]['subtitle'],'原台词')
        self.assertEqual(result['frames'][7]['subtitle'],'')
        self.assertIsNone(result['frames'][7]['speaker_id'])
        self.assertFalse(result['frames'][7]['actors'][0]['future_entry'])
        self.assertEqual(result['frames'][8]['subtitle'],'原旁白')
        self.assertEqual(plan['manifest']['duration_ms'],800)
        self.assertNotEqual(result['plan_hash'],plan['plan_hash'])
    def test_source_version(self):
        plan,audio=fixtures();audio['source_version_id']='v2'
        with self.assertRaisesRegex(IntegrationError,'源版本'):align(plan,audio)
    def test_text_and_role_preserved(self):
        for field,value in [('text','改写了'),('role_id','other'),('scene_id','other')]:
            plan,audio=fixtures();audio['timeline']['placements'][0][field]=value
            with self.assertRaisesRegex(IntegrationError,'原文'):align(plan,audio)
    def test_duplicate_and_missing_events(self):
        plan,audio=fixtures();audio['timeline']['placements'].append(deepcopy(audio['timeline']['placements'][0]))
        with self.assertRaisesRegex(IntegrationError,'重复'):align(plan,audio)
        plan,audio=fixtures();audio['timeline']['placements'].pop()
        with self.assertRaisesRegex(IntegrationError,'集合'):align(plan,audio)
    def test_overlap_rejected(self):
        plan,audio=fixtures();audio['timeline']['placements'][1]['start_frame']=500
        with self.assertRaisesRegex(IntegrationError,'非重叠'):align(plan,audio)
    def test_remote_generation_not_enabled(self):
        _,audio=fixtures();script={'events':[]}
        config={'text_model':{'enabled':False},'visual':{'enabled':False},'tts':{'adapter':'local'},'sfx':{'adapter':'local'},'call_budget':{'max_calls':0,'batch_max_calls':0}}
        local_audio_only(script,config)
        config['tts']['adapter']='remote'
        with self.assertRaises(IntegrationError):local_audio_only(script,config)
    def test_path_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(within(folder,'audio/test.wav'),(Path(folder)/'audio/test.wav').resolve())
            with self.assertRaises(IntegrationError):within(folder,'../outside.wav')
            outside=Path(folder).parent/'outside-preview-test'
            (Path(folder)/'escape').symlink_to(outside)
            with self.assertRaises(IntegrationError):within(folder,'escape/test.wav')
    def test_completed_stage_verifies_cached_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            job=Job.__new__(Job);job.root=Path(folder);job.state_file=job.root/'state.json';job.state={'stages':{}}
            artifact=job.root/'test.json';calls=[]
            def work():calls.append(1);artifact.write_text('{}')
            job.stage('demo',work,artifact);job.stage('demo',work,artifact)
            self.assertEqual(len(calls),1)
            artifact.write_text('{"changed":true}')
            with self.assertRaisesRegex(IntegrationError,'产物已变化'):job.stage('demo',work,artifact)
    def test_python_symlink_keeps_virtualenv_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'base-python').touch();(root/'venv-python').symlink_to(root/'base-python')
            job=Job.__new__(Job);job.request_file=root/'request.json'
            self.assertEqual(job.path('venv-python'),root/'venv-python')
    def test_script_text_cannot_close_export_script(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);draft=root/'draft';draft.mkdir();target=root/'render'
            source=Path(__file__).resolve().parents[1]/'app/visual_workflow/preview.html'
            (draft/'index.html').write_text(source.read_text());(root/'mix.wav').touch();(root/'gsap.js').touch()
            plan={'manifest':{'duration_ms':800},'document':{'title':'</script><script>alert(1)</script>'}}
            prepare(plan,draft,root/'mix.wav',target,root/'gsap.js')
            rendered=(target/'index.html').read_text()
            self.assertNotIn('</script><script>alert(1)',rendered)
            self.assertIn('<\\/script><script>alert(1)',rendered)

if __name__=='__main__':unittest.main()
