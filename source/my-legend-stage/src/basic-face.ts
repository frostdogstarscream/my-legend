import type {ActorState,LifeCatalog,LifeDiagnostic,LifeScript,LifeSkeleton} from './life-types'
import {checkCueRanges,checkTrackBindings,type Emotion,type FaceTrack} from './performance-tracks'

export interface FaceProfile {
  actor_id:string;actor_revision:string;asset_id:string;source_filename:string;original_sha256:string;skeleton_sha256:string;part_slot:string;actions:string[];
  mouth?:{slot:string;closed:string;open:string;period_ms:number;open_start_ms:number;open_end_ms:number;lead_ms:number;tail_ms:number};
  blink?:{slots:{slot:string;closed:string}[];period_ms:number;offset_ms:number;duration_ms:number};
  eyes?:{slot:string;rest:string;closed:string}[];
  emotions?:Partial<Record<Exclude<Emotion,'neutral'>,{label:string;qa:'visual_checked';attachments:Record<string,string>}>>;
}
export interface BasicFace {
  schema_version:'legend_basic_face/1'|'legend_expression_face/1';profile_sha256:string;
  profile_set:{version:string;algorithm_version:string;profiles:FaceProfile[]};
  bindings:Record<string,string>;diagnostics:(LifeDiagnostic&{instance_id:string})[];
  track?:FaceTrack;
}
const canonical=(v:any):string=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}'
export async function profileHash(value:unknown){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)))),v=>v.toString(16).padStart(2,'0')).join('')}
export function matchesProfile(p:FaceProfile,revision:string,asset:LifeSkeleton){return p.actor_revision===revision&&p.asset_id===asset.asset_id&&p.source_filename===asset.source_filename&&p.original_sha256===asset.original.sha256&&p.skeleton_sha256===asset.skeleton.sha256}

/** Version 1 uses audio sample positions only, including deterministic blink phase. */
export function faceAttachments(p:FaceProfile,script:LifeScript,state:ActorState,frame:number):Record<string,string>{
  if(!state.visible||!p.actions.includes(state.action))return {}
  const result:Record<string,string>={},ms=frame/script.sample_rate*1000
  if(p.mouth){
    const m=p.mouth,binding=script.actors.find(a=>a.instance_id===state.instance_id)
    const line=script.subtitles.find(s=>s.role_id===binding?.role_id&&s.start_frame<=frame&&frame<s.end_frame)
    if(line){
      const elapsed=(frame-line.start_frame)/script.sample_rate*1000,remaining=(line.end_frame-frame)/script.sample_rate*1000,phase=elapsed%m.period_ms
      result[m.slot]=elapsed>=m.lead_ms&&remaining>m.tail_ms&&phase>=m.open_start_ms&&phase<m.open_end_ms?m.open:m.closed
    }
  }
  if(p.blink){const b=p.blink;if(ms>=b.offset_ms&&(ms-b.offset_ms)%b.period_ms<b.duration_ms)for(const s of b.slots)result[s.slot]=s.closed}
  return result
}

/** A blink always wins over expression; outside its window the active expression resumes. */
export function expressionAttachments(p:FaceProfile,script:LifeScript,state:ActorState,frame:number,track:FaceTrack,native:Record<string,string>):Record<string,string>{
  const result=faceAttachments(p,script,state,frame)
  if(!state.visible||!p.actions.includes(state.action)||!p.eyes)return result
  const cues=track.cues.filter(c=>c.enabled&&c.instance_id===state.instance_id&&c.start_frame<=frame&&frame<c.end_frame)
  const emotion=cues.find(c=>c.type==='emotion')?.emotion
  const blinking=cues.some(c=>c.type==='blink')||p.eyes.some(e=>native[e.slot]===e.closed||result[e.slot]===e.closed)
  if(emotion&&emotion!=='neutral')Object.assign(result,p.emotions?.[emotion]?.attachments)
  if(blinking)for(const eye of p.eyes)result[eye.slot]=eye.closed
  return result
}

