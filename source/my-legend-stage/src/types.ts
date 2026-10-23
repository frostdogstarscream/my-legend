export interface AssetRef { sha256:string; size_bytes:number; media_type:string }
export interface ActorAsset {
  actor_id:string; display_name:string; version:string; format:string;
  skeleton:AssetRef; atlas:AssetRef; pages:Record<string,AssetRef>;
  animations:Record<string,{duration:number}>; idle_animation:string;
  mouth_control:false; alpha_mode:'premultiplied-alpha'|'premultiply-alpha-on-upload';
}
export interface StageCatalog {
  schema_version:'spine_preview_catalog/1.0'; catalog_sha256:string;
  mouth_control:false; runtime:AssetRef; actors:ActorAsset[];
}
export interface StageActor {
  role_id:string; name:string; actor_id:string; actor_version:string;
  x:number; animation:string; loop:boolean;
}
export interface StageCue {
  source:{episode_id:string;scene_id:string;line_id:string};
  role_id:string|null; speaker:string; text:string; start_frame:number; end_frame:number;
}
export interface StageScript {
  schema_version:'spine_stage_script/1.0'; sample_rate:number; total_frames:number;
  actors:StageActor[]; cues:StageCue[];
}
export interface StagePreview {
  schema_version:'spine_stage_preview/1.0'; job_id:string; title:string;
  source_version_id:string; preview_sha256:string; catalog_sha256:string; mouth_control:false;
  audio:{artifact:AssetRef & {kind:string;source_version_id:string};content_path:string};
  timeline:{artifact:AssetRef & {kind:string;source_version_id:string};content_path:string};
  stage:StageScript; warnings:string[];
}

export function cuesAt(script:StageScript, seconds:number):StageCue[] {
  const frame=Math.max(0,seconds)*script.sample_rate;
  return script.cues.filter(c=>c.start_frame<=frame&&frame<c.end_frame);
}
export function actorTime(seconds:number, duration:number, loop:boolean):number {
  const t=Math.max(0,Number.isFinite(seconds)?seconds:0);
  return duration>0?(loop?t%duration:Math.min(t,duration)):0;
}
