export interface ReviewField { label:string; text:string }
export interface ReviewSection { id:string; title:string; fields:ReviewField[] }
export interface OutlineReview { sections:ReviewSection[]; complete:boolean; warnings:string[] }
type RecordValue=Record<string,unknown>
const object=(value:unknown):RecordValue|undefined=>value!==null&&typeof value==='object'&&!Array.isArray(value)?value as RecordValue:undefined
const text=(value:unknown)=>typeof value==='string'?value:''
const labels:Record<string,string>={title:'故事名称',premise:'故事概要',theme:'主题',beginning:'开场',development:'发展',climax:'高潮',ending:'结局',
  core_goal:'本章目标',major_beats:'主要事件',external_goal:'角色目标',emotional_avoidance:'角色回避的情绪',speech_style:'说话风格',relationship_stance:'关系立场',
  dramatic_purpose:'剧情作用',actual_event_timeline:'实际事件顺序',witnessed_event:'目击到的现象',actor_opportunity:'行动机会',
  audible_trace_before_reveal:'揭晓前可听见的线索',onstage_verification:'剧情中的验证',narration:'旁白',narrator_style:'旁白风格',
  pacing:'节奏',emotion:'情绪',emotional_arc:'情绪变化',sound_design:'声音设计',music:'音乐',sfx:'音效',dialogue:'对白',notes:'补充说明'}

// Preserve nested production instructions as text; never execute HTML from a generated plan.
export function describePlanValue(value:unknown, depth=0):string {
  if(value===null||value===undefined)return ''
  if(typeof value==='string')return value
  if(typeof value==='number'||typeof value==='boolean')return String(value)
  if(depth>12)return JSON.stringify(value)
  if(Array.isArray(value))return value.map((item,i)=>`${i+1}. ${describePlanValue(item,depth+1)}`).join('\n')
  const record=object(value)
  return record?Object.entries(record).map(([key,item])=>`${labels[key]||key}：${describePlanValue(item,depth+1)}`).join('\n'):''
}

export function buildOutlineReview(raw:unknown):OutlineReview {
  const plan=object(raw),sections:ReviewSection[]=[],warnings:string[]=[]
  if(!plan)return {sections,complete:false,warnings:['大纲内容尚未完整加载，请刷新后再确认。']}
  const summaryKeys=['title','premise','theme','beginning','development','climax','ending']
  if(summaryKeys.some(key=>!text(plan[key]).trim()))warnings.push('故事概要或情节结构不完整。')
  sections.push({id:'summary',title:'故事大纲',fields:summaryKeys.filter(key=>text(plan[key])).map(key=>({label:labels[key],text:text(plan[key])}))})
  const episodes=Array.isArray(plan.episode_outlines)?plan.episode_outlines:[]
  if(!episodes.length)warnings.push('章节大纲尚未完整加载。')
  const indexes=new Set<number>()
  episodes.forEach((item,i)=>{
    const episode=object(item)||{},beats=episode.major_beats
    if(!Number.isInteger(episode.index)||Number(episode.index)<1||indexes.has(Number(episode.index))||
      ['title','core_goal','ending'].some(key=>!text(episode[key]).trim())||!Array.isArray(beats)||!beats.length||beats.some(v=>!text(v).trim())||
      typeof episode.target_duration_sec!=='number'||episode.target_duration_sec<=0)warnings.push(`第 ${i+1} 份章节大纲不完整。`)
    indexes.add(Number(episode.index))
    sections.push({id:`episode-${i}`,title:`第 ${episode.index||i+1} 章 · ${text(episode.title)||'未命名章节'}`,fields:[
      {label:'本章目标',text:text(episode.core_goal)},{label:'主要事件',text:describePlanValue(beats)},
      {label:'本章结局',text:text(episode.ending)},{label:'目标时长',text:`${episode.target_duration_sec??'未提供'} 秒（以制作后的实测结果为准）`} ]})
  })
  const collections=[{key:'character_portrayals',title:'角色',fields:['external_goal','emotional_avoidance','speech_style','relationship_stance']},
    {key:'fictional_voice_roles',title:'剧情声音角色',fields:['dramatic_purpose']}] as const
  for(const collection of collections){
    const values=plan[collection.key]
    if(values!==undefined&&!Array.isArray(values)){warnings.push(`${collection.title}说明格式不完整。`);continue}
    ;(values as unknown[]||[]).forEach((item,i)=>{
      const role=object(item)||{}
      if(!text(role.role_id).trim()||!text(role.story_name).trim()||collection.fields.some(key=>!text(role[key]).trim()))warnings.push(`${collection.title}说明不完整。`)
      const fields:ReviewField[]=collection.fields.map(key=>({label:labels[key],text:text(role[key])}))
      if(collection.key==='fictional_voice_roles'){
        const media:Record<string,string>={live:'现场',recording:'录音',telephone:'电话',broadcast:'广播'}
        if(!media[text(role.medium)])warnings.push('剧情声音的出场方式不完整。')
        fields.unshift({label:'出场方式',text:media[text(role.medium)]||'未提供'})
      }
      sections.push({id:`${collection.key}-${i}`,title:`${collection.title}：${text(role.story_name)||'未命名'}`,fields})
    })
  }
  for(const [key,title] of [['mystery_causal_proof','悬疑因果说明'],['performance_plan','表演与声音安排'],['continuity_constraints','连续性约束'],['change_summary','本次变更说明']]){
    const value=plan[key]
    if(value!==null&&value!==undefined&&describePlanValue(value))sections.push({id:key,title,fields:[{label:'',text:describePlanValue(value)}]})
  }
  const known=new Set([...summaryKeys,'episode_outlines','character_portrayals','fictional_voice_roles','mystery_causal_proof','performance_plan','continuity_constraints','change_summary'])
  const additional=Object.fromEntries(Object.entries(plan).filter(([key])=>!known.has(key)))
  if(Object.keys(additional).length)sections.push({id:'additional',title:'补充大纲信息',fields:[{label:'',text:describePlanValue(additional)}]})
  return {sections,complete:warnings.length===0,warnings:Array.from(new Set(warnings))}
}
