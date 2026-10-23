import type {AssetRef} from './types'
import type {ActorState,LifeScript,LifeState} from './life-types'

export interface SceneRef {catalog_sha256:string;scene_id:string}
export interface SceneObject {
  id:string;image:AssetRef;source_name:string;position:[number,number];width:number;height:number;
  anchor:[number,number];layer:'wall'|'floor'|'back'|'depth'|'front';depth_y:number;
  repeat:boolean;tile_scale:number;tint:string;
  source_size:[number,number];
}
export interface SceneSeat {
  seat_id:string;object_id:string;label:string;actor_id:string;actor_revision:string;outfit_id:null;
  approach:[number,number];position:[number,number];exit:[number,number];sit_offset:[number,number];facing:1|-1;
}
export interface SceneDefinition {
  scene_id:string;display_name:string;resource_group:string;name_evidence:string;
  description?:string;
  width:960;height:540;layout_kind:'authored_performance_sample';actor_scale:number;
  activity_area:number[];objects:SceneObject[];seats:SceneSeat[];
}
export interface SceneTheme {
  source_key:string;name:string;name_found:boolean;resources_located:boolean;pairing_verified:boolean;
  scene_review:string;candidate_resource_groups:string[];note:string;
}
export interface SceneCatalog {schema_version:'legend_scene_catalog/1';catalog_sha256:string;scenes:SceneDefinition[];themes:SceneTheme[];notes:string[]}
export interface FurnitureCue {id:string;instance_id:string;seat_id:string;start_frame:number;arrive_frame:number;leave_frame:number;end_frame:number}
export interface FurnitureTrack {schema_version:'legend_furniture_track/1';cues:FurnitureCue[]}
export const FURNITURE_STAND_SECONDS=.35

/** A separate absolute-time layer. Its producer validates exclusivity with body/direction tracks. */
export function evaluateFurniture(state:LifeState,script:LifeScript,scene?:SceneDefinition,track?:FurnitureTrack):LifeState {
  if(!track)return state
  if(!scene||script.usage!=='technical_coverage')throw Error('家具互动只允许已选场景的技术样例')
  const frame=state.sample_frame
  const actors=state.actors.map<ActorState>(original=>{
    const cue=track.cues.find(c=>c.instance_id===original.instance_id)
    if(!cue)return original
    const seat=scene.seats.find(s=>s.seat_id===cue.seat_id),binding=script.actors.find(a=>a.instance_id===original.instance_id)
    if(!seat||!binding||binding.actor_id!==seat.actor_id||binding.actor_revision!==seat.actor_revision||original.outfit_id!==seat.outfit_id)throw Error('家具座位尚未校准此演员版本')
    const actor={...original,position:[...seat.approach],visible:true,speaking:false,action:'idle',mode:'loop' as const,time:frame/script.sample_rate,facing:seat.facing}
    const move=(from:number[],to:number[],start:number,end:number)=>{
      const p=Math.max(0,Math.min(1,(frame-start)/(end-start)))
      actor.position=from.map((v,i)=>v+(to[i]-v)*p)
      actor.action='walk';actor.time=(frame-start)/script.sample_rate
      actor.facing=to[0]<from[0]?-1:1
    }
    if(frame<cue.start_frame)return actor
    if(frame<cue.arrive_frame){move(seat.approach,seat.position,cue.start_frame,cue.arrive_frame);return actor}
    // Native sit is a seated idle loop, not a sit-down/stand-up transition.
    // Switch poses at exact boundaries; never lift a seated pose through the furniture.
    const standEnd=cue.leave_frame+Math.ceil(FURNITURE_STAND_SECONDS*script.sample_rate)
    if(frame<cue.leave_frame)return {...actor,position:[seat.position[0]+seat.sit_offset[0]/960,seat.position[1]+seat.sit_offset[1]/540],
      action:'sit',mode:'loop',time:(frame-cue.arrive_frame)/script.sample_rate,seat_id:seat.seat_id}
    if(frame<standEnd)return {...actor,position:[...seat.position],time:(frame-cue.leave_frame)/script.sample_rate}
    if(frame<cue.end_frame){move(seat.position,seat.exit,standEnd,cue.end_frame);return actor}
    return {...actor,position:[...seat.exit],facing:seat.exit[0]<seat.position[0]?-1:1}
  })
  const target=actors.find(a=>a.instance_id===state.camera.target_instance_id)
  return {...state,actors,camera:target?{...state.camera,x:target.position[0]}:state.camera}
}

export function furnitureSample(script:LifeScript,scene:SceneDefinition):{stage:LifeScript;track:FurnitureTrack} {
  const seat=scene.seats[0],actor=script.actors.find(a=>a.actor_id===seat?.actor_id&&a.actor_revision===seat.actor_revision&&a.initial_outfit_id===seat.outfit_id)
  if(!seat||!actor||!['idle','walk','sit'].every(a=>actor.actions[a]>0))throw Error('此样例需要已校准的 HK416 演员')
  const sr=script.sample_rate,arrive=2*sr,leave=Math.max(6*sr,arrive+Math.ceil(actor.actions.sit*sr)),end=leave+Math.ceil(FURNITURE_STAND_SECONDS*sr)+2*sr
  if(end>script.total_frames)throw Error('样例时长不足以完整演示坐姿与离开')
  return {stage:{...script,usage:'technical_coverage',actors:[{...actor,position:[...seat.approach],visible:true,facing:seat.facing}],cues:[]},
    track:{schema_version:'legend_furniture_track/1',cues:[{id:'furniture_'+scene.scene_id,instance_id:actor.instance_id,seat_id:seat.seat_id,start_frame:Math.round(sr/2),arrive_frame:arrive,leave_frame:leave,end_frame:end}]}}
}

/** Standalone preview only. Story scene changes retain the existing story tracks. */
export function sceneSample(script:LifeScript,scene:SceneDefinition):{stage:LifeScript;track?:FurnitureTrack} {
  if(scene.seats.length)return furnitureSample(script,scene)
  const actor=script.actors.find(a=>a.actor_id==='hk416_dorm'&&a.actions.idle>0&&a.actions.walk>0)
    ||script.actors.find(a=>a.actions.idle>0&&a.actions.walk>0)
  if(!actor)throw Error('此场景样例需要支持站立和行走的演员')
  const sr=script.sample_rate,end=Math.min(script.total_frames,Math.round(4.5*sr))
  if(end<=sr/2)throw Error('场景样例时长不足')
  return {stage:{...script,usage:'technical_coverage',subtitles:[],
    actors:[{...actor,position:[.35,.825],visible:true,facing:1}],
    cues:[{id:'scene_walk_'+scene.scene_id,type:'actor.move_to',instance_id:actor.instance_id,
      start_frame:Math.round(sr/2),end_frame:end,enabled:true,params:{to:[.65,.825],visible_after:true},
      source:null,source_text:'固定场景站立与行走预览',rule_id:'manual',reason:'独立无声场景样例'}]}}
}

export function sceneObjectDepth(item:SceneObject,index=0):number {
  return item.layer==='depth'?item.depth_y*10+index/100:({wall:-30000,floor:-20000,back:-10000,front:20000}[item.layer]+index)
}
