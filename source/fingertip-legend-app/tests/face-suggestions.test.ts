import {describe,it,expect} from 'vitest'
import type {LifePreview} from '../../my-legend-stage/src/life-types'
import {trackBindings} from '../../my-legend-stage/src/performance-tracks'
import {checkSuggestionContext,mergeFaceSuggestions,type FaceSuggestions} from '../src/services/face-suggestions'
function fixture(){
  const stage={job_id:'job',source_version_id:'v',audio_sha256:'a',timeline_sha256:'t',catalog_sha256:'c',sample_rate:8000,total_frames:80000,
    actors:[{instance_id:'one',role_id:'a',actor_id:'hk',actor_revision:'r'}]}
  const bindings=trackBindings(stage as any)
  const manual={id:'manual',instance_id:'one',type:'emotion',start_frame:0,end_frame:8000,enabled:true,emotion:'squint'}
  const preview={preview_sha256:'p',stage,script:{artifact:{sha256:'s'}},face:{profile_sha256:'f',track:{schema_version:'legend_face_track/1',bindings,profile_sha256:'f',cues:[manual]}}} as LifePreview
  const value={schema_version:'legend_face_suggestions/1',source_preview_sha256:'p',bindings:structuredClone(bindings),source_script_sha256:'s',profile_sha256:'f',engine_version:'zh_explicit_face/1',
    suggestions:[{face_cue:{...manual,id:'suggested',start_frame:8000,end_frame:16000,emotion:'surprised'}}]} as FaceSuggestions
  return {preview,value}
}
describe('reviewed face suggestions',()=>{
  it('merges selected cues without mutating source, manual track or unselected proposals',()=>{
    const {preview,value}=fixture(),before=JSON.stringify({preview,value})
    const merged=mergeFaceSuggestions(value,preview,preview.face!.track!,['suggested'])
    expect(merged.cues.map(c=>c.id)).toEqual(['manual','suggested']);expect(JSON.stringify({preview,value})).toBe(before)
    expect(mergeFaceSuggestions(value,preview,preview.face!.track!,[])).toEqual(preview.face!.track)
  })
  for(const field of ['source_preview_sha256','profile_sha256','source_script_sha256','engine_version'] as const)it('rejects stale '+field,()=>{
    const {preview,value}=fixture();value[field]='stale';expect(()=>checkSuggestionContext(value,preview)).toThrow()
  })
  it('rejects stale audio, actor and catalog bindings',()=>{
    for(const mutate of [(v:FaceSuggestions)=>v.bindings.audio_sha256='wrong',(v:FaceSuggestions)=>v.bindings.catalog_sha256='wrong',(v:FaceSuggestions)=>v.bindings.actors[0].actor_revision='wrong']){
      const {preview,value}=fixture();mutate(value);expect(()=>checkSuggestionContext(value,preview)).toThrow()
    }
  })
  it('does not overwrite new manual edits made after generation',()=>{
    const {preview,value}=fixture(),track=structuredClone(preview.face!.track!);track.cues[0].emotion='neutral'
    expect(()=>mergeFaceSuggestions(value,preview,track,['suggested'])).toThrow('手工轨道')
  })
  it('rejects overlapping, duplicate, unknown or unsupported proposals before save',()=>{
    const {preview,value}=fixture()
    expect(()=>mergeFaceSuggestions(value,preview,preview.face!.track!,['missing'])).toThrow()
    expect(()=>mergeFaceSuggestions(value,preview,preview.face!.track!,['suggested','suggested'])).toThrow()
    value.suggestions[0].face_cue.start_frame=4000
    expect(()=>mergeFaceSuggestions(value,preview,preview.face!.track!,['suggested'])).toThrow('冲突')
    value.suggestions[0].face_cue.start_frame=8000;value.suggestions[0].face_cue.emotion='happy' as any
    expect(()=>mergeFaceSuggestions(value,preview,preview.face!.track!,['suggested'])).toThrow('未经核对')
  })
})
