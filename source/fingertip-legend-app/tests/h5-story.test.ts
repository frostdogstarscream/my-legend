import {describe,it,expect} from 'vitest'
import {evaluateH5,segmentAt,type H5Preview} from '../../my-legend-stage/src/h5-types'
const actor=(id:string,x:number)=>({instance_id:id,role_id:id,name:id,actor_id:id,actor_revision:'r',initial_outfit_id:null,position:[x,.825],facing:1 as const,visible:true,actions:{idle:1,walk:1}})
function fixture(){
 const actors=[actor('a',.2),actor('b',.8)]
 return {stage:{schema_version:'legend_stage_script/0.3',job_id:'j',source_version_id:'v',audio_sha256:'a',timeline_sha256:'t',catalog_sha256:'c',rule_version:'zh_life_keywords/1',sample_rate:100,total_frames:300,actors,subtitles:[],usage:'story_preview',cues:[
  {id:'walk',type:'actor.move_to',instance_id:'a',start_frame:10,end_frame:80,enabled:true,params:{to:[.4,.825]},source:null,source_text:'',rule_id:'manual',reason:''},
  {id:'exit',type:'actor.move_to',instance_id:'b',start_frame:100,end_frame:150,enabled:true,params:{to:[.94,.825],visible_after:false},source:null,source_text:'',rule_id:'manual',reason:''}]},
  scene_track:{schema_version:'legend_scene_track/1',segments:[
   {episode_id:'ep',scene_id:'s1',scene:{catalog_sha256:'r',scene_id:'library'},title:'1',location:'书店',start_frame:0,end_frame:100,source_role_ids:[],confirmed:true,note:'',actors:actors.map(a=>({instance_id:a.instance_id,position:a.position,facing:a.facing,visible:a.visible}))},
   {episode_id:'ep',scene_id:'s2',scene:{catalog_sha256:'r',scene_id:'cafe'},title:'2',location:'咖啡馆',start_frame:100,end_frame:200,source_role_ids:[],confirmed:true,note:'',actors:[{instance_id:'a',position:[.6,.825],facing:-1,visible:true},{instance_id:'b',position:[.8,.825],facing:-1,visible:true}]},
   {episode_id:'ep',scene_id:'s3',scene:{catalog_sha256:'r',scene_id:'cafe'},title:'3',location:'咖啡馆',start_frame:200,end_frame:300,source_role_ids:[],confirmed:true,note:'',actors:[{instance_id:'a',position:[.2,.825],facing:1,visible:true},{instance_id:'b',position:[.8,.825],facing:-1,visible:true}]}]},expression_track:[],dialogue_enabled:false} as unknown as H5Preview
}
describe('H5 absolute scene timeline',()=>{
 it('switches at exact boundaries and resets repeated-room states',()=>{const p=fixture();expect(evaluateH5(p,99).state.actors[0].position[0]).toBe(.4);expect(evaluateH5(p,100).segment.scene.scene_id).toBe('cafe');expect(evaluateH5(p,100).state.actors[0].position[0]).toBe(.6);expect(evaluateH5(p,150).state.actors[1].visible).toBe(false);expect(evaluateH5(p,200).state.actors[1].visible).toBe(true);expect(evaluateH5(p,200).state.actors[0].position[0]).toBe(.2)})
 it('does not depend on playback history when seeking backwards',()=>{const p=fixture(),before=JSON.stringify(p),a=evaluateH5(p,45);for(const f of [250,0,199,100,300,12])evaluateH5(p,f);expect(evaluateH5(p,45)).toEqual(a);expect(JSON.stringify(p)).toBe(before)})
 it('preserves audio clock and retains final scene at end',()=>{const p=fixture();expect(evaluateH5(p,300).state.sample_frame).toBe(300);expect(segmentAt(p.scene_track.segments,300).scene_id).toBe('s3')})
 it('supports deterministic expressions and facing without modifying base data',()=>{const p=fixture();p.dialogue_enabled=true;p.expression_track=[{id:'f',instance_id:'a',start_frame:210,end_frame:220,preset:'closed',enabled:true}];expect(evaluateH5(p,215).state.actors[0].expression).toBe('closed');expect(evaluateH5(p,220).state.actors[0].expression).toBe('native');expect(evaluateH5(p,215).state.actors[1].facing).toBe(-1)})
})


it('snaps binary rounding noise at an exact audio scene boundary',()=>{
 const p=fixture();p.stage.sample_rate=48000;p.stage.total_frames=1500000;p.scene_track.segments[0].end_frame=971520;p.scene_track.segments[1].start_frame=971520;p.scene_track.segments[1].end_frame=1400000;p.scene_track.segments[2].start_frame=1400000;p.scene_track.segments[2].end_frame=1500000;
 expect(evaluateH5(p,20.24*48000).segment.scene_id).toBe('s2');expect(evaluateH5(p,20.24*48000).state.sample_frame).toBe(971520);
 expect(evaluateH5(p,971519.99).segment.scene_id).toBe('s1');expect(evaluateH5(p,NaN).state.sample_frame).toBe(0)
})
