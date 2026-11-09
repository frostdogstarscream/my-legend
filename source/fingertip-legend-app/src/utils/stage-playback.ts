import type {StageCue} from '../../../my-legend-stage/src/types'
export function adjacentCue(cues:StageCue[],frame:number,direction:number){
 const ordered=[...cues].sort((a,b)=>a.start_frame-b.start_frame)
 return direction>0?ordered.find(c=>c.start_frame>frame+1):ordered.filter(c=>c.start_frame<frame-1).slice(-1)[0]
}
export function sentenceEnd(cues:StageCue[],frame:number){
 const current=cues.filter(c=>c.start_frame<=frame&&frame<c.end_frame)
 return current.length?Math.max(...current.map(c=>c.end_frame)):[...cues].sort((a,b)=>a.start_frame-b.start_frame).find(c=>c.start_frame>=frame)?.end_frame
}
