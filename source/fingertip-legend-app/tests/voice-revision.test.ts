import {describe,it,expect} from 'vitest'
import {voiceRows,voiceRevision} from '../src/utils/voice-revision'
import type {StoryDeliveryView} from '../src/types/production'

const choices=[{id:'one',name:'音色一'},{id:'two',name:'音色二'}]
function delivery():StoryDeliveryView{return {title:'声音测试',story_version_id:'v',character_snapshot:[{role_id:'hero',canon:{name:'小雨'}}],episodes:[{episode_id:'ep',title:'章节',scenes:[{scene_id:'sc',title:'院子',utterances:[
  {line_id:'1',kind:'dialogue',speaker_role_id:'hero',text:'你好'},
  {line_id:'2',kind:'dialogue',speaker_role_id:'hero',text:'再见'},
  {line_id:'3',kind:'narration',narrator_id:'narrator',text:'她离开了'},
  {line_id:'4',kind:'sfx',text:'脚步声'},
]}]}]}}
describe('同版作品的音色重制',()=>{
  it('按对白和旁白身份统计范围，不把音效当作配音角色',()=>{
    const rows=voiceRows(delivery(),{bindings:{hero:'one',narrator:'two'}})
    expect(rows.map(r=>[r.identity,r.name,r.segments])).toEqual([['hero','小雨',2],['narrator','旁白',1]])
    expect(voiceRevision(rows,choices)).toMatchObject({ready:true,changedCount:0,affectedSegments:0})
  })
  it('提交完整绑定，改角色音色时保留旁白和原数据',()=>{
    const original={bindings:{hero:'one',narrator:'two'}},rows=voiceRows(delivery(),original)
    rows[0].selected='two'
    expect(voiceRevision(rows,choices)).toEqual({ready:true,bindings:{hero:'two',narrator:'two'},changedCount:1,affectedSegments:2})
    expect(original.bindings.hero).toBe('one')
  })
  it('缺失或多余绑定、无身份的旁白不能悄悄套用默认音色',()=>{
    for(const bindings of [{hero:'one'},{hero:'one',narrator:'two',extra:'one'},{hero:'',narrator:'two'}])expect(()=>voiceRows(delivery(),{bindings})).toThrow()
    const value=delivery();delete value.episodes[0].scenes[0].utterances[2].narrator_id
    expect(()=>voiceRows(value,{bindings:{hero:'one',narrator:'two'}})).toThrow('声音角色信息不完整')
  })
  it('旧音色不在目录时保留原值并阻止提交，明确选择后才恢复',()=>{
    const rows=voiceRows(delivery(),{bindings:{hero:'retired',narrator:'two'}})
    expect(rows[0].selected).toBe('retired')
    expect(voiceRevision(rows,choices).ready).toBe(false)
    rows[0].selected='one'
    expect(voiceRevision(rows,choices)).toMatchObject({ready:true,changedCount:1})
  })
  it('恢复原选择即取消变更，返回绑定的顺序稳定',()=>{
    const rows=voiceRows(delivery(),{bindings:{narrator:'two',hero:'one'}})
    rows[1].selected='one';rows[1].selected='two'
    expect(voiceRevision(rows,choices).changedCount).toBe(0)
    expect(JSON.stringify(voiceRevision([...rows].reverse(),choices).bindings)).toBe(JSON.stringify(voiceRevision(rows,choices).bindings))
  })
})
