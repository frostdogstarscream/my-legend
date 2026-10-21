import { describe, it, expect } from 'vitest'
import { playbackSounds, audibleSound, toggleSoundSelection } from '../src/utils/audio-timeline'
import type { AudioTimeline } from '../src/utils/audio-timeline'

const source={episode_id:'e',scene_id:'s',line_id:'l'}
describe('measured source/operation playback',()=>{
  it('retains source and original text while exposing each timed operation',()=>{
    const timeline:AudioTimeline={schema_version:'measured_timeline/2.0',source_version_id:'v',sample_rate:48000,events:[{
      source,kind:'sfx',text:'雨声渐弱，滴水声响起。',operations:[
        {operation_id:'fade',mode:'change_background',cue_text:'雨声渐弱',start_frame:48000,end_frame:48000},
        {operation_id:'drips',mode:'background',cue_text:'滴水声响起',start_frame:48000,end_frame:144000},
      ],
    }]}
    const sounds=playbackSounds(timeline)
    expect(sounds).toHaveLength(2)
    expect(sounds[1].source).toEqual({...source,operation_id:'drips'})
    expect(sounds[1].original_text).toBe('雨声渐弱，滴水声响起。')
    expect(sounds.filter(audibleSound).map(o=>o.operation_id)).toEqual(['drips'])
  })
  it('keeps legacy speech captions and marker selections compatible',()=>{
    const sounds=playbackSounds({schema_version:'measured_timeline/1.2',source_version_id:'v',sample_rate:48000,
      events:[{...source,text:'完整台词',kind:'dialogue',start_frame:0,end_frame:48000},
        {...source,line_id:'stop',text:'声音停止',kind:'sfx',start_frame:48000,end_frame:48000,marker_type:'stop_sound'}]})
    expect(sounds[0].cue_text).toBe('完整台词');expect(sounds[0].mode).toBe('speech')
    expect(sounds[0].source.operation_id).toBeUndefined();expect(audibleSound(sounds[1])).toBe(false)
  })
  it('replaces overlapping whole-line and per-operation selections without losing other lines',()=>{
    const other={...source,line_id:'other'},one={...source,operation_id:'one'},two={...source,operation_id:'two'}
    let choices=toggleSoundSelection([source,other],one)
    expect(choices).toEqual([other,one])
    choices=toggleSoundSelection(choices,two);expect(choices).toEqual([other,one,two])
    expect(toggleSoundSelection(choices,one)).toEqual([other,two])
    expect(toggleSoundSelection(choices,source)).toEqual([other,source])
  })
})