/** Errors are contained here; body animation is sampled before every application. */
export class BasicFaceRuntime {
  readonly diagnostics:(LifeDiagnostic&{instance_id?:string})[]=[]
  private profiles=new Map<string,FaceProfile>()
  private disabled=new Set<string>()
  private track:FaceTrack|undefined
  private constructor(private script:LifeScript){}
  private note(code:string,message:string,instance_id?:string){if(!this.diagnostics.some(d=>d.code===code&&d.instance_id===instance_id))this.diagnostics.push({code,message,instance_id})}
  disable(instance_id:string,error:unknown){
    if(!this.profiles.has(instance_id)||this.disabled.has(instance_id))return false
    this.disabled.add(instance_id);this.note('FACE_RUNTIME_FALLBACK',String(error)+'；该演员恢复原生动作',instance_id);return true
  }
  static async prepare(script:LifeScript,catalog:LifeCatalog,face?:BasicFace){
    const runtime=new BasicFaceRuntime(script)
    if(!face)return runtime
    try{
      const basic=face.schema_version==='legend_basic_face/1'&&face.profile_set.algorithm_version==='basic_face/1'&&face.profile_set.version==='basic_face_profiles/1'
      const expressive=face.schema_version==='legend_expression_face/1'&&face.profile_set.algorithm_version==='expression_face/1'&&face.profile_set.version==='expression_profiles/1'
      if(!basic&&!expressive)throw Error('不支持的面部算法或 Profile 版本')
      if(await profileHash(face.profile_set)!==face.profile_sha256)throw Error('面部 Profile 内容校验失败')
      for(const key of ['job_id','source_version_id','catalog_sha256','audio_sha256','timeline_sha256'] as const)if(face.bindings[key]!==script[key])throw Error('面部与音频／素材版本不一致')
      if(catalog.catalog_sha256!==script.catalog_sha256)throw Error('面部素材目录不一致')
      if(expressive){
        const t=face.track
        if(!t||t.schema_version!=='legend_face_track/1'||t.profile_sha256!==face.profile_sha256||t.cues.length>512)throw Error('表情轨道 Profile 或版本无效')
        checkTrackBindings(script,t.bindings);checkCueRanges(script,t.cues)
        for(const c of t.cues){
          const actor=script.actors.find(a=>a.instance_id===c.instance_id),p=face.profile_set.profiles.find(p=>p.actor_id===actor?.actor_id)
          if(!p?.eyes||!['emotion','blink'].includes(c.type)||c.type==='blink'&&c.emotion!==null||c.type==='emotion'&&(!c.emotion||c.emotion!=='neutral'&&!p.emotions?.[c.emotion]))throw Error('表情指令类型或角色语义无效')
          if(c.enabled&&t.cues.some(o=>o!==c&&o.enabled&&o.instance_id===c.instance_id&&o.type===c.type&&Math.max(c.start_frame,o.start_frame)<Math.min(c.end_frame,o.end_frame)))throw Error('同类表情轨道重叠')
        }
        runtime.track=t
      }
      for(const a of script.actors){
        const p=face.profile_set.profiles.find(p=>p.actor_id===a.actor_id),asset=catalog.skeletons.find(s=>s.asset_id===p?.asset_id)
        const actor=catalog.actors.find(v=>v.actor_id===a.actor_id&&v.revision===a.actor_revision)
        if(!p||!asset||actor?.asset_id!==p.asset_id||!matchesProfile(p,a.actor_revision,asset))runtime.note('FACE_PROFILE_UNAVAILABLE','素材版本没有匹配的面部 Profile，使用原生动作',a.instance_id)
        else runtime.profiles.set(a.instance_id,p)
      }
    }catch(e){runtime.profiles.clear();runtime.note('FACE_DISABLED',String(e)+'；身体、字幕与音频继续')}
    return runtime
  }
  apply(skeleton:any,asset:LifeSkeleton,part:string,state:ActorState,frame:number){
    const p=this.profiles.get(state.instance_id)
    if(!p||this.disabled.has(state.instance_id)||p.part_slot!==part)return
    const previous:{slot:any;attachment:any;vertices:any[]}[]=[]
    try{
      if(asset.skeleton.sha256!==p.skeleton_sha256)throw Error('实际渲染素材版本不匹配')
      // Validate every capability up front, including attachments not visible in this frame.
      const required:Record<string,string[]>=p.mouth?{[p.mouth.slot]:[p.mouth.closed,p.mouth.open]}:Object.fromEntries((p.blink?.slots||[]).map(s=>[s.slot,[s.closed]]))
      if(this.track)for(const eye of p.eyes||[])required[eye.slot]=[eye.rest,eye.closed,...Object.values(p.emotions||{}).map(e=>e!.attachments[eye.slot])]
      for(const [slot,names] of Object.entries(required))for(const name of names){const index=skeleton.findSlotIndex(slot);if(index<0||!skeleton.getAttachmentBySlotIndex(index,name))throw Error('缺少面部附件 '+slot+'/'+name)}
      const native=Object.fromEntries((p.eyes||[]).map(e=>[e.slot,skeleton.findSlot(e.slot)?.attachment?.name]))
      const changes=this.track?expressionAttachments(p,this.script,state,frame,this.track,native):faceAttachments(p,this.script,state,frame)
      for(const [name,attachment] of Object.entries(changes)){
        const slot=skeleton.findSlot(name);previous.push({slot,attachment:slot.attachment,vertices:[...(slot.attachmentVertices||[])]})
        skeleton.setAttachment(name,attachment)
      }
      this.note('FACE_ACTIVE','基础面部附件已校验并启用',state.instance_id)
    }catch(e){
      for(const v of previous){v.slot.attachment=v.attachment;v.slot.attachmentVertices=v.vertices}
      this.disable(state.instance_id,e)
    }
  }
}
