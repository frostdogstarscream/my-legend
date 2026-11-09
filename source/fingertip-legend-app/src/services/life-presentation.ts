import type {LifeCatalog} from '../../../my-legend-stage/src/life-types'

/** Cosmetic projection only: saved actor revisions, rigs and actions stay frozen. */
export function lifePresentation(saved:LifeCatalog,current:LifeCatalog):LifeCatalog {
 const actors=new Map(current.actors.map(a=>[a.actor_id,a]))
 const outfits=new Map(current.outfits.map(o=>[o.outfit_id,o]))
 return {...saved,actors:saved.actors.map(a=>a.actor_id.startsWith('gfl-guofeng-')?{...a,
   display_name:actors.get(a.actor_id)?.display_name.split(' · ').pop()||a.display_name.split(' · ').pop()!,
   thumbnail:actors.get(a.actor_id)?.thumbnail}:a),
   outfits:saved.outfits.map(o=>o.actor_id.startsWith('gfl-guofeng-')?{...o,
     display_name:outfits.get(o.outfit_id)?.display_name.split(' · ').pop()||o.display_name.split(' · ').pop()!,
     thumbnail:outfits.get(o.outfit_id)?.thumbnail}:o)}
}
