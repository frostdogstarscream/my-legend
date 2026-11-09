export interface PerformanceTracks {dialogue_enabled?:boolean;dialogue_track?:import('../../../my-legend-stage/src/performance-tracks').DirectionTrack;face_track?:import('../../../my-legend-stage/src/performance-tracks').FaceTrack;face_mode?:'basic'|'expressions';scene?:import('../../../my-legend-stage/src/scene-types').SceneRef;furniture_track?:import('../../../my-legend-stage/src/scene-types').FurnitureTrack}
import { config } from '../config'
import {lifePresentation} from './life-presentation'
import type {ProductionFlow,FlowInput} from '../types/flow'
import type {CreationDraft,CreationPayload,WorkSummary} from '../types/works'
import {redirectToLogin} from '../utils/login-return'
import { clearSession, getToken } from '../stores/session'
import type { Character, CreateStoryInput, GenerationTask, HomeSummary, LoginInput, LoginResult, Product, Story, ProductionOptions, ProductionContext, ProductionTask } from '../types/domain'
import { createHttpClient, type Transport } from './http'
import { mockGateway } from './mock'
import { getStory, getDemoTask, startDemoTask, saveStory } from './story-repository'
import type { IndependentJob, IndependentRequest, ProductionCapabilities, PlaybackView, RemakeAudioInput } from '../types/production'

function unauthorized() {
  clearSession()
  redirectToLogin()
}
const transport: Transport = request => new Promise((resolve, reject) => {
  uni.request({
    url: request.url, method: request.method, data: request.data,
    header: request.headers, timeout: request.timeout,
    success: result => resolve({ statusCode: result.statusCode, body: result.data }),
    fail: reject,
  })
})
const request = createHttpClient({ baseUrl: config.apiBaseUrl, timeout: config.requestTimeout, getToken, onUnauthorized: unauthorized, transport })

