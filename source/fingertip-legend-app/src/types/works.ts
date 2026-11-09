import type {FlowCasting,FlowRecovery} from './flow'
import type {PageId} from '../utils/navigation'
export interface CreationPayload {
 source:{kind:'creative';title:string;idea:string;genre:string;character_ids:string[];target_duration_seconds:number|null}
 output:'script'|'audio'|'h5';casting:FlowCasting|null
}
export interface CreationDraft {id:string;work_id:string;payload:CreationPayload;revision:number;flow_id:string|null;legacy_story_id:string|null;created_at:string;updated_at:string}
export interface WorkDestination {page:PageId;query:Record<string,string>}
export interface WorkSummary {id:string;kind:'flow'|'job'|'draft'|'legacy';title:string;status:string;updated_at:string;available_outputs:string[];available_actions:string[];latest_flow_id:string|null;playable_flow_id:string|null;open:WorkDestination;play:WorkDestination|null;kind_of_version?:string;recovery?:FlowRecovery}

