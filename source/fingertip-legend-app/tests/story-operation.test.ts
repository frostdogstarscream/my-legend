import {describe,it,expect} from 'vitest'
import {storyOperationRequest} from '../src/utils/story-operation'
import type {IndependentJob} from '../src/types/production'
const job={id:'j',work_id:'w',artifacts:[
  {kind:'StoryDelivery',source_version_id:'v1',sha256:'a'.repeat(64),size_bytes:10,media_type:'application/json'},
  {kind:'RenderManifest',source_version_id:'v1',sha256:'b'.repeat(64),size_bytes:10,media_type:'application/json'}
]} as IndependentJob
describe('story operations',()=>{
  for(const intent of ['revise','continue','remix'] as const)it(intent+' binds source and delivers without approval',()=>{
    const value=storyOperationRequest(job,'operation-key',intent,'创作要求','角色','背景')
    expect(value.source.kind).toBe('operation')
    expect(value.review_policy).toBe('listen_first')
    expect(value.mode).toBe('automatic')
    expect(value.work_id).toBe(intent==='remix'?undefined:'w')
    expect(value.outputs).toEqual(['script','audio'])
  })
  it('rejects missing source and blank instructions',()=>{
    expect(()=>storyOperationRequest({...job,artifacts:[]},'key','continue','要求','','')).toThrow()
    expect(()=>storyOperationRequest(job,'key','revise',' ','','')).toThrow()
    expect(()=>storyOperationRequest(job,'key','remix','要求','','')).toThrow()
  })
})