// UI pages use this contract in both modes. Adapt existing backends here only.
export const api = {
  works:(offset=0,search='',status='all'):Promise<{items:WorkSummary[];next_offset:number|null}>=>request(`/works?offset=${offset}&search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}`),
  work:(id:string):Promise<WorkSummary>=>request(`/works/${encodeURIComponent(id)}`),
  creationDraft:(id:string):Promise<CreationDraft>=>request(`/creation-drafts/${encodeURIComponent(id)}`),
  createCreationDraft:(body:CreationPayload&{idempotency_key:string;legacy_story_id?:string}):Promise<CreationDraft>=>request('/creation-drafts','POST',{...body}),
  updateCreationDraft:(id:string,body:CreationPayload&{expected_revision:number}):Promise<CreationDraft>=>request(`/creation-drafts/${encodeURIComponent(id)}`,'POST',{...body}),
  createFlow:(input:FlowInput):Promise<ProductionFlow>=>request('/production-flows','POST',{...input}),
  flow:(id:string):Promise<ProductionFlow>=>request(`/production-flows/${encodeURIComponent(id)}`),
  flows:(offset=0,workId?:string):Promise<{items:ProductionFlow[];next_offset:number|null}>=>request(`/production-flows?offset=${offset}${workId?'&work_id='+encodeURIComponent(workId):''}`),
  controlFlow:(id:string,action:'cancel'|'resume'|'reconcile',revision:number):Promise<ProductionFlow>=>request(`/production-flows/${encodeURIComponent(id)}/${action}`,'POST',{expected_revision:revision}),
  reviseFlow:(id:string,input:Record<string,unknown>):Promise<ProductionFlow>=>request(`/production-flows/${encodeURIComponent(id)}/revisions`,'POST',input),
  createH5Delivery:(id:string,input:{preview_sha256:string;idempotency_key:string;parent_job_id?:string}):Promise<IndependentJob>=>request(`/production-jobs/${encodeURIComponent(id)}/h5-deliveries`,'POST',input),
  createH5Directing:(id:string,input:{catalog_sha256:string;bindings:Record<string,string>;initial_outfits:Record<string,string>;casting_confirmed:true;idempotency_key:string;parent_job_id?:string}):Promise<IndependentJob>=>request(`/production-jobs/${encodeURIComponent(id)}/h5-directing-jobs`,'POST',input),
  h5Performance:(id:string):Promise<{manifest:import('../types/production').PerformanceManifest;preview:import('../../../my-legend-stage/src/h5-types').H5Preview|import('../../../my-legend-stage/src/commander-types').CommanderPreview}>=>request(`/production-jobs/${encodeURIComponent(id)}/h5-performance`),
  sceneCatalog:(digest?:string):Promise<import('../../../my-legend-stage/src/scene-types').SceneCatalog>=>request('/stage-preview/scenes'+(digest?'?catalog_sha256='+encodeURIComponent(digest):'')),
  lifeSuggestFace:(id:string,source_preview_sha256:string):Promise<import('./face-suggestions').FaceSuggestions>=>request(`/production-jobs/${encodeURIComponent(id)}/stage-preview/suggest-face`,'POST',{profile:'life_v1',source_preview_sha256,mode:'offline_rules',max_suggestions:32,include_diagnostics:true}),
  h5Catalog:async(digest?:string):Promise<import('../../../my-legend-stage/src/life-types').LifeCatalog>=>{
    const catalog=await request<import('../../../my-legend-stage/src/life-types').LifeCatalog>('/stage-preview/catalog?profile=life_v2'+(digest?'&catalog_sha256='+encodeURIComponent(digest):''))
    if(digest&&catalog.actors.some(a=>a.actor_id.startsWith('gfl-guofeng-')&&a.display_name.includes(' · '))){
      const current=await request<import('../../../my-legend-stage/src/life-types').LifeCatalog>('/stage-preview/catalog?profile=life_v2')
      return lifePresentation(catalog,current)
    }
    return catalog
  },
  h5Prepare:(id:string,catalog_sha256?:string):Promise<import('../../../my-legend-stage/src/h5-types').H5Context>=>request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{profile:'life_v2',operation:'prepare',catalog_sha256}),
  h5Compile:(id:string,body:import('../../../my-legend-stage/src/h5-types').H5Request):Promise<import('../../../my-legend-stage/src/h5-types').H5Preview|import('../../../my-legend-stage/src/commander-types').CommanderPreview>=>request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{...body}),
  h5Saved:(id:string,digest:string):Promise<import('../../../my-legend-stage/src/h5-types').H5Preview|import('../../../my-legend-stage/src/commander-types').CommanderPreview>=>request(`/production-jobs/${encodeURIComponent(id)}/stage-preview/${encodeURIComponent(digest)}`),
  lifeCatalog: (digest?:string):Promise<import('../../../my-legend-stage/src/life-types').LifeCatalog> => request('/stage-preview/catalog?profile=life_v1'+(digest?'&catalog_sha256='+encodeURIComponent(digest):'')),
  lifeCompile: (id:string,bindings:Record<string,string>={},initial_outfits:Record<string,string|null>={},face_enabled=false,face_profile_sha256?:string,tracks:PerformanceTracks={dialogue_enabled:true}):Promise<import('../../../my-legend-stage/src/life-types').LifePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{profile:'life_v1',operation:'compile',bindings,initial_outfits,face_enabled,face_profile_sha256,...tracks}),
  lifeSave: (id:string,script:import('../../../my-legend-stage/src/life-types').LifeScript,face_enabled=false,face_profile_sha256?:string,tracks:PerformanceTracks={}):Promise<import('../../../my-legend-stage/src/life-types').LifePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{profile:'life_v1',operation:'validate_and_save',script,face_enabled,face_profile_sha256,...tracks}),
  lifeSaved: (id:string,digest:string):Promise<import('../../../my-legend-stage/src/life-types').LifePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview/${encodeURIComponent(digest)}`),
  stageCatalog: ():Promise<import('../../../my-legend-stage/src/types').StageCatalog> => request('/stage-preview/catalog'),
  stagePreview: (id:string):Promise<import('../../../my-legend-stage/src/types').StagePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{}),
  independentCapabilities: (): Promise<ProductionCapabilities> => request('/production-capabilities'),
  independentJobs: (offset=0, workId?:string,excludeFlows=false): Promise<{items:IndependentJob[];next_offset:number|null}> => request(`/production-jobs?offset=${offset}${workId?'&work_id='+encodeURIComponent(workId):''}${excludeFlows?'&exclude_flows=true':''}`),
  independentJob: (id:string): Promise<IndependentJob> => request(`/production-jobs/${encodeURIComponent(id)}`),
  independentPlayback: (id:string): Promise<PlaybackView> => request(`/production-jobs/${encodeURIComponent(id)}/playback`),
  startIndependent: (input:IndependentRequest): Promise<IndependentJob> => request('/production-jobs','POST',{...input}),
  cancelIndependent: (id:string): Promise<IndependentJob> => request(`/production-jobs/${encodeURIComponent(id)}/cancel`,'POST',{}),
  resumeIndependent: (id:string,revision:number,resolution:Record<string,unknown>={}): Promise<IndependentJob> => request(`/production-jobs/${encodeURIComponent(id)}/resume`,'POST',{expected_revision:revision,resolution}),
  remakeAudio: (id:string,input:RemakeAudioInput):Promise<IndependentJob> => request(`/production-jobs/${encodeURIComponent(id)}/remake-audio`,'POST',{...input}),
  reconcileIndependent: (id:string,revision:number):Promise<{job:IndependentJob;settled:string[];unresolved:{call_key:string;reason:string}[]}> => request(`/production-jobs/${encodeURIComponent(id)}/reconcile`,'POST',{expected_revision:revision}),
  localLogin: (): Promise<LoginResult> => request('/auth/local', 'POST', {}, false),
  login: (input: LoginInput): Promise<LoginResult> => config.useMock ? mockGateway.login(input) : request('/auth/login', 'POST', { ...input }, false),
  home: (): Promise<HomeSummary> => config.useMock ? mockGateway.home() : request('/home'),
  stories: (): Promise<Story[]> => config.useMock ? mockGateway.stories() : request('/stories'),
  story: (id: string): Promise<Story> => config.useMock ? Promise.resolve().then(() => getStory(id)) : request(`/stories/${encodeURIComponent(id)}`),
  startGeneration: (storyId: string): Promise<GenerationTask> => config.useMock ? Promise.resolve().then(() => startDemoTask(storyId)) : request(`/stories/${encodeURIComponent(storyId)}/generation-tasks`, 'POST', {}),
  generationTask: (id: string): Promise<GenerationTask> => config.useMock ? Promise.resolve().then(() => getDemoTask(id)) : request(`/generation-tasks/${encodeURIComponent(id)}`),
  approveOutline: (id: string, revision: number, fingerprint: string): Promise<GenerationTask> => request(`/generation-tasks/${encodeURIComponent(id)}/approve`, 'POST', { revision, fingerprint }),
  reviseOutline: (id: string, revision: number, fingerprint: string, feedback: string): Promise<GenerationTask> => request(`/generation-tasks/${encodeURIComponent(id)}/revise`, 'POST', { revision, fingerprint, feedback }),
  retryGeneration: (id: string): Promise<GenerationTask> => request(`/generation-tasks/${encodeURIComponent(id)}/retry`, 'POST', {}),
  cancelGeneration: (id: string): Promise<GenerationTask> => request(`/generation-tasks/${encodeURIComponent(id)}/cancel`, 'POST', {}),
  productionOptions: ():Promise<ProductionOptions> => request('/production-options'),
  productionContext: (id:string):Promise<ProductionContext> => request(`/stories/${encodeURIComponent(id)}/production-context`),
  startProduction: (id:string,voices:Record<string,string>,newRevision=false):Promise<ProductionTask> => request(`/stories/${encodeURIComponent(id)}/production-tasks`,'POST',{voices,acknowledgeStoryboard:true,newRevision}),
  productionTask: (id:string):Promise<ProductionTask> => request(`/production-tasks/${encodeURIComponent(id)}`),
  retryProduction: (id:string):Promise<ProductionTask> => request(`/production-tasks/${encodeURIComponent(id)}/retry`,'POST',{}),
  reviewProduction: (id:string,kind:'audio'|'visual',decision:'approve'|'changes',storyVersionId:string,videoSha256:string,notes:string):Promise<ProductionTask> => request(`/production-tasks/${encodeURIComponent(id)}/reviews`,'POST',{kind,decision,storyVersionId,videoSha256,notes}),
  createDraft: (input: CreateStoryInput): Promise<Story> => config.useMock ? mockGateway.createDraft(input) : request('/stories', 'POST', { ...input }),
  updateDraft: (id: string, input: CreateStoryInput): Promise<Story> => config.useMock ? Promise.resolve().then(() => saveStory({ ...getStory(id), ...input, status: 'draft' })) : request(`/stories/${encodeURIComponent(id)}`, 'POST', { ...input }),
  characters: (): Promise<Character[]> => config.useMock ? mockGateway.characters() : request('/characters'),
  products: (): Promise<Product[]> => config.useMock ? mockGateway.products() : request('/shop/products'),
  logout: (): Promise<null> => config.useMock ? Promise.resolve(null) : request('/auth/logout', 'POST', {}),
}
