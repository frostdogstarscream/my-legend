import {Application,Container,Graphics,ImageSource,Mesh,MeshGeometry,Texture} from 'pixi.js'
import {installLegacyFixes,expandLegacyDeforms} from './legacy-fixes'
import type {BasicFaceRuntime} from './basic-face'
import {actorTime,type AssetRef} from './types'
import type {ActorState,Calibration,LifeAsset,LifeBinding,LifeCatalog,LifeOutfit,LifeSkeleton,LifeState} from './life-types'
import {SceneDecor} from './SceneDecor'
import type {SceneDefinition} from './scene-types'

type Legacy=any
type FetchAsset=(ref:AssetRef)=>Promise<ArrayBuffer>
type Part={slot:string;asset:LifeSkeleton;skeleton:Legacy;meshes:Map<Legacy,Mesh>}
type Variant={outfit?:LifeOutfit;parts:Part[];visual:Container;accessory?:{mesh:Mesh;bone:Legacy;local:number[];hidden:string[]}}
type Entity={binding:LifeBinding;asset:LifeAsset;root:Container;shadow?:Graphics;variants:Map<string,Variant>;scale:number;seatScale:number;center:number;floor:number;actionFloors:Map<string,number>}
const layerBands:Record<string,number>={HairLayer:0,LHandLayer:10,FootLayer:20,BodyLayer:30,FaceLayer:40,RHandLayer:50,FrontHairLayer:60}
const blank:Calibration={x:0,y:0,scale:1,rotation:0,flip:false,layer:0,actions:{}}

/** Multi-skeleton stage adapter. Every draw is a fresh absolute-time sample. */
export class LifeStage {
  private app=new Application()
  private world=new Container()
  private core:Legacy
  private entities:Entity[]=[]
  private textures=new Map<string,Texture>()
  private initialized=false
  private disposed=false
  private moduleUrl=''
  private lastState:LifeState|undefined
  private face:BasicFaceRuntime|undefined
  private room:SceneDecor|undefined
  private rooms=new Map<string,SceneDecor>()
  setFace(face:BasicFaceRuntime){this.face=face}
  get faceDiagnostics(){return this.face?.diagnostics||[]}
  get cameraTransform(){return {x:this.world.x,y:this.world.y,zoom:this.world.scale.x}}
  readonly width=960
  readonly height=540

