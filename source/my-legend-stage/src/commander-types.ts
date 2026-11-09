import {evaluateH5,type H5Preview} from './h5-types'
import type {CommanderFace,LifeCue,LifeScript} from './life-types'
export interface CommanderExpression {id:string;instance_id:string;start_frame:number;end_frame:number;enabled:boolean;channel:'eyes'|'mouth';preset:'native'|'open'|'closed'|'smile'|'squint'|'surprised'}
export interface CommanderFurnitureCue {id:string;instance_id:string;episode_id:string;scene_id:string;seat_id:string;calibration_revision:string;outfit_id:string;start_frame:number;arrive_frame:number;leave_frame:number|null;end_frame:number;enabled:boolean;source:LifeCue['source'];source_text:string;reason:string}
export interface ResolvedSeat {object_id:string;position:number[];approach:number[];exit:number[];root_offset:number[];facing:1|-1;actor_scale:number}
export interface CommanderFurnitureTrack {schema_version:'commander_furniture/1';cues:CommanderFurnitureCue[];resolved:Record<string,ResolvedSeat>}
export interface CommanderPreview extends Omit<H5Preview,'schema_version'|'expression_track'|'furniture_track'|'mouth_control'|'rule_version'> {schema_version:'spine_stage_preview/3.1'|'spine_stage_preview/3.2';rule_version:'h5_rules/1'|'spine_director/1';mouth_control:true;expression_track:CommanderExpression[];mouth_enabled:Record<string,boolean>;face_profiles:Record<string,CommanderFace|null>;outfit_actions:Record<string,Record<string,number>>;furniture_track:CommanderFurnitureTrack;upgraded_from?:string|null;directing?:{casting_confirmed:boolean;review_status:string;human_acceptance:'pending';edited_from:string|null}}

/** All wardrobe, furniture and face state derives from the same audio frame. */
export function evaluateCommander(preview:CommanderPreview,sampleFrame:number){
 const frame=Math.max(0,Math.min(preview.stage.total_frames,Math.floor((Number.isFinite(sampleFrame)?sampleFrame:0)+1e-6)))
 const segment=preview.scene_track.segments.find(s=>s.start_frame<=frame&&frame<s.end_frame)||preview.scene_track.segments[preview.scene_track.segments.length-1]!
 const furniture=preview.furniture_track.cues.filter(c=>c.enabled&&c.episode_id===segment.episode_id&&c.scene_id===segment.scene_id).sort((a,b)=>a.start_frame-b.start_frame||a.id.localeCompare(b.id))
 const cues=[...preview.stage.cues]
 for(const cue of furniture){if(cue.leave_frame!==null&&cue.end_frame<=frame){const seat=preview.furniture_track.resolved[cue.id];cues.push({id:cue.id+'_exit',type:'actor.place',instance_id:cue.instance_id,start_frame:cue.end_frame,end_frame:cue.end_frame,enabled:true,params:{position:seat.exit,facing:seat.exit[0]>=seat.approach[0]?1:-1,visible:true},source:null,source_text:'',rule_id:'manual',reason:'家具轨道结束位置'})}}
 const actors=preview.stage.actors.map(a=>{const changes=cues.filter(c=>c.enabled&&c.instance_id===a.instance_id&&c.type==='actor.change_outfit'&&segment.start_frame<=c.start_frame&&c.start_frame<=frame).sort((a,b)=>a.start_frame-b.start_frame);const oid=changes[changes.length-1]?.params.outfit_id||a.initial_outfit_id!;return {...a,actions:preview.outfit_actions[oid]||a.actions}})
 const script:LifeScript={...preview.stage,actors,cues},base={...preview,stage:script,expression_track:[]} as unknown as H5Preview
 const result=evaluateH5(base,frame)
 for(const actor of result.state.actors){
  const cue=furniture.find(c=>c.instance_id===actor.instance_id&&c.start_frame<=frame&&(frame<c.end_frame||c.leave_frame===null&&frame===script.total_frames&&c.end_frame===frame))
  if(cue){
   const seat=preview.furniture_track.resolved[cue.id],atStart=evaluateH5(base,cue.start_frame).state.actors.find(a=>a.instance_id===actor.instance_id)!
   actor.visible=true;actor.facing=seat.facing;actor.outfit_id=cue.outfit_id
   const move=(from:number[],to:number[],a:number,b:number)=>{const t=(frame-a)/(b-a);actor.position=from.map((v,i)=>v+(to[i]-v)*t);actor.action='walk';actor.mode='loop';actor.time=(frame-a)/script.sample_rate;if(Math.abs(to[0]-from[0])>1e-8)actor.facing=to[0]<from[0]?-1:1}
   if(frame<cue.arrive_frame)move(atStart.position,seat.approach,cue.start_frame,cue.arrive_frame)
   else if(cue.leave_frame===null||frame<cue.leave_frame){actor.position=seat.position.map((v,i)=>v+seat.root_offset[i]*seat.actor_scale/(i?540:960));actor.action='sit';actor.mode='loop';actor.time=(frame-cue.arrive_frame)/script.sample_rate;actor.seat_id=cue.seat_id;actor.seat_object_id=seat.object_id}
   else{const standEnd=cue.leave_frame+Math.ceil(.35*script.sample_rate);if(frame<standEnd){actor.position=[...seat.approach];actor.action='idle';actor.mode='loop';actor.time=(frame-cue.leave_frame)/script.sample_rate}else move(seat.approach,seat.exit,standEnd,cue.end_frame)}
  }
  const profile=preview.face_profiles[actor.instance_id],expressions=preview.expression_track.filter(c=>c.enabled&&c.instance_id===actor.instance_id&&c.start_frame<=frame&&frame<c.end_frame)
  actor.expression=expressions.find(c=>c.channel==='eyes')?.preset||'native';actor.mouth='native'
  if(profile&&actor.visible){
   if(preview.mouth_enabled[actor.instance_id]&&profile.mouth.actions.includes(actor.action)){
    actor.mouth='closed';const binding=script.actors.find(a=>a.instance_id===actor.instance_id)!,line=script.subtitles.find(s=>s.role_id===binding.role_id&&s.start_frame<=frame&&frame<s.end_frame)
    if(line){const m=profile.mouth,elapsed=(frame-line.start_frame)/script.sample_rate*1000,remaining=(line.end_frame-frame)/script.sample_rate*1000,phase=elapsed%m.period_ms;if(elapsed>=m.lead_ms&&remaining>m.tail_ms&&phase>=m.open_start_ms&&phase<m.open_end_ms)actor.mouth='open'}
   }
   const manual=expressions.find(c=>c.channel==='mouth');if(manual)actor.mouth=manual.preset as 'native'|'open'|'closed'
  }
 }
 return result
}
export function evaluatePerformance(preview:H5Preview|CommanderPreview,frame:number){return preview.schema_version!=='spine_stage_preview/3.0'?evaluateCommander(preview,frame):evaluateH5(preview,frame)}
