import type {LifePreview} from '../../../my-legend-stage/src/life-types'
import {checkCueRanges,checkTrackBindings,type FaceCue,type FaceTrack,type TrackBindings} from '../../../my-legend-stage/src/performance-tracks'

export interface FaceSuggestion {
  face_cue:FaceCue;
  evidence:{source:{episode_id:string;scene_id:string;line_id:string};quote:string;decision:string;confidence:number;rule_version:string;operation_id:string};
}
export interface FaceSuggestions {
  schema_version:'legend_face_suggestions/1';source_preview_sha256:string;bindings:TrackBindings;profile_sha256:string|null;
  source_script_sha256:string;engine_version:string;classifier_version:string|null;ai_provider_validation:'not_run';request_fingerprint:string;
  suggestions:FaceSuggestion[];diagnostics:{code:string;message:string;source:FaceSuggestion['evidence']['source']|null;quote:string}[];
}
const stable=(v:any):string=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}'
export function checkSuggestionContext(value:FaceSuggestions,preview:LifePreview){
  if(value.schema_version!=='legend_face_suggestions/1'||value.engine_version!=='zh_explicit_face/1'||
     value.source_preview_sha256!==preview.preview_sha256||value.profile_sha256!==preview.face?.profile_sha256||
     value.source_script_sha256!==preview.script.artifact.sha256)throw Error('建议来源或表情版本已变化，请重新生成。')
  checkTrackBindings(preview.stage,value.bindings)
}

/** Local review only. The existing save endpoint remains the final track validator. */
export function mergeFaceSuggestions(value:FaceSuggestions,preview:LifePreview,current:FaceTrack,selected:string[]):FaceTrack{
  checkSuggestionContext(value,preview)
  if(stable(current)!==stable(preview.face?.track))throw Error('已有手工轨道已修改，请先应用或撤销修改，再生成建议。')
  if(new Set(selected).size!==selected.length)throw Error('重复选择建议。')
  const cues=selected.map(id=>{const item=value.suggestions.find(s=>s.face_cue.id===id);if(!item)throw Error('建议不存在。');return item.face_cue})
  const result:FaceTrack=JSON.parse(JSON.stringify({...current,cues:[...current.cues,...cues]}))
  checkCueRanges(preview.stage,result.cues)
  for(const cue of cues){
    if(cue.type!=='emotion'||!['surprised','squint'].includes(cue.emotion||''))throw Error('建议包含未经核对的表情。')
    if(result.cues.some(c=>c.id!==cue.id&&c.enabled&&cue.enabled&&c.type==='emotion'&&c.instance_id===cue.instance_id&&Math.max(c.start_frame,cue.start_frame)<Math.min(c.end_frame,cue.end_frame)))throw Error('建议与已有表情冲突，请重新生成。')
  }
  return result
}