  async mount(host:HTMLElement,catalog:LifeCatalog,bindings:LifeBinding[],fetchAsset:FetchAsset,scene?:SceneDefinition){
    try{
      await this.app.init({width:this.width,height:this.height,backgroundAlpha:0,antialias:true,autoStart:false,sharedTicker:false,
        preference:'webgl',resolution:Math.min(window.devicePixelRatio||1,2)})
      this.initialized=true
      if(this.disposed){this.cleanup();return}
      this.app.stage.addChild(this.world)
      this.world.sortableChildren=true
      if(scene){this.room=new SceneDecor(scene);await this.room.mount(this.world,fetchAsset)}
      if(this.disposed){this.cleanup();return}
      this.app.canvas.style.width='100%';this.app.canvas.style.height='100%'
      this.app.canvas.setAttribute('aria-label','生活演绎舞台')
      const runtime=await fetchAsset(catalog.runtime)
      this.moduleUrl=URL.createObjectURL(new Blob([runtime],{type:'text/javascript'}))
      this.core=(await import(/* @vite-ignore */this.moduleUrl)).default
      installLegacyFixes(this.core)
      URL.revokeObjectURL(this.moduleUrl);this.moduleUrl=''
      for(const binding of bindings){
        if(this.disposed)break
        const asset=catalog.actors.find(a=>a.actor_id===binding.actor_id&&a.revision===binding.actor_revision)
        if(!asset)throw Error('演员版本不一致')
        const entity:Entity={binding,asset,root:new Container(),variants:new Map(),scale:1,seatScale:1,center:0,floor:0,actionFloors:new Map()}
        this.entities.push(entity);this.world.addChild(entity.root)
        if(scene?.blocking){
          entity.shadow=new Graphics().ellipse(0,0,42,8).fill({color:0x172622,alpha:.16})
          this.world.addChild(entity.shadow)
        }
        const outfits=asset.outfit_ids.length?catalog.outfits.filter(o=>asset.outfit_ids.includes(o.outfit_id)):[undefined]
        for(const outfit of outfits){
          const variant:Variant={outfit,parts:[],visual:new Container()}
          variant.visual.sortableChildren=true
          entity.variants.set(outfit?.outfit_id||'',variant);entity.root.addChild(variant.visual)
          for(const [slot,id] of Object.entries(outfit?.parts||{full:asset.asset_id!})){
            const skeleton=catalog.skeletons.find(s=>s.asset_id===id)
            if(!skeleton)throw Error('服装部件缺失')
            variant.parts.push(await this.part(skeleton,slot,fetchAsset))
          }
          if(asset.face_profile){
            const profile=asset.face_profile,head=variant.parts.find(p=>p.slot==='head')
            if(!head||head.asset.asset_id!==profile.head_asset_id||head.asset.skeleton.sha256!==profile.head_sha256)throw Error('固定头部配置版本不匹配')
            const required=[...Object.values(profile.eyes),{[profile.mouth.slot]:profile.mouth.closed},{[profile.mouth.slot]:profile.mouth.open}]
            for(const preset of required)for(const [slot,name] of Object.entries(preset))if(!head.skeleton.getAttachmentBySlotName(slot,name))throw Error('面部附件缺失：'+name)
          }
          if(outfit?.accessory)await this.prepareAccessory(variant,fetchAsset)
          this.pose(entity,variant,{instance_id:binding.instance_id,position:binding.position,facing:1,visible:true,
            outfit_id:outfit?.outfit_id||null,action:'idle',time:0,mode:'loop',speaking:false})
        }
        const reference=entity.variants.get(asset.normalization_outfit_id||binding.initial_outfit_id||'')
        if(!reference)throw Error('初始服装不存在')
        // Use one fixed reference for all actions/outfits, including its calibrated transforms.
        const bounds=reference.visual.getLocalBounds()
        if(!Number.isFinite(bounds.width)||bounds.width<=0||bounds.height<=0)throw Error('角色无可显示的有效几何')
        entity.scale=Math.min(300/bounds.height,235/bounds.width)
        entity.seatScale=entity.scale
        if(asset.face_profile){
          // Hair and ornaments must not change a commander's body size. Measure
          // the fixed face once in the reference idle pose, before any scaling.
          const head=reference.parts.find(p=>p.slot==='head')
          const face=head?.skeleton.findSlot('face')?.attachment
          const faceWidth=face?head?.meshes.get(face)?.getBounds().width:undefined
          if(!faceWidth||!Number.isFinite(faceWidth))throw Error('固定人物缺少有效脸部尺寸')
          entity.scale=104/faceWidth
        }
        entity.center=bounds.x+bounds.width/2;entity.floor=bounds.y
        // Dorm rigs use a different origin while hanging/lying. Keep each native
        // action's lowest sampled point on the stage baseline, retaining motion.
        if(asset.asset_mode==='single_skeleton'){
          for(const action of Object.keys(asset.action_map)){
            let floor=Infinity
            for(let i=0;i<=24;i++){
              this.pose(entity,reference,{instance_id:binding.instance_id,position:binding.position,facing:1,visible:true,
                outfit_id:binding.initial_outfit_id,action,time:(binding.actions[action]||0)*i/24,mode:'once',speaking:false})
              floor=Math.min(floor,reference.visual.getLocalBounds().y)
            }
            entity.actionFloors.set(action,floor)
          }
        }
        for(const variant of entity.variants.values()){
          variant.visual.scale.set(entity.scale,-entity.scale)
          variant.visual.position.set(-entity.center*entity.scale,entity.floor*entity.scale)
          variant.visual.visible=variant===reference
        }
      }
      if(this.disposed){this.cleanup();return}
      host.appendChild(this.app.canvas)
    }catch(e){this.destroy();throw e}
  }

