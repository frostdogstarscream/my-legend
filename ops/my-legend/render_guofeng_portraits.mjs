// Export posed native dorm geometry for offline thumbnail rasterization.
import fs from 'node:fs'
import ts from '../../source/fingertip-legend-app/node_modules/typescript/lib/typescript.js'
const root='runtime/my-legend-spine',out='output/gfl-guofeng-presentation-20261102'
fs.mkdirSync(out,{recursive:true})
const read=p=>fs.readFileSync(`${root}/${p}`,'utf8')
const load=js=>import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))
const {installLegacyFixes,expandLegacyDeforms}=await load(ts.transpileModule(fs.readFileSync('source/my-legend-stage/src/legacy-fixes.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText)
const cat=JSON.parse(read(`catalogs/${JSON.parse(read('life-v2-current.json')).catalog_sha256}.json`))
const spine=(await load(read(`content/${cat.runtime.sha256}`))).default;installLegacyFixes(spine)
const portraits=[]
for(const actor of cat.actors.filter(a=>a.actor_id.startsWith('gfl-guofeng-'))){
 const asset=cat.skeletons.find(s=>s.asset_id===actor.asset_id)
 const raw=expandLegacyDeforms(JSON.parse(read(`content/${asset.skeleton.sha256}`)))
 const atlas=new spine.Atlas(read(`content/${asset.atlas.sha256}`),{load:(page,name)=>{const png=fs.readFileSync(`${root}/content/${asset.pages[name].sha256}`);page.width=png.readUInt32BE(16);page.height=png.readUInt32BE(20);page.rendererObject=asset.pages[name].sha256},unload:()=>{}})
 const data=new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(raw)
 data.bones.forEach((b,i)=>{b.flipX=raw.bones[i].flipX;b.flipY=raw.bones[i].flipY})
 const sk=new spine.Skeleton(data);sk.slots.forEach(s=>s.attachmentVertices=[])
 const action=actor.action_map.idle
 sk.setToSetupPose();data.findAnimation(action).apply(sk,-1,0,false,null);sk.updateWorldTransform()
 const meshes=[]
 for(const slot of sk.drawOrder){
  const a=slot.attachment;if(!a?.uvs)continue
  const v=new Float32Array(a.uvs.length)
  if(a.type===0)a.computeVertices(sk.x,sk.y,slot.bone,v);else a.computeWorldVertices(sk.x,sk.y,slot,v)
  meshes.push({slot:slot.data.name,attachment:a.name,page:a.rendererObject.page.rendererObject,
   vertices:Array.from(v),uvs:Array.from(a.uvs),triangles:Array.from(a.triangles||[0,1,2,2,3,0]),alpha:slot.a??1})
 }
 portraits.push({actor_id:actor.actor_id,skin_id:actor.source_skin_id,name:actor.display_name.split(' · ').at(-1),action,time:0,meshes})
}
fs.writeFileSync(`${out}/posed-meshes.json`,JSON.stringify(portraits))
console.log(`Exported ${portraits.length} native life portraits`)
