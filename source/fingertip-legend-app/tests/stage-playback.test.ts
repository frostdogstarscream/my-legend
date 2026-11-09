import {describe,it,expect} from 'vitest'
import {adjacentCue,sentenceEnd} from '../src/utils/stage-playback'
import type {StageCue} from '../../my-legend-stage/src/types'
const cue=(start:number,end:number)=>({start_frame:start,end_frame:end} as StageCue)
describe('real audio sentence navigation',()=>{
 const cues=[cue(500,900),cue(100,300),cue(100,350),cue(1000,1200)]
 it('finds next distinct start and previous start without mutating saved cues',()=>{
  expect(adjacentCue(cues,100,1)?.start_frame).toBe(500)
  expect(adjacentCue(cues,500,-1)?.start_frame).toBe(100)
  expect(adjacentCue(cues,700,-1)?.start_frame).toBe(500)
  expect(adjacentCue(cues,0,-1)).toBeUndefined()
  expect(adjacentCue(cues,1200,1)).toBeUndefined()
  expect(cues[0].start_frame).toBe(500)
 })
 it('stops after all overlapping lines, handles silence and resumes the next line',()=>{
  expect(sentenceEnd(cues,200)).toBe(350)
  expect(sentenceEnd(cues,350)).toBe(900)
  expect(sentenceEnd(cues,900)).toBe(1200)
  expect(sentenceEnd(cues,1200)).toBeUndefined()
  expect(sentenceEnd([],0)).toBeUndefined()
 })
})
