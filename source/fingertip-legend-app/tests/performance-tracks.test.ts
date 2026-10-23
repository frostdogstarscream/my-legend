import {describe,it,expect} from 'vitest'
import {evaluateStage,type LifeScript} from '../../my-legend-stage/src/life-types'
import {evaluateDialogue,emptyDirectionTrack,emptyFaceTrack,prepareDirection,type DirectionCue} from '../../my-legend-stage/src/performance-tracks'
import {expressionAttachments,type FaceProfile} from '../../my-legend-stage/src/basic-face'
import profileSet from '../../my-legend-production/src/my_legend_production/production/schemas/face-expressions-v1.json'
function script():LifeScript{return {schema_version:'legend_stage_script/0.3',rule_version:'zh_life_keywords/1',job_id:'j',source_version_id:'v',audio_sha256:'a',timeline_sha256:'t',catalog_sha256:'c',sample_rate:48000,total_frames:480000,usage:'story_preview',subtitles:[],cues:[],
  actors:['a','b'].map((id,i)=>({instance_id:id,role_id:id,name:id,actor_id:i?'hk416_dorm':'ak15mod_dorm',actor_revision:'r',initial_outfit_id:null,position:[i?.7:.3,.8],facing:1,visible:true,actions:{idle:2,walk:2,lie:2,pick:2}}))}}
const direction=(mode:DirectionCue['mode'],start=48000,end=144000,id='d',target:string|null=null):DirectionCue=>({id,instance_id:'a',start_frame:start,end_frame:end,enabled:true,mode,target_instance_id:target,source:null,source_text:'',rule_id:'manual'})
describe('versioned dialogue direction',()=>{
  it('preserves old previews and defaults two visible actors toward one another',()=>{
    const s=script(),t=emptyDirectionTrack(s)
    expect(evaluateDialogue(s,0)).toEqual(evaluateStage(s,0))
    expect(evaluateDialogue(s,0,t).actors.map(a=>a.facing)).toEqual([1,-1])
    s.actors[0].visible=false;expect(evaluateDialogue(s,0,t).actors[1].facing).toBe(1)
    s.actors[0].visible=true;s.actors[0].position=[.8,.8];expect(evaluateDialogue(s,0,t).actors.map(a=>a.facing)).toEqual([-1,1])
  })
  it('supports target, left/right, away, turn and frozen hold on arbitrary seek order',()=>{
    const s=script(),t=emptyDirectionTrack(s)
    for(const [mode,target,expected] of [['target','b',1],['away','b',-1],['left',null,-1],['right',null,1],['turn',null,-1]] as const){t.cues=[direction(mode,48000,144000,'d',target)];expect(evaluateDialogue(s,60000,t).actors[0].facing).toBe(expected)}
    t.cues=[direction('left',0,48000,'before'),direction('hold',48000,144000)]
    for(const f of [60000,0,47999,144000,60000,48000]){evaluateDialogue(s,f,t);expect(evaluateDialogue(s,60000,t).actors[0].facing).toBe(-1)}
    expect(evaluateDialogue(s,144000,t).actors[0].facing).toBe(1)
    t.cues=[direction('turn',0,48000,'first'),direction('turn',48000,144000,'second')]
    expect(evaluateDialogue(s,60000,t).actors[0].facing).toBe(1)
  })
  it('prioritizes manual over exit movement, movement over automatic relation, and hides exits',()=>{
    const s=script(),t=emptyDirectionTrack(s)
    s.cues=[{id:'exit',instance_id:'a',type:'actor.move_to',start_frame:48000,end_frame:144000,enabled:true,params:{to:[.06,.8],visible_after:false},source:null,source_text:'',rule_id:'manual',reason:''}]
    t.cues=[{...direction('right',0,480000),rule_id:'zh_dialogue/1'}]
    expect(evaluateDialogue(s,60000,t).actors[0].facing).toBe(-1)
    t.cues.push(direction('right',48000,96000,'manual'));expect(evaluateDialogue(s,60000,t).actors[0].facing).toBe(1)
    expect(evaluateDialogue(s,96000,t).actors[0].facing).toBe(-1)
    expect(evaluateDialogue(s,144000,t).actors[0].visible).toBe(false)
  })
  it('fails closed for stale actor bindings and duplicate direction IDs',()=>{
    const s=script(),t=emptyDirectionTrack(s);t.bindings.actors[0].actor_revision='other'
    expect(prepareDirection(s,t).diagnostics[0].code).toBe('DIALOGUE_FALLBACK')
    const v=emptyDirectionTrack(s);v.cues=[direction('left'),direction('right')]
    expect(prepareDirection(s,v).track).toBeUndefined()
  })
})
describe('expression / blink priority per real actor mapping',()=>{
  for(const profile of profileSet.profiles as FaceProfile[])it(profile.actor_id+' restores expression after manual/native/procedural blink and preserves protected poses',()=>{
    const s=script(),a=s.actors.find(a=>a.actor_id===profile.actor_id)!,state=evaluateStage(s,0).actors.find(x=>x.instance_id===a.instance_id)!
    state.instance_id=a.instance_id
    const t=emptyFaceTrack(s,'hash');t.cues=[{id:'emotion',instance_id:a.instance_id,type:'emotion',start_frame:0,end_frame:144000,enabled:true,emotion:'surprised'},
      {id:'blink',instance_id:a.instance_id,type:'blink',start_frame:48000,end_frame:54000,enabled:true,emotion:null}]
    const native=Object.fromEntries(profile.eyes!.map(e=>[e.slot,e.rest])),closed=Object.fromEntries(profile.eyes!.map(e=>[e.slot,e.closed]))
    expect(expressionAttachments(profile,s,state,48000,t,native)).toEqual(closed)
    expect(expressionAttachments(profile,s,state,54000,t,native)).toEqual(profile.emotions!.surprised!.attachments)
    expect(expressionAttachments(profile,s,state,60000,t,closed)).toEqual(closed)
    expect(expressionAttachments(profile,s,state,144000,t,native)).toEqual({})
    if(profile.blink){expect(expressionAttachments(profile,s,state,21600,t,native)).toEqual(closed);expect(expressionAttachments(profile,s,state,28800,t,native)).toEqual(profile.emotions!.surprised!.attachments)}
    for(const action of ['lie','pick'])expect(expressionAttachments(profile,s,{...state,action},48000,t,native)).toEqual({})
  })
})
