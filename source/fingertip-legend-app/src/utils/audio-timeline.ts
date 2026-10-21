import type { AudioEventRef, AudioRemakeSelection } from '../types/production'

export interface SoundOperation {
  operation_id:string; cue_text:string; mode:string; start_frame:number; end_frame:number
}
export interface LegacyTimelineEvent extends AudioEventRef {
  text:string; kind:string; start_frame:number; end_frame:number; render_role?:string; marker_type?:string
}
export interface CompoundTimelineEvent {
  source:AudioEventRef; text:string; kind:string; operations:SoundOperation[]
}
export type AudioTimeline = { sample_rate:number; source_version_id:string } & (
  { schema_version:'measured_timeline/2.0'; events:CompoundTimelineEvent[] } |
  { schema_version:'measured_timeline/1.0'|'measured_timeline/1.1'|'measured_timeline/1.2'; events:LegacyTimelineEvent[] }
)
export interface PlaybackSound extends SoundOperation { source:AudioRemakeSelection; kind:string; original_text:string }

export function playbackSounds(timeline:AudioTimeline|null):PlaybackSound[] {
  if(!timeline)return []
  if(timeline.schema_version==='measured_timeline/2.0')return timeline.events.flatMap(e=>e.operations.map(o=>({
    ...o,source:{...e.source,operation_id:o.operation_id},kind:e.kind,original_text:e.text,
  })))
  return timeline.events.map(e=>({operation_id:JSON.stringify([e.episode_id,e.scene_id,e.line_id]),cue_text:e.text,
    mode:e.marker_type||e.render_role||(e.kind==='action'?'action_note':['dialogue','narration'].includes(e.kind)?'speech':'one_shot'),
    start_frame:e.start_frame,end_frame:e.end_frame,source:{episode_id:e.episode_id,scene_id:e.scene_id,line_id:e.line_id},kind:e.kind,original_text:e.text}))
}
export const audibleSound=(o:SoundOperation)=>['speech','one_shot','background'].includes(o.mode)
export const sameSource=(a:AudioEventRef,b:AudioEventRef)=>a.episode_id===b.episode_id&&a.scene_id===b.scene_id&&a.line_id===b.line_id
export function selectionIncludes(items:AudioRemakeSelection[],value:AudioRemakeSelection){
  return items.some(e=>sameSource(e,value)&&e.operation_id===value.operation_id)
}
export function toggleSoundSelection(items:AudioRemakeSelection[],value:AudioRemakeSelection):AudioRemakeSelection[]{
  if(selectionIncludes(items,value))return items.filter(e=>!(sameSource(e,value)&&e.operation_id===value.operation_id))
  // A full-line selection and one of its operations cannot coexist.
  return [...items.filter(e=>!sameSource(e,value)||(e.operation_id!==undefined&&value.operation_id!==undefined)),value]
}
export const soundModeLabel=(mode:string)=>({speech:'配音',one_shot:'音效',background:'背景声音',action_note:'动作记录',
  stop_sound:'停止声音',continue_background:'延续背景',change_background:'背景音量变化'}[mode]||mode)
