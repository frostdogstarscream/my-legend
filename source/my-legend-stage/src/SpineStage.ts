import { Application, Container, ImageSource, Mesh, MeshGeometry, Texture } from 'pixi.js'
import { actorTime, type ActorAsset, type AssetRef, type StageActor, type StageCatalog } from './types'
import { installLegacyFixes,expandLegacyDeforms } from './legacy-fixes'

// The 2015 runtime is loaded only from the local, hash-pinned catalog. It is not
// bundled into the public source or coupled to the later Spine 4.x adapter.
type Legacy = any
type FetchAsset = (ref:AssetRef)=>Promise<ArrayBuffer>

async function imageTexture(data:ArrayBuffer, alphaMode:ActorAsset['alpha_mode']):Promise<Texture> {
  const url=URL.createObjectURL(new Blob([data],{type:'image/png'}))
  try {
    const img=new Image();img.src=url;await img.decode()
    return new Texture({source:new ImageSource({resource:img,alphaMode,scaleMode:'linear'})})
  } finally {URL.revokeObjectURL(url)}
}

export class SpineStage {
  private app=new Application()
  private actors:{binding:StageActor;asset:ActorAsset;skeleton:Legacy;container:Container;meshes:Map<Legacy,Mesh>;bounds:number[]}[]=[]
  private textures:Texture[]=[]
  private disposed=false
  private initialized=false
  private moduleUrl=''
  private core:Legacy
  readonly width=960
  readonly height=540

  async mount(host:HTMLElement,catalog:StageCatalog,bindings:StageActor[],fetchAsset:FetchAsset) {
    try {
      await this.app.init({width:this.width,height:this.height,backgroundAlpha:0,antialias:true,
        autoStart:false,sharedTicker:false,preference:'webgl',resolution:Math.min(window.devicePixelRatio||1,2)})
      this.initialized=true
      if(this.disposed){this.cleanup();return}
      this.app.canvas.style.width='100%';this.app.canvas.style.height='100%'
      this.app.canvas.setAttribute('aria-label','Spine 角色舞台')
      host.appendChild(this.app.canvas)
      const script=await fetchAsset(catalog.runtime)
      if(this.disposed)return
      this.moduleUrl=URL.createObjectURL(new Blob([script],{type:'text/javascript'}))
      this.core=(await import(/* @vite-ignore */this.moduleUrl)).default
      installLegacyFixes(this.core)
      URL.revokeObjectURL(this.moduleUrl);this.moduleUrl=''
      for(const binding of bindings) {
        if(this.disposed)return
        const asset=catalog.actors.find(a=>a.actor_id===binding.actor_id&&a.version===binding.actor_version)
        if(!asset)throw new Error('演员资源版本不一致')
        const pages:Record<string,Texture>={}
        for(const [name,ref] of Object.entries(asset.pages)) {
          const bytes=await fetchAsset(ref)
          if(this.disposed)return
          const tex=await imageTexture(bytes,asset.alpha_mode)
          if(this.disposed){tex.destroy(true);return}
          pages[name]=tex;this.textures.push(tex)
        }
        const [atlasBytes,skeletonBytes]=await Promise.all([fetchAsset(asset.atlas),fetchAsset(asset.skeleton)])
        if(this.disposed)return
        const atlas=new this.core.Atlas(new TextDecoder().decode(atlasBytes),{
          load:(page:Legacy,name:string)=>{
            const texture=pages[name];if(!texture)throw new Error('角色贴图不完整')
            page.rendererObject=texture;page.width=texture.width;page.height=texture.height
          },unload:()=>{},
        })
        const raw=expandLegacyDeforms(JSON.parse(new TextDecoder().decode(skeletonBytes)))
        const data=new this.core.SkeletonJson(new this.core.AtlasAttachmentLoader(atlas)).readSkeletonData(raw)
        // Legacy JSON reader omits setup-pose flips; retain the binary's values.
        data.bones.forEach((bone:Legacy,i:number)=>{bone.flipX=raw.bones[i].flipX;bone.flipY=raw.bones[i].flipY})
        data.animations.forEach((animation:Legacy)=>{animation.duration=asset.animations[animation.name].duration})
        const skeleton=new this.core.Skeleton(data)
        // This legacy build stores the deform array on Slot.prototype. Each
        // slot must own it, otherwise the final FFD track overwrites all slots.
        skeleton.slots.forEach((slot:Legacy)=>{slot.attachmentVertices=[]})
        const actor={binding,asset,skeleton,container:new Container(),meshes:new Map<Legacy,Mesh>(),bounds:[0,0,1,1]}
        this.actors.push(actor);this.app.stage.addChild(actor.container)
        this.pose(actor,0,binding.animation,true)
        actor.bounds=this.bounds(actor)
        const [minX,minY,maxX,maxY]=actor.bounds
        const scale=Math.min(305/Math.max(1,maxY-minY),240/Math.max(1,maxX-minX))
        actor.container.scale.set(scale,-scale)
        actor.container.position.set(binding.x*this.width-(minX+maxX)/2*scale,446+minY*scale)
      }
      this.renderAt(0)
    } catch(error) {this.destroy();throw error}
  }

