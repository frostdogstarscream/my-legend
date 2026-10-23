import {Container,ImageSource,Sprite,Texture,TilingSprite} from 'pixi.js'
import type {AssetRef} from './types'
import {sceneObjectDepth,type SceneDefinition} from './scene-types'

/** Source PNGs remain separate sprites; no flattened background screenshot. */
export class SceneDecor {
  private textures=new Map<string,Texture>()
  private sprites:(Sprite|TilingSprite)[]=[]
  private disposed=false
  constructor(readonly definition:SceneDefinition){}
  async mount(world:Container,fetchAsset:(ref:AssetRef)=>Promise<ArrayBuffer>){
    for(const [index,item] of this.definition.objects.entries()){
      if(this.disposed)return
      if(!this.textures.has(item.image.sha256)){
        const bytes=await fetchAsset(item.image),url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}))
        try{
          const img=new Image();img.src=url;await img.decode()
          if(this.disposed)return
          this.textures.set(item.image.sha256,new Texture({source:new ImageSource({resource:img,alphaMode:'premultiply-alpha-on-upload',scaleMode:'linear'})}))
        }finally{URL.revokeObjectURL(url)}
      }
      const texture=this.textures.get(item.image.sha256)!
      const sprite=item.repeat?new TilingSprite({texture,width:item.width,height:item.height}):new Sprite(texture)
      if(sprite instanceof TilingSprite)sprite.tileScale.set(item.tile_scale)
      sprite.anchor.set(...item.anchor);sprite.position.set(...item.position)
      sprite.width=item.width;sprite.height=item.height;sprite.tint=item.tint;sprite.zIndex=sceneObjectDepth(item,index)
      sprite.label=item.id;world.addChild(sprite);this.sprites.push(sprite)
    }
  }
  snapshot(){return {scene_id:this.definition.scene_id,object_count:this.sprites.length,objects:this.sprites.map(s=>({id:s.label,x:s.x,y:s.y,width:s.width,height:s.height,z:s.zIndex}))}}
  destroy(){
    this.disposed=true
    for(const s of this.sprites){s.removeFromParent();s.destroy()}
    this.sprites=[]
    for(const t of this.textures.values())t.destroy(true)
    this.textures.clear()
  }
}
