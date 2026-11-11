import type { ErrorDetail, IndependentJob, IndependentRequest } from './production'
export interface FlowCasting {catalog_sha256:string;bindings:Record<string,string>;initial_outfits:Record<string,string>;casting_confirmed:true}
export interface FlowRecovery {state:'active'|'complete'|'unknown'|'remake'|'resumable'|'cancelled';message:string;actions:('cancel'|'reconcile'|'resume'|'remake')[];remake_kind:'restart'|'audio_full'|'stage_basic'}
export interface ProductionFlow {
  id:string;work_id:string;parent_id:string|null;title:string;status:IndependentJob['status'];revision:number;error_code:string|null;error_detail?:ErrorDetail;created_at:string
  updated_at:string;kind:string;stage_mode?:string;output:'script'|'audio'|'h5';casting:FlowCasting|null;jobs:{sound?:IndependentJob;stage?:IndependentJob};sound_job_id:string|null;stage_job_id:string|null
  available_outputs:string[];offstage_roles:{role_id:string;name:string}[];actual_calls:number
  recovery?:FlowRecovery
}
export interface FlowInput {idempotency_key:string;source:Extract<IndependentRequest['source'],{kind:'creative'}>;casting:FlowCasting|null;output?:'script'|'audio'|'h5';draft_id?:string;expected_draft_revision?:number}