  private vertices(attachment:Legacy,slot:Legacy,skeleton:Legacy):Float32Array|null {
    if(attachment.type===this.core.AttachmentType.region) {
      const out=new Float32Array(8);attachment.computeVertices(skeleton.x,skeleton.y,slot.bone,out);return out
    }
    if(attachment.type===this.core.AttachmentType.mesh||attachment.type===this.core.AttachmentType.skinnedmesh) {
      const out=new Float32Array(attachment.uvs.length);attachment.computeWorldVertices(skeleton.x,skeleton.y,slot,out);return out
    }
    return null
  }

  private pose(actor:typeof this.actors[number],t:number,name:string,loop:boolean) {
    const skeleton=actor.skeleton,animation=skeleton.data.findAnimation(name)
    if(!animation)throw new Error(`素材没有动作：${name}`)
    // Single-base-track profile is deliberately stateless. Reset slots, draw
    // order, bones, and IK before sampling; suppress all external sound events.
    skeleton.setToSetupPose()
    animation.apply(skeleton,-1,actorTime(t,animation.duration,loop),false,null)
    skeleton.updateWorldTransform()
    actor.container.removeChildren()
    for(const slot of skeleton.drawOrder) {
      const attachment=slot.attachment;if(!attachment)continue
      const positions=this.vertices(attachment,slot,skeleton);if(!positions)continue
      let mesh=actor.meshes.get(attachment)
      if(!mesh) {
        const geometry=new MeshGeometry({positions,uvs:new Float32Array(attachment.uvs),
          indices:new Uint32Array(attachment.type===this.core.AttachmentType.region?[0,1,2,2,3,0]:attachment.triangles)})
        mesh=new Mesh({geometry,texture:attachment.rendererObject.page.rendererObject})
        actor.meshes.set(attachment,mesh)
      } else {mesh.geometry.positions.set(positions);mesh.geometry.getBuffer('aPosition').update()}
      const color=[slot.r*attachment.r,slot.g*attachment.g,slot.b*attachment.b].map(v=>Math.round(Math.max(0,Math.min(1,v))*255))
      mesh.tint=(color[0]<<16)|(color[1]<<8)|color[2]
      mesh.alpha=slot.a*attachment.a
      mesh.blendMode=slot.data.additiveBlending?'add':'normal'
      actor.container.addChild(mesh)
    }
  }

  private bounds(actor:typeof this.actors[number]) {
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity
    for(const slot of actor.skeleton.drawOrder) {
      if(!slot.attachment)continue
      const vertices=this.vertices(slot.attachment,slot,actor.skeleton);if(!vertices)continue
      for(let i=0;i<vertices.length;i+=2){minX=Math.min(minX,vertices[i]);maxX=Math.max(maxX,vertices[i]);minY=Math.min(minY,vertices[i+1]);maxY=Math.max(maxY,vertices[i+1])}
    }
    if(!Number.isFinite(minX))throw new Error('演员没有可显示的贴图')
    return [minX,minY,maxX,maxY]
  }

  renderAt(seconds:number,activeRoles:string[]=[],sample?:{roleId:string;animation:string;loop?:boolean}) {
    if(this.disposed||!this.initialized)return
    for(const actor of this.actors) {
      const selected=sample?.roleId===actor.binding.role_id
      this.pose(actor,seconds,selected?sample!.animation:actor.binding.animation,selected?(sample!.loop??true):actor.binding.loop)
      // Dim color, not child alpha: alpha would reveal overlapping face/hair
      // layers inside an otherwise opaque character.
      if(activeRoles.length&&!activeRoles.includes(actor.binding.role_id)){
        for(const child of actor.container.children as Mesh[]){
          const tint=child.tint
          child.tint=(Math.round(((tint>>16)&255)*.72)<<16)|(Math.round(((tint>>8)&255)*.72)<<8)|Math.round((tint&255)*.72)
        }
      }
    }
    this.app.render()
  }

  snapshot() {
    return this.actors.map(a=>({role_id:a.binding.role_id,bones:a.skeleton.bones.map((b:Legacy)=>[b.worldX,b.worldY,b.m00,b.m01,b.m10,b.m11]),
      slots:a.skeleton.drawOrder.map((s:Legacy)=>s.attachment?.name||null)}))
  }

  private cleanup() {
    for(const actor of this.actors){for(const mesh of actor.meshes.values()){mesh.geometry.destroy();mesh.destroy()}actor.container.destroy()}
    this.actors=[]
    for(const texture of this.textures)texture.destroy(true)
    this.textures=[]
    if(this.moduleUrl)URL.revokeObjectURL(this.moduleUrl)
    if(this.initialized){this.app.destroy(true,{children:true});this.initialized=false}
  }
  destroy(){this.disposed=true;this.cleanup()}
}
