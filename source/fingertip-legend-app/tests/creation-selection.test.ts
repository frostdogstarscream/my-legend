import {beforeEach,expect,it,vi} from 'vitest'
vi.mock('../src/stores/session',()=>({sessionState:{session:{user:{id:'alice'}}}}))
import {sessionState} from '../src/stores/session'
import {activateSelection,migrateSelection,readSelection,toggleSelection,finishSelection,selectionScope} from '../src/stores/creation-selection'
beforeEach(()=>{const values=new Map();vi.stubGlobal('uni',{getStorageSync:(k:string)=>values.get(k),setStorageSync:(k:string,v:unknown)=>values.set(k,v),removeStorageSync:(k:string)=>values.delete(k)});sessionState.session!.user.id='alice'})
it('retains order, removes selections and limits the cast to six',()=>{for(let n=0;n<6;n++)expect(toggleSelection(String(n))).toBe(true);expect(toggleSelection('six')).toBe(false);toggleSelection('2');toggleSelection('six');expect(readSelection()).toEqual(['0','1','3','4','5','six'])})
it('isolates users and drafts and clears submitted selection',()=>{activateSelection('draft-a',['a']);activateSelection('draft-b',['b']);activateSelection('draft-a');expect(readSelection()).toEqual(['a']);sessionState.session!.user.id='bob';expect(readSelection()).toEqual([]);activateSelection('draft-a',['other']);sessionState.session!.user.id='alice';expect(readSelection()).toEqual(['a']);finishSelection();expect(selectionScope()).toBe('new');expect(readSelection()).toEqual([])})
it('moves a new selection into its saved draft without leaving a stale new cast',()=>{toggleSelection('a');migrateSelection('saved',['a']);expect(readSelection()).toEqual(['a']);finishSelection();expect(readSelection()).toEqual([])})
