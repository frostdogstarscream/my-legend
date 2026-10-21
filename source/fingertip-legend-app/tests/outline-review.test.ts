import {describe,expect,it} from 'vitest'
import {buildOutlineReview,describePlanValue} from '../src/utils/outline-review'

function plan(){return {
  title:'测试故事',premise:'两人因约定产生误会。',theme:'互相理解',beginning:'甲进入院子。',development:'两人说明原委。',climax:'误会被澄清。',ending:'两人约定再见。',
  episode_outlines:[{index:1,title:'院中相遇',core_goal:'澄清误会',major_beats:['甲入场。','乙说明原委。','甲离场。'],ending:'约定再见。',target_duration_sec:90}],
  character_portrayals:[{role_id:'a',story_name:'甲',external_goal:'获得解释',emotional_avoidance:'不愿承认担心',speech_style:'先反问再说明',relationship_stance:'想保持信任'}],
  fictional_voice_roles:[{role_id:'radio',story_name:'广播员',performer_id:'p1',medium:'broadcast',dramatic_purpose:'提供时间线索'}],
  mystery_causal_proof:{actual_event_timeline:'真实顺序与听到的顺序不同。',witnessed_event:'听到旧录音。',actor_opportunity:'提前录制。',audible_trace_before_reveal:'同一声钟响。',onstage_verification:'两次声音对应。'},
  performance_plan:{pacing:'由紧张转为舒缓',sound_design:{sfx:['风声','脚步声']}},continuity_constraints:['甲从院门入场。'],change_summary:['补充约定的原因。']
}}

describe('实际 PlanContent 大纲展示',()=>{
  it('展示章节、人物、画外声音、因果与表演安排，保留全部事件',()=>{
    const value=plan(),before=JSON.stringify(value),review=buildOutlineReview(value)
    expect(review.complete).toBe(true)
    const copy=JSON.stringify(review.sections)
    for(const sentence of ['甲入场。','乙说明原委。','甲离场。','先反问再说明','提供时间线索','两次声音对应。','由紧张转为舒缓','补充约定的原因。'])expect(copy).toContain(sentence)
    expect(copy).toContain('90 秒（以制作后的实测结果为准）')
    expect(JSON.stringify(value)).toBe(before)
  })
  it('只有摘要时不允许确认',()=>{
    const {episode_outlines,...partial}=plan()
    expect(buildOutlineReview(partial).complete).toBe(false)
    expect(buildOutlineReview(undefined).complete).toBe(false)
  })
  it('空事件、重复章节序号和缺失人物说明都不能通过完整性检查',()=>{
    const empty=plan();empty.episode_outlines[0].major_beats=[]
    expect(buildOutlineReview(empty).complete).toBe(false)
    const repeated=plan();repeated.episode_outlines.push({...repeated.episode_outlines[0]})
    expect(buildOutlineReview(repeated).complete).toBe(false)
    const role=plan();role.character_portrayals[0].speech_style=''
    expect(buildOutlineReview(role).complete).toBe(false)
  })
  it('保留未识别补充内容，不截断长台词或执行生成文本',()=>{
    const raw='<img src=x onerror=alert(1)>'+ '细节'.repeat(600)
    const review=buildOutlineReview({...plan(),extra_notes:raw})
    expect(review.sections.find(section=>section.id==='additional')?.fields[0].text).toContain(raw)
    expect(describePlanValue({sfx:['风声','脚步声']})).toBe('音效：1. 风声\n2. 脚步声')
  })
})
