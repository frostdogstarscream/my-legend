"""Expand verified original-line native templates into explicit sound operations."""
from copy import deepcopy
from hashlib import sha256

from ..core import require
from .audio_arrangement import source_key
from .compound_audio import AUDIBLE_MODES


def expand_compound_script(script, mapping, plan):
    choices={source_key(e.source):e for e in plan.events}
    templates={e['block_id']:e for e in script['events']}
    events,operations=[] ,[]
    for coverage in mapping['coverage']:
        source=coverage['source']
        key=tuple(source[k] for k in ('episode_id','scene_id','line_id'))
        require(key in choices and len(coverage['target_block_ids'])==1,'COMPOUND_TEMPLATE_MAPPING_INVALID')
        template=templates[coverage['target_block_ids'][0]]
        event=choices[key]
        require(template['text']==event.text,'COMPOUND_TEMPLATE_TEXT_CHANGED')
        for choice in event.operations:
            native=deepcopy(template)
            block='op_'+sha256((plan.source_version_id+'\0'+choice.operation_id).encode()).hexdigest()[:32]
            native.update(block_id=block,text=choice.cue_text,timing=[])
            native.pop('duration_ms',None)
            if choice.mode=='speech':
                require(template['kind']=='speech' and choice.cue_text==event.text,'COMPOUND_SPEECH_TEMPLATE_INVALID')
            elif choice.mode in AUDIBLE_MODES:
                require(event.kind in ('sfx','music'),'COMPOUND_AUDIO_KIND_INVALID')
                # New music generation is still outside the native capability profile.
                require(event.kind!='music','MUSIC_MEDIA_REQUIRED')
                native.update(kind='ambience' if choice.mode=='background' else 'sfx',role_id=None,performance={},media={})
                native['sound']['production_description']=choice.cue_text
            else:
                native.update(kind='action',duration_ms=0,role_id=None,performance={},media={},sound={})
            events.append(native)
            operations.append(dict(operation_id=choice.operation_id,source=event.source.model_dump(),
                source_kind=event.kind,source_text=event.text,cue_text=choice.cue_text,mode=choice.mode,block_id=block))
    require(len({e['block_id'] for e in events})==len(events),'COMPOUND_BLOCK_COLLISION')
    result=deepcopy(script);result['events']=events
    return result,operations
