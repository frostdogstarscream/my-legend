import {describe,it,expect} from 'vitest'
import {evaluateStage,technicalCoverage,type LifeCue,type LifeScript} from '../../my-legend-stage/src/life-types'

function script():LifeScript{return {schema_version:'legend_stage_script/0.3',job_id:'j',source_version_id:'v',audio_sha256:'a',timeline_sha256:'t',catalog_sha256:'c',rule_version:'zh_life_keywords/1',
  sample_rate:8000,total_frames:80000,usage:'story_preview',actors:[{instance_id:'a',role_id:'role-a',name:'甲',actor_id:'commander',actor_revision:'r',initial_outfit_id:'uniform',position:[.2,.8],facing:1,visible:true,actions:{idle:1,walk:1,sit:1,pick:1}}],
  subtitles:[{source:{episode_id:'e',scene_id:'s',line_id:'l'},role_id:'role-a',speaker:'甲',text:'你好',start_frame:16000,end_frame:24000}],cues:[]}}
const cue=(type:LifeCue['type'],start:number,end:number,params:LifeCue['params'],id='cue'):LifeCue=>({id,type,instance_id:'a',start_frame:start,end_frame:end,params,enabled:true,source:null,source_text:'',rule_id:'manual',reason:'test'})

describe('life-stage deterministic sampling',()=>{
  it('reconstructs movement, outfit and speech after 24 forward/backward seeks without mutation',()=>{
    const s=script();s.cues=[cue('actor.move_to',8000,24000,{to:[.6,.8],visible_after:true}),cue('actor.change_outfit',16000,16000,{outfit_id:'summer'},'outfit')]
    const before=JSON.stringify(s),reference=evaluateStage(s,20000)
    for(const f of [0,8000,15999,16000,20000,24000,40000,80000,60000,30000,20000,1,16001,23999,9000,0,79999,8000,7000,50000,16000,20000,0,80000]){
      evaluateStage(s,f);expect(evaluateStage(s,20000)).toEqual(reference)
    }
    expect(reference.actors[0].position[0]).toBeCloseTo(.5)
    expect(reference.actors[0].outfit_id).toBe('summer');expect(reference.actors[0].speaking).toBe(true)
    expect(evaluateStage(s,15999).actors[0].outfit_id).toBe('uniform')
    expect(evaluateStage(s,24000).actors[0]).toMatchObject({position:[.6,.8],action:'idle',speaking:false})
    expect(JSON.stringify(s)).toBe(before)
  })
  it('distinguishes once, hold and loop and returns to idle at exact end',()=>{
    const s=script()
    for(const mode of ['once','hold','loop'] as const){
      s.cues=[cue('body.play',8000,40000,{action:'pick',mode})]
      const state=evaluateStage(s,24000).actors[0]
      expect(state.action).toBe(mode==='once'?'idle':'pick')
      if(mode==='hold')expect(state.time).toBe(1)
      if(mode==='loop')expect(state.time).toBe(2)
      expect(evaluateStage(s,40000).actors[0].action).toBe('idle')
    }
  })
  it('applies instant outfit/place before same-frame motion, keeps exit hidden and restores on seek',()=>{
    const s=script();s.cues=[cue('actor.move_to',8000,16000,{to:[.9,.8],visible_after:false},'move'),
      cue('actor.place',8000,8000,{position:[.06,.8],facing:1,visible:true},'place'),cue('actor.change_outfit',8000,8000,{outfit_id:'summer'},'outfit')]
    expect(evaluateStage(s,8000).actors[0]).toMatchObject({position:[.06,.8],outfit_id:'summer',action:'walk',visible:true})
    expect(evaluateStage(s,16000).actors[0].visible).toBe(false)
    expect(evaluateStage(s,7999).actors[0]).toMatchObject({position:[.2,.8],visible:true,outfit_id:'uniform'})
  })
  it('restores full camera and disregards disabled cues at boundaries',()=>{
    const s=script();s.cues=[cue('camera.focus',8000,16000,{target_instance_id:'a'}),{...cue('actor.change_outfit',0,0,{outfit_id:'summer'},'disabled'),enabled:false}]
    expect(evaluateStage(s,8000).camera).toEqual({target_instance_id:'a',zoom:1.08,x:.2})
    expect(evaluateStage(s,16000).camera).toEqual({target_instance_id:null,zoom:1,x:.5})
    expect(evaluateStage(s,10000).actors[0].outfit_id).toBe('uniform')
    expect(evaluateStage(s,-10).sample_frame).toBe(0)
    expect(evaluateStage(s,Infinity).sample_frame).toBe(0)
    expect(evaluateStage(s,90000).sample_frame).toBe(s.total_frames)
  })
  it('marks coverage as technical and preserves actual subtitles and provenance',()=>{
    const s=script();const result=technicalCoverage(s,{outfits:[]} as any)
    expect(result.usage).toBe('technical_coverage');expect(result.audio_sha256).toBe(s.audio_sha256)
    expect(result.subtitles).toEqual(s.subtitles);expect(s.cues).toEqual([])
    expect(result.cues.every(c=>c.source===null&&c.rule_id==='manual'&&c.end_frame<=s.total_frames)).toBe(true)
  })
})
