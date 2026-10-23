import { describe,it,expect } from 'vitest'
import { actorTime,cuesAt,type StageScript } from '../../my-legend-stage/src/types'
import { expandLegacyDeforms,installLegacyFixes } from '../../my-legend-stage/src/legacy-fixes'

describe('Spine absolute clock and legacy compatibility',()=>{
  it('replays the same time after backward seeks, loops or clamps at boundaries',()=>{
    expect(actorTime(7.25,2,true)).toBe(1.25)
    expect(actorTime(.25,2,true)).toBe(.25)
    expect(actorTime(7.25,2,false)).toBe(2)
    expect(actorTime(-1,2,true)).toBe(0)
    expect(actorTime(NaN,2,true)).toBe(0)
  })
  it('keeps overlapping dialogue and excludes an exact end boundary',()=>{
    const script={sample_rate:100,cues:[{start_frame:0,end_frame:200,role_id:'a'},{start_frame:100,end_frame:300,role_id:'b'}]} as StageScript
    expect(cuesAt(script,1.5).map(c=>c.role_id)).toEqual(['a','b'])
    expect(cuesAt(script,2).map(c=>c.role_id)).toEqual(['b'])
    expect(cuesAt(script,3)).toEqual([])
  })
  it('expands weighted mesh offsets by influence count, including zero frames',()=>{
    const raw={skins:{default:{hair:{hair:{type:'skinnedmesh',vertices:[2,0,0,0,.5,1,0,0,.5,1,0,0,0,1]}}}},animations:{wait:{ffd:{default:{hair:{hair:[{time:0},{time:1,offset:2,vertices:[4,5]}]}}}}}}
    const frames=expandLegacyDeforms(raw).animations.wait.ffd.default.hair.hair
    expect(frames[0].vertices).toEqual([0,0,0,0,0,0])
    expect(frames[1].vertices).toEqual([0,0,4,5,0,0])
  })
  it('applies flips to the specified bone and preserves setup before first key',()=>{
    const core={FlipXTimeline:function(){},FlipYTimeline:function(){}} as any
    installLegacyFixes(core)
    const timeline=new core.FlipXTimeline();timeline.boneIndex=1;timeline.frames=[1,1,2,0]
    const skeleton={bones:[{flipX:false},{flipX:false}]}
    timeline.apply(skeleton,-1,.5);expect(skeleton.bones[1].flipX).toBe(false)
    timeline.apply(skeleton,-1,1.5);expect(skeleton.bones.map(b=>b.flipX)).toEqual([false,true])
    timeline.apply(skeleton,-1,2);expect(skeleton.bones[1].flipX).toBe(false)
  })
})
