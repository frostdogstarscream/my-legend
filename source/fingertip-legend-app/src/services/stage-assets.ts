import { config } from '../config'
import { getToken } from '../stores/session'
import type { AssetRef } from '../../../my-legend-stage/src/types'

export async function stageAsset(ref:AssetRef):Promise<ArrayBuffer> {
  return verifiedBytes(`${config.apiBaseUrl}/stage-preview/assets/${ref.sha256}`,ref)
}
export async function stageAudio(jobId:string,ref:AssetRef):Promise<string> {
  const bytes=await verifiedBytes(`${config.apiBaseUrl}/production-jobs/${encodeURIComponent(jobId)}/artifacts/${ref.sha256}`,ref)
  return URL.createObjectURL(new Blob([bytes],{type:ref.media_type}))
}
export async function stageArtifactJson<T>(jobId:string,ref:AssetRef):Promise<T>{
  return JSON.parse(new TextDecoder().decode(await verifiedBytes(`${config.apiBaseUrl}/production-jobs/${encodeURIComponent(jobId)}/artifacts/${ref.sha256}`,ref))) as T
}
async function verifiedBytes(url:string,ref:AssetRef):Promise<ArrayBuffer> {
  if(!/^[a-f0-9]{64}$/.test(ref.sha256))throw new Error('演员文件标识无效')
  const token=getToken();if(!token)throw new Error('请先登录')
  const response=await fetch(url,{headers:{Authorization:`Bearer ${token}`}})
  if(!response.ok)throw new Error('演绎文件加载失败，请重新打开')
  const bytes=await response.arrayBuffer()
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('')
  if(bytes.byteLength!==ref.size_bytes||digest!==ref.sha256)throw new Error('演绎文件校验失败')
  return bytes
}
