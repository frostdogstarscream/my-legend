import {describe,it,expect} from 'vitest'
import {evaluateStage,type LifeScript} from '../../my-legend-stage/src/life-types'
import {evaluateFurniture,furnitureSample,sceneSample,sceneObjectDepth,type SceneDefinition} from '../../my-legend-stage/src/scene-types'

function fixture(){
  const stage:LifeScript={schema_version:'legend_stage_script/0.3',job_id:'j',source_version_id:'s',audio_sha256:'a',timeline_sha256:'t',catalog_sha256:'c',rule_version:'zh_life_keywords/1',sample_rate:8000,total_frames:96000,usage:'story_preview',subtitles:[],cues:[],
    actors:[{instance_id:'hk',role_id:'r',name:'HK416',actor_id:'hk416_dorm',actor_revision:'r1',initial_outfit_id:null,position:[.5,.825],facing:1,visible:true,actions:{idle:1,walk:1,sit:1.25}}]}
  const scene:SceneDefinition={scene_id:'loft',display_name:'LOFT',resource_group:'loft2016',name_evidence:'fixture',width:960,height:540,actor_scale:.72,layout_kind:'authored_performance_sample',activity_area:[.05,.7,.95,.9],objects:[],
    seats:[{seat_id:'sofa',object_id:'sofa',label:'沙发',actor_id:'hk416_dorm',actor_revision:'r1',outfit_id:null,approach:[.4,.825],position:[.7,.825],exit:[.3,.825],sit_offset:[0,-45],facing:1}]}
  return {stage,scene}
}
describe('room and furniture absolute-time sampling',()=>{
  it('previews uncalibrated rooms with only standing/walking and preserves the source script',()=>{
    const {stage,scene}=fixture(),before=JSON.stringify(stage);scene.seats=[]
    const sample=sceneSample(stage,scene)
    expect(sample.track).toBeUndefined();expect(JSON.stringify(stage)).toBe(before)
    expect(sample.stage.usage).toBe('technical_coverage');expect(sample.stage.subtitles).toEqual([])
    expect(evaluateStage(sample.stage,0).actors[0]).toMatchObject({action:'idle',position:[.35,.825]})
    expect(evaluateStage(sample.stage,20000).actors[0]).toMatchObject({action:'walk',position:[.5,.825]})
    expect(evaluateStage(sample.stage,36000).actors[0]).toMatchObject({action:'idle',position:[.65,.825]})
    expect(()=>furnitureSample(stage,scene)).toThrow('已校准')
  })
  it('retains calibrated seat previews without changing their track',()=>{
    const {stage,scene}=fixture();expect(sceneSample(stage,scene)).toEqual(furnitureSample(stage,scene))
  })
  it('keeps story provenance immutable and explicitly marks its independent sample',()=>{
    const {stage,scene}=fixture(),before=JSON.stringify(stage),sample=furnitureSample(stage,scene)
    expect(JSON.stringify(stage)).toBe(before);expect(sample.stage.usage).toBe('technical_coverage')
    for(const k of ['audio_sha256','timeline_sha256','source_version_id','subtitles'] as const)expect(sample.stage[k]).toEqual(stage[k])
    expect(sample.track.cues[0].end_frame).toBe(66800)
  })
  it('walks, holds a seated native loop aligned to the seat, switches to idle, then walks away',()=>{
    const {stage,scene}=fixture(),s=furnitureSample(stage,scene)
    const at=(t:number)=>evaluateFurniture(evaluateStage(s.stage,t*8000),s.stage,scene,s.track).actors[0]
    expect(at(0)).toMatchObject({position:[.4,.825],action:'idle'})
    expect(at(1).position[0]).toBeCloseTo(.5);expect(at(1).action).toBe('walk')
    expect(at(2)).toMatchObject({action:'sit',time:0})
    expect(at(2.625).position[1]).toBeCloseTo(.825-45/540)
    expect(at(5)).toMatchObject({action:'sit',mode:'loop',time:3,seat_id:'sofa'})
    expect(at(6.15)).toMatchObject({action:'idle',time:.15,facing:1,position:[.7,.825]})
    expect(at(6.35)).toMatchObject({action:'walk',position:[.7,.825],facing:-1})
    expect(at(8.35)).toMatchObject({action:'idle',position:[.3,.825],facing:-1})
  })
  it('reconstructs every phase after repeated reverse seeks without mutating a prior state',()=>{
    const {stage,scene}=fixture(),s=furnitureSample(stage,scene),before=JSON.stringify(s)
    const frames=[0,4000,15999,16000,26000,47999,48000,50000,50799,50800,66799,66800,96000]
    const expected=frames.map(f=>evaluateFurniture(evaluateStage(s.stage,f),s.stage,scene,s.track))
    for(let n=0;n<20;n++)for(const i of [...frames.keys()].reverse()){
      const raw=evaluateStage(s.stage,frames[i]),copy=JSON.stringify(raw)
      expect(evaluateFurniture(raw,s.stage,scene,s.track)).toEqual(expected[i]);expect(JSON.stringify(raw)).toBe(copy)
    }
    expect(JSON.stringify(s)).toBe(before)
  })
  it('does not affect legacy scenes or unrelated actors',()=>{
    const {stage,scene}=fixture(),original=evaluateStage(stage,1000)
    expect(evaluateFurniture(original,stage)).toBe(original)
    const s=furnitureSample(stage,scene);s.stage.actors.push({...stage.actors[0],instance_id:'other',position:[.2,.825]})
    const raw=evaluateStage(s.stage,40000)
    expect(evaluateFurniture(raw,s.stage,scene,s.track).actors[1]).toBe(raw.actors[1])
  })
  it.each(['revision','actor','outfit','sit','duration'] as const)('rejects unsupported %s before authoring a sample',change=>{
    const {stage,scene}=fixture()
    if(change==='revision')stage.actors[0].actor_revision='other'
    if(change==='actor')stage.actors[0].actor_id='other'
    if(change==='outfit')stage.actors[0].initial_outfit_id='other'
    if(change==='sit')delete stage.actors[0].actions.sit
    if(change==='duration')stage.total_frames=8000
    expect(()=>furnitureSample(stage,scene)).toThrow()
  })
  it('rejects scene mismatch at rendering time and keeps layer ordering explicit',()=>{
    const {stage,scene}=fixture(),s=furnitureSample(stage,scene),state=evaluateStage(s.stage,40000)
    expect(()=>evaluateFurniture(state,s.stage,undefined,s.track)).toThrow()
    scene.seats[0].actor_revision='changed'
    expect(()=>evaluateFurniture(state,s.stage,scene,s.track)).toThrow()
    const zs=['wall','floor','back','depth','front'].map(layer=>sceneObjectDepth({layer,depth_y:420} as any))
    expect(zs).toEqual([...zs].sort((a,b)=>a-b))
  })
  it('focuses the furniture actor at its evaluated position, not its original binding',()=>{
    const {stage,scene}=fixture(),s=furnitureSample(stage,scene),state=evaluateStage(s.stage,40000)
    state.camera={target_instance_id:'hk',zoom:1.08,x:.4}
    const final=evaluateFurniture(state,s.stage,scene,s.track)
    expect(final.camera.x).toBe(.7);expect(state.camera.x).toBe(.4)
  })
})
