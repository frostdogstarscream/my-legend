import {expect,it} from 'vitest'
import {lifePresentation} from '../src/services/life-presentation'
import type {LifeCatalog} from '../../my-legend-stage/src/life-types'

it('uses current cosmetic labels and portraits without changing saved actor bindings',()=>{
 const old={catalog_sha256:'old',actors:[{actor_id:'gfl-guofeng-405',display_name:'95式 · 玉玲珑',revision:'saved',thumbnail:{sha256:'battle'}}],
 outfits:[{actor_id:'gfl-guofeng-405',outfit_id:'native',display_name:'95式 · 玉玲珑',revision:'saved-outfit',thumbnail:{sha256:'battle'}}]} as LifeCatalog
 const current={...old,actors:[{...old.actors[0],display_name:'玉玲珑',revision:'new',thumbnail:{sha256:'life'}}],outfits:[{...old.outfits[0],display_name:'玉玲珑',thumbnail:{sha256:'life'}}]} as LifeCatalog
 const shown=lifePresentation(old,current)
 expect(shown.catalog_sha256).toBe('old');expect(shown.actors[0].revision).toBe('saved')
 expect(shown.outfits[0].revision).toBe('saved-outfit')
 expect(shown.actors[0].display_name).toBe('玉玲珑');expect(shown.outfits[0].thumbnail?.sha256).toBe('life')
 expect(old.actors[0].thumbnail?.sha256).toBe('battle')
})
