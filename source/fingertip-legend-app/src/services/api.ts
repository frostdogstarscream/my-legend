import { config } from '../config'
import { clearSession, getToken } from '../stores/session'
import type { Character, CreateStoryInput, GenerationTask, HomeSummary, LoginInput, LoginResult, Product, Story, ProductionOptions, ProductionContext, ProductionTask } from '../types/domain'
import { createHttpClient, type Transport } from './http'
import { mockGateway } from './mock'
import { getStory, getDemoTask, startDemoTask, saveStory } from './story-repository'
import type { IndependentJob, IndependentRequest, ProductionCapabilities, PlaybackView, RemakeAudioInput } from '../types/production'

let redirecting = false
function unauthorized() {
  clearSession()
  if (redirecting) return
  redirecting = true
  uni.reLaunch({ url: '/pages/login/index', complete: () => { redirecting = false } })
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
  lifeCatalog: (digest?:string):Promise<import('../../../my-legend-stage/src/life-types').LifeCatalog> => request('/stage-preview/catalog?profile=life_v1'+(digest?'&catalog_sha256='+encodeURIComponent(digest):'')),
  lifeCompile: (id:string,bindings:Record<string,string>={},initial_outfits:Record<string,string|null>={}):Promise<import('../../../my-legend-stage/src/life-types').LifePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{profile:'life_v1',operation:'compile',bindings,initial_outfits}),
  lifeSave: (id:string,script:import('../../../my-legend-stage/src/life-types').LifeScript):Promise<import('../../../my-legend-stage/src/life-types').LifePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{profile:'life_v1',operation:'validate_and_save',script}),
  lifeSaved: (id:string,digest:string):Promise<import('../../../my-legend-stage/src/life-types').LifePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview/${encodeURIComponent(digest)}`),
  stageCatalog: ():Promise<import('../../../my-legend-stage/src/types').StageCatalog> => request('/stage-preview/catalog'),
  stagePreview: (id:string):Promise<import('../../../my-legend-stage/src/types').StagePreview> => request(`/production-jobs/${encodeURIComponent(id)}/stage-preview`,'POST',{}),
  independentCapabilities: (): Promise<ProductionCapabilities> => request('/production-capabilities'),
  independentJobs: (offset=0, workId?:string): Promise<{items:IndependentJob[];next_offset:number|null}> => request(`/production-jobs?offset=${offset}${workId?'&work_id='+encodeURIComponent(workId):''}`),
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
