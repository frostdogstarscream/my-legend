export interface User {
  id: string
  displayName: string
  avatarUrl: string
  vipDays: number
  balance: number
}
export interface Session {
  accessToken: string
  expiresAt: number
  mode: 'mock' | 'live'
  user: User
}
export interface LoginInput {
  account: string
  password: string
  inviteCode?: string
}
export interface LoginResult { accessToken: string; expiresIn: number; user: User }
export type StoryStatus = 'draft' | 'generating' | 'ready' | 'failed'
export interface Story {
  id: string
  title: string
  genre: string
  idea: string
  status: StoryStatus
  progress: number
  chapter: string
  coverUrl: string
  createdAt: string
  roleIds?: string[]
  chapters?: Chapter[]
  media?: PerformanceMedia
  generationTaskId?: string
  storyVersionId?: string
  production?: { story: string; audio: string; visual: string; message: string }
  productionTaskId?: string
}
export interface CreateStoryInput { title: string; genre: string; idea: string; roleIds?: string[] }
export interface HomeSummary { user: User; latestStory: Story | null }
export interface Character {
  id: string; name: string; description: string; avatarUrl: string; owned: boolean
  tags?: string[]; biography?: string; voiceDescription?: string
}
export interface Product {
  id: string; name: string; description: string; imageUrl: string; price: number; currency: 'coins'
}
export interface ApiEnvelope<T> { code: number; message: string; data: T; requestId?: string }
export interface Chapter { id: string; title: string; paragraphs: string[]; source: 'demo' | 'generated' }
export interface PerformanceCue { speakerId: string; text: string; start: number; end: number; kind?: string; sourceLineId?: string; sceneId?: string; durationBasis?: string }
export interface PerformanceActor { id: string; name: string; portraitUrl: string }
export interface PerformanceMedia { posterUrl: string; videoUrl?: string; audioUrl?: string; duration: number; actors: PerformanceActor[]; cues: PerformanceCue[]; sourceVersionId?: string; videoSha256?: string; presentation?: string; humanApproval?: string }
export interface ProductionOptions { voices: {id:string;name:string}[]; defaults: Record<string,string>; description:string; credentialConfigured:boolean }
export interface ProductionContext { storyVersionId:string; roles:{id:string;name:string;voice:string}[]; scenes:{id:string;title:string;events:number}[]; presentation:string }
export interface ProductionTask { id:string; storyId:string; storyVersionId:string; status:string; progress:number; message:string; voices:Record<string,string>; media?:PerformanceMedia; audioReview?:string; visualReview?:string; errorCode?:string; publication?:string }
export interface StoryOutline {
  id: string; revision: number; fingerprint: string; targetDurationSec: number; targetEpisodes: number
  content: {
    title: string; premise: string; theme: string; beginning: string; development: string; climax: string; ending: string
    episode_outlines: { index: number; title: string; core_goal: string; major_beats: string[]; ending: string }[]
    character_portrayals?: { role_id: string; story_name: string; speech_style?: string }[]
  }
}
export interface GenerationTask {
  id: string; storyId: string; status: 'running' | 'awaiting_approval' | 'succeeded' | 'failed' | 'cancelled'
  progress: number; message: string; phase?: 'planning' | 'writing'; plan?: StoryOutline; errorCode?: string; storyVersionId?: string
}
