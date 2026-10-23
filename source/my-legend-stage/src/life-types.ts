import type { ActorAsset, AssetRef, StageCue, StagePreview } from './types'

export interface Calibration {
  x:number; y:number; scale:number; rotation:number; flip:boolean; layer:number;
  actions:Record<string,Partial<Omit<Calibration,'actions'>>>;
}
export interface LifeSkeleton extends Omit<ActorAsset,'actor_id'|'display_name'|'version'|'idle_animation'|'mouth_control'> {
  asset_id:string; source_asset_id:string; source_filename:string; original:AssetRef; qa:'pass';
}
export interface LifeAsset {
  schema_version:'legend_actor/0.3'; actor_id:string; display_name:string; revision:string;
  asset_mode:'single_skeleton'|'commander_tripartite'; asset_id:string|null; outfit_ids:string[];
  action_map:Record<string,string>; lifecycle:'ready'; asset_qa:'pass'; mouth_control:false;
}
export interface LifeOutfit {
  schema_version:'legend_outfit/0.3'; outfit_id:string; actor_id:string; display_name:string; revision:string;
  parts:Record<'head'|'body'|'foot',string>; allowed_actions:string[];
  calibration:Record<string,Calibration>; calibration_revision:string; qa:'pass';
}
export interface LifeCatalog {
  schema_version:'spine_life_catalog/2.0'; profile:'life_v1'; catalog_sha256:string; runtime:AssetRef;
  actors:LifeAsset[]; outfits:LifeOutfit[]; skeletons:LifeSkeleton[];
}
export interface LifeBinding {
  instance_id:string; role_id:string; name:string; actor_id:string; actor_revision:string;
  initial_outfit_id:string|null; position:number[]; facing:1|-1; visible:boolean; actions:Record<string,number>;
}
export type CueType='body.play'|'actor.move_to'|'actor.place'|'actor.change_outfit'|'camera.focus'
export type PlayMode='once'|'loop'|'hold'
export interface LifeCue {
  id:string; type:CueType; instance_id:string; start_frame:number; end_frame:number; enabled:boolean;
  params:{action?:string;mode?:PlayMode;to?:number[];visible_after?:boolean;position?:number[];visible?:boolean;facing?:1|-1;outfit_id?:string;target_instance_id?:string};
  source:StageCue['source']|null; source_text:string; rule_id:string; reason:string;
}
export interface LifeScript {
  schema_version:'legend_stage_script/0.3'; job_id:string; source_version_id:string;
  audio_sha256:string; timeline_sha256:string; catalog_sha256:string; rule_version:'zh_life_keywords/1';
  sample_rate:number; total_frames:number; actors:LifeBinding[]; subtitles:StageCue[]; cues:LifeCue[];
  usage:'story_preview'|'technical_coverage';
}
export interface LifeDiagnostic {code:string;message:string;source?:StageCue['source'];text?:string;cue_id?:string}
export interface LifePreview extends Omit<StagePreview,'schema_version'|'stage'|'warnings'> {
  schema_version:'spine_stage_preview/2.0'; profile:'life_v1'; stage:LifeScript;
  roles:{role_id:string;name:string}[]; diagnostics:LifeDiagnostic[];
}
export interface ActorState {
  instance_id:string; position:number[]; facing:1|-1; visible:boolean; outfit_id:string|null;
  action:string; time:number; mode:PlayMode; speaking:boolean;
}
export interface LifeState {sample_frame:number;actors:ActorState[];camera:{target_instance_id:string|null;zoom:number;x:number}}

