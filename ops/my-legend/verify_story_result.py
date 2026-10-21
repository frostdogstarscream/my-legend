"""Verify existing prototype results without submitting/retrying a generation task."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import httpx

from drama_engine.service.downstream import RenderManifest
from drama_engine.workflow.delivery import validate_delivery_integrity
from drama_engine.workflow.schemas import StoryDelivery


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('story_id')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2] / 'runtime/my-legend-story'
    report = {'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'app_story_id': args.story_id}
    with httpx.Client(base_url='http://127.0.0.1:8895/api/v1', trust_env=False, timeout=25) as client:
        login = client.post('/auth/local')
        login.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + login.json()['data']['accessToken']
        try:
            response = client.get('/stories/' + args.story_id)
            response.raise_for_status()
            story = response.json()['data']
            report.update(status=story['status'], production=story.get('production'))
            if story['status'] == 'ready':
                artifacts = {}
                for item in story['handoffArtifacts']:
                    raw = (root / 'app/artifacts' / (item['sha256'] + '.json')).read_bytes()
                    assert hashlib.sha256(raw).hexdigest() == item['sha256'], 'ARTIFACT_HASH_MISMATCH'
                    artifacts[item['kind']] = json.loads(raw)
                delivery = StoryDelivery.model_validate(artifacts['StoryDelivery'])
                manifest = RenderManifest.model_validate(artifacts['RenderManifest'])
                assert delivery.status == 'READY' and validate_delivery_integrity(delivery).valid
                assert delivery.story_version_id == manifest.story_version_id == story['storyVersionId']
                lines = [line for episode in delivery.episodes for scene in episode.scenes for line in scene.utterances]
                assert [(line.line_id, line.text) for line in lines] == [(task.line_id, task.text) for task in manifest.tasks]
                assert len(story['chapters']) == len(delivery.episodes)
                for chapter, episode in zip(story['chapters'], delivery.episodes):
                    assert chapter['id'] == episode.episode_id and chapter['source'] == 'generated'
                    shown = '\n'.join(chapter['paragraphs'])
                    assert all(line.text in shown for scene in episode.scenes for line in scene.utterances)
                if story.get('media'):
                    assert story['media'].get('sourceVersionId') == delivery.story_version_id, 'UNRELATED_SAMPLE_MEDIA_ATTACHED'
                else:
                    assert story['production']['audio'] == story['production']['visual'] == 'not_started'
                report.update(story_version_id=delivery.story_version_id, approved_plan_id=delivery.approved_plan_id,
                              approved_plan_revision=delivery.approved_plan_revision, chapters=len(delivery.episodes),
                              scenes=sum(len(ep.scenes) for ep in delivery.episodes), utterances=len(lines),
                              estimated_seconds=[ep.duration_estimate_sec for ep in delivery.episodes],
                              quality=delivery.quality_report.validation_status,
                              audio_state=manifest.audio_state, playback_state=manifest.playback_state,
                              artifacts=story['handoffArtifacts'], integrity_verified=True, source_text_preserved=True,
                              human_acceptance=False)
        finally:
            client.post('/auth/logout')
    text = json.dumps(report, ensure_ascii=False, indent=2)
    (root / ('verified-' + args.story_id + '.json')).write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
