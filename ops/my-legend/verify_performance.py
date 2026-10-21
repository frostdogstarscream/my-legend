"""Read-only verification of the produced pilot. No generation, retry or review submission."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import time

import httpx
import soundfile as sf


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--story-id',default='b00d1336-46bc-4b29-9700-30e3be6eaefb')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]/'runtime/my-legend-production/runs/story-20261019-001')
    args=parser.parse_args();workspace=Path(__file__).resolve().parents[2];root=args.root
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://127.0.0.1:8895/api/v1',trust_env=False,timeout=25) as client:
        token=client.post('/auth/local').json()['data']['accessToken'];client.headers['Authorization']='Bearer '+token
        try:
            response=client.get('/stories/'+args.story_id);response.raise_for_status();story=response.json()['data']
            task=client.get('/production-tasks/'+story['productionTaskId']).json()['data'];media=story['media']
            assert media['sourceVersionId']==story['storyVersionId']==task['storyVersionId']
            with httpx.Client(trust_env=False,timeout=25) as public:
                ranged=public.get(media['videoUrl'],headers={'Range':'bytes=0-31'})
                assert ranged.status_code==206 and len(ranged.content)==32
                video=public.get(media['videoUrl']);video.raise_for_status()
                assert hashlib.sha256(video.content).hexdigest()==media['videoSha256']
                audio=public.get(media['audioUrl']);audio.raise_for_status()
                assert hashlib.sha256(audio.content).hexdigest()==media['audioSha256']
                info=sf.info(io.BytesIO(audio.content))
                assert abs(info.duration-media['duration'])<1/info.samplerate
                assert public.get(media['videoUrl'][:-1]+('0' if media['videoUrl'][-1]!='0' else '1')).status_code==403
            artifact=next(a for a in story['handoffArtifacts'] if a['kind']=='StoryDelivery')
            delivery=read(workspace/'runtime/my-legend-story/app/artifacts'/(artifact['sha256']+'.json'))
            expected=[(sc['scene_id'],u['line_id'],u['kind'],u['text']) for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances']]
            actual=[(c['sceneId'],c['sourceLineId'],c['kind'],c['text']) for c in media['cues']]
            assert actual==expected
            for c in media['cues']:
                assert 0<=c['start']<c['end']<=media['duration']
                assert c['durationBasis']==('planned_silent_hold' if c['kind']=='action' else 'measured_audio')
            report=read(root/'production-report.json');audio_results=read(root/'audio-results.json')
            assert report['model_calls']==sum(a['call_budget']['production_calls'] for a in audio_results)
            assert all(a['technical_passed'] for a in audio_results)
            paths=read(workspace/'runtime/my-legend-production/workflows.json');dependency=workspace/'runtime/my-legend-production'
            checked=0
            for relative,digest in read(dependency/'dependency-lock.json')['files'].items():
                assert hashlib.sha256((dependency/relative).read_bytes()).hexdigest()==digest;checked+=1
            assets=Path(paths['visual_root'])/'pixel-story-mobile/packages/story_player/assets'
            plans=list((root/'segments').glob('*/visual-workflow/compiled-plan.json'))
            for plan_file in plans:
                plan=read(plan_file)
                for relative,digest in plan['asset_bindings']['file_hashes'].items():
                    assert hashlib.sha256((assets/relative).read_bytes()).hexdigest()==digest
            evidence={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'story_id':story['id'],'story_version_id':story['storyVersionId'],
                'production_id':task['id'],'status':task['status'],'duration_seconds':info.duration,'sample_rate':info.samplerate,'audio_frames':info.frames,
                'source_events':len(actual),'source_order_and_text_preserved':True,'segments':len(plans),'dependency_files_checked':checked,
                'video_sha256':media['videoSha256'],'audio_sha256':media['audioSha256'],'http_range':ranged.status_code,'invalid_media_signature':403,
                'voices':task['voices'],'production_model_calls':report['model_calls'],'probe_model_calls':1,'total_audio_model_calls':report['model_calls']+1,
                'audio_review':task['audioReview'],'visual_review':task['visualReview'],'external_publication':'not_requested',
                'word_alignment':'not_available','visual_presentation':media['presentation'],'synthetic_speech':False,'synthetic_sfx':False,
                'planned_silent_action_holds':report['planned_silent_action_holds'],'human_content_acceptance':task.get('audioReview')==task.get('visualReview')=='approved'}
            (workspace/'ops/my-legend/evidence/performance-live-20261019.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(evidence,ensure_ascii=False,indent=2))
        finally:client.post('/auth/logout')


if __name__=='__main__':main()
