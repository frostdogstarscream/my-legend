import { config } from '../config'
import { getToken, clearSession } from '../stores/session'
import type { ProductionArtifact, PlaybackView } from '../types/production'

export function playbackArtifacts(view:PlaybackView, jobId:string):ProductionArtifact[] {
  if(view.schema_version!=='production_playback_view/1.0'||view.job_id!==jobId)throw new Error('作品播放清单不匹配，请刷新')
  const manifest=view.manifest;if(!manifest)return []
  if(manifest.schema_version!=='production_playback/1.0'||manifest.job_id!==jobId)throw new Error('作品播放清单不匹配，请刷新')
  const kinds={script:'StoryDelivery',audio:'AudioMix',timeline:'MeasuredTimeline',video:'Video',poster:'Poster'} as const
  return (Object.keys(kinds) as (keyof typeof kinds)[]).flatMap(key=>{
    const entry=manifest[key];if(!entry)return []
    const ref=entry.artifact
    if(ref.kind!==kinds[key]||ref.source_version_id!==manifest.source_version_id||!/^[a-f0-9]{64}$/.test(ref.sha256)
      ||entry.content_path!==`/api/v1/production-jobs/${jobId}/artifacts/${ref.sha256}`)throw new Error('作品文件版本或引用不一致，请刷新')
    return [ref]
  })
}

export function artifactUrl(jobId:string, artifact:ProductionArtifact) {
  if (!/^[a-f0-9]{64}$/.test(artifact.sha256)) throw new Error('作品文件标识无效')
  return `${config.apiBaseUrl}/production-jobs/${encodeURIComponent(jobId)}/artifacts/${artifact.sha256}`
}
function headers() {
  const token = getToken()
  if (!token) throw new Error('登录已过期，请重新登录')
  return { Authorization: `Bearer ${token}` }
}
function validStatus(status:number) {
  if(status===401){clearSession();throw new Error('登录已过期，请重新登录')}
  if(status!==200)throw new Error(status===404?'作品文件暂不可用':'作品文件加载失败，请重试')
}
export function readProductionArtifact<T>(jobId:string, artifact:ProductionArtifact):Promise<T> {
  return new Promise((resolve,reject)=>uni.request({url:artifactUrl(jobId,artifact),header:headers(),timeout:30000,
    success:result=>{try{validStatus(result.statusCode);resolve(result.data as T)}catch(e){reject(e)}},
    fail:()=>reject(new Error('作品文件加载失败，请检查网络'))}))
}
export function downloadProductionMedia(jobId:string, artifact:ProductionArtifact):Promise<string> {
  return new Promise((resolve,reject)=>uni.downloadFile({url:artifactUrl(jobId,artifact),header:headers(),timeout:120000,
    success:result=>{try{validStatus(result.statusCode);resolve(result.tempFilePath)}catch(e){releaseProductionMedia(result.tempFilePath);reject(e)}},
    fail:()=>reject(new Error('媒体加载失败，请检查网络后重试'))}))
}
export function releaseProductionMedia(path:string) {
  // #ifdef H5
  if(path.startsWith('blob:'))URL.revokeObjectURL(path)
  // #endif
}
