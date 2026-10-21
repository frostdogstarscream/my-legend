import type { StoryDeliveryView } from '../types/production'

export interface VoiceChoice { id:string; name:string }
export interface VoiceRow { identity:string; name:string; original:string; selected:string; segments:number }
export interface VoiceRevisionState { ready:boolean; bindings:Record<string,string>; changedCount:number; affectedSegments:number }

export function voiceRows(delivery:StoryDeliveryView, raw:unknown):VoiceRow[] {
  const record=raw as {bindings?:unknown}|null
  if(!record||!record.bindings||typeof record.bindings!=='object'||Array.isArray(record.bindings))throw new Error('作品音色记录不完整，请重新加载。')
  const bindings=record.bindings as Record<string,unknown>
  const names=Object.fromEntries(delivery.character_snapshot.map(c=>[c.role_id,c.canon?.name||c.role_id]))
  const required=new Map<string,{names:Set<string>;segments:number}>()
  const narrators=new Map<string,number>()
  for(const episode of delivery.episodes)for(const scene of episode.scenes)for(const line of scene.utterances){
    if(!['dialogue','narration'].includes(line.kind))continue
    const identity=line.kind==='dialogue'?line.speaker_role_id:line.narrator_id
    if(!identity)throw new Error('剧本中的声音角色信息不完整。')
    const entry=required.get(identity)||{names:new Set<string>(),segments:0}
    if(line.kind==='narration'&&!narrators.has(identity))narrators.set(identity,narrators.size+1)
    entry.names.add(line.kind==='narration'?'旁白':(names[identity]||identity));entry.segments++
    required.set(identity,entry)
  }
  if(!required.size||Object.keys(bindings).length!==required.size||[...required.keys()].some(id=>typeof bindings[id]!=='string'||!(bindings[id] as string).trim()))throw new Error('作品音色与当前剧本角色不一致，请重新加载。')
  return [...required].map(([identity,value])=>({identity,name:[...value.names].map(name=>name==='旁白'&&narrators.size>1?`旁白 ${narrators.get(identity)}`:name).join(' / '),original:bindings[identity] as string,selected:bindings[identity] as string,segments:value.segments}))
}

export function voiceRevision(rows:VoiceRow[], choices:VoiceChoice[]):VoiceRevisionState {
  const allowed=new Set(choices.map(v=>v.id))
  const changed=rows.filter(row=>row.selected!==row.original)
  return {ready:rows.length>0&&rows.every(row=>allowed.has(row.selected)),
    bindings:Object.fromEntries([...rows].sort((a,b)=>a.identity.localeCompare(b.identity)).map(row=>[row.identity,row.selected])),
    changedCount:changed.length,affectedSegments:changed.reduce((sum,row)=>sum+row.segments,0)}
}
