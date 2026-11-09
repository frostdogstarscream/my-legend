import {describe,it,expect,vi} from 'vitest'
vi.mock('../src/config',()=>({config:{apiBaseUrl:'https://example.test/api/v1'}}))
vi.mock('../src/stores/session',()=>({getToken:()=>null,clearSession:()=>{}}))
import {playbackArtifacts} from '../src/services/production-media'
import type {PlaybackView,PlaybackEntry} from '../src/types/production'

function view():PlaybackView{
 const entry=(kind:string):PlaybackEntry=>({content_path:'/api/v1/production-jobs/job/artifacts/'+'a'.repeat(64),artifact:{kind,sha256:'a'.repeat(64),source_version_id:'v1',media_type:kind==='AudioMix'?'audio/wav':'application/json',size_bytes:3}})
 const inputs={script:entry('StoryDelivery'),audio:entry('AudioMix'),timeline:entry('MeasuredTimeline')}
 return {schema_version:'production_playback_view/1.0',job_id:'job',job_status:'succeeded',available_outputs:['script','audio','h5'],
  manifest:{schema_version:'production_playback/1.0',job_id:'job',work_id:'work',source_version_id:'v1',...inputs,human_review:'pending',publication:'not_requested'},
  performance_manifest:{schema_version:'performance_manifest/1.0',job_id:'job',work_id:'work',source_version_id:'v1',source_job_id:'source',parent_job_id:null,title:'作品',status:'needs_attention',...structuredClone(inputs),preview:entry('SpineStagePreview'),diagnostics:[{code:'ACTION_UNSUPPORTED',message:'翻书尚未演出'}],delivery_warnings:['音效省略'],human_review:'pending',publication:'not_requested'}}
}
describe('正式 H5 交付',()=>{
 it('待校正 H5 不伪造 Video，也不屏蔽已有声音',()=>{expect(playbackArtifacts(view(),'job').map(a=>a.kind)).toEqual(['StoryDelivery','AudioMix','MeasuredTimeline'])})
 it('拒绝跨作品或音频版本混用',()=>{
  const v=view();v.performance_manifest!.work_id='other';expect(()=>playbackArtifacts(v,'job')).toThrow('H5 交付版本')
  const w=view();w.performance_manifest!.audio.artifact.sha256='b'.repeat(64);expect(()=>playbackArtifacts(w,'job')).toThrow('H5 声音')
 })
 it('结构比较不受 JSON 属性顺序影响',()=>{
  const v=view(),a=v.performance_manifest!.audio;v.performance_manifest!.audio={artifact:a.artifact,content_path:a.content_path}
  expect(playbackArtifacts(v,'job')).toHaveLength(3)
 })
 it('拒绝外部预览地址和错误制品类型',()=>{
  const v=view();v.performance_manifest!.preview.content_path='https://other.test/a';expect(()=>playbackArtifacts(v,'job')).toThrow('H5 预览')
  const w=view();w.performance_manifest!.preview.artifact.kind='Video';expect(()=>playbackArtifacts(w,'job')).toThrow('H5 预览')
 })
})