  private async part(asset:LifeSkeleton,slot:string,fetchAsset:FetchAsset):Promise<Part>{
    const pages:Record<string,Texture>={}
    for(const [name,ref] of Object.entries(asset.pages)){
      if(!this.textures.has(ref.sha256)){
        const bytes=await fetchAsset(ref),url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}))
        try{
          const image=new Image();image.src=url
          try{await image.decode()}catch(e){throw Error('人物贴图解码失败：'+ref.sha256+' · '+String(e))}
          if(this.disposed)throw Error('舞台加载已取消')
          this.textures.set(ref.sha256,new Texture({source:new ImageSource({resource:image,alphaMode:asset.alpha_mode,scaleMode:'linear'})}))
        }finally{URL.revokeObjectURL(url)}
      }
      pages[name]=this.textures.get(ref.sha256)!
    }
    const [atlasBytes,rawBytes]=await Promise.all([fetchAsset(asset.atlas),fetchAsset(asset.skeleton)])
    const atlas=new this.core.Atlas(new TextDecoder().decode(atlasBytes),{load:(page:Legacy,name:string)=>{
      if(!pages[name])throw Error('图集页面缺失：'+name)
      page.rendererObject=pages[name];page.width=pages[name].width;page.height=pages[name].height
    },unload:()=>{}})
    const raw=expandLegacyDeforms(JSON.parse(new TextDecoder().decode(rawBytes)))
    const data=new this.core.SkeletonJson(new this.core.AtlasAttachmentLoader(atlas)).readSkeletonData(raw)
    data.bones.forEach((b:Legacy,i:number)=>{b.flipX=raw.bones[i].flipX;b.flipY=raw.bones[i].flipY})
    data.slots.forEach((s:Legacy,i:number)=>{s.lifeBlend=raw.slots[i].blendMode??(raw.slots[i].additive==='true'?1:0)})
    data.animations.forEach((a:Legacy)=>{if(!asset.animations[a.name])throw Error('动作清单不一致');a.duration=asset.animations[a.name].duration})
    const skeleton=new this.core.Skeleton(data)
    skeleton.slots.forEach((s:Legacy)=>{s.attachmentVertices=[]})
    return {slot,asset,skeleton,meshes:new Map()}
  }

  async prepareScenes(entries:{key:string;scene:SceneDefinition}[],fetchAsset:FetchAsset){
    for(const [index,entry] of entries.entries()){
      if(this.disposed)throw Error('舞台已关闭')
      if(this.rooms.has(entry.key))continue
      const decor=index===0&&this.room?this.room:new SceneDecor(entry.scene)
      this.rooms.set(entry.key,decor)
      if(decor!==this.room)await decor.mount(this.world,fetchAsset)
      decor.setVisible(index===0)
    }
    if(entries[0])this.setScene(entries[0].key)
  }
  setScene(key:string){
    const next=this.rooms.get(key);if(!next)throw Error('场景未预加载')
    for(const room of this.rooms.values())room.setVisible(room===next)
    this.room=next
  }
  private async prepareAccessory(variant:Variant,fetchAsset:FetchAsset){
    const spec=variant.outfit!.accessory!,head=variant.parts.find(p=>p.slot==='head')!
    head.skeleton.setToSetupPose();head.skeleton.data.findAnimation('wait').apply(head.skeleton,-1,0,false,null);head.skeleton.updateWorldTransform()
    const bone=head.skeleton.findBone(spec.bone);if(!bone)throw Error('束发绑定骨骼缺失')
    const [l,b,r,t]=spec.world_at_wait0,world=[l,t,r,t,r,b,l,b],local:number[]=[],d=bone.m00*bone.m11-bone.m01*bone.m10
    if(Math.abs(d)<1e-8)throw Error('束发绑定矩阵不可逆')
    for(let i=0;i<world.length;i+=2){const x=world[i]-bone.worldX,y=world[i+1]-bone.worldY;local.push((x*bone.m11-y*bone.m01)/d,(y*bone.m00-x*bone.m10)/d)}
    if(!this.textures.has(spec.image.sha256)){
      const bytes=await fetchAsset(spec.image),url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}))
      try{const image=new Image();image.src=url;await image.decode();if(this.disposed)throw Error('舞台已关闭');this.textures.set(spec.image.sha256,new Texture({source:new ImageSource({resource:image,alphaMode:'premultiply-alpha-on-upload',scaleMode:'linear'})}))}finally{URL.revokeObjectURL(url)}
    }
    const mesh=new Mesh({geometry:new MeshGeometry({positions:new Float32Array(world),uvs:new Float32Array([0,0,1,0,1,1,0,1]),indices:new Uint32Array([0,1,2,2,3,0])}),texture:this.textures.get(spec.image.sha256)!})
    mesh.zIndex=60100;variant.visual.addChild(mesh);variant.accessory={mesh,bone,local,hidden:spec.hidden_slots}
  }
  private expression(sk:Legacy,preset:ActorState['expression']){
    if(!preset||preset==='native')return
    const choices:Record<string,string[]>={Leye1:preset==='closed'?['Leye5','eyeL5']:['Leye1','eyeL1'],Reye1:preset==='closed'?['Reye5','eyeR5']:['Reye1','eyeR1'],mouth1:preset==='talk'?['mouth4']:['mouth1']}
    for(const [slot,names] of Object.entries(choices)){const name=names.find(n=>sk.getAttachmentBySlotName(slot,n));if(!name)throw Error('该头部不支持表情：'+preset);sk.setAttachment(slot,name)}
  }

  private vertices(a:Legacy,slot:Legacy,sk:Legacy):Float32Array|null{
    if(a.type===this.core.AttachmentType.region){const v=new Float32Array(8);a.computeVertices(sk.x,sk.y,slot.bone,v);return v}
    if(a.type===this.core.AttachmentType.mesh||a.type===this.core.AttachmentType.skinnedmesh){const v=new Float32Array(a.uvs.length);a.computeWorldVertices(sk.x,sk.y,slot,v);return v}
    return null
  }

  private pose(entity:Entity,variant:Variant,state:ActorState){
    try{this.poseCore(entity,variant,state)}catch(e){
      if(!this.face?.disable(state.instance_id,e))throw e
      // Retry the complete pose and mesh pass from setup + native animation this frame.
      this.poseCore(entity,variant,state)
    }
  }
  private poseCore(entity:Entity,variant:Variant,state:ActorState){
    const name=entity.asset.action_map[state.action]
    if(!name||variant.outfit&&!variant.outfit.allowed_actions.includes(state.action))throw Error('当前服装不支持动作：'+state.action)
    for(const part of variant.parts){
      const sk=part.skeleton,animation=sk.data.findAnimation(name)
      if(!animation)throw Error('部件缺少动作：'+name)
      sk.setToSetupPose()
      animation.apply(sk,-1,actorTime(state.time,animation.duration,state.mode==='loop'),false,null)
      this.face?.apply(sk,part.asset,part.slot,state,this.lastState?.sample_frame||0)
      if(part.slot==='head'){
        const profile=entity.asset.face_profile
        if(profile){
          if(profile.head_asset_id!==part.asset.asset_id||profile.head_sha256!==part.asset.skeleton.sha256)throw Error('指挥官头部配置版本不匹配')
          const changes={...(profile.eyes[state.expression||'native']||{})}
          if(state.mouth&&state.mouth!=='native')changes[profile.mouth.slot]=profile.mouth[state.mouth]
          for(const [slot,name] of Object.entries(changes)){if(!sk.getAttachmentBySlotName(slot,name))throw Error('面部附件缺失：'+name);sk.setAttachment(slot,name)}
        }else this.expression(sk,state.expression)
      }
      sk.updateWorldTransform()
      for(const mesh of part.meshes.values())mesh.visible=false
      const base=variant.outfit?.calibration[part.slot]||blank,c={...base,...base.actions?.[name]}
      let band=part.slot==='foot'?20:part.slot==='body'?30:40,index=0
      for(const slot of sk.drawOrder){
        if(slot.data.name in layerBands)band=layerBands[slot.data.name]
        if(part.slot==='head'&&variant.accessory?.hidden.includes(slot.data.name))continue
        const attachment=slot.attachment;if(!attachment)continue
        const positions=this.vertices(attachment,slot,sk);if(!positions)continue
        if(!positions.every(Number.isFinite))throw Error('角色顶点无效')
        let mesh=part.meshes.get(attachment)
        if(!mesh){
          const geometry=new MeshGeometry({positions,uvs:new Float32Array(attachment.uvs),indices:new Uint32Array(
            attachment.type===this.core.AttachmentType.region?[0,1,2,2,3,0]:attachment.triangles)})
          mesh=new Mesh({geometry,texture:attachment.rendererObject.page.rendererObject});part.meshes.set(attachment,mesh);variant.visual.addChild(mesh)
        }else{mesh.geometry.positions.set(positions);mesh.geometry.getBuffer('aPosition').update()}
        mesh.visible=true
        const dim=this.lastState?.actors.some(a=>a.speaking)&&!state.speaking? .72:1
        const rgb=[slot.r*attachment.r,slot.g*attachment.g,slot.b*attachment.b].map(v=>Math.round(Math.max(0,Math.min(1,v))*255*dim))
        mesh.tint=(rgb[0]<<16)|(rgb[1]<<8)|rgb[2];mesh.alpha=slot.a*attachment.a
        mesh.blendMode=(['normal','add','multiply','screen'] as const)[slot.data.lifeBlend]||'normal'
        mesh.position.set(c.x,c.y);mesh.scale.set(c.scale*(c.flip?-1:1),c.scale);mesh.rotation=c.rotation*Math.PI/180
        mesh.zIndex=(variant.outfit?band+c.layer:0)*1000+index++
      }
    }
    if(variant.accessory){const {mesh,bone,local}=variant.accessory;mesh.geometry.positions.set(local.map((v,i)=>i%2?bone.m10*local[i-1]+bone.m11*v+bone.worldY:bone.m00*v+bone.m01*local[i+1]+bone.worldX));mesh.geometry.getBuffer('aPosition').update()}
    variant.visual.sortChildren()
  }

  render(state:LifeState){
    if(this.disposed||!this.initialized)return
    this.lastState=state
    for(const entity of this.entities){
      const actor=state.actors.find(s=>s.instance_id===entity.binding.instance_id)
      if(!actor)throw Error('舞台实例缺失')
      const selected=entity.variants.get(actor.outfit_id||'')
      if(!selected)throw Error('服装尚未加载')
      entity.root.visible=actor.visible
      entity.root.position.set(actor.position[0]*this.width,actor.position[1]*this.height)
      const scale=this.room?.definition.actor_scale||1
      entity.root.scale.set(actor.facing*scale,scale)
      const seat=this.room?.definition.seats.find(s=>s.seat_id===actor.seat_id)
      const furniture=this.room?.definition.objects.find(o=>o.id===(actor.seat_object_id||seat?.object_id))
      entity.root.zIndex=this.room?(furniture?furniture.depth_y*10+5:actor.position[1]*this.height*10+5):this.entities.indexOf(entity)
      if(entity.shadow){
        entity.shadow.visible=actor.visible;entity.shadow.position.copyFrom(entity.root.position)
        entity.shadow.scale.set(scale/.64);entity.shadow.zIndex=entity.root.zIndex-1
      }
      for(const variant of entity.variants.values())variant.visual.visible=variant===selected
      this.pose(entity,selected,actor)
      if(entity.actionFloors.has(actor.action))selected.visual.y=entity.actionFloors.get(actor.action)!*entity.scale
      if(actor.seat_id&&entity.scale!==entity.seatScale){
        // Saved seat offsets were authored with the old silhouette scale.
        // Keep the same hip contact while displaying the new body proportion.
        const hip=selected.parts.find(p=>p.slot==='body')?.skeleton.findBone('hip')
        if(hip){
          const delta=(entity.seatScale-entity.scale)*scale
          entity.root.x+=(hip.worldX-entity.center)*delta*actor.facing
          entity.root.y-=(hip.worldY-entity.floor)*delta
        }
      }
    }
    const {zoom,x}=state.camera
    const cameraX=this.room?Math.max(.5/zoom,Math.min(1-.5/zoom,x)):x
    this.world.scale.set(zoom);this.world.position.set(this.width/2-cameraX*this.width*zoom,this.height*(1-zoom)*.825)
    this.world.sortChildren()
    this.app.render()
  }

  capture(){this.app.render();return this.app.canvas.toDataURL('image/png')}
  snapshot(){
    return {state:this.lastState,scene:this.room?.snapshot(),camera:{x:this.world.x,y:this.world.y,zoom:this.world.scale.x},face_diagnostics:this.face?.diagnostics||[],texture_count:this.textures.size,actors:this.entities.map(e=>{
      const v=[...e.variants.values()].find(v=>v.visual.visible)!
      const hip=v.parts.find(p=>p.slot==='body')?.skeleton.findBone('hip');const hipPoint=hip?v.visual.toGlobal({x:hip.worldX,y:hip.worldY}):undefined
      const reference=e.variants.get(e.asset.normalization_outfit_id||e.binding.initial_outfit_id||'')!
      const bounds=(visual:Container)=>{const b=visual.getLocalBounds();return {x:b.x,y:b.y,width:b.width,height:b.height}}
      return {instance_id:e.binding.instance_id,scale:e.scale,reference_bounds:bounds(reference.visual),visible_bounds:bounds(v.visual),hip_stage:hipPoint?{x:hipPoint.x,y:hipPoint.y}:null,position:[e.root.x,e.root.y],parts:v.parts.map(p=>({slot:p.slot,
        attachments:p.skeleton.slots.map((s:Legacy)=>({slot:s.data.name,attachment:s.attachment?.name||null})),
        bones:p.skeleton.bones.map((b:Legacy)=>[b.worldX,b.worldY,b.m00,b.m01,b.m10,b.m11]),
        meshes:[...p.meshes.values()].filter(m=>m.visible).map(m=>({vertices:Array.from(m.geometry.positions),uv:Array.from(m.geometry.uvs),indices:Array.from(m.geometry.indices),z:m.zIndex}))}))}
    })}
  }
  private cleanup(){
    for(const room of new Set([this.room,...this.rooms.values()]))room?.destroy();this.rooms.clear();this.room=undefined
    for(const e of this.entities)for(const v of e.variants.values())if(v.accessory){v.accessory.mesh.geometry.destroy();v.accessory.mesh.destroy()}
    for(const e of this.entities)for(const v of e.variants.values())for(const p of v.parts)for(const m of p.meshes.values()){m.geometry.destroy();m.destroy()}
    this.entities=[]
    // Pixi 8 treats boolean `true` as releasing global pools. During an atomic
    // stage swap another renderer is still alive; only destroy this instance.
    // Release texture listeners while this renderer is still available.
    for(const texture of this.textures.values())texture.destroy(true)
    this.textures.clear()
    if(this.initialized){this.initialized=false;this.app.destroy({removeView:true,releaseGlobalResources:false},{children:true})}
    if(this.moduleUrl){URL.revokeObjectURL(this.moduleUrl);this.moduleUrl=''}
  }
  destroy(){this.disposed=true;this.cleanup()}
}
