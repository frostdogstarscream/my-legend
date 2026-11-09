import {goPage} from './navigation'
import type {WorkSummary,WorkDestination} from '../types/works'
export const workStatus:Record<string,string>={draft:'创作草稿',queued:'等待制作',running:'制作中',succeeded:'已完成',failed:'制作失败',needs_input:'需要处理',cancelled:'已停止'}
export const versionKind:Record<string,string>={stage_basic:'基础舞台制作',restart:'重新制作',create:'首次制作',revise:'修改剧本',continue:'续写',remix:'改编',audio:'局部声音重制',audio_full:'整段声音重制',stage:'人物服装调整',stage_saved:'舞台编辑另存'}
export function openDestination(target:WorkDestination){goPage(target.page,target.query)}
export function defaultWorkDestination(work:WorkSummary){return work.status==='succeeded'&&work.play?work.play:work.open}

