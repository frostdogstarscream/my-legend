import {evaluateStage,type LifeDiagnostic,type LifeScript,type LifeState} from './life-types'

export interface TrackBindings {
  job_id:string;source_version_id:string;audio_sha256:string;timeline_sha256:string;catalog_sha256:string;
  sample_rate:number;total_frames:number;actors:{instance_id:string;role_id:string;actor_id:string;actor_revision:string}[];
}
export function trackBindings(s:LifeScript):TrackBindings{return {job_id:s.job_id,source_version_id:s.source_version_id,audio_sha256:s.audio_sha256,
  timeline_sha256:s.timeline_sha256,catalog_sha256:s.catalog_sha256,sample_rate:s.sample_rate,total_frames:s.total_frames,
  actors:s.actors.map(({instance_id,role_id,actor_id,actor_revision})=>({instance_id,role_id,actor_id,actor_revision}))}}
export interface DirectionCue {
  id:string;instance_id:string;start_frame:number;end_frame:number;enabled:boolean;
  mode:'target'|'left'|'right'|'hold'|'away'|'turn';target_instance_id:string|null;
  source:{episode_id:string;scene_id:string;line_id:string}|null;source_text:string;rule_id:'manual'|'zh_dialogue/1';
}
export interface DirectionTrack {schema_version:'legend_dialogue_track/1';algorithm_version:'dialogue_facing/1';bindings:TrackBindings;cues:DirectionCue[]}
export type Emotion='neutral'|'squint'|'surprised'
export interface FaceCue {id:string;instance_id:string;type:'emotion'|'blink';start_frame:number;end_frame:number;enabled:boolean;emotion:Emotion|null}
export interface FaceTrack {schema_version:'legend_face_track/1';bindings:TrackBindings;profile_sha256:string;cues:FaceCue[]}
const stable=(v:any):string=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}'
export function checkTrackBindings(script:LifeScript,bindings:TrackBindings){if(stable(bindings)!==stable(trackBindings(script)))throw Error('轨道与演员、素材或音频版本不一致')}
export function emptyDirectionTrack(script:LifeScript):DirectionTrack{return {schema_version:'legend_dialogue_track/1',algorithm_version:'dialogue_facing/1',bindings:trackBindings(script),cues:[]}}
export function emptyFaceTrack(script:LifeScript,hash:string):FaceTrack{return {schema_version:'legend_face_track/1',bindings:trackBindings(script),profile_sha256:hash,cues:[]}}
export function checkCueRanges(script:LifeScript,cues:(FaceCue|DirectionCue)[]){
  const seen=new Set<string>()
  for(const c of cues){
    if(typeof c.id!=='string'||!c.id||seen.has(c.id)||!script.actors.some(a=>a.instance_id===c.instance_id)||typeof c.enabled!=='boolean'||
      !Number.isSafeInteger(c.start_frame)||!Number.isSafeInteger(c.end_frame)||c.start_frame<0||c.start_frame>=c.end_frame||c.end_frame>script.total_frames)throw Error('轨道指令 ID、演员、开关或时间无效')
    seen.add(c.id)
  }
}
export function prepareDirection(script:LifeScript,track?:DirectionTrack):{track?:DirectionTrack;diagnostics:LifeDiagnostic[]}{
  if(!track)return {diagnostics:[]}
  try{
    if(track.schema_version!=='legend_dialogue_track/1'||track.algorithm_version!=='dialogue_facing/1'||track.cues.length>256)throw Error('不支持的对白关系轨道版本')
    checkTrackBindings(script,track.bindings);checkCueRanges(script,track.cues)
    for(const c of track.cues){
      if(!['target','away','left','right','hold','turn'].includes(c.mode)||!['manual','zh_dialogue/1'].includes(c.rule_id))throw Error('朝向模式无效')
      if(['target','away'].includes(c.mode)&&(!script.actors.some(a=>a.instance_id===c.target_instance_id)||c.target_instance_id===c.instance_id))throw Error('朝向目标无效')
      if(c.enabled&&track.cues.some(o=>o!==c&&o.enabled&&o.instance_id===c.instance_id&&o.rule_id===c.rule_id&&Math.max(o.start_frame,c.start_frame)<Math.min(o.end_frame,c.end_frame)))throw Error('同一朝向轨道区间重叠')
    }
    return {track,diagnostics:[]}
  }catch(e){return {diagnostics:[{code:'DIALOGUE_FALLBACK',message:String(e)+'；使用原有身体朝向'}]}}
}

/** Only previews carrying this versioned sidecar receive new facing behavior. */
export function evaluateDialogue(script:LifeScript,frame:number,track?:DirectionTrack):LifeState {
  const result=evaluateStage(script,frame)
  if(!track)return result
  const cache=new Map<number,LifeState>([[result.sample_frame,result]])
  const at=(f:number)=>{if(!cache.has(f))cache.set(f,evaluateStage(script,f));return cache.get(f)!}
  const direction=(iid:string,f:number):1|-1=>{
    const state=at(f),actor=state.actors.find(a=>a.instance_id===iid)!,active=track.cues.filter(c=>c.enabled&&c.instance_id===iid&&c.start_frame<=f&&f<c.end_frame)
    const manual=active.find(c=>c.rule_id==='manual'),automatic=active.find(c=>c.rule_id!=='manual')
    const moving=script.cues.some(c=>c.enabled&&c.instance_id===iid&&c.type==='actor.move_to'&&c.start_frame<=f&&f<c.end_frame)
    const cue=manual||(!moving?automatic:undefined)
    if(cue){
      if(cue.mode==='left')return -1
      if(cue.mode==='right')return 1
      if(cue.mode==='hold'||cue.mode==='turn'){
        const previous=cue.start_frame===0?baseline(iid,0):direction(iid,cue.start_frame-1)
        return (cue.mode==='turn'?-previous:previous) as 1|-1
      }
      const target=state.actors.find(a=>a.instance_id===cue.target_instance_id&&a.visible)
      if(target&&Math.abs(target.position[0]-actor.position[0])>1e-6)return ((target.position[0]>actor.position[0]?1:-1)*(cue.mode==='away'?-1:1)) as 1|-1
      return actor.facing
    }
    return baseline(iid,f)
  }
  const baseline=(iid:string,f:number):1|-1=>{
    const state=at(f),actor=state.actors.find(a=>a.instance_id===iid)!
    if(script.cues.some(c=>c.enabled&&c.instance_id===iid&&c.type==='actor.move_to'&&c.start_frame<=f&&f<c.end_frame))return actor.facing
    const placed=script.cues.some(c=>c.enabled&&c.instance_id===iid&&c.type==='actor.place'&&c.rule_id==='manual'&&c.start_frame<=f)
    if(placed)return actor.facing
    const visible=state.actors.filter(a=>a.visible)
    if(actor.visible&&visible.length===2){const target=visible.find(a=>a.instance_id!==iid)!;if(Math.abs(target.position[0]-actor.position[0])>1e-6)return target.position[0]>actor.position[0]?1:-1}
    return actor.facing
  }
  // Resolve before assigning, so another actor's evaluation cannot observe partial results.
  const facings=result.actors.map(a=>direction(a.instance_id,result.sample_frame))
  result.actors.forEach((a,i)=>a.facing=facings[i]);return result
}
