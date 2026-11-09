import {sessionState} from './session'
const user=()=>sessionState.session?.user.id
const activeKey=()=>`my-legend:active-draft:${user()}`
export const selectionScope=()=>uni.getStorageSync(activeKey())||'new'
const key=()=>`my-legend:selection:${user()}:${selectionScope()}`
export function readSelection():string[]{return [...(uni.getStorageSync(key())?.ids||[])]}
export function writeSelection(ids:string[]){if(user())uni.setStorageSync(key(),{ids:[...ids].slice(0,6)})}
export function hasSelection(){return !!uni.getStorageSync(key())}
export function activateSelection(scope:string,ids?:string[]){uni.setStorageSync(activeKey(),scope);if(ids)writeSelection(ids)}
export function migrateSelection(scope:string,ids:string[]){if(selectionScope()==='new'&&scope!=='new')uni.removeStorageSync(key());activateSelection(scope,ids)}
export function toggleSelection(id:string){const ids=readSelection();if(ids.includes(id))writeSelection(ids.filter(x=>x!==id));else if(ids.length<6)writeSelection([...ids,id]);else return false;return true}
export function finishSelection(){uni.removeStorageSync(key());uni.removeStorageSync(activeKey())}
