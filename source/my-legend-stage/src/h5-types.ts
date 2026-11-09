import {evaluateStage,type LifeBinding,type LifeCatalog,type LifeCue,type LifePreview,type LifeScript,type LifeState} from './life-types'
import type {SceneRef,SceneDefinition} from './scene-types'
import type {StageCue} from './types'

export interface SceneActor {instance_id:string;position:number[];facing:1|-1;visible:boolean}
export interface SceneBinding {episode_id:string;scene_id:string;scene:SceneRef;actors:SceneActor[];confirmed:boolean;note:string}
export interface SourceScene {episode_id:string;scene_id:string;title:string;location:string;start_frame:number|null;end_frame:number|null;source_role_ids:string[]}
export interface SceneSegment extends SceneBinding {title:string;location:string;start_frame:number;end_frame:number;source_role_ids:string[]}
export interface H5Context {
  schema_version:'h5_story_context/1';profile:'life_v2';job_id:string;title:string;source_version_id:string;catalog_sha256:string;
  audio:LifePreview['audio'];timeline:LifePreview['timeline'];script:LifePreview['script'];sample_rate:number;total_frames:number;
  roles:{role_id:string;name:string}[];scenes:SourceScene[];diagnostics:{code:string;message:string;scene_id?:string}[];max_visual_actors:3;
  scene_catalog_sha256?:string;
}
export interface CoverageItem {
  presentation?:'direct'|'adapted'|'audio_led';intent?:string;simplification?:string;
  id:string;source:StageCue['source'];text:string;word:string;action:string;start_frame:number;end_frame:number;
  status:'compiled'|'manual'|'needs_correction'|'unsupported'|'omitted'|'not_executed';cue_ids:string[];message:string;
  instance_id:string|null;target:{kind:'actor'|'object';id:string}|null;
}
export interface CoverageDecision {coverage_id:string;resolution:'manual'|'omitted';cue_ids:string[];note:string}
export interface ExpressionCue {id:string;instance_id:string;start_frame:number;end_frame:number;preset:'native'|'open'|'closed'|'talk';enabled:boolean}
export interface H5Preview extends Omit<LifePreview,'schema_version'|'profile'> {
  visual_policy?:'asset_stage/1';capability_sha256?:string;
  interpretation_notes?:{code:string;message:string;scene_id?:string;source?:StageCue['source'];presentation?:string}[];
  schema_version:'spine_stage_preview/3.0';profile:'life_v2';scene_track:{schema_version:'legend_scene_track/1';segments:SceneSegment[]};
  scene_bindings:SceneBinding[];coverage:CoverageItem[];coverage_decisions:CoverageDecision[];expression_track:ExpressionCue[];
  dialogue_enabled:boolean;review_confirmed:boolean;status:'needs_attention'|'preview_ready'|'confirmed'|'confirmed_with_omissions';rule_version:'h5_rules/1';
}
export interface H5Request {
  directed_from?:string;
  profile:'life_v2';operation:'prepare'|'compile'|'validate_and_save';catalog_sha256?:string;bindings?:Record<string,string>;
  initial_outfits?:Record<string,string|null>;script?:LifeScript;scene_bindings?:SceneBinding[];
  coverage_decisions?:CoverageDecision[];expression_track?:(ExpressionCue|import('./commander-types').CommanderExpression)[];dialogue_enabled?:boolean;review_confirmed?:boolean;
  mouth_enabled?:Record<string,boolean>;furniture_track?:{schema_version:'commander_furniture/1';cues:import('./commander-types').CommanderFurnitureCue[]};upgraded_from?:string|null;
}
export const sceneKey=(ref:SceneRef)=>ref.catalog_sha256+':'+ref.scene_id
export function segmentAt(segments:SceneSegment[],frame:number):SceneSegment {
  const segment=segments.find(s=>s.start_frame<=frame&&frame<s.end_frame)||segments[segments.length-1]
  if(!segment)throw Error('作品没有可播放的分场');return segment
}
/** Scene boundaries reset state before applying only the current scene's cues. */
export function evaluateH5(preview:Pick<H5Preview,'stage'|'scene_track'|'expression_track'|'dialogue_enabled'>,sampleFrame:number):{segment:SceneSegment;state:LifeState} {
  const script=preview.stage,frame=Math.max(0,Math.min(script.total_frames,Math.floor((Number.isFinite(sampleFrame)?sampleFrame:0)+1e-6))),segment=segmentAt(preview.scene_track.segments,frame)
  const actors=script.actors.map(a=>{const initial=segment.actors.find(v=>v.instance_id===a.instance_id);if(!initial)throw Error('分场演员配置不完整');return {...a,...initial}})
  const cues=script.cues.filter(c=>segment.start_frame<=c.start_frame&&c.start_frame<segment.end_frame)
  const state=evaluateStage({...script,actors,cues},frame)
  for(const actor of state.actors){
    const moving=cues.some(c=>c.enabled&&c.instance_id===actor.instance_id&&c.type==='actor.move_to'&&c.start_frame<=frame&&frame<c.end_frame)
    const facingOverride=cues.some(c=>c.enabled&&c.instance_id===actor.instance_id&&c.type==='actor.place'&&c.start_frame<=frame)
    if(preview.dialogue_enabled&&!moving&&!facingOverride&&actor.visible){const visible=state.actors.filter(a=>a.visible);if(visible.length===2){const other=visible.find(a=>a!==actor)!;actor.facing=other.position[0]>actor.position[0]?1:-1}}
    if(actor.action==='idle'&&!cues.some(c=>c.enabled&&c.instance_id===actor.instance_id&&c.type==='body.play'&&c.start_frame<=frame&&frame<c.end_frame))actor.time=(frame-segment.start_frame)/script.sample_rate
    actor.expression=preview.expression_track.find(c=>c.enabled&&c.instance_id===actor.instance_id&&c.start_frame<=frame&&frame<c.end_frame)?.preset||'native'
  }
  return {segment,state}
}
export function defaultSceneActors(bindings:LifeBinding[],room:SceneDefinition):SceneActor[]{
  const points=bindings.length===3?room.blocking?.ensemble_slots:bindings.length===2?room.blocking?.dialogue_slots:[[.5,room.blocking?.foot_y||.825]]
  return bindings.map((a,i)=>({instance_id:a.instance_id,position:[...(points?.[i]||[.5,.825])],facing:i===0?1:-1,visible:true}))
}
