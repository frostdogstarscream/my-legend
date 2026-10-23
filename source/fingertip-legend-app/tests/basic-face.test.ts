import {describe,it,expect} from 'vitest'
import {BasicFaceRuntime,faceAttachments,profileHash,type BasicFace} from '../../my-legend-stage/src/basic-face'
import profiles from '../../my-legend-production/src/my_legend_production/production/schemas/face-basic-v1.json'
import type {ActorState,LifeScript} from '../../my-legend-stage/src/life-types'
const p=profiles.profiles[0]
const state:ActorState={instance_id:'a',action:'idle',visible:true,time:0,mode:'loop',speaking:true,position:[.3,.8],facing:1,outfit_id:null}
const script={job_id:'j',source_version_id:'v',audio_sha256:'audio',timeline_sha256:'timeline',catalog_sha256:'cat',sample_rate:48000,
  actors:[{instance_id:'a',role_id:'r',actor_id:p.actor_id,actor_revision:p.actor_revision}],subtitles:[{role_id:'r',start_frame:48000,end_frame:96000}]} as LifeScript
const asset={asset_id:p.asset_id,source_filename:p.source_filename,skeleton:{sha256:p.skeleton_sha256},original:{sha256:p.original_sha256}} as any
const catalog={catalog_sha256:'cat',actors:[{actor_id:p.actor_id,revision:p.actor_revision,asset_id:p.asset_id}],skeletons:[asset]} as any
async function envelope():Promise<BasicFace>{return {schema_version:'legend_basic_face/1',profile_set:profiles,profile_sha256:await profileHash(profiles),bindings:Object.fromEntries(['job_id','source_version_id','audio_sha256','timeline_sha256','catalog_sha256'].map(k=>[k,(script as any)[k]])),diagnostics:[]}}
describe('basic face v1 safety and deterministic audio clock',()=>{
  it('mouth opens only inside own speech, respects edges and protected actions',()=>{
    expect(faceAttachments(p,script,state,52800)).toEqual({mouth1:'mouth2'})
    for(const frame of [0,47999,96000,144000])expect(faceAttachments(p,script,state,frame)).toEqual({})
    for(const frame of [48000,95999])expect(faceAttachments(p,script,state,frame)).toEqual({mouth1:'mouth1'})
    for(const action of ['lie','pick'])expect(faceAttachments(p,script,{...state,action},52800)).toEqual({})
    expect(faceAttachments(p,{...script,subtitles:script.subtitles.map(s=>({...s,role_id:'other'}))},state,52800)).toEqual({})
  })
  it('seeks and repeated paused frames do not accumulate mouth or blink state',()=>{
    for(const profile of profiles.profiles){
      const frames=[0,20160,22000,25680,52800,95000,48000,20160,0,52800]
      const reference=frames.map(f=>faceAttachments(profile,script,state,f))
      for(let i=0;i<4;i++)expect(frames.map(f=>faceAttachments(profile,script,state,f))).toEqual(reference)
    }
    expect(faceAttachments(profiles.profiles[1],script,state,20160)).toEqual({Leye1:'eyeR5',Reye1:'eyeL5'})
    expect(faceAttachments(profiles.profiles[1],script,state,25680)).toEqual({})
  })
  it('old previews stay native, corrupted hashes and mismatched provenance independently disable faces',async()=>{
    expect((await BasicFaceRuntime.prepare(script,catalog)).diagnostics).toEqual([])
    for(const mutate of [(e:BasicFace)=>e.profile_sha256='bad',(e:BasicFace)=>e.bindings.audio_sha256='other',(e:BasicFace)=>e.profile_set={...e.profile_set,algorithm_version:'future'}]){
      const e=await envelope();mutate(e);expect((await BasicFaceRuntime.prepare(script,catalog,e)).diagnostics[0].code).toBe('FACE_DISABLED')
    }
    const runtime=await BasicFaceRuntime.prepare(script,{...catalog,skeletons:[{...asset,skeleton:{sha256:'other'}}]},await envelope())
    expect(runtime.diagnostics[0].code).toBe('FACE_PROFILE_UNAVAILABLE')
  })
  it('a missing attachment falls back once, while an application exception restores native attachment and deform',async()=>{
    const missing=await BasicFaceRuntime.prepare(script,catalog,await envelope())
    const sk={findSlotIndex:()=>-1}
    for(let i=0;i<3;i++)expect(()=>missing.apply(sk,asset,'full',state,52800)).not.toThrow()
    expect(missing.diagnostics.filter(d=>d.code==='FACE_RUNTIME_FALLBACK')).toHaveLength(1)
    const native={name:'native'},slot={attachment:native,attachmentVertices:[1,2]}
    const broken={findSlotIndex:()=>0,getAttachmentBySlotIndex:()=>({}),findSlot:()=>slot,setAttachment:()=>{slot.attachment={name:'changed'};slot.attachmentVertices=[];throw Error('injected')}}
    const runtime=await BasicFaceRuntime.prepare(script,catalog,await envelope());runtime.apply(broken,asset,'full',state,52800)
    expect(slot.attachment).toBe(native);expect(slot.attachmentVertices).toEqual([1,2])
  })
})
