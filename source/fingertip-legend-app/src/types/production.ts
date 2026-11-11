export type OutputKind = 'script' | 'audio' | 'visual' | 'h5'
export type JobStatus = 'queued' | 'running' | 'needs_input' | 'succeeded' | 'failed' | 'cancelling' | 'cancelled'
export interface AudioEventRef { episode_id:string; scene_id:string; line_id:string }
export interface AudioRemakeSelection extends AudioEventRef { operation_id?:string }
export interface RemakeAudioInput {
  expected_revision:number; idempotency_key:string; regenerate:AudioRemakeSelection[]; outputs:('audio'|'visual')[]
  voices?:Record<string,string>
}
export interface ProductionArtifact {
  kind: string; sha256: string; source_version_id: string; media_type: string; size_bytes: number
}
export type StoryIntent = 'revise' | 'continue' | 'remix'
export interface StoryOperationSource {
  kind:'operation'; intent:StoryIntent; source_version_id:string; delivery:ProductionArtifact; manifest:ProductionArtifact
  instruction:string; target_duration_seconds:number
  edit?:{scope:'whole_story'|'scene'|'ending';target:Record<string,string>;downstream_policy:'replan_affected'}
  remix?:{retain_elements:string[];transform_elements:string[];relation_to_source:'adaptation'}
}
export interface IndependentRequest {
  idempotency_key: string
  work_id?: string
  source: { kind: 'creative'; title: string; idea: string; genre: string; character_ids: string[]; target_duration_seconds: number | null }
    | { kind: 'delivery'; source_version_id: string; delivery: ProductionArtifact; manifest: ProductionArtifact }
    | StoryOperationSource
    | {kind:'saved_performance';source_job_id:string;source_version_id:string;preview:ProductionArtifact;parent_job_id:string|null;inputs:ProductionArtifact[]}
    | {kind:'directed_performance';source_job_id:string;source_version_id:string;parent_job_id:string|null;inputs:ProductionArtifact[];catalog_sha256:string;scene_catalog_sha256:string;bindings:Record<string,string>;initial_outfits:Record<string,string>;casting_confirmed:true}
  outputs: OutputKind[]
  sound_workflow?: 'sound_bridge/1'
  audio_delivery_policy?: 'strict' | 'audio_first'
  review_policy?: 'standard' | 'listen_first'
  mode: 'automatic' | 'reviewed'
  visual_profile: 'standing_performance/1' | 'pixel_performance/1' | 'basic_storyboard/1'
}
export interface ErrorDetail { code:string; message:string; retryable:boolean; http_status:number|null; provider_message:string|null }
export interface IndependentStage {
  name: string; status: JobStatus; error_code: string | null; human_review: string
  error_detail?: ErrorDetail
  input_request?: { plan_id: string; revision: number; fingerprint: string; outline: Record<string, unknown> }
}
export interface IndependentJob {
  id: string; work_id: string; source_version_id: string | null; revision: number; status: JobStatus
  request: IndependentRequest; stages: IndependentStage[]; artifacts: ProductionArtifact[]
  error_code: string | null; error_detail?: ErrorDetail; created_at: string; actual_calls: number; unsettled_calls: number; unknown_calls?: number
}
export interface ProductionCapabilities {
  h5_flow?:{configured:boolean;services:Record<string,boolean>;max_stage_roles:number;catalog_sha256:string|null}
  h5_directing?: {configured:boolean;mode:string;user_confirmation:string;max_model_calls:number;tts_calls:number;preview_version:string}
  h5_delivery?: {configured:boolean;mode:'saved_performance_import';preview_versions:string[]}
  outputs: OutputKind[]; modes: ('automatic' | 'reviewed')[]; target_duration_seconds: number[]
  modules: Record<string, { configured: boolean }>
}
export interface PlaybackEntry { artifact: ProductionArtifact; content_path: string }
export interface PlaybackManifest {
  schema_version: 'production_playback/1.0'; job_id: string; work_id: string; source_version_id: string
  script: PlaybackEntry; audio?: PlaybackEntry; timeline?: PlaybackEntry; video?: PlaybackEntry; poster?: PlaybackEntry
  sample_rate?: number; total_frames?: number; duration_seconds?: number
  human_review: 'pending' | 'not_required'; publication: 'not_requested'
  delivery_warnings?: string[]
}
export interface PlaybackView {
  performance_manifest?: PerformanceManifest
  schema_version: 'production_playback_view/1.0'; job_id: string; job_status: JobStatus
  available_outputs: OutputKind[]; manifest: PlaybackManifest | null
}
export interface PerformanceManifest {
  casting_confirmed?:boolean;model_review?:string|null;
  schema_version:'performance_manifest/1.0';job_id:string;work_id:string;source_version_id:string
  source_job_id:string;parent_job_id:string|null;title:string;status:'needs_attention'|'preview_ready'
  preview:PlaybackEntry;script:PlaybackEntry;audio:PlaybackEntry;timeline:PlaybackEntry
  diagnostics:{code:string;message:string;text?:string}[];delivery_warnings:string[]
  human_review:'pending';publication:'not_requested'
}
export interface StoryDeliveryView {
  title: string; story_version_id: string
  character_snapshot: { role_id: string; canon?: { name?: string } }[]
  character_portrayals?: {role_id:string;story_name?:string}[]
  fictional_voice_roles?: {role_id:string;story_name?:string;name?:string}[]
  episodes: { episode_id: string; title: string; scenes: { scene_id: string; title: string; utterances: {
    line_id: string; kind: string; text: string; speaker_role_id?: string | null; narrator_id?: string | null
  }[] }[] }[]
}
