import { beforeEach,describe,expect,it,vi } from 'vitest'
const session=vi.hoisted(()=>({getToken:vi.fn<()=>string|null>(()=> 'fixture-token'),clearSession:vi.fn()}))
vi.mock('../src/stores/session',()=>session)
vi.mock('../src/config',()=>({config:{apiBaseUrl:'https://example.test/api/v1'}}))
import { artifactUrl,downloadProductionMedia,readProductionArtifact,playbackArtifacts } from '../src/services/production-media'
import { jobActive,productionError,needsReconciliation } from '../src/utils/production'
import type { IndependentJob,ProductionArtifact,PlaybackView } from '../src/types/production'
const artifact:ProductionArtifact={kind:'Video',sha256:'a'.repeat(64),source_version_id:'v1',media_type:'video/mp4',size_bytes:12}
beforeEach(()=>{vi.clearAllMocks();session.getToken.mockReturnValue('fixture-token')})
describe('独立作品媒体权限',()=>{
  it('媒体地址不携带凭据，并编码任务标识',()=>{
    expect(artifactUrl('job/one',artifact)).toBe('https://example.test/api/v1/production-jobs/job%2Fone/artifacts/'+'a'.repeat(64))
    expect(()=>artifactUrl('job',{...artifact,sha256:'../other'})).toThrow('作品文件标识无效')
  })
  it('下载携带认证头，播放器仅接收本地临时路径',async()=>{
    const downloadFile=vi.fn((options)=>{expect(options.header.Authorization).toBe('Bearer fixture-token');expect(options.url).not.toContain('fixture-token');options.success({statusCode:200,tempFilePath:'temporary-video.mp4'})})
    vi.stubGlobal('uni',{downloadFile})
    expect(await downloadProductionMedia('job',artifact)).toBe('temporary-video.mp4')
  })
  it('失效会话不启动下载',async()=>{
    session.getToken.mockReturnValue(null)
    const downloadFile=vi.fn();vi.stubGlobal('uni',{downloadFile})
    await expect(downloadProductionMedia('job',artifact)).rejects.toThrow('登录已过期')
    expect(downloadFile).not.toHaveBeenCalled()
  })
  it('权限失败不能将错误响应作为媒体播放',async()=>{
    vi.stubGlobal('uni',{downloadFile:(options: {success:(value:unknown)=>void})=>options.success({statusCode:401,tempFilePath:'denied'})})
    await expect(downloadProductionMedia('job',artifact)).rejects.toThrow('登录已过期')
    expect(session.clearSession).toHaveBeenCalledOnce()
  })
  it('剧本直接读取文件响应，不要求业务 envelope',async()=>{
    vi.stubGlobal('uni',{request:(options:{success:(value:unknown)=>void})=>options.success({statusCode:200,data:{story_version_id:'v1'}})})
    expect(await readProductionArtifact('job',artifact)).toEqual({story_version_id:'v1'})
  })
  it('未知外部结果不展示普通重试提示',()=>{
    expect(productionError('EXTERNAL_RESULT_UNKNOWN')).toContain('对账')
    expect(jobActive({status:'needs_input'} as IndependentJob)).toBe(false)
    expect(jobActive({status:'running'} as IndependentJob)).toBe(true)
    expect(needsReconciliation({status:'failed',error_code:'DEADLINE_EXCEEDED',unknown_calls:1} as IndependentJob)).toBe(true)
    expect(needsReconciliation({status:'failed',error_code:'DEADLINE_EXCEEDED',unknown_calls:0} as IndependentJob)).toBe(false)
    expect(needsReconciliation({status:'needs_input',error_code:'EXTERNAL_RESULT_UNKNOWN',unknown_calls:0} as IndependentJob)).toBe(false)
  })
})

describe('作品播放结果版本绑定',()=>{
  function view():PlaybackView {
    const script={...artifact,kind:'StoryDelivery',media_type:'application/json'}
    return {schema_version:'production_playback_view/1.0',job_id:'job',job_status:'failed',available_outputs:['script'],
      manifest:{schema_version:'production_playback/1.0',job_id:'job',work_id:'work',source_version_id:'v1',
        script:{artifact:script,content_path:'/api/v1/production-jobs/job/artifacts/'+script.sha256},human_review:'pending',publication:'not_requested'}}
  }
  it('任务部分失败时仍提供已完成剧本',()=>{
    expect(playbackArtifacts(view(),'job').map(a=>a.kind)).toEqual(['StoryDelivery'])
    expect(playbackArtifacts({...view(),manifest:null,available_outputs:[]},'job')).toEqual([])
  })
  it('拒绝其他任务或来源版本的结果',()=>{
    expect(()=>playbackArtifacts(view(),'other')).toThrow('清单不匹配')
    const value=view();value.manifest!.script.artifact.source_version_id='v2'
    expect(()=>playbackArtifacts(value,'job')).toThrow('版本或引用不一致')
  })
  it('拒绝外部下载地址和与文件不匹配的标识',()=>{
    const value=view();value.manifest!.script.content_path='https://unexpected.test/file'
    expect(()=>playbackArtifacts(value,'job')).toThrow('版本或引用不一致')
    const other=view();other.manifest!.script.artifact.kind='Video'
    expect(()=>playbackArtifacts(other,'job')).toThrow('版本或引用不一致')
  })
})