/** Pure absolute-frame evaluation: no accumulated clock, pose, or event state. */
export function evaluateStage(script:LifeScript,sampleFrame:number):LifeState {
  const frame=Math.min(script.total_frames,Math.max(0,Math.floor(Number.isFinite(sampleFrame)?sampleFrame:0)))
  const order=(c:LifeCue)=>c.type==='actor.change_outfit'?0:c.type==='actor.place'?1:2
  const cues=script.cues.filter(c=>c.enabled&&c.start_frame<=frame).slice().sort((a,b)=>a.start_frame-b.start_frame||order(a)-order(b)||a.id.localeCompare(b.id))
  const actors=script.actors.map(binding=>{
    const state:ActorState={instance_id:binding.instance_id,position:[...binding.position],facing:binding.facing,visible:binding.visible,
      outfit_id:binding.initial_outfit_id,action:'idle',time:frame/script.sample_rate,mode:'loop',
      speaking:script.subtitles.some(s=>s.role_id===binding.role_id&&s.start_frame<=frame&&frame<s.end_frame)}
    for(const cue of cues){
      if(cue.instance_id!==binding.instance_id)continue
      if(cue.type==='actor.change_outfit')state.outfit_id=cue.params.outfit_id!
      if(cue.type==='actor.place'){
        state.position=[...cue.params.position!];state.visible=cue.params.visible??true;state.facing=cue.params.facing??1
      }
      if(cue.type==='actor.move_to'){
        const p=Math.min(1,(frame-cue.start_frame)/(cue.end_frame-cue.start_frame)),to=cue.params.to!
        const dx=to[0]-state.position[0]
        if(Math.abs(dx)>1e-8)state.facing=dx<0?-1:1
        state.position=state.position.map((from,i)=>from+(to[i]-from)*p)
        state.visible=p<1?true:(cue.params.visible_after??true)
        if(p<1){state.action='walk';state.mode='loop';state.time=(frame-cue.start_frame)/script.sample_rate}
      }
      if(cue.type==='body.play'&&frame<cue.end_frame){
        const elapsed=(frame-cue.start_frame)/script.sample_rate,action=cue.params.action!,mode=cue.params.mode!,duration=binding.actions[action]
        if(mode!=='once'||elapsed<duration){state.action=action;state.mode=mode;state.time=mode==='loop'?elapsed:Math.min(elapsed,duration)}
      }
    }
    return state
  })
  const focus=cues.find(c=>c.type==='camera.focus'&&frame<c.end_frame)
  const target=focus?actors.find(a=>a.instance_id===focus.params.target_instance_id):undefined
  return {sample_frame:frame,actors,camera:{target_instance_id:target?.instance_id||null,zoom:target?1.08:1,x:target?.position[0]??.5}}
}

/** A separate, explicitly labelled asset demonstration; never passed off as plot reconstruction. */
export function technicalCoverage(script:LifeScript,catalog:LifeCatalog):LifeScript {
  const result=JSON.parse(JSON.stringify(script)) as LifeScript
  result.usage='technical_coverage';result.cues=[]
  const sr=result.sample_rate,end=result.total_frames
  function cue(type:CueType,actor:LifeBinding,start:number,duration:number,params:LifeCue['params']){
    const a=Math.round(start*sr),b=Math.min(end,Math.round((start+duration)*sr))
    if(a>=end||duration>0&&b<=a)return
    result.cues.push({id:'coverage_'+result.cues.length,type,instance_id:actor.instance_id,start_frame:a,end_frame:b,params,
      enabled:true,source:null,source_text:'技术覆盖脚本：验证素材能力',rule_id:'manual',reason:'技术覆盖，不代表剧情动作'})
  }
  result.actors.forEach((actor,i)=>{
    let cursor=2+i*.4
    for(const action of ['sit','pick','lie'])if(actor.actions[action]){cue('body.play',actor,cursor,3,{action,mode:action==='pick'?'once':'hold'});cursor+=4}
    cue('actor.move_to',actor,16,2,{to:[Math.min(.92,actor.position[0]+.06),actor.position[1]],visible_after:true})
    const outfit=catalog.outfits.find(o=>o.actor_id===actor.actor_id&&o.outfit_id!==actor.initial_outfit_id)
    if(outfit){cue('actor.change_outfit',actor,22,0,{outfit_id:outfit.outfit_id});cue('body.play',actor,24,3,{action:'sit',mode:'loop'})}
  })
  if(result.actors[0])cue('camera.focus',result.actors[0],28,3,{target_instance_id:result.actors[0].instance_id})
  return result
}
