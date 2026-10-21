"""Synthetic stage integration; partial native PCM has a separate offline media test."""
from hashlib import sha256
import json

from my_legend_production.production.audio import AudioStage
from my_legend_production.production.audio_delivery_policy import omit_sounds
from my_legend_production.production.compound_audio import CompoundArrangement, compound_bytes
from my_legend_production.production.contracts import ArtifactRef
from my_legend_production.production.sound_bridge import SoundBridgeStage
from my_legend_production.production.worker import ProductionWorker
from my_legend_production.production.story import StoryStage
from my_legend_production.production.playback import playback_view
from my_legend_production.production.acceptance_media import audit_downloads
from my_legend_production.worker import save
from test_production_queue import env
from test_production_contracts import production_request
from test_production_story import StoryService, advance
from test_sound_bridge import BridgeModel
from test_compound_audio_worker import compound_audio


def test_partial_execution_preserves_original_plan_and_publishes_warning(env,tmp_path):
    repo,store=env
    def partial(root,request_file,timeout):
        request=json.loads(request_file.read_bytes())
        plan=CompoundArrangement.model_validate_json(open(request['arrangement_path'],'rb').read())
        selected=next(o.operation_id for e in plan.events for o in e.operations if o.mode=='one_shot')
        effective,omissions=omit_sounds(plan,[selected],'sound_generation_failed')
        raw=compound_bytes(effective)
        effective_file=root/'fixture-effective.json';effective_file.write_bytes(raw)
        request.update(arrangement_path=str(effective_file),arrangement_sha256=sha256(raw).hexdigest())
        transformed=root/'fixture-request.json';save(transformed,request)
        result=compound_audio(root,transformed,timeout)
        (root/'output/arrangement.json').write_bytes(raw)
        report=json.loads((root/'result.json').read_bytes())
        report.update(omitted_sound_operations=[selected],omissions=omissions,delivery_policy='audio_first')
        save(root/'result.json',report)
        return result
    worker=ProductionWorker(repo,store,{'story':StoryStage(StoryService()),'sound_plan':SoundBridgeStage(BridgeModel()),
        'audio':AudioStage(store.root.parent,store.root.parent,runner=partial)})
    identifier=repo.create('alice',production_request(outputs=['audio'],sound_workflow='sound_bridge/1',audio_delivery_policy='audio_first'))
    advance(worker,repo,7)
    job=repo.get(identifier,'alice')
    assert job['status']=='succeeded',job
    refs={a['kind']:ArtifactRef.model_validate(a) for a in job['artifacts']}
    assert refs['AudioExecutionArrangement'].sha256!=refs['AudioArrangement'].sha256
    view=playback_view(job,store)
    assert view['manifest']['delivery_warnings']
    directory=tmp_path/'downloads';directory.mkdir()
    for ref in refs.values():(directory/ref.sha256).write_bytes(store.verify(ref).read_bytes())
    assert audit_downloads(job,view,directory,ffmpeg=None,report_directory=directory)['technical_media_audit']=='passed'
